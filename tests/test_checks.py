"""Testes do checks.py (item 2: R1 a R3), com DataFrames sintéticos mínimos.

Cada regra tem pelo menos 1 caso positivo e 1 negativo, mais os casos que fixam as
decisões do item 2 (carência de dia útil, fórmula exata de atraso, bordas da R1).
Os IDs abaixo são inventados: não correspondem a nada do cenário A.
"""
import re
from pathlib import Path

import pandas as pd
import pytest

from src import checks
from src.carga import Base, carregar, politica
from src.schemas import Alerta

DADOS_REAIS = Path(__file__).resolve().parents[1] / "pagamentos"

POL = {
    "tres_vias": {"tolerancia_titulo_x_duplicata": 0.10, "dispensas": {"nf": "aluguel"}},
    "atraso": {"multa": 0.02, "juros_ao_mes": 0.01},
}

RAIZ_A, RAIZ_B = "11111111", "22222222"


def _ts(texto):
    return pd.Timestamp(texto) if texto else pd.NaT


def montar(titulos, notas=(), feriados=()):
    """titulos: dicts com o mínimo; `agenda=True` põe o título também na agenda."""
    contas, agenda = [], []
    for t in titulos:
        n, total = t.get("parcela", "1/1").split("/")
        contas.append({
            "titulo_id": t["id"], "fornecedor_id": t.get("forn", "F1"),
            "numero_nf_digitado": t.get("nf"),
            "nf_norm": re.split(r"[-/]", t["nf"])[0].lstrip("0") if t.get("nf") else None,
            "parcela": t.get("parcela", "1/1"), "parcela_n": float(n), "parcela_total": float(total),
            "origem_lancamento": t.get("origem", "xml_portal"), "lancado_por": t.get("por", "u1"),
            "data_lancamento": _ts(t.get("lancado", "2026-10-01 09:00")),
            "status": t.get("status", "agendado"), "valor": t["valor"],
            "vencimento": _ts(t.get("venc", "2026-11-04")),
            "data_pagamento": _ts(t.get("pago_em")),
        })
        if t.get("agenda", True):
            agenda.append({
                "lote_remessa": "L1", "titulo_id": t["id"], "fornecedor_id": t.get("forn", "F1"),
                "valor": t["valor"], "data_prevista": _ts(t.get("prevista", "2026-11-04")),
                "vencimento": _ts(t.get("venc", "2026-11-04")), "forma_pagamento": "ted",
            })
    fornecedores = pd.DataFrame([
        {"fornecedor_id": "F1", "cnpj_raiz": RAIZ_A, "categoria": "produto"},
        {"fornecedor_id": "F2", "cnpj_raiz": RAIZ_B, "categoria": "aluguel"},
        {"fornecedor_id": "F3", "cnpj_raiz": "33333333", "categoria": "frete"},
    ])
    nf_rows, dup_rows = [], []
    for nota in notas:
        nf_rows.append({"chave_acesso": nota["chave"], "numero_nf": nota["nf"],
                        "cnpj_emitente_raiz": nota.get("raiz", RAIZ_A),
                        "nf_norm": nota["nf"].lstrip("0")})
        for i, (valor, venc) in enumerate(nota["dups"], start=1):
            dup_rows.append({"chave_acesso": nota["chave"], "n_dup": str(i).zfill(3),
                             "valor": valor, "vencimento": _ts(venc)})
    vazio = pd.DataFrame()
    return Base(
        agenda=pd.DataFrame(agenda), contas=pd.DataFrame(contas), fornecedores=fornecedores,
        alteracoes=vazio, aprovacoes=vazio,
        notas=pd.DataFrame(nf_rows, columns=["chave_acesso", "numero_nf", "cnpj_emitente_raiz", "nf_norm"]),
        duplicatas=pd.DataFrame(dup_rows, columns=["chave_acesso", "n_dup", "valor", "vencimento"]),
        pedidos=vazio, recebimentos=vazio, dda=vazio, contratos=vazio, usuarios=vazio,
        log_erp=vazio, comunicacoes=vazio, lotes=vazio,
        feriados=pd.DataFrame([{"data": _ts(d), "afeta_remessa": "sim"} for d in feriados],
                              columns=["data", "afeta_remessa"]),
    )


