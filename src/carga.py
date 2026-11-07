"""Carga dos dados do desafio: lê, normaliza e valida (SPEC §5 passo 1, item 1).

- Tudo é lido com dtype=str (pandas 3) e convertido aqui, num lugar só.
- Normalizações: CNPJ só dígitos, raiz (8 dígitos), NF normalizada, datas, valores.
- Linhas de agenda_remessa e contas_a_pagar passam por Pydantic. Linha inválida NÃO
  segue para as regras: vai para `Base.invalidos`, e o guardião (item 4) a encaminha
  para a triagem manual (fail-safe: nunca sai do lote "sem conferência" em silêncio).
- Nenhum ID, fornecedor ou limiar fixo: só nomes de arquivo e de coluna.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError

# --------------------------------------------------------------------- normalizações


def _vazio(valor: Any) -> bool:
    return valor is None or (isinstance(valor, float) and pd.isna(valor)) or str(valor).strip() == ""


def normalizar_cnpj(valor: Any) -> Optional[str]:
    """'85.779.751/0001-00' ou '85779751000100' -> '85779751000100'. Inválido -> None."""
    if _vazio(valor):
        return None
    digitos = re.sub(r"\D", "", str(valor))
    return digitos if len(digitos) == 14 else None


def raiz_cnpj(valor: Any) -> Optional[str]:
    """8 primeiros dígitos (matriz e filiais compartilham a raiz)."""
    cnpj = normalizar_cnpj(valor)
    return cnpj[:8] if cnpj else None


def normalizar_nf(valor: Any) -> Optional[str]:
    """Corta no primeiro '-' ou '/' (série) e remove zeros à esquerda.

    '000009671' -> '9671'; '9671-1' -> '9671'; '9671/1' -> '9671'; '000' -> '0'; vazio -> None.
    """
    if _vazio(valor):
        return None
    numero = re.split(r"[-/]", str(valor).strip(), maxsplit=1)[0].strip()
    if numero == "":
        return None
    sem_zeros = numero.lstrip("0")
    return sem_zeros or "0"


def _datas(serie: pd.Series) -> pd.Series:
    return pd.to_datetime(serie, format="ISO8601", errors="coerce")


def _numeros(serie: pd.Series) -> pd.Series:
    return pd.to_numeric(serie, errors="coerce")


# --------------------------------------------------------------------- leitura


def _celula_texto(valor: Any) -> Optional[str]:
    if isinstance(valor, list):  # ex.: 'anexos' no .jsonl vira o mesmo formato do .csv
        return " | ".join(str(v) for v in valor)
    return None if _vazio(valor) else str(valor)


def _como_texto(df: pd.DataFrame) -> pd.DataFrame:
    """Mesma semântica do read_csv(dtype=str): tudo texto, vazio = ausente."""
    return df.apply(lambda col: col.map(_celula_texto)).astype("str")


def ler_tabela(dados_dir: str | Path, nome: str) -> pd.DataFrame:
    """Lê `<nome>.csv`; se não existir, aceita `<nome>.jsonl` ou `<nome>.json` (SPEC §2)."""
    base = Path(dados_dir)
    csv = base / f"{nome}.csv"
    if csv.exists():
        return pd.read_csv(csv, dtype=str, encoding="utf-8-sig")
    jsonl = base / f"{nome}.jsonl"
    if jsonl.exists():
        return _como_texto(pd.read_json(jsonl, lines=True, dtype=False))
    js = base / f"{nome}.json"
    if js.exists():
        conteudo = json.loads(js.read_text(encoding="utf-8"))
        return _como_texto(pd.DataFrame(conteudo))
    raise FileNotFoundError(f"{nome}: nenhum .csv, .jsonl ou .json em {base}")


# --------------------------------------------------------------------- políticas

_NUMERO_DECIMAL = re.compile(r"^-?\d+\.\d+$")
_SEGMENTO_LISTA = re.compile(r"^(?P<nome>[^\[\]]+)\[(?P<idx>\d+)\]$")


def _valor_politica(valor: Any) -> Any:
    """'5000.0' -> 5000.0. Inteiros ('612', '168') ficam texto: são códigos ou contagens
    de exibição, e converter mudaria comparações com as colunas (que são texto)."""
    if _vazio(valor):
        return None
    texto = str(valor).strip()
    return float(texto) if _NUMERO_DECIMAL.match(texto) else texto


def _inserir(raiz: Dict[str, Any], caminho: List[str], valor: Any) -> None:
    atual: Any = raiz
    for i, seg in enumerate(caminho):
        ultimo = i == len(caminho) - 1
        m = _SEGMENTO_LISTA.match(seg)
        if m:  # 'pessoas[3]' -> lista 1-based no CSV, 0-based em Python
            lista = atual.setdefault(m["nome"], [])
            idx = int(m["idx"]) - 1
            while len(lista) <= idx:
                lista.append({})
            if ultimo:
                lista[idx] = valor
            else:
                atual = lista[idx]
        elif ultimo:
            atual[seg] = valor
        else:
            atual = atual.setdefault(seg, {})


def politica(dados_dir: str | Path) -> Dict[str, Any]:
    """politicas.csv (secao, regra, valor) -> dict aninhado, no formato do politicas.json.

    Ex.: pol['alcadas']['analista_cp']['liberar_ou_agendar_ate'] == 5000.0
         pol['acionamento_humano']['pessoas'][0]['pessoa_id'] == 'PS01'
    Se existir politicas.json, ele é lido diretamente.
    """
    base = Path(dados_dir)
    if not (base / "politicas.csv").exists() and (base / "politicas.json").exists():
        return json.loads((base / "politicas.json").read_text(encoding="utf-8"))
    df = ler_tabela(base, "politicas")
    pol: Dict[str, Any] = {}
    for linha in df.itertuples(index=False):
        secao, regra = str(linha.secao).strip(), str(linha.regra).strip()
        caminho = [secao] if regra == secao else [secao, *regra.split(".")]
        _inserir(pol, caminho, _valor_politica(linha.valor))
    return pol


# --------------------------------------------------------------------- validação Pydantic


class LinhaAgenda(BaseModel):
    model_config = ConfigDict(extra="ignore")

    titulo_id: str = Field(min_length=1)
    fornecedor_id: str = Field(min_length=1)
    lote_remessa: str = Field(min_length=1)
    valor: float = Field(gt=0)
    data_prevista: date
    horario_fechamento: time
    vencimento: date
    forma_pagamento: str = Field(pattern=r"^(ted|pix|boleto)$")


class LinhaContaPagar(BaseModel):
    model_config = ConfigDict(extra="ignore")

    titulo_id: str = Field(min_length=1)
    fornecedor_id: str = Field(min_length=1)
    valor: float = Field(gt=0)
    vencimento: date
    forma_pagamento: str = Field(pattern=r"^(ted|pix|boleto)$")
    parcela: str = Field(pattern=r"^\d+/\d+$")
    status: str = Field(min_length=1)
    data_lancamento: datetime
    lancado_por: str = Field(min_length=1)


def _validar(df: pd.DataFrame, modelo: type[BaseModel], tabela: str) -> tuple[pd.DataFrame, List[dict]]:
    """Separa linhas válidas das inválidas (erro curto, sem traceback)."""
    ok, invalidos = [], []
    campos = list(modelo.model_fields)
    for idx, registro in zip(df.index, df[campos].to_dict("records")):
        limpo = {k: (None if _vazio(v) else v) for k, v in registro.items()}
        try:
            modelo.model_validate(limpo)
            ok.append(idx)
        except ValidationError as erro:
            campos_erro = sorted({str(e["loc"][0]) for e in erro.errors()})
            invalidos.append({
                "tabela": tabela,
                "titulo_id": limpo.get("titulo_id") or "",
                "erro": "campos inválidos: " + ", ".join(campos_erro),
            })
    return df.loc[ok], invalidos


# --------------------------------------------------------------------- Base


@dataclass
class Base:
    agenda: pd.DataFrame
    contas: pd.DataFrame
    fornecedores: pd.DataFrame
    alteracoes: pd.DataFrame
    aprovacoes: pd.DataFrame
    notas: pd.DataFrame
    duplicatas: pd.DataFrame
    pedidos: pd.DataFrame
    recebimentos: pd.DataFrame
    dda: pd.DataFrame
    contratos: pd.DataFrame
    usuarios: pd.DataFrame
    log_erp: pd.DataFrame
    comunicacoes: pd.DataFrame
    lotes: pd.DataFrame
    feriados: pd.DataFrame
    invalidos: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame(columns=["tabela", "titulo_id", "erro"])
    )


def _agenda(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["valor"] = _numeros(df["valor"])
    for col in ("data_prevista", "vencimento"):
        df[col] = _datas(df[col])
    df["fechamento"] = pd.to_datetime(
        df["data_prevista"].dt.strftime("%Y-%m-%d") + " " + df["horario_fechamento"].fillna(""),
        format="%Y-%m-%d %H:%M", errors="coerce",
    )
    return df


def _contas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["valor"] = _numeros(df["valor"])
    for col in ("vencimento", "data_lancamento", "data_pagamento", "data_aprovacao",
                "data_baixa", "data_cancelamento"):
        df[col] = _datas(df[col])
    df["nf_norm"] = df["numero_nf_digitado"].map(normalizar_nf)
    partes = df["parcela"].str.extract(r"^(\d+)/(\d+)$")
    df["parcela_n"], df["parcela_total"] = _numeros(partes[0]), _numeros(partes[1])
    df["cnpj_beneficiario_boleto_num"] = df["cnpj_beneficiario_boleto"].map(normalizar_cnpj)
    df["cnpj_beneficiario_boleto_raiz"] = df["cnpj_beneficiario_boleto"].map(raiz_cnpj)
    return df


def _com_cnpj(df: pd.DataFrame, coluna: str, prefixo: str) -> pd.DataFrame:
    df = df.copy()
    df[f"{prefixo}_num"] = df[coluna].map(normalizar_cnpj)
    df[f"{prefixo}_raiz"] = df[coluna].map(raiz_cnpj)
    return df


def _converter(df: pd.DataFrame, datas=(), numeros=()) -> pd.DataFrame:
    df = df.copy()
    for col in datas:
        df[col] = _datas(df[col])
    for col in numeros:
        df[col] = _numeros(df[col])
    return df


def carregar(dados_dir: str | Path) -> Base:
    """Lê os CSVs (ou .json/.jsonl), normaliza e valida. Não grava nada em disco."""
    t = lambda nome: ler_tabela(dados_dir, nome)  # noqa: E731

    agenda_bruta, contas_brutas = t("agenda_remessa"), t("contas_a_pagar")
    agenda_ok, inv_agenda = _validar(agenda_bruta, LinhaAgenda, "agenda_remessa")
    contas_ok, inv_contas = _validar(contas_brutas, LinhaContaPagar, "contas_a_pagar")

    fornecedores = _com_cnpj(t("fornecedores"), "cnpj", "cnpj")
    fornecedores = _converter(fornecedores, datas=("data_cadastro", "data_ultima_alteracao"))

    notas = _com_cnpj(t("notas_fiscais"), "cnpj_emitente", "cnpj_emitente")
    notas = _converter(notas, datas=("data_emissao", "data_recebimento_portal", "data_manifestacao"),
                       numeros=("valor_total", "valor_produtos_servicos"))
    notas["nf_norm"] = notas["numero_nf"].map(normalizar_nf)

    dda = _com_cnpj(t("dda_boletos"), "cnpj_beneficiario", "cnpj_beneficiario")
    dda = _converter(dda, datas=("data_registro", "vencimento", "data_situacao"), numeros=("valor",))

    pedidos = _com_cnpj(t("pedidos_compra"), "cnpj_fornecedor", "cnpj_fornecedor")
    pedidos = _converter(pedidos, datas=("data_emissao", "data_aprovacao"),
                         numeros=("valor_total", "valor_mensal"))

    recebimentos = _com_cnpj(t("recebimentos_mercadoria"), "cnpj_emitente", "cnpj_emitente")
    recebimentos = _converter(recebimentos, datas=("data_hora_recebimento",))
    recebimentos["nf_norm"] = recebimentos["numero_nf"].map(normalizar_nf)

    return Base(
        agenda=_agenda(agenda_ok),
        contas=_contas(contas_ok),
        fornecedores=fornecedores,
        alteracoes=_converter(t("alteracoes_cadastro"), datas=("data_hora",)),
        aprovacoes=_converter(t("aprovacoes"), datas=("data_hora",), numeros=("valor_titulo",)),
        notas=notas,
        duplicatas=_converter(t("nf_duplicatas"), datas=("vencimento",), numeros=("valor",)),
        pedidos=pedidos,
        recebimentos=recebimentos,
        dda=dda,
        contratos=_converter(t("contratos_fornecedores"), numeros=("valor_mensal",)),
        usuarios=_converter(t("usuarios_sistema"), datas=("data_admissao", "data_desligamento"),
                            numeros=("alcada_rs",)),
        log_erp=_converter(t("log_auditoria_erp"), datas=("data_hora",)),
        comunicacoes=_converter(t("comunicacoes_fornecedores"), datas=("data_hora",)),
        lotes=t("lotes_remessa"),
        feriados=_converter(t("feriados_bancarios"), datas=("data",)),
        invalidos=pd.DataFrame(inv_agenda + inv_contas, columns=["tabela", "titulo_id", "erro"]),
    )
