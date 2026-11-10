"""Testes do carga.py: normalizações, loader csv/json, politica() e validação Pydantic."""
import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from src.carga import (
    carregar, ler_tabela, normalizar_cnpj, normalizar_nf, politica, raiz_cnpj,
)

DADOS_REAIS = Path(__file__).resolve().parents[1] / "pagamentos"


# --------------------------------------------------------------------- normalizações

@pytest.mark.parametrize("entrada, esperado", [
    ("85.779.751/0001-00", "85779751000100"),
    ("85779751000100", "85779751000100"),
    (" 13.030.916/0002-17 ", "13030916000217"),
    ("123", None),          # tamanho errado não vira CNPJ "parecido"
    ("", None),
    (None, None),
    (float("nan"), None),
])
def test_normalizar_cnpj(entrada, esperado):
    assert normalizar_cnpj(entrada) == esperado


def test_raiz_cnpj_iguala_matriz_e_filial():
    assert raiz_cnpj("13.030.916/0001-36") == raiz_cnpj("13030916000217") == "13030916"
    assert raiz_cnpj("lixo") is None


@pytest.mark.parametrize("entrada, esperado", [
    ("9671", "9671"),
    ("000009671", "9671"),   # digitado com zeros
    ("9671-1", "9671"),      # série depois de '-'
    ("9671/1", "9671"),      # série depois de '/'
    ("0009671-001", "9671"),
    ("000", "0"),
    ("", None),
    (None, None),
])
def test_normalizar_nf(entrada, esperado):
    assert normalizar_nf(entrada) == esperado


# --------------------------------------------------------------------- loader

def test_ler_tabela_prefere_csv_e_le_tudo_como_texto(tmp_path):
    (tmp_path / "t.csv").write_text("id,valor\n007,10.50\n", encoding="utf-8")
    df = ler_tabela(tmp_path, "t")
    assert df.loc[0, "id"] == "007"      # zeros à esquerda preservados
    assert df.loc[0, "valor"] == "10.50"


def test_ler_tabela_aceita_jsonl_quando_nao_ha_csv(tmp_path):
    linhas = [{"comunicacao_id": "EM000001", "texto": "oi", "anexos": ["a.pdf", "b.pdf"]},
              {"comunicacao_id": "EM000002", "texto": None, "anexos": []}]
    (tmp_path / "c.jsonl").write_text("\n".join(json.dumps(l) for l in linhas), encoding="utf-8")
    df = ler_tabela(tmp_path, "c")
    assert list(df["comunicacao_id"]) == ["EM000001", "EM000002"]
    assert pd.isna(df.loc[1, "texto"])           # vazio = ausente, como no CSV
    assert df.loc[0, "anexos"] == "a.pdf | b.pdf"   # lista vira o formato do CSV


def test_ler_tabela_sem_arquivo_falha_com_mensagem(tmp_path):
    with pytest.raises(FileNotFoundError, match="nenhum"):
        ler_tabela(tmp_path, "inexistente")


# --------------------------------------------------------------------- politica()

def test_politica_aninha_secoes_listas_e_converte_decimais(tmp_path):
    (tmp_path / "politicas.csv").write_text(
        "secao,regra,valor\r\n"
        "versao,versao,1\r\n"
        "alcadas,analista_cp.liberar_ou_agendar_ate,5000.0\r\n"
        "remessa,bancos_pagadores.Banco X.codigo,612\r\n"
        "tres_vias,dispensas.nf,aluguel\r\n"
        "acionamento_humano,pessoas[2].pessoa_id,PS02\r\n"
        "acionamento_humano,pessoas[1].pessoa_id,PS01\r\n"
        "acionamento_humano,pessoas[1].nome,\"Fulana, Silva\"\r\n",
        encoding="utf-8",
    )
    pol = politica(tmp_path)
    assert pol["versao"] == "1"
    assert pol["alcadas"]["analista_cp"]["liberar_ou_agendar_ate"] == 5000.0
    assert pol["remessa"]["bancos_pagadores"]["Banco X"]["codigo"] == "612"  # código fica texto
    assert pol["tres_vias"]["dispensas"]["nf"] == "aluguel"
    assert [p["pessoa_id"] for p in pol["acionamento_humano"]["pessoas"]] == ["PS01", "PS02"]
    assert pol["acionamento_humano"]["pessoas"][0]["nome"] == "Fulana, Silva"


def test_politica_le_json_quando_nao_ha_csv(tmp_path):
    (tmp_path / "politicas.json").write_text(json.dumps({"atraso": {"multa": 0.02}}), encoding="utf-8")
    assert politica(tmp_path)["atraso"]["multa"] == 0.02


# --------------------------------------------------------------------- carregar() com dados sintéticos

