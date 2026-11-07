"""Regras determinísticas do guardião (SPEC §4). Item 2: R1 a R3. Item 3: R4 a R8 + A1.

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

Decisões tomadas com o humano antes do código (item 3):
- R5: o "antes" vale para o TELEFONE, não para a ligação. A ligação pode ser posterior à
  gravação no ERP, desde que vá para o telefone vigente antes de qualquer troca recente
  (o telefone do cadastro N dias antes da alteração, N de dados_bancarios.troca_de_conta).
- R4: recebimento só é exigido de NF achada no portal com modelo de mercadoria (NF-e 55).
  NF fora do portal fica com a R3 (sem alerta duplicado).
- A1: "admitido há menos de N dias" é medido na data de lançamento do título (era novato
  quando lançou). N vem de pol['guardiao']['dias_lancador_recente'], injetado pelo guardião
  a partir do config; ausente -> ValueError.
- R8: segue a letra da SPEC (lançou/alterou e aprovou; alteração bancária no log fora de
  alteracoes_cadastro). "Alterou e lançou" não entra.
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional, Set

import pandas as pd

from src.carga import Base, dias_telefone_recente, tolerancia_nf_x_oc, tolerancia_titulo_x_duplicata
from src.schemas import Severidade

COLUNAS = ["titulo_id", "regra", "severidade", "evidencia", "valor"]

# Origem de lançamento dispensada da R3 (SPEC §4, R3). Não é ID nem limiar: é um valor
# do domínio de `origem_lancamento` (dicionário de dados), citado explicitamente na SPEC.
ORIGENS_DISPENSADAS_NF = frozenset({"integracao_frete"})

# Valores de domínio do dicionário de dados (não são IDs nem limiares):
STATUS_OC_INVALIDOS = frozenset({"cancelada", "pendente_aprovacao"})  # pedidos_compra.status
MODELOS_COM_RECEBIMENTO = frozenset({"55"})            # NF-e de mercadoria (notas_fiscais.modelo)
SITUACAO_DDA_PAGAVEL = "em aberto"                     # dda_boletos.situacao
PERFIS_SERVICO = frozenset({"integracao"})             # usuarios_sistema.perfil (contas de serviço)
# Campos de alteracoes_cadastro que definem o destino de cada forma de pagamento.
# Boleto não usa a conta do cadastro (SPEC R5): fica com a R6.
CAMPOS_DESTINO = {"ted": frozenset({"banco", "agencia", "conta"}),
                  "pix": frozenset({"chave_pix"})}
CAMPOS_BANCARIOS = frozenset().union(*CAMPOS_DESTINO.values())

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
                  "origem_lancamento", "lancado_por", "data_lancamento", "status", "oc_id",
                  "linha_digitavel", "cnpj_beneficiario_boleto_raiz"]
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


# --------------------------------------------------------------------- utilidades (item 3)


def _linha(titulo_id: str, regra: str, sev: Severidade, evid: str, valor: float) -> Dict[str, Any]:
    return {"titulo_id": titulo_id, "regra": regra, "severidade": sev.value,
            "evidencia": evid, "valor": float(valor)}


def _digitos(valor: Any) -> str:
    return re.sub(r"\D", "", "" if pd.isna(valor) else str(valor))


def _tel_mascarado(valor: Any) -> str:
    """'(85) 3836-0134' -> '(85) ****-0134' (PII: só DDD e 4 finais na evidência)."""
    d = _digitos(valor)
    return f"({d[:2]}) ****-{d[-4:]}" if len(d) >= 10 else "telefone ?"


def _servico(base: Base) -> Set[str]:
    """Logins de contas de serviço (perfil em PERFIS_SERVICO), que não contam na R8/A1."""
    u = base.usuarios
    if u is None or u.empty or "perfil" not in u:
        return set()
    return set(u.loc[u["perfil"].isin(PERFIS_SERVICO), "login"])


def _notas_por_nf(base: Base) -> pd.DataFrame:
    """Uma nota por (raiz, NF normalizada), mesma escolha determinística de _casar_duplicata."""
    cols = [c for c in ("cnpj_emitente_raiz", "nf_norm", "chave_acesso", "numero_nf", "modelo",
                        "valor_total") if c in base.notas]
    return (base.notas[cols].dropna(subset=["cnpj_emitente_raiz", "nf_norm"])
            .sort_values("chave_acesso").drop_duplicates(["cnpj_emitente_raiz", "nf_norm"])
            .rename(columns={"cnpj_emitente_raiz": "cnpj_raiz", "numero_nf": "numero_nf_portal",
                             "valor_total": "valor_nf"}))


# --------------------------------------------------------------------- R4


def r4_oc_x_nf(agenda: pd.DataFrame, base: Base, pol: Dict[str, Any]) -> pd.DataFrame:
    """Três vias OC × NF × recebimento (ACIONA). Uma linha por título, motivos juntos.

    - sem OC, OC inexistente em Compras, OC cancelada ou pendente de aprovação;
    - NF acima da OC além de max(percentual, R$) de tres_vias.nf_x_oc; contrato compara
      com valor_mensal, pontual com valor_total;
    - NF-e de mercadoria (modelo 55) achada no portal sem entrada em recebimentos_mercadoria.
    Dispensa: categorias em tres_vias.dispensas.oc. NF fora do portal fica com a R3.
    """
    tol = tolerancia_nf_x_oc(pol)
    dispensas_oc = _lista_politica(((pol.get("tres_vias") or {}).get("dispensas") or {}).get("oc"))
    df = _titulos(agenda, base)
    df = df[~df["categoria"].isin(dispensas_oc)]
    ocs = (base.pedidos[["oc_id", "status", "tipo", "valor_total", "valor_mensal"]]
           .drop_duplicates("oc_id")
           .rename(columns={"status": "status_oc", "tipo": "tipo_oc", "valor_total": "valor_oc",
                            "valor_mensal": "mensal_oc"}))
    df = df.merge(ocs, on="oc_id", how="left").merge(_notas_por_nf(base), on=["cnpj_raiz", "nf_norm"],
                                                    how="left")
    rec = base.recebimentos
    recebidas = set(zip(rec["cnpj_emitente_raiz"], rec["nf_norm"])) if not rec.empty else set()
    ocs_existentes = set(ocs["oc_id"])

    linhas = []
    for t in df.itertuples(index=False):
        motivos = []
        nf = t.numero_nf_portal if pd.notna(t.numero_nf_portal) else t.nf_norm
        if pd.isna(t.oc_id):
            motivos.append(f"título sem OC (categoria {t.categoria} não é dispensada)")
        elif t.oc_id not in ocs_existentes:
            motivos.append(f"{t.oc_id} não existe em Compras")
        elif t.status_oc in STATUS_OC_INVALIDOS:
            motivos.append(f"{t.oc_id} está {t.status_oc}")
        if pd.notna(t.valor_nf) and pd.notna(t.tipo_oc):
            contrato = str(t.tipo_oc).strip().lower() == "contrato"
            ref = t.mensal_oc if contrato else t.valor_oc
            if pd.notna(ref):
                folga = max(tol["percentual"] * ref, tol["reais"])
                if t.valor_nf > ref + folga + _EPS:
                    motivos.append(
                        f"NF {nf} {_brl(t.valor_nf)} acima de {t.oc_id} "
                        f"({'mensal ' if contrato else ''}{_brl(ref)}) em {_brl(t.valor_nf - ref)}; "
                        f"tolerância {_brl(folga)}")
        if (pd.notna(t.chave_acesso) and str(t.modelo).strip() in MODELOS_COM_RECEBIMENTO
                and (t.cnpj_raiz, t.nf_norm) not in recebidas):
            motivos.append(f"NF-e {nf} sem entrada de mercadoria no recebimento")
        if motivos:
            linhas.append(_linha(t.titulo_id, "R4_oc_x_nf", Severidade.ACIONA,
                                 "; ".join(motivos), t.valor))
    return _saida(linhas)


# --------------------------------------------------------------------- R5

_TELEFONE = re.compile(r"\(?\d{2}\)?\s*9?\d{4}-?\d{4}")


def _telefone_vigente(historico: pd.DataFrame, cadastro: Any, quando: pd.Timestamp) -> Any:
    """Telefone do cadastro no instante `quando`, a partir do histórico de trocas (ordenado)."""
    antes = historico[historico["data_hora"] <= quando]
    if not antes.empty:
        return antes.iloc[-1]["valor_novo"]
    if not historico.empty:
        return historico.iloc[0]["valor_anterior"]
    return cadastro


def _problema_troca(evento: pd.DataFrame, historico_tel: pd.DataFrame, tel_cadastro: Any,
                    dias: int) -> Optional[str]:
    """None se a troca bancária tem confirmação válida; senão, o motivo (curto, sem PII)."""
    quando = evento["data_hora"].iloc[0]
    registrada = evento["confirmacao_registrada"].fillna("").str.strip().str.lower()
    textos = evento["confirmacao_como"].dropna()
    texto = str(textos.iloc[0]) if not textos.empty else ""
    if not registrada.str.startswith("sim").any():
        return "sem confirmação registrada"
    if "liga" not in texto.lower():
        return "confirmação registrada sem ligação (ex.: e-mail), que não vale para troca bancária"
    achado = _TELEFONE.search(texto)
    if not achado:
        return "ligação registrada sem telefone identificável"
    chamado = _digitos(achado.group(0))
    referencia = _telefone_vigente(historico_tel, tel_cadastro, quando - timedelta(days=dias))
    if chamado == _digitos(referencia):
        return None
    recente = historico_tel[historico_tel["valor_novo"].map(_digitos) == chamado]
    if not recente.empty:
        troca = recente.iloc[-1]["data_hora"]
        delta = (quando - troca).days
        quando_txt = (f"{delta} dias antes da alteração (< {dias})" if troca <= quando
                      else "depois da alteração")
        return (f"ligação para {_tel_mascarado(chamado)}, telefone trocado em {_data(troca)}, "
                f"{quando_txt}; o válido era {_tel_mascarado(referencia)}")
    return (f"ligação para {_tel_mascarado(chamado)}, que não era o telefone do cadastro "
            f"antes da alteração ({_tel_mascarado(referencia)})")


def r5_conta_alterada_sem_confirmacao_valida(agenda: pd.DataFrame, base: Base,
                                             pol: Dict[str, Any]) -> pd.DataFrame:
    """Título ted/pix cujo destino (última troca de banco/agência/conta, ou de chave pix)
    não tem confirmação válida (BLOQUEIA).

    Válida = confirmação registrada, por ligação, para o telefone do cadastro N dias antes
    da alteração (N de dados_bancarios.troca_de_conta). A data da ligação não importa.
    """
    dias = dias_telefone_recente(pol)
    alt = base.alteracoes
    if alt.empty:
        return _vazio_saida()
    alt = alt.sort_values(["data_hora", "alteracao_id"], kind="stable")
    tel_cadastro = base.fornecedores.drop_duplicates("fornecedor_id").set_index("fornecedor_id")[
        "telefone_cadastrado"]
    df = agenda[agenda["forma_pagamento"].isin(CAMPOS_DESTINO)]

    cache: Dict[tuple, Optional[str]] = {}
    linhas = []
    for t in df.itertuples(index=False):
        chave = (t.fornecedor_id, t.forma_pagamento)
        if chave not in cache:
            do_forn = alt[alt["fornecedor_id"] == t.fornecedor_id]
            trocas = do_forn[do_forn["campo"].isin(CAMPOS_DESTINO[t.forma_pagamento])]
            if trocas.empty:
                cache[chave] = None
            else:
                evento = trocas[trocas["data_hora"] == trocas["data_hora"].max()]
                problema = _problema_troca(evento, do_forn[do_forn["campo"] == "telefone"],
                                           tel_cadastro.get(t.fornecedor_id), dias)
                ev = evento.iloc[0]
                cache[chave] = None if problema is None else (
                    f"{t.forma_pagamento} para destino alterado em "
                    f"{pd.Timestamp(ev.data_hora):%d/%m %H:%M} "
                    f"({', '.join(sorted(evento['campo']))}; {ev.alteracao_id}, origem {ev.origem}, "
                    f"por {ev.alterado_por}): {problema}")
        if cache[chave]:
            linhas.append(_linha(t.titulo_id, "R5_conta_alterada_sem_confirmacao_valida",
                                 Severidade.BLOQUEIA, cache[chave], t.valor))
    return _saida(linhas)


# --------------------------------------------------------------------- R6


def r6_boleto_divergente(agenda: pd.DataFrame, base: Base, pol: Dict[str, Any]) -> pd.DataFrame:
    """Boleto que não deve ser pago como está (BLOQUEIA). Uma linha por título, motivos juntos.

    - linha digitável fora do DDA, ou DDA em situação diferente de 'em aberto';
    - raiz do CNPJ do beneficiário (boleto ou DDA) diferente da do fornecedor;
    - banco do boleto (posições 1-3) fora dos bancos de cobrança já pagos do fornecedor
      E diferente do banco do cadastro.
    Evidência sem linha digitável nem CNPJ (só códigos de banco e o id do DDA).
    """
    df = _titulos(agenda[agenda["forma_pagamento"] == "boleto"], base)
    if df.empty:
        return _vazio_saida()
    df = df.merge(base.fornecedores[["fornecedor_id", "banco"]].drop_duplicates("fornecedor_id"),
                  on="fornecedor_id", how="left")
    df["linha_num"] = df["linha_digitavel"].map(_digitos)
    dda = base.dda.assign(linha_num=base.dda["linha_digitavel"].map(_digitos))
    dda = dda.sort_values("dda_id").drop_duplicates("linha_num")[
        ["linha_num", "dda_id", "situacao", "data_situacao", "cnpj_beneficiario_raiz"]]
    df = df.merge(dda, on="linha_num", how="left")

    contas = base.contas
    pagos = contas[(contas["status"] == "pago") & (contas["forma_pagamento"] == "boleto")]
    pagos = pagos.assign(banco_bol=pagos["linha_digitavel"].map(_digitos).str[:3])
    pagos = pagos[pagos["banco_bol"].str.len() == 3]
    historico = pagos.groupby("fornecedor_id")["banco_bol"].agg(lambda s: sorted(set(s))).to_dict()

    linhas = []
    for t in df.itertuples(index=False):
        motivos = []
        if not t.linha_num:
            motivos.append("boleto sem linha digitável")
        elif pd.isna(t.dda_id):
            motivos.append("linha digitável não está no DDA")
        elif str(t.situacao).strip().lower() != SITUACAO_DDA_PAGAVEL:
            motivos.append(f"DDA {t.dda_id} {t.situacao} em {_data(t.data_situacao)}")
        raizes = {r for r in (t.cnpj_beneficiario_boleto_raiz, t.cnpj_beneficiario_raiz) if pd.notna(r)}
        if pd.notna(t.cnpj_raiz) and any(r != t.cnpj_raiz for r in raizes):
            motivos.append("beneficiário com raiz de CNPJ diferente da do fornecedor")
        banco_bol = t.linha_num[:3] if t.linha_num else ""
        bancos_hist = historico.get(t.fornecedor_id, [])
        banco_cad = _digitos(t.banco).zfill(3) if pd.notna(t.banco) else ""
        if banco_bol and banco_bol not in bancos_hist and banco_bol != banco_cad:
            motivos.append(f"banco do boleto {banco_bol} fora do histórico de cobrança "
                           f"({', '.join(bancos_hist) or 'nenhum'}) e ≠ banco do cadastro "
                           f"({banco_cad or '?'})")
        if motivos:
            linhas.append(_linha(t.titulo_id, "R6_boleto_divergente", Severidade.BLOQUEIA,
                                 "; ".join(motivos), t.valor))
    return _saida(linhas)


# --------------------------------------------------------------------- R7


def _alcada_analista(pol: Dict[str, Any]) -> float:
    try:
        return float(pol["alcadas"]["analista_cp"]["liberar_ou_agendar_ate"])
    except (KeyError, TypeError, ValueError) as erro:
        raise ValueError("politicas: falta alcadas.analista_cp.liberar_ou_agendar_ate (R7)") from erro


def r7_fracionamento(agenda: pd.DataFrame, base: Base, pol: Dict[str, Any]) -> pd.DataFrame:
    """Mesmo fornecedor + mesma NF + mesmo dia de lançamento, cada título ≤ alçada do
    analista, soma acima dela, e mais títulos do que duplicatas da NF (ACIONA).

    Conta todos os títulos não cancelados do grupo; alerta os que estão na agenda.
    NF fora do portal não tem duplicatas para comparar: fica com a R3.
    """
    limite = _alcada_analista(pol)
    c = base.contas
    vivos = c[(c["status"].fillna("") != "cancelado") & c["nf_norm"].notna()
              & c["data_lancamento"].notna()].copy()
    vivos["dia"] = vivos["data_lancamento"].dt.normalize()
    chave = ["fornecedor_id", "nf_norm", "dia"]
    g = vivos.groupby(chave)["valor"]
    vivos["n"], vivos["soma"], vivos["maior"] = g.transform("size"), g.transform("sum"), g.transform("max")
    ids_agenda = set(agenda["titulo_id"])
    cand = vivos[(vivos["n"] > 1) & (vivos["maior"] <= limite + _EPS) & (vivos["soma"] > limite + _EPS)]
    cand = cand[cand.groupby(chave)["titulo_id"].transform(lambda s: s.isin(ids_agenda).any())]
    if cand.empty:
        return _vazio_saida()

    forn = base.fornecedores[["fornecedor_id", "cnpj_raiz"]].drop_duplicates("fornecedor_id")
    ndup = base.duplicatas.groupby("chave_acesso").size().rename("n_dups").reset_index()
    cand = (cand.merge(forn, on="fornecedor_id", how="left")
            .merge(_notas_por_nf(base)[["cnpj_raiz", "nf_norm", "chave_acesso"]],
                   on=["cnpj_raiz", "nf_norm"], how="left")
            .merge(ndup, on="chave_acesso", how="left"))
    cand = cand[cand["chave_acesso"].notna() & (cand["n"] > cand["n_dups"].fillna(0))]

    linhas = []
    for (_, nf, dia), grupo in cand.groupby(chave, sort=True):
        grupo = grupo.sort_values("titulo_id")
        for t in grupo.itertuples(index=False):
            if t.titulo_id not in ids_agenda:
                continue
            outros = [i for i in grupo["titulo_id"] if i != t.titulo_id]
            evid = (f"{int(t.n)} títulos do fornecedor na NF {nf} lançados em {_data(dia)}, cada "
                    f"um ≤ {_brl(limite)}, somando {_brl(t.soma)}; a NF tem "
                    f"{int(t.n_dups if pd.notna(t.n_dups) else 0)} duplicata(s); "
                    f"demais: {', '.join(outros)}")
            linhas.append(_linha(t.titulo_id, "R7_fracionamento", Severidade.ACIONA, evid, t.valor))
    return _saida(linhas)


# --------------------------------------------------------------------- R8

_CAMPO_LOG = re.compile(r"campo\s+(\w+)")
_ID_ALTERACAO = re.compile(r"\bAC\d+\b")


def r8_segregacao(agenda: pd.DataFrame, base: Base, pol: Dict[str, Any]) -> pd.DataFrame:
    """Segregação de funções (ACIONA). Uma linha por título, motivos juntos.

    - quem lançou o título também o aprovou (aprovacoes, contas.aprovado_por, log do ERP);
    - quem alterou o cadastro do fornecedor também aprovou o título;
    - alteração bancária no log do ERP sem linha correspondente em alteracoes_cadastro
      (alteração fora do processo): atinge os títulos cuja forma usa o campo alterado.
    Contas de serviço (perfil de integração) não contam.
    """
    servico = _servico(base)
    ids_agenda = set(agenda["titulo_id"])
    contas = base.contas[base.contas["titulo_id"].isin(ids_agenda)]
    lancador = contas.set_index("titulo_id")["lancado_por"].to_dict()
    forn_do_titulo = agenda.set_index("titulo_id")["fornecedor_id"].to_dict()
    motivos: Dict[str, List[str]] = {}

    def anotar(titulo_id: str, texto: str) -> None:
        lista = motivos.setdefault(titulo_id, [])
        if texto not in lista:
            lista.append(texto)

    # Quem aprovou cada título da agenda (três fontes do ERP).
    aprovacoes = []
    ap = base.aprovacoes
    if not ap.empty:
        ok = ap[ap["titulo_id"].isin(ids_agenda)
                & ap["decisao"].fillna("").str.strip().str.lower().str.startswith("aprov")]
        aprovacoes += [(t, u, f"aprovação {a}") for t, u, a in
                       zip(ok["titulo_id"], ok["aprovador"], ok["aprovacao_id"])]
    if "aprovado_por" in contas:
        com = contas[contas["aprovado_por"].notna()]
        aprovacoes += [(t, u, "aprovado_por no ERP") for t, u in zip(com["titulo_id"], com["aprovado_por"])]
    log = base.log_erp
    if not log.empty:
        apl = log[(log["acao"] == "aprovacao_titulo") & log["entidade_id"].isin(ids_agenda)]
        aprovacoes += [(t, u, f"log {lg}") for t, u, lg in
                       zip(apl["entidade_id"], apl["usuario"], apl["log_id"])]

    alt = base.alteracoes
    alteradores: Dict[str, Set[str]] = {}
    if not alt.empty:
        for f, u in zip(alt["fornecedor_id"], alt["alterado_por"]):
            alteradores.setdefault(f, set()).add(u)

    for titulo_id, aprovador, fonte in sorted(aprovacoes):
        if pd.isna(aprovador) or aprovador in servico:
            continue
        if aprovador == lancador.get(titulo_id):
            anotar(titulo_id, f"{aprovador} lançou e aprovou o título ({fonte})")
        if aprovador in alteradores.get(forn_do_titulo.get(titulo_id), set()):
            anotar(titulo_id, f"{aprovador} alterou o cadastro do fornecedor e aprovou o título ({fonte})")

    # Alteração bancária no log sem registro em alteracoes_cadastro.
    if not log.empty:
        la = log[(log["acao"] == "alteracao_fornecedor") & ~log["usuario"].isin(servico)].copy()
        la["campo"] = la["detalhe"].fillna("").map(
            lambda d: (m.group(1) if (m := _CAMPO_LOG.search(d)) else None))
        la = la[la["campo"].isin(CAMPOS_BANCARIOS)]
        ids_alt = set(alt["alteracao_id"]) if not alt.empty else set()
        chaves_alt = set(zip(alt["fornecedor_id"], alt["data_hora"])) if not alt.empty else set()
        for r in la.sort_values("log_id").itertuples(index=False):
            ac = _ID_ALTERACAO.search(str(r.detalhe or ""))
            registrada = ac.group(0) in ids_alt if ac else (r.entidade_id, r.data_hora) in chaves_alt
            if registrada:
                continue
            formas = {f for f, campos in CAMPOS_DESTINO.items() if r.campo in campos}
            alvo = agenda[(agenda["fornecedor_id"] == r.entidade_id)
                          & agenda["forma_pagamento"].isin(formas)]
            for titulo_id in alvo["titulo_id"]:
                anotar(titulo_id, f"alteração de {r.campo} no log ({r.log_id}, {r.usuario}, "
                                  f"{_data(r.data_hora)}) sem registro em alteracoes_cadastro")

    valor = agenda.set_index("titulo_id")["valor"]
    linhas = [_linha(t, "R8_segregacao", Severidade.ACIONA, "; ".join(m), valor[t])
              for t, m in motivos.items()]
    return _saida(linhas)


# --------------------------------------------------------------------- A1


def _dias_lancador_recente(pol: Dict[str, Any]) -> int:
    valor = (pol.get("guardiao") or {}).get("dias_lancador_recente")
    if valor is None:
        raise ValueError("pol['guardiao']['dias_lancador_recente'] ausente: o guardião injeta "
                         "o valor do config (DIAS_LANCADOR_RECENTE)")
    return int(valor)


def a1_lancador_recente(agenda: pd.DataFrame, base: Base, pol: Dict[str, Any]) -> pd.DataFrame:
    """Título lançado por quem tinha menos de N dias de casa NA DATA DO LANÇAMENTO (AGRAVA).

    Nunca gera alerta sozinha: o priorizador só usa para subir a prioridade de um título
    que já tem BLOQUEIA/ACIONA. Contas de serviço não contam.
    """
    dias = _dias_lancador_recente(pol)
    u = base.usuarios
    if u.empty:
        return _vazio_saida()
    admissao = u.drop_duplicates("login").set_index("login")["data_admissao"]
    servico = _servico(base)
    df = _titulos(agenda, base)
    df = df[df["lancado_por"].notna() & ~df["lancado_por"].isin(servico)]
    df = df.assign(admissao=df["lancado_por"].map(admissao))
    df = df[df["admissao"].notna() & df["data_lancamento"].notna()]
    df["casa"] = (df["data_lancamento"].dt.normalize() - df["admissao"].dt.normalize()).dt.days

    linhas = []
    for t in df[(df["casa"] >= 0) & (df["casa"] < dias)].itertuples(index=False):
        evid = (f"lançado por {t.lancado_por} em {_data(t.data_lancamento)}, "
                f"{int(t.casa)} dias após a admissão ({pd.Timestamp(t.admissao):%d/%m/%Y}); "
                f"limite {dias} dias")
        linhas.append(_linha(t.titulo_id, "A1_lancador_recente", Severidade.AGRAVA, evid, t.valor))
    return _saida(linhas)


# --------------------------------------------------------------------- todas

REGRAS: List[Callable[[pd.DataFrame, Base, Dict[str, Any]], pd.DataFrame]] = [
    r1_duplicidade, r2_valor_x_duplicata, r3_nf_inexistente,
    r4_oc_x_nf, r5_conta_alterada_sem_confirmacao_valida, r6_boleto_divergente,
    r7_fracionamento, r8_segregacao, a1_lancador_recente,
]


def rodar_regras(agenda: pd.DataFrame, base: Base, pol: Dict[str, Any],
                 regras: Optional[List[Callable]] = None) -> pd.DataFrame:
    """Concatena a saída das regras (um título pode aparecer em mais de uma regra)."""
    saidas = [r(agenda, base, pol) for r in (regras or REGRAS)]
    saidas = [s for s in saidas if not s.empty]
    return pd.concat(saidas, ignore_index=True) if saidas else _vazio_saida()
