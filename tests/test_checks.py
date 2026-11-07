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
    "tres_vias": {"tolerancia_titulo_x_duplicata": 0.10,
                  "tolerancia_nf_x_oc": {"percentual": 0.02, "reais": 50.0},
                  "dispensas": {"nf": "aluguel", "oc": "utilidades"}},
    "atraso": {"multa": 0.02, "juros_ao_mes": 0.01},
    "dados_bancarios": {"dias_telefone_recente": 30},
    "alcadas": {"analista_cp": {"liberar_ou_agendar_ate": 5000.0}},
    "guardiao": {"dias_lancador_recente": 45},
}

RAIZ_A, RAIZ_B = "11111111", "22222222"


def _ts(texto):
    return pd.Timestamp(texto) if texto else pd.NaT


COLS = {
    "alteracoes": ["alteracao_id", "data_hora", "fornecedor_id", "campo", "valor_anterior",
                   "valor_novo", "alterado_por", "origem", "confirmacao_registrada", "confirmacao_como"],
    "aprovacoes": ["aprovacao_id", "titulo_id", "data_hora", "aprovador", "decisao"],
    "pedidos": ["oc_id", "status", "tipo", "valor_total", "valor_mensal"],
    "recebimentos": ["cnpj_emitente_raiz", "nf_norm", "situacao"],
    "dda": ["dda_id", "linha_digitavel", "situacao", "data_situacao", "cnpj_beneficiario_raiz"],
    "usuarios": ["login", "perfil", "data_admissao"],
    "log_erp": ["log_id", "data_hora", "usuario", "acao", "entidade", "entidade_id", "detalhe"],
}
OC_PADRAO = {"oc_id": "OC-1", "status": "aprovada", "tipo": "pontual",
             "valor_total": 1_000_000.0, "valor_mensal": None}


def _tabela(nome, linhas):
    df = pd.DataFrame(list(linhas), columns=COLS[nome])
    for col in ("data_hora", "data_situacao", "data_admissao"):
        if col in df:
            df[col] = pd.to_datetime(df[col])
    return df