TABELAS_VAZIAS = {
    "fornecedores": "fornecedor_id,cnpj,data_cadastro,data_ultima_alteracao\n"
                    "F1,85.779.751/0001-00,2020-01-01,2020-01-01 10:00:00\n",
    "alteracoes_cadastro": "alteracao_id,data_hora\n",
    "aprovacoes": "aprovacao_id,data_hora,valor_titulo\n",
    "notas_fiscais": "chave_acesso,numero_nf,cnpj_emitente,data_emissao,data_recebimento_portal,"
                     "data_manifestacao,valor_total,valor_produtos_servicos\n"
                     "K1,000123,85779751000217,2026-10-01,,,100.00,100.00\n",
    "nf_duplicatas": "chave_acesso,n_dup,vencimento,valor\n",
    "pedidos_compra": "oc_id,cnpj_fornecedor,data_emissao,data_aprovacao,valor_total,valor_mensal\n",
    "recebimentos_mercadoria": "recebimento_id,numero_nf,cnpj_emitente,data_hora_recebimento\n",
    "dda_boletos": "dda_id,cnpj_beneficiario,data_registro,vencimento,data_situacao,valor\n",
    "contratos_fornecedores": "contrato_id,valor_mensal\n",
    "usuarios_sistema": "login,data_admissao,data_desligamento,alcada_rs\n",
    "log_auditoria_erp": "log_id,data_hora\n",
    "comunicacoes_fornecedores": "comunicacao_id,data_hora\n",
    "lotes_remessa": "lote_remessa\n",
    "feriados_bancarios": "data\n",
}

CONTAS_CAB = ("titulo_id,fornecedor_id,numero_nf_digitado,parcela,valor,vencimento,forma_pagamento,"
              "cnpj_beneficiario_boleto,lancado_por,data_lancamento,status,data_pagamento,"
              "data_aprovacao,data_baixa,data_cancelamento\n")
AGENDA_CAB = ("lote_remessa,data_prevista,horario_fechamento,titulo_id,fornecedor_id,valor,"
              "vencimento,forma_pagamento\n")


def _montar(tmp_path, agenda_linhas, contas_linhas):
    for nome, conteudo in TABELAS_VAZIAS.items():
        (tmp_path / f"{nome}.csv").write_text(conteudo, encoding="utf-8")
    (tmp_path / "agenda_remessa.csv").write_text(AGENDA_CAB + "".join(agenda_linhas), encoding="utf-8")
    (tmp_path / "contas_a_pagar.csv").write_text(CONTAS_CAB + "".join(contas_linhas), encoding="utf-8")
    return tmp_path


def test_carregar_normaliza_tipos_e_chaves(tmp_path):
    d = _montar(
        tmp_path,
        ["L1,2026-11-03,10:00,T1,F1,1234.56,2026-11-03,ted\n"],
        ["T1,F1,000123-1,2/3,1234.56,2026-11-03,boleto,85.779.751/0001-00,u1,"
         "2026-10-01 09:00:00,agendado,,,,\n"],
    )
    base = carregar(d)
    a, c = base.agenda.iloc[0], base.contas.iloc[0]
    assert a["valor"] == pytest.approx(1234.56)
    assert a["fechamento"] == pd.Timestamp("2026-11-03 10:00")
    assert c["nf_norm"] == "123"
    assert (c["parcela_n"], c["parcela_total"]) == (2, 3)
    assert c["cnpj_beneficiario_boleto_raiz"] == "85779751"
    assert c["data_lancamento"] == pd.Timestamp("2026-10-01 09:00")
    assert base.fornecedores.iloc[0]["cnpj_raiz"] == base.notas.iloc[0]["cnpj_emitente_raiz"]  # filial
    assert base.notas.iloc[0]["nf_norm"] == "123"
    assert base.invalidos.empty


def test_carregar_separa_linha_invalida_para_triagem(tmp_path):
    d = _montar(
        tmp_path,
        ["L1,2026-11-03,10:00,T1,F1,100.00,2026-11-03,ted\n",
         "L1,2026-11-03,10:00,T2,F1,abc,2026-11-03,cheque\n"],      # valor e forma inválidos
        ["T1,F1,123,1/1,100.00,2026-11-03,ted,,u1,2026-10-01 09:00:00,agendado,,,,\n",
         "T3,F1,123,1-1,100.00,2026-13-45,ted,,u1,2026-10-01 09:00:00,aberto,,,,\n"],  # parcela/data
    )
    base = carregar(d)
    assert list(base.agenda["titulo_id"]) == ["T1"]
    assert list(base.contas["titulo_id"]) == ["T1"]
    inv = base.invalidos.set_index("titulo_id")
    assert inv.loc["T2", "tabela"] == "agenda_remessa"
    assert "valor" in inv.loc["T2", "erro"] and "forma_pagamento" in inv.loc["T2", "erro"]
    assert "parcela" in inv.loc["T3", "erro"] and "vencimento" in inv.loc["T3", "erro"]
    assert "Traceback" not in " ".join(base.invalidos["erro"])