def rodar(regra, base):
    saida = regra(base.agenda, base, POL)
    assert list(saida.columns) == checks.COLUNAS
    return saida


NOTA_123 = {"chave": "K123", "nf": "123", "dups": [(1000.00, "2026-11-04"), (1000.00, "2026-12-04")]}


# --------------------------------------------------------------------- R1 duplicidade

def test_r1_retem_o_que_nao_bate_mesmo_lancado_antes():
    base = montar([
        {"id": "TP000001", "nf": "000123", "parcela": "1/2", "valor": 1003.50,
         "lancado": "2026-10-01 09:00", "origem": "boleto_email"},
        {"id": "TP000002", "nf": "123-1", "parcela": "1/2", "valor": 1000.00,
         "lancado": "2026-10-05 09:00"},
    ], notas=[NOTA_123])
    saida = rodar(checks.r1_duplicidade, base)
    assert saida["titulo_id"].tolist() == ["TP000001"]
    assert saida.loc[0, "severidade"] == "BLOQUEIA" and saida.loc[0, "valor"] == 1003.50
    assert "TP000002" in saida.loc[0, "evidencia"] and "não bate" in saida.loc[0, "evidencia"]


def test_r1_ambos_batem_retem_o_lancado_por_ultimo():
    base = montar([
        {"id": "TP000001", "nf": "123", "valor": 1000.05, "lancado": "2026-10-01 09:00"},
        {"id": "TP000002", "nf": "123/1", "valor": 1000.00, "lancado": "2026-10-02 09:00"},
    ], notas=[NOTA_123])
    assert rodar(checks.r1_duplicidade, base)["titulo_id"].tolist() == ["TP000002"]


def test_r1_negativo_parcelas_diferentes_e_duplicata_cancelada():
    base = montar([
        {"id": "TP000001", "nf": "123", "parcela": "1/2", "valor": 1000.00},
        {"id": "TP000002", "nf": "123", "parcela": "2/2", "valor": 1000.00},   # parcelamento legítimo
        {"id": "TP000003", "nf": "123", "parcela": "1/2", "valor": 1000.00,
         "status": "cancelado", "agenda": False},                              # cancelado não conta
    ], notas=[NOTA_123])
    assert rodar(checks.r1_duplicidade, base).empty


def test_r1_negativo_nf_vazia_nao_forma_grupo():
    base = montar([
        {"id": "TP000001", "forn": "F2", "nf": None, "valor": 500.00},
        {"id": "TP000002", "forn": "F2", "nf": None, "valor": 500.00, "status": "pago", "agenda": False},
    ])
    assert rodar(checks.r1_duplicidade, base).empty


def test_r1_legitimo_ja_pago_retem_o_da_agenda():
    base = montar([
        {"id": "TP000001", "nf": "123", "valor": 1000.00, "status": "pago",
         "pago_em": "2026-11-04", "agenda": False, "lancado": "2026-10-01 09:00"},
        {"id": "TP000002", "nf": "123", "valor": 1000.00, "lancado": "2026-10-02 09:00"},
    ], notas=[NOTA_123])
    assert rodar(checks.r1_duplicidade, base)["titulo_id"].tolist() == ["TP000002"]


def test_r1_nenhum_bate_e_grupo_de_tres_mantem_so_o_primeiro_lancado():
    base = montar([
        {"id": "TP000001", "nf": "123", "valor": 1100.00, "lancado": "2026-10-01 09:00"},
        {"id": "TP000002", "nf": "123", "valor": 1200.00, "lancado": "2026-10-02 09:00"},
        {"id": "TP000003", "nf": "123", "valor": 1300.00, "lancado": "2026-10-03 09:00"},
    ], notas=[NOTA_123])
    saida = rodar(checks.r1_duplicidade, base)
    assert saida["titulo_id"].tolist() == ["TP000002", "TP000003"]
    assert saida["evidencia"].str.contains("nenhum bate").all()