def montar(titulos, notas=(), feriados=(), **tabelas):
    """titulos: dicts com o mínimo; `agenda=True` põe o título também na agenda.

    Item 3: `forma`, `oc`, `linha`, `benef_raiz`, `aprovado_por` no título; `modelo`,
    `total` e `recebida` na nota (por padrão toda nota tem recebimento); demais tabelas
    por nome em `tabelas` (listas de dicts com as colunas de COLS). Sem `pedidos`, existe
    só a OC-1 aprovada e enorme, que é a OC padrão dos títulos.
    """
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
            "oc_id": t.get("oc", "OC-1"), "forma_pagamento": t.get("forma", "ted"),
            "linha_digitavel": t.get("linha"), "cnpj_beneficiario_boleto_raiz": t.get("benef_raiz"),
            "aprovado_por": t.get("aprovado_por"),
        })
        if t.get("agenda", True):
            agenda.append({
                "lote_remessa": "L1", "titulo_id": t["id"], "fornecedor_id": t.get("forn", "F1"),
                "valor": t["valor"], "data_prevista": _ts(t.get("prevista", "2026-11-04")),
                "vencimento": _ts(t.get("venc", "2026-11-04")),
                "forma_pagamento": t.get("forma", "ted"),
            })
    fornecedores = pd.DataFrame([
        {"fornecedor_id": "F1", "cnpj_raiz": RAIZ_A, "categoria": "produto", "banco": "111",
         "telefone_cadastrado": "(85) 3000-1111"},
        {"fornecedor_id": "F2", "cnpj_raiz": RAIZ_B, "categoria": "aluguel", "banco": "222",
         "telefone_cadastrado": "(85) 3000-2222"},
        {"fornecedor_id": "F3", "cnpj_raiz": "33333333", "categoria": "frete", "banco": "333",
         "telefone_cadastrado": "(85) 3000-3333"},
        {"fornecedor_id": "F4", "cnpj_raiz": "44444444", "categoria": "utilidades", "banco": "444",
         "telefone_cadastrado": "(85) 3000-4444"},
    ])
    nf_rows, dup_rows, recebidas = [], [], []
    for nota in notas:
        nf_norm = nota["nf"].lstrip("0")
        nf_rows.append({"chave_acesso": nota["chave"], "numero_nf": nota["nf"],
                        "cnpj_emitente_raiz": nota.get("raiz", RAIZ_A), "nf_norm": nf_norm,
                        "modelo": nota.get("modelo", "55"),
                        "valor_total": nota.get("total", sum(v for v, _ in nota["dups"]))})
        for i, (valor, venc) in enumerate(nota["dups"], start=1):
            dup_rows.append({"chave_acesso": nota["chave"], "n_dup": str(i).zfill(3),
                             "valor": valor, "vencimento": _ts(venc)})
        if nota.get("recebida", True):
            recebidas.append({"cnpj_emitente_raiz": nota.get("raiz", RAIZ_A), "nf_norm": nf_norm,
                              "situacao": "conferido"})
    tabelas.setdefault("pedidos", [OC_PADRAO])
    tabelas.setdefault("recebimentos", recebidas)
    t = {nome: _tabela(nome, tabelas.get(nome, ())) for nome in COLS}
    vazio = pd.DataFrame()
    return Base(
        agenda=pd.DataFrame(agenda), contas=pd.DataFrame(contas), fornecedores=fornecedores,
        alteracoes=t["alteracoes"], aprovacoes=t["aprovacoes"],
        notas=pd.DataFrame(nf_rows, columns=["chave_acesso", "numero_nf", "cnpj_emitente_raiz",
                                             "nf_norm", "modelo", "valor_total"]),
        duplicatas=pd.DataFrame(dup_rows, columns=["chave_acesso", "n_dup", "valor", "vencimento"]),
        pedidos=t["pedidos"], recebimentos=t["recebimentos"], dda=t["dda"], contratos=vazio,
        usuarios=t["usuarios"], log_erp=t["log_erp"], comunicacoes=vazio, lotes=vazio,
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


# --------------------------------------------------------------------- R4 OC × NF

NOTA_500 = {"chave": "K500", "nf": "500", "dups": [(1000.00, "2026-11-04")]}


@pytest.mark.parametrize("oc, pedidos, trecho", [
    ("OC-9", [OC_PADRAO], "OC-9 não existe"),
    ("OC-1", [{**OC_PADRAO, "status": "cancelada"}], "OC-1 está cancelada"),
    ("OC-1", [{**OC_PADRAO, "status": "pendente_aprovacao"}], "OC-1 está pendente_aprovacao"),
    (None, [OC_PADRAO], "sem OC"),
])
def test_r4_oc_inexistente_cancelada_pendente_ou_ausente(oc, pedidos, trecho):
    base = montar([{"id": "TP000001", "nf": "500", "valor": 1000.00, "oc": oc}],
                  notas=[NOTA_500], pedidos=pedidos)
    saida = rodar(checks.r4_oc_x_nf, base)
    assert saida["titulo_id"].tolist() == ["TP000001"]
    assert saida.loc[0, "severidade"] == "ACIONA" and trecho in saida.loc[0, "evidencia"]


def test_r4_nf_acima_da_oc_respeita_maior_entre_percentual_e_reais():
    oc = [{**OC_PADRAO, "valor_total": 1000.00}]           # folga = max(2% × 1000, R$ 50) = 50
    dentro = montar([{"id": "TP000001", "nf": "500", "valor": 1000.00}],
                    notas=[{**NOTA_500, "total": 1049.99}], pedidos=oc)
    assert rodar(checks.r4_oc_x_nf, dentro).empty
    fora = montar([{"id": "TP000001", "nf": "500", "valor": 1000.00}],
                  notas=[{**NOTA_500, "total": 1050.50}], pedidos=oc)
    saida = rodar(checks.r4_oc_x_nf, fora)
    assert saida["titulo_id"].tolist() == ["TP000001"]
    assert "acima de OC-1" in saida.loc[0, "evidencia"] and "tolerância R$ 50,00" in saida.loc[0, "evidencia"]


def test_r4_contrato_compara_com_valor_mensal():
    contrato = [{**OC_PADRAO, "tipo": "contrato", "valor_total": 12000.00, "valor_mensal": 1000.00}]
    mensal_ok = montar([{"id": "TP000001", "nf": "500", "valor": 1000.00}],
                       notas=[{**NOTA_500, "total": 1000.00}], pedidos=contrato)
    assert rodar(checks.r4_oc_x_nf, mensal_ok).empty
    acima_do_mensal = montar([{"id": "TP000001", "nf": "500", "valor": 1100.00}],
                             notas=[{**NOTA_500, "total": 1100.00}], pedidos=contrato)
    saida = rodar(checks.r4_oc_x_nf, acima_do_mensal)
    assert saida["titulo_id"].tolist() == ["TP000001"]       # 1100 < 12000, mas > 1000 + 50
    assert "mensal R$ 1.000,00" in saida.loc[0, "evidencia"]


def test_r4_nfe_de_mercadoria_sem_recebimento():
    base = montar([{"id": "TP000001", "nf": "500", "valor": 1000.00}],
                  notas=[{**NOTA_500, "recebida": False}])
    saida = rodar(checks.r4_oc_x_nf, base)
    assert saida["titulo_id"].tolist() == ["TP000001"]
    assert "sem entrada de mercadoria" in saida.loc[0, "evidencia"]


def test_r4_negativo_servico_nf_fora_do_portal_e_dispensa_de_oc():
    base = montar([
        {"id": "TP000001", "nf": "500", "valor": 1000.00},              # NFS-e: sem recebimento
        {"id": "TP000002", "nf": "999", "valor": 1000.00},              # fora do portal: é da R3
        {"id": "TP000003", "forn": "F4", "nf": "777", "valor": 300.00, "oc": None},  # utilidades
    ], notas=[{**NOTA_500, "modelo": "NFS-e", "recebida": False}])
    assert rodar(checks.r4_oc_x_nf, base).empty


# --------------------------------------------------------------------- R5 conta alterada

def _troca(data, confirmada="sim", como=None, forn="F1", campos=("banco", "agencia", "conta"),
           por="u2", origem="email"):
    return [{"alteracao_id": f"AC{data[:10].replace('-', '')}{i}", "data_hora": data, "fornecedor_id": forn,
             "campo": c, "valor_anterior": "x", "valor_novo": "y", "alterado_por": por,
             "origem": origem, "confirmacao_registrada": confirmada, "confirmacao_como": como}
            for i, c in enumerate(campos)]


def _troca_telefone(data, antes, depois, forn="F1"):
    return [{"alteracao_id": f"AC{data[:10].replace('-', '')}9", "data_hora": data, "fornecedor_id": forn,
             "campo": "telefone", "valor_anterior": antes, "valor_novo": depois,
             "alterado_por": "u2", "origem": "email", "confirmacao_registrada": "não",
             "confirmacao_como": None}]


TITULOS_F1 = [
    {"id": "TP000001", "nf": "123", "valor": 1000.00, "forma": "ted"},
    {"id": "TP000002", "nf": "123", "parcela": "2/2", "valor": 1000.00, "forma": "boleto",
     "linha": "111" + "0" * 44},
]


def test_r5_troca_sem_confirmacao_retem_ted_e_nao_boleto():
    base = montar(TITULOS_F1, alteracoes=_troca("2026-10-30 16:05", confirmada="não"))
    saida = rodar(checks.r5_conta_alterada_sem_confirmacao_valida, base)
    assert saida["titulo_id"].tolist() == ["TP000001"]
    assert saida.loc[0, "severidade"] == "BLOQUEIA"
    assert "sem confirmação registrada" in saida.loc[0, "evidencia"]


def test_r5_confirmacao_por_email_nao_vale():
    base = montar(TITULOS_F1, alteracoes=_troca(
        "2026-10-30 16:05", como="confirmado por e-mail do domínio cadastrado"))
    saida = rodar(checks.r5_conta_alterada_sem_confirmacao_valida, base)
    assert saida["titulo_id"].tolist() == ["TP000001"] and "e-mail" in saida.loc[0, "evidencia"]


def test_r5_ligacao_para_telefone_trocado_ha_menos_de_30_dias():
    # cenário do teste ponta a ponta (SPEC §11): telefone trocado 5 dias antes da conta
    alt = (_troca_telefone("2026-10-25 10:00", "(85) 3000-1111", "(85) 99999-0000")
           + _troca("2026-10-30 16:05", como="ligação para o telefone do cadastro (85) 99999-0000"))
    saida = rodar(checks.r5_conta_alterada_sem_confirmacao_valida, montar(TITULOS_F1, alteracoes=alt))
    assert saida["titulo_id"].tolist() == ["TP000001"]
    evid = saida.loc[0, "evidencia"]
    assert "trocado em 25/10, 5 dias antes da alteração (< 30)" in evid
    assert "99999-0000" not in evid and "(85) ****-0000" in evid       # telefone mascarado


def test_r5_negativo_ligacao_ao_telefone_antigo_mesmo_depois_da_gravacao():
    # decisão do item 3: o "antes" vale para o telefone; a ligação pode ser no dia seguinte
    alt = _troca("2026-10-20 14:10",
                 como="ligação para o telefone do cadastro (85) 3000-1111 em 21/10/2026 10:05")
    assert rodar(checks.r5_conta_alterada_sem_confirmacao_valida, montar(TITULOS_F1, alteracoes=alt)).empty


def test_r5_negativo_telefone_trocado_ha_mais_de_30_dias_vale():
    alt = (_troca_telefone("2026-08-01 10:00", "(85) 3000-1111", "(85) 3555-0000")
           + _troca("2026-10-30 16:05", como="ligação para o telefone do cadastro (85) 3555-0000"))
    assert rodar(checks.r5_conta_alterada_sem_confirmacao_valida, montar(TITULOS_F1, alteracoes=alt)).empty


def test_r5_pix_so_olha_troca_de_chave_pix():
    titulos = [{"id": "TP000001", "nf": "123", "valor": 1000.00, "forma": "pix"}]
    troca_conta = montar(titulos, alteracoes=_troca("2026-10-30 16:05", confirmada="não"))
    assert rodar(checks.r5_conta_alterada_sem_confirmacao_valida, troca_conta).empty
    troca_chave = montar(titulos, alteracoes=_troca("2026-10-30 16:05", confirmada="não",
                                                    campos=("chave_pix",)))
    assert rodar(checks.r5_conta_alterada_sem_confirmacao_valida, troca_chave)["titulo_id"].tolist() == ["TP000001"]


# --------------------------------------------------------------------- R6 boleto

def _boleto(id_, banco="777", benef=RAIZ_A, **extra):
    return {"id": id_, "nf": "123", "valor": 1000.00, "forma": "boleto",
            "linha": banco + id_[-4:].zfill(44), "benef_raiz": benef, **extra}


HIST_777 = _boleto("TP000900", banco="777", status="pago", agenda=False)


def _dda(id_, banco="777", situacao="em aberto", benef=RAIZ_A):
    return {"dda_id": f"DDA{id_}", "linha_digitavel": banco + id_[-4:].zfill(44),
            "situacao": situacao, "data_situacao": "2026-10-28", "cnpj_beneficiario_raiz": benef}


def test_r6_negativo_boleto_em_aberto_banco_do_historico():
    base = montar([_boleto("TP000001"), HIST_777], dda=[_dda("TP000001")])
    assert rodar(checks.r6_boleto_divergente, base).empty


@pytest.mark.parametrize("titulo, dda, trecho", [
    (_boleto("TP000001"), [], "não está no DDA"),
    (_boleto("TP000001"), [_dda("TP000001", situacao="liquidado")], "liquidado em 28/10"),
    (_boleto("TP000001"), [_dda("TP000001", situacao="baixado pelo beneficiário")], "baixado"),
    (_boleto("TP000001", benef="99999999"), [_dda("TP000001")], "raiz de CNPJ diferente"),
    (_boleto("TP000001", banco="555"), [_dda("TP000001", banco="555")], "banco do boleto 555"),
])
def test_r6_divergencias(titulo, dda, trecho):
    base = montar([titulo, HIST_777], dda=dda)
    saida = rodar(checks.r6_boleto_divergente, base)
    assert saida["titulo_id"].tolist() == ["TP000001"]
    assert saida.loc[0, "severidade"] == "BLOQUEIA" and trecho in saida.loc[0, "evidencia"]
    assert "0" * 20 not in saida.loc[0, "evidencia"]          # sem linha digitável na evidência


def test_r6_negativo_banco_novo_igual_ao_do_cadastro():
    # F1 tem banco 111 no cadastro: boleto pelo 111 é aceito mesmo fora do histórico
    base = montar([_boleto("TP000001", banco="111"), HIST_777], dda=[_dda("TP000001", banco="111")])
    assert rodar(checks.r6_boleto_divergente, base).empty


# --------------------------------------------------------------------- R7 fracionamento

def test_r7_mais_titulos_que_duplicatas_no_mesmo_dia_somando_acima_da_alcada():
    base = montar([
        {"id": f"TP00000{i}", "nf": "123", "valor": 2000.00, "lancado": f"2026-10-20 1{i}:00"}
        for i in (1, 2, 3)                                    # NF 123 tem 2 duplicatas
    ], notas=[NOTA_123])
    saida = rodar(checks.r7_fracionamento, base)
    assert saida["titulo_id"].tolist() == ["TP000001", "TP000002", "TP000003"]
    assert (saida["severidade"] == "ACIONA").all()
    assert "somando R$ 6.000,00; a NF tem 2 duplicata(s)" in saida.loc[0, "evidencia"]


def test_r7_negativo_parcelamento_legitimo_dias_diferentes_e_titulo_acima_da_alcada():
    parcelada = montar([
        {"id": "TP000001", "nf": "123", "parcela": "1/2", "valor": 3000.00, "lancado": "2026-10-20 10:00"},
        {"id": "TP000002", "nf": "123", "parcela": "2/2", "valor": 3000.00, "lancado": "2026-10-20 11:00"},
    ], notas=[NOTA_123])
    assert rodar(checks.r7_fracionamento, parcelada).empty
    dias = montar([
        {"id": f"TP00000{i}", "nf": "123", "valor": 2000.00, "lancado": f"2026-10-2{i} 10:00"}
        for i in (1, 2, 3)
    ], notas=[NOTA_123])
    assert rodar(checks.r7_fracionamento, dias).empty
    um_grande = montar([
        {"id": "TP000001", "nf": "123", "valor": 6000.00, "lancado": "2026-10-20 10:00"},
        {"id": "TP000002", "nf": "123", "valor": 100.00, "lancado": "2026-10-20 11:00"},
        {"id": "TP000003", "nf": "123", "valor": 100.00, "lancado": "2026-10-20 12:00"},
    ], notas=[NOTA_123])
    assert rodar(checks.r7_fracionamento, um_grande).empty


# --------------------------------------------------------------------- R8 segregação

def _aprov(titulo, aprovador):
    return {"aprovacao_id": f"AP{titulo}", "titulo_id": titulo, "data_hora": "2026-11-01 08:00",
            "aprovador": aprovador, "decisao": "aprovado"}


def test_r8_lancou_e_aprovou():
    base = montar([{"id": "TP000001", "nf": "123", "valor": 9000.00, "por": "u3"}],
                  aprovacoes=[_aprov("TP000001", "u3")])
    saida = rodar(checks.r8_segregacao, base)
    assert saida["titulo_id"].tolist() == ["TP000001"]
    assert saida.loc[0, "severidade"] == "ACIONA" and "u3 lançou e aprovou" in saida.loc[0, "evidencia"]


def test_r8_alterou_cadastro_e_aprovou():
    base = montar([{"id": "TP000001", "nf": "123", "valor": 9000.00, "por": "u1"}],
                  aprovacoes=[_aprov("TP000001", "u5")],
                  alteracoes=_troca("2026-10-01 10:00", por="u5", campos=("endereco",)))
    saida = rodar(checks.r8_segregacao, base)
    assert "u5 alterou o cadastro do fornecedor e aprovou" in saida.loc[0, "evidencia"]


def test_r8_alteracao_bancaria_no_log_fora_de_alteracoes_cadastro():
    log = [{"log_id": "LG1", "data_hora": "2026-10-31 22:00", "usuario": "u4",
            "acao": "alteracao_fornecedor", "entidade": "fornecedor", "entidade_id": "F1",
            "detalhe": "campo conta; origem email; AC99999"}]
    base = montar(TITULOS_F1, log_erp=log)
    saida = rodar(checks.r8_segregacao, base)
    assert saida["titulo_id"].tolist() == ["TP000001"]                  # ted sim, boleto não
    assert "sem registro em alteracoes_cadastro" in saida.loc[0, "evidencia"]


def test_r8_negativo_aprovador_diferente_conta_de_servico_e_log_registrado():
    alt = _troca("2026-10-01 10:00", por="u4", campos=("conta",))
    log = [{"log_id": "LG1", "data_hora": "2026-10-01 10:00", "usuario": "u4",
            "acao": "alteracao_fornecedor", "entidade": "fornecedor", "entidade_id": "F1",
            "detalhe": f"campo conta; origem email; {alt[0]['alteracao_id']}"}]
    base = montar([
        {"id": "TP000001", "nf": "123", "valor": 9000.00, "por": "u1"},
        {"id": "TP000002", "nf": "123", "parcela": "2/2", "valor": 9000.00, "por": "robo"},
    ], aprovacoes=[_aprov("TP000001", "u9"), _aprov("TP000002", "robo")],
        alteracoes=alt, log_erp=log, usuarios=[{"login": "robo", "perfil": "integracao",
                                                "data_admissao": "2020-01-01"}])
    assert rodar(checks.r8_segregacao, base).empty


# --------------------------------------------------------------------- A1 lançador recente

USUARIOS = [{"login": "novo", "perfil": "analista_cp", "data_admissao": "2026-10-01"},
            {"login": "antigo", "perfil": "analista_cp", "data_admissao": "2024-01-01"},
            {"login": "robo", "perfil": "integracao", "data_admissao": "2026-10-01"}]


def test_a1_lancador_admitido_ha_menos_de_n_dias_no_lancamento():
    base = montar([{"id": "TP000001", "nf": "123", "valor": 500.00, "por": "novo",
                    "lancado": "2026-10-10 09:00"}], usuarios=USUARIOS)
    saida = rodar(checks.a1_lancador_recente, base)
    assert saida["titulo_id"].tolist() == ["TP000001"]
    assert saida.loc[0, "severidade"] == "AGRAVA" and "9 dias após a admissão" in saida.loc[0, "evidencia"]


def test_a1_negativo_antigo_conta_de_servico_e_novato_ha_mais_de_n_dias():
    base = montar([
        {"id": "TP000001", "nf": "123", "valor": 500.00, "por": "antigo", "lancado": "2026-10-10 09:00"},
        {"id": "TP000002", "nf": "123", "valor": 500.00, "por": "robo", "lancado": "2026-10-10 09:00"},
        {"id": "TP000003", "nf": "123", "valor": 500.00, "por": "novo", "lancado": "2026-11-15 09:00"},
    ], usuarios=USUARIOS)
    assert rodar(checks.a1_lancador_recente, base).empty


def test_a1_sem_n_injetado_falha_alto():
    base = montar([{"id": "TP000001", "nf": "123", "valor": 500.00}], usuarios=USUARIOS)
    with pytest.raises(ValueError, match="dias_lancador_recente"):
        checks.a1_lancador_recente(base.agenda, base, {})


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
    pol["guardiao"] = {"dias_lancador_recente": 45}  # o guardião injeta do config
    alertas = checks.rodar_regras(base.agenda, base, pol)
    assert not alertas.empty
    for linha in alertas.to_dict("records"):
        Alerta.model_validate(linha)
    assert set(alertas["titulo_id"]) <= set(base.agenda["titulo_id"])