def test_carregar_nao_altera_arquivos(tmp_path):
    d = _montar(tmp_path, ["L1,2026-11-03,10:00,T1,F1,100.00,2026-11-03,ted\n"], [])
    antes = {p.name: p.read_bytes() for p in d.iterdir()}
    carregar(d)
    assert {p.name: p.read_bytes() for p in d.iterdir()} == antes


# --------------------------------------------------------------------- integração no cenário A

@pytest.mark.skipif(not DADOS_REAIS.exists(), reason="dados do desafio fora do git")
def test_cenario_a_carrega_completo():
    base = carregar(DADOS_REAIS)
    assert len(base.agenda) == 876 and base.invalidos.empty
    assert base.agenda["fechamento"].notna().all()
    assert base.contas["nf_norm"].notna().sum() == len(base.contas) - base.contas["numero_nf_digitado"].isna().sum()
    assert base.fornecedores["cnpj_raiz"].notna().all()
    pol = politica(DADOS_REAIS)
    assert isinstance(pol["alcadas"]["analista_cp"]["liberar_ou_agendar_ate"], float)
    assert len(pol["acionamento_humano"]["pessoas"]) == 11


# --------------------------------------------------------------------- limiares em prosa (item 2)

from src.carga import reais_na_prosa, tolerancia_titulo_x_duplicata  # noqa: E402


@pytest.mark.parametrize("texto, esperado", [
    ("diferença de até R$ 0,10 por arredondamento é aceita", 0.10),
    ("até R$ 1.234,56", 1234.56),
    ("até R$50", 50.0),
])
def test_reais_na_prosa(texto, esperado):
    assert reais_na_prosa(texto) == pytest.approx(esperado)


@pytest.mark.parametrize("texto", ["sem valor nenhum", "R$ 0,10 ou R$ 0,20", "", None])
def test_reais_na_prosa_falha_se_nao_houver_exatamente_um_valor(texto):
    with pytest.raises(ValueError):
        reais_na_prosa(texto)


def _politica_com(tmp_path, texto):
    (tmp_path / "politicas.csv").write_text(
        f'secao,regra,valor\r\ntres_vias,titulo_x_duplicata,"{texto}"\r\n', encoding="utf-8")
    return tmp_path


def test_politica_deriva_tolerancia_da_prosa(tmp_path):
    pol = politica(_politica_com(tmp_path, "valor da duplicata; diferença de até R$ 0,25 é aceita"))
    assert pol["tres_vias"]["tolerancia_titulo_x_duplicata"] == pytest.approx(0.25)
    assert tolerancia_titulo_x_duplicata(pol) == pytest.approx(0.25)


def test_politica_falha_alto_com_tolerancia_ilegivel(tmp_path):
    with pytest.raises(ValueError, match="titulo_x_duplicata"):
        politica(_politica_com(tmp_path, "pequenas diferenças são aceitas"))


def test_tolerancia_aceita_politica_json_numerica():
    assert tolerancia_titulo_x_duplicata({"tres_vias": {"titulo_x_duplicata": 0.1}}) == 0.1
    with pytest.raises(ValueError, match="falta"):
        tolerancia_titulo_x_duplicata({})


# --------------------------------------------------------------------- limiares em prosa (item 3)

from src.carga import dias_telefone_recente, tolerancia_nf_x_oc  # noqa: E402


def test_tolerancia_nf_x_oc_extrai_percentual_e_reais():
    texto = "a NF pode passar do valor da OC em até 2% ou R$ 50,00 (o que for maior)"
    assert tolerancia_nf_x_oc({"tres_vias": {"nf_x_oc": texto}}) == {"percentual": 0.02, "reais": 50.0}
    with pytest.raises(ValueError, match="nf_x_oc"):
        tolerancia_nf_x_oc({"tres_vias": {"nf_x_oc": "até 2% ou 3% ou R$ 50"}})
    with pytest.raises(ValueError, match="falta"):
        tolerancia_nf_x_oc({})


def test_dias_telefone_recente_extrai_da_prosa_e_falha_alto():
    texto = "nunca para um número informado na própria mensagem, nem para um telefone trocado há menos de 30 dias"
    assert dias_telefone_recente({"dados_bancarios": {"troca_de_conta": texto}}) == 30
    with pytest.raises(ValueError, match="troca_de_conta"):
        dias_telefone_recente({"dados_bancarios": {"troca_de_conta": "telefone recente não vale"}})


@pytest.mark.skipif(not DADOS_REAIS.exists(), reason="dados do desafio fora do git")
def test_politica_real_deriva_limiares_do_item_3():
    pol = politica(DADOS_REAIS)
    assert pol["tres_vias"]["tolerancia_nf_x_oc"] == {"percentual": 0.02, "reais": 50.0}
    assert pol["dados_bancarios"]["dias_telefone_recente"] == 30