# --------------------------------------------------------------------- R2 valor × duplicata

def test_r2_pega_erro_de_virgula():
    base = montar([{"id": "TP000001", "nf": "123", "parcela": "2/2", "valor": 10000.00,
                    "prevista": "2026-12-04", "venc": "2026-12-04"}], notas=[NOTA_123])
    saida = rodar(checks.r2_valor_x_duplicata, base)
    assert saida["titulo_id"].tolist() == ["TP000001"]
    assert "duplicata 002" in saida.loc[0, "evidencia"] and "10,00×" in saida.loc[0, "evidencia"]


def test_r2_negativo_dentro_da_tolerancia():
    base = montar([{"id": "TP000001", "nf": "123", "parcela": "1/2", "valor": 1000.10}],
                  notas=[NOTA_123])
    assert rodar(checks.r2_valor_x_duplicata, base).empty


def test_r2_carencia_ate_proximo_dia_util_nao_admite_multa():
    # duplicata vence num domingo; pagar na segunda não é atraso: +R$ 4,50 é divergência
    nota = {"chave": "K9", "nf": "9", "dups": [(837.08, "2026-11-08")]}
    base = montar([{"id": "TP000001", "nf": "9", "valor": 841.58, "venc": "2026-11-08",
                    "prevista": "2026-11-09"}], notas=[nota])
    saida = rodar(checks.r2_valor_x_duplicata, base)
    assert saida["titulo_id"].tolist() == ["TP000001"]
    assert "multa" not in saida.loc[0, "evidencia"]


def test_r2_carencia_pula_feriado_bancario():
    # vence sexta 13/11 (feriado de remessa) -> próximo dia útil é segunda 16/11
    nota = {"chave": "K9", "nf": "9", "dups": [(1000.00, "2026-11-13")]}
    base = montar([{"id": "TP000001", "nf": "9", "valor": 1000.00, "venc": "2026-11-13",
                    "prevista": "2026-11-16"}], notas=[nota], feriados=["2026-11-13"])
    assert rodar(checks.r2_valor_x_duplicata, base).empty


def test_r2_atraso_aceita_formula_exata_e_rejeita_valor_intermediario():
    # duplicata 1000 vencida em 02/10 (sexta), paga em 01/11: 30 dias -> 1000 × 1,03 = 1030
    nota = {"chave": "K9", "nf": "9", "dups": [(1000.00, "2026-10-02")]}
    exato = montar([{"id": "TP000001", "nf": "9", "valor": 1030.00, "venc": "2026-10-02",
                     "prevista": "2026-11-01"}], notas=[nota])
    assert rodar(checks.r2_valor_x_duplicata, exato).empty
    sem_acrescimo = montar([{"id": "TP000001", "nf": "9", "valor": 1000.00, "venc": "2026-10-02",
                             "prevista": "2026-11-01"}], notas=[nota])
    assert rodar(checks.r2_valor_x_duplicata, sem_acrescimo).empty
    intermediario = montar([{"id": "TP000001", "nf": "9", "valor": 1015.00, "venc": "2026-10-02",
                             "prevista": "2026-11-01"}], notas=[nota])
    saida = rodar(checks.r2_valor_x_duplicata, intermediario)
    assert saida["titulo_id"].tolist() == ["TP000001"]
    assert "30 dias" in saida.loc[0, "evidencia"] and "R$ 1.030,00" in saida.loc[0, "evidencia"]


def test_r2_nf_sem_duplicata_da_parcela_e_alerta_e_sem_nf_fica_com_r3():
    base = montar([
        {"id": "TP000001", "nf": "123", "parcela": "3/3", "valor": 1000.00},  # NF só tem 2 dups
        {"id": "TP000002", "nf": "999", "valor": 1000.00},                    # NF fora do portal
    ], notas=[NOTA_123])
    saida = rodar(checks.r2_valor_x_duplicata, base)
    assert saida["titulo_id"].tolist() == ["TP000001"]
    assert "não tem a duplicata 003" in saida.loc[0, "evidencia"]


