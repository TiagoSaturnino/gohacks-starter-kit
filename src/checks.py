"""Regras determinísticas do guardião (SPEC §4). Item 2: R1 a R3.

Contrato de toda regra:
    regra(agenda, base, pol) -> DataFrame com colunas exatas COLUNAS
    - pura: não lê disco, não altera as entradas, mesma entrada -> mesma saída;
    - uma linha por título alertado; `valor` = valor do título (o que deixa de sair);
    - `evidencia` curta e verificável (IDs e números comparados), sem CNPJ nem conta.

Nenhum ID, fornecedor, login ou limiar fixo: limiares vêm de `pol` (politicas.csv),
cadastros vêm de `base`.

Decisões tomadas com o humano antes do código (item 2):
- Tolerância título × duplicata: extraída da prosa de politicas.csv na carga
  (`carga.tolerancia_titulo_x_duplicata`), que falha alto se não conseguir ler.
- Acréscimo por atraso (R2): carência até o próximo dia útil após o vencimento da
  duplicata (`tres_vias.prazo`). Depois disso, o título pode valer a duplicata OU a
  duplicata + multa + juros pro rata pela fórmula exata, sempre ± tolerância.
- R1 em casos de borda: o título legítimo do grupo é o primeiro lançado que bate com a
  duplicata (se nenhum bate, o primeiro lançado). Todos os outros do grupo que estão na
  agenda são retidos. Cobre "nenhum bate", "legítimo já pago" e grupos com 3+ títulos.
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional, Set

import pandas as pd

from src.carga import Base, tolerancia_titulo_x_duplicata
from src.schemas import Severidade

COLUNAS = ["titulo_id", "regra", "severidade", "evidencia", "valor"]

# Origem de lançamento dispensada da R3 (SPEC §4, R3). Não é ID nem limiar: é um valor
# do domínio de `origem_lancamento` (dicionário de dados), citado explicitamente na SPEC.
ORIGENS_DISPENSADAS_NF = frozenset({"integracao_frete"})

_EPS = 1e-6  # folga de ponto flutuante nas comparações em R$


# --------------------------------------------------------------------- utilidades


def _vazio_saida() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUNAS)


def _saida(linhas: List[Dict[str, Any]]) -> pd.DataFrame:
    if not linhas:
        return _vazio_saida()
    df = pd.DataFrame(linhas, columns=COLUNAS)
    df["valor"] = df["valor"].astype(float)
    return df.sort_values(["titulo_id", "regra"], kind="stable").reset_index(drop=True)


def _brl(valor: float) -> str:
    """1234.5 -> 'R$ 1.234,50' (sem locale do sistema: saída igual em qualquer máquina)."""
    sinal = "-" if valor < 0 else ""
    inteiro, centavos = f"{abs(valor):,.2f}".split(".")
    return f"{sinal}R$ {inteiro.replace(',', '.')},{centavos}"


def _razao(a: float, b: float) -> str:
    return f"{a / b:.2f}×".replace(".", ",") if b else "?"


def _data(valor: Any) -> str:
    return pd.Timestamp(valor).strftime("%d/%m") if pd.notna(valor) else "?"


def _lista_politica(valor: Any) -> Set[str]:
    """'aluguel' ou 'aluguel, outra' -> {'aluguel', 'outra'}."""
    if valor is None:
        return set()
    if isinstance(valor, (list, tuple, set)):
        return {str(v).strip() for v in valor if str(v).strip()}
    return {p.strip() for p in re.split(r"[,;|]", str(valor)) if p.strip()}


def _dias_sem_remessa(base: Base) -> Set[pd.Timestamp]:
    """Feriados que param a remessa. Usa a coluna `afeta_remessa` quando existe."""
    fer = base.feriados
    if fer is None or fer.empty or "data" not in fer:
        return set()
    if "afeta_remessa" in fer:
        fer = fer[fer["afeta_remessa"].fillna("").str.strip().str.lower().str.startswith("sim")]
    return {pd.Timestamp(d).normalize() for d in fer["data"].dropna()}


def proximo_dia_util(data: pd.Timestamp, sem_remessa: Set[pd.Timestamp]) -> pd.Timestamp:
    """A própria data se for dia útil; senão, o próximo dia útil (sem fim de semana/feriado)."""
    dia = pd.Timestamp(data).normalize()
    while dia.weekday() >= 5 or dia in sem_remessa:
        dia += timedelta(days=1)
    return dia


def _titulos(agenda: pd.DataFrame, base: Base) -> pd.DataFrame:
    """Agenda + campos do título no ERP (NF, parcela, lançamento) + categoria do fornecedor."""
    campos_erp = ["titulo_id", "numero_nf_digitado", "nf_norm", "parcela", "parcela_n",
                  "origem_lancamento", "lancado_por", "data_lancamento", "status"]
    erp = base.contas[[c for c in campos_erp if c in base.contas]]
    forn = base.fornecedores[["fornecedor_id", "cnpj_raiz", "categoria"]].drop_duplicates("fornecedor_id")
    df = agenda.merge(erp, on="titulo_id", how="left").merge(forn, on="fornecedor_id", how="left")
    return df


def _casar_duplicata(df: pd.DataFrame, base: Base) -> pd.DataFrame:
    """Acha a NF no portal (raiz do CNPJ + NF normalizada) e a duplicata da parcela.

    Acrescenta: chave_acesso, numero_nf_portal, n_dup_esperado, valor_dup, venc_dup.
    `df` precisa de cnpj_raiz, nf_norm e parcela_n. Se a mesma raiz + NF aparecer em mais
    de uma nota (não ocorre no cenário A), fica a de menor chave_acesso (determinístico).
    """
    notas = (base.notas[["cnpj_emitente_raiz", "nf_norm", "chave_acesso", "numero_nf"]]
             .dropna(subset=["cnpj_emitente_raiz", "nf_norm"])
             .sort_values("chave_acesso")
             .drop_duplicates(["cnpj_emitente_raiz", "nf_norm"])
             .rename(columns={"cnpj_emitente_raiz": "cnpj_raiz", "numero_nf": "numero_nf_portal"}))
    out = df.merge(notas, on=["cnpj_raiz", "nf_norm"], how="left")
    out["n_dup_esperado"] = out["parcela_n"].map(
        lambda n: str(int(n)).zfill(3) if pd.notna(n) else None
    )
    dups = (base.duplicatas[["chave_acesso", "n_dup", "valor", "vencimento"]]
            .assign(n_dup=lambda d: d["n_dup"].astype(str).str.strip().str.zfill(3))
            .drop_duplicates(["chave_acesso", "n_dup"])
            .rename(columns={"n_dup": "n_dup_esperado", "valor": "valor_dup",
                             "vencimento": "venc_dup"}))
    return out.merge(dups, on=["chave_acesso", "n_dup_esperado"], how="left")


def conferir_valor(valor: float, valor_dup: float, venc_dup: Any, data_pagamento: Any,
                   tol: float, multa: float, juros_mes: float,
                   sem_remessa: Set[pd.Timestamp]) -> Dict[str, Any]:
    """Confere o valor do título com a duplicata (R2; também decide o 'legítimo' da R1).

    Bate se |valor − duplicata| ≤ tol. Se a data de pagamento passa do próximo dia útil
    após o vencimento da duplicata, também bate se |valor − esperado| ≤ tol, com
    esperado = duplicata × (1 + multa + juros_mes × dias/30), dias contados do vencimento.
    """
    resultado = {"bate": abs(valor - valor_dup) <= tol + _EPS, "dias_atraso": 0, "esperado": None}
    if resultado["bate"] or pd.isna(venc_dup) or pd.isna(data_pagamento):
        return resultado
    venc = pd.Timestamp(venc_dup).normalize()
    pagto = pd.Timestamp(data_pagamento).normalize()
    if pagto <= proximo_dia_util(venc, sem_remessa):
        return resultado  # dentro do prazo: só vale o valor da duplicata
    dias = (pagto - venc).days
    esperado = round(valor_dup * (1 + multa + juros_mes * dias / 30), 2)
    resultado.update(dias_atraso=dias, esperado=esperado,
                     bate=abs(valor - esperado) <= tol + _EPS)
    return resultado


def _parametros_valor(pol: Dict[str, Any]) -> Dict[str, float]:
    atraso = pol.get("atraso") or {}
    if "multa" not in atraso or "juros_ao_mes" not in atraso:
        raise ValueError("politicas: faltam atraso.multa e/ou atraso.juros_ao_mes")
    return {"tol": tolerancia_titulo_x_duplicata(pol),
            "multa": float(atraso["multa"]), "juros_mes": float(atraso["juros_ao_mes"])}


# --------------------------------------------------------------------- R1


def r1_duplicidade(agenda: pd.DataFrame, base: Base, pol: Dict[str, Any]) -> pd.DataFrame:
    """Mesma (fornecedor, NF normalizada, parcela) em mais de um título não cancelado,
    com pelo menos um na agenda. Retém os títulos da agenda que não são o legítimo."""
    params = _parametros_valor(pol)
    sem_remessa = _dias_sem_remessa(base)

    contas = base.contas
    vivos = contas[(contas["status"].fillna("") != "cancelado") & contas["nf_norm"].notna()
                   & contas["parcela"].notna()]
    chave = ["fornecedor_id", "nf_norm", "parcela"]
    tamanho = vivos.groupby(chave)["titulo_id"].transform("size")
    grupos = vivos[tamanho > 1]
    ids_agenda = set(agenda["titulo_id"])
    grupos = grupos[grupos.groupby(chave)["titulo_id"].transform(lambda s: s.isin(ids_agenda).any())]
    if grupos.empty:
        return _vazio_saida()

    forn = base.fornecedores[["fornecedor_id", "cnpj_raiz"]].drop_duplicates("fornecedor_id")
    cand = _casar_duplicata(grupos.merge(forn, on="fornecedor_id", how="left"), base)
    # Data de pagamento de referência: a prevista na agenda; senão a efetiva; senão o vencimento.
    prevista = agenda.set_index("titulo_id")["data_prevista"]
    valor_agenda = agenda.set_index("titulo_id")["valor"]
    cand["data_ref"] = (cand["titulo_id"].map(prevista)
                        .fillna(cand["data_pagamento"]).fillna(cand["vencimento"]))
    cand["valor_ref"] = cand["titulo_id"].map(valor_agenda).fillna(cand["valor"])
    cand["bate"] = [
        pd.notna(vd) and conferir_valor(v, vd, venc, dr, sem_remessa=sem_remessa, **params)["bate"]
        for v, vd, venc, dr in zip(cand["valor_ref"], cand["valor_dup"], cand["venc_dup"],
                                   cand["data_ref"])
    ]

    linhas = []
    for (_, nf, parcela), g in cand.groupby(chave, sort=True):
        g = g.sort_values(["data_lancamento", "titulo_id"], kind="stable")
        batem = g[g["bate"]]
        legitimo = (batem if not batem.empty else g).iloc[0]
        for t in g.itertuples(index=False):
            if t.titulo_id == legitimo.titulo_id or t.titulo_id not in ids_agenda:
                continue
            criterio = ("este não bate com a duplicata" if not t.bate and legitimo.bate
                        else "ambos batem com a duplicata; este foi lançado depois" if t.bate
                        else "nenhum bate com a duplicata; este foi lançado depois")
            evid = (f"NF {nf} parc {parcela} também em {legitimo.titulo_id} "
                    f"({legitimo.origem_lancamento}, {legitimo.lancado_por}, "
                    f"{_data(legitimo.data_lancamento)}, {legitimo.status}, "
                    f"{_brl(legitimo.valor_ref)}); este via {t.origem_lancamento} "
                    f"({t.lancado_por}, {_data(t.data_lancamento)}, {_brl(t.valor_ref)}); {criterio}")
            linhas.append({"titulo_id": t.titulo_id, "regra": "R1_duplicidade",
                           "severidade": Severidade.BLOQUEIA.value, "evidencia": evid,
                           "valor": float(t.valor_ref)})
    return _saida(linhas)


# --------------------------------------------------------------------- R2


def r2_valor_x_duplicata(agenda: pd.DataFrame, base: Base, pol: Dict[str, Any]) -> pd.DataFrame:
    """Valor do título ≠ duplicata da parcela (± tolerância e acréscimo legítimo por atraso).

    Título sem NF no portal fica com a R3. NF encontrada sem a duplicata da parcela
    também é alerta: o título não tem com o que bater.
    """
    params = _parametros_valor(pol)
    sem_remessa = _dias_sem_remessa(base)
    df = _casar_duplicata(_titulos(agenda, base), base)
    df = df[df["chave_acesso"].notna()]

    linhas = []
    for t in df.itertuples(index=False):
        nf = t.numero_nf_portal if pd.notna(t.numero_nf_portal) else t.nf_norm
        if pd.isna(t.valor_dup):
            evid = (f"NF {nf} não tem a duplicata {t.n_dup_esperado} "
                    f"(título parc {t.parcela}, {_brl(t.valor)})")
        else:
            conf = conferir_valor(t.valor, t.valor_dup, t.venc_dup, t.data_prevista,
                                  sem_remessa=sem_remessa, **params)
            if conf["bate"]:
                continue
            dif = t.valor - t.valor_dup
            evid = (f"título {_brl(t.valor)} × duplicata {t.n_dup_esperado} da NF {nf} "
                    f"{_brl(t.valor_dup)}: diferença {'+' if dif > 0 else ''}{_brl(dif)} "
                    f"({_razao(t.valor, t.valor_dup)}); tolerância {_brl(params['tol'])}")
            if conf["esperado"] is not None:
                evid += (f"; pago {conf['dias_atraso']} dias após o vencimento da duplicata "
                         f"({_data(t.venc_dup)}), com multa+juros seria {_brl(conf['esperado'])}")
        linhas.append({"titulo_id": t.titulo_id, "regra": "R2_valor_x_duplicata",
                       "severidade": Severidade.BLOQUEIA.value, "evidencia": evid,
                       "valor": float(t.valor)})
    return _saida(linhas)


# --------------------------------------------------------------------- R3


def r3_nf_inexistente(agenda: pd.DataFrame, base: Base, pol: Dict[str, Any]) -> pd.DataFrame:
    """NF digitada não encontrada no portal para a raiz do CNPJ do fornecedor.

    Dispensas: categorias em tres_vias.dispensas.nf e origens em ORIGENS_DISPENSADAS_NF.
    NF não informada (vazia) fora das dispensas também é alerta.
    """
    dispensas_cat = _lista_politica(((pol.get("tres_vias") or {}).get("dispensas") or {}).get("nf"))
    df = _titulos(agenda, base)
    df = df[~df["categoria"].isin(dispensas_cat)
            & ~df["origem_lancamento"].isin(ORIGENS_DISPENSADAS_NF)]
    no_portal = set(zip(base.notas["cnpj_emitente_raiz"], base.notas["nf_norm"]))

    linhas = []
    for t in df.itertuples(index=False):
        if pd.notna(t.nf_norm) and pd.notna(t.cnpj_raiz) and (t.cnpj_raiz, t.nf_norm) in no_portal:
            continue
        if pd.isna(t.nf_norm):
            evid = f"título sem número de NF (categoria {t.categoria}, {t.origem_lancamento}, {t.lancado_por})"
        else:
            evid = (f"NF '{t.numero_nf_digitado}' (normalizada {t.nf_norm}) não está no portal "
                    f"para o CNPJ de {t.fornecedor_id}; lançada via {t.origem_lancamento} "
                    f"por {t.lancado_por}")
        linhas.append({"titulo_id": t.titulo_id, "regra": "R3_nf_inexistente",
                       "severidade": Severidade.BLOQUEIA.value, "evidencia": evid,
                       "valor": float(t.valor)})
    return _saida(linhas)


# --------------------------------------------------------------------- todas

REGRAS: List[Callable[[pd.DataFrame, Base, Dict[str, Any]], pd.DataFrame]] = [
    r1_duplicidade, r2_valor_x_duplicata, r3_nf_inexistente,
]


def rodar_regras(agenda: pd.DataFrame, base: Base, pol: Dict[str, Any],
                 regras: Optional[List[Callable]] = None) -> pd.DataFrame:
    """Concatena a saída das regras (um título pode aparecer em mais de uma regra)."""
    saidas = [r(agenda, base, pol) for r in (regras or REGRAS)]
    saidas = [s for s in saidas if not s.empty]
    return pd.concat(saidas, ignore_index=True) if saidas else _vazio_saida()
