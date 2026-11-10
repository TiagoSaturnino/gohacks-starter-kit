from src.rotear import agrupar, pessoas_para

POL = {"acionamento_humano": {"pessoas": [{"pessoa_id": f"PS{i:02d}"} for i in range(1, 12)]},
       "alcadas": {"diretora_financeira": {"aprova_acima_de": 50000.0, "pessoa_id": "PS03"}}}


def test_fraude_aciona_coordenadora_analista_e_dono_da_categoria():
    assert pessoas_para(["R5_conta"], "embalagens", 1000.0, POL) == {"PS01", "PS08", "PS04"}


def test_acima_da_alcada_aciona_diretoria_e_abaixo_nao():
    assert "PS03" in pessoas_para(["R2_valor"], "frete", 60000.0, POL)
    assert pessoas_para(["R2_valor"], "frete", 1000.0, POL) == {"PS07"}


def test_agrupa_uma_mensagem_por_pessoa_por_lote():
    g = agrupar([("TP000001", "L1", {"PS04"}), ("TP000002", "L1", {"PS04", "PS01"}),
                 ("TP000003", "L2", {"PS04"})])
    assert g == {("PS01", "L1"): ["TP000002"], ("PS04", "L1"): ["TP000001", "TP000002"],
                 ("PS04", "L2"): ["TP000003"]}