# --------------------------------------------------------------------- R3 NF inexistente

def test_r3_pega_nf_fora_do_portal():
    base = montar([{"id": "TP000001", "nf": "000456", "valor": 2500.00}], notas=[NOTA_123])
    saida = rodar(checks.r3_nf_inexistente, base)
    assert saida["titulo_id"].tolist() == ["TP000001"]
    assert "'000456'" in saida.loc[0, "evidencia"] and RAIZ_A not in saida.loc[0, "evidencia"]


def test_r3_negativo_nf_de_filial_com_zeros_e_serie():
    # mesma raiz, NF digitada com zeros e série: é a mesma nota
    base = montar([{"id": "TP000001", "nf": "000123-1", "valor": 1000.00}], notas=[NOTA_123])
    assert rodar(checks.r3_nf_inexistente, base).empty


def test_r3_negativo_dispensas_aluguel_e_integracao_frete():
    base = montar([
        {"id": "TP000001", "forn": "F2", "nf": None, "valor": 8000.00},                # aluguel
        {"id": "TP000002", "forn": "F3", "nf": "777", "valor": 900.00,
         "origem": "integracao_frete"},                                              # frete integrado
    ])
    assert rodar(checks.r3_nf_inexistente, base).empty


def test_r3_nf_vazia_fora_das_dispensas_e_alerta():
    base = montar([{"id": "TP000001", "nf": None, "valor": 700.00}])
    saida = rodar(checks.r3_nf_inexistente, base)
    assert saida["titulo_id"].tolist() == ["TP000001"]
    assert "sem número de NF" in saida.loc[0, "evidencia"]


# --------------------------------------------------------------------- contrato

def test_regras_sao_puras_e_deterministicas():
    base = montar([
        {"id": "TP000001", "nf": "123", "valor": 1003.50, "lancado": "2026-10-01 09:00"},
        {"id": "TP000002", "nf": "123", "valor": 1000.00, "lancado": "2026-10-05 09:00"},
        {"id": "TP000003", "nf": "456", "valor": 50.00},
    ], notas=[NOTA_123])
    antes = {nome: df.copy() for nome, df in (("agenda", base.agenda), ("contas", base.contas),
                                               ("notas", base.notas), ("dups", base.duplicatas))}
    primeira = checks.rodar_regras(base.agenda, base, POL)
    segunda = checks.rodar_regras(base.agenda, base, POL)
    pd.testing.assert_frame_equal(primeira, segunda)
    for nome, df in (("agenda", base.agenda), ("contas", base.contas),
                     ("notas", base.notas), ("dups", base.duplicatas)):
        pd.testing.assert_frame_equal(df, antes[nome])
    assert set(primeira["regra"]) == {"R1_duplicidade", "R2_valor_x_duplicata", "R3_nf_inexistente"}


def test_regra_sem_tolerancia_na_politica_falha_alto():
    base = montar([{"id": "TP000001", "nf": "123", "valor": 1000.00}], notas=[NOTA_123])
    with pytest.raises(ValueError, match="titulo_x_duplicata"):
        checks.r2_valor_x_duplicata(base.agenda, base, {"atraso": POL["atraso"]})


@pytest.mark.skipif(not DADOS_REAIS.exists(), reason="dados do desafio fora do git")
def test_cenario_a_saida_valida_no_schema_alerta():
    base, pol = carregar(DADOS_REAIS), politica(DADOS_REAIS)
    alertas = checks.rodar_regras(base.agenda, base, pol)
    assert not alertas.empty
    for linha in alertas.to_dict("records"):
        Alerta.model_validate(linha)
    assert set(alertas["titulo_id"]) <= set(base.agenda["titulo_id"])
