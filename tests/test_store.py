from src.schemas import Acao, Alerta, Decisao, OrigemDecisao, Severidade
from src.store import Store

A = Alerta(titulo_id="TP000001", regra="R1_duplicidade", severidade=Severidade.BLOQUEIA,
           evidencia="NF 123 parc 1/1 também em TP000002", valor=10.0)


def test_alertas_idempotentes():
    s = Store(":memory:")
    assert s.record_alertas([A], "2026-11-02T08:00", {"TP000001": "RM20261103-MER"}) == 1
    assert s.record_alertas([A], "2026-11-02T09:00") == 0
    assert len(s.alertas()) == 1


def test_acionamento_uma_vez_por_pessoa_lote():
    s = Store(":memory:")
    _, c1 = s.record_acionamento("PS01", "RM1", ["TP2", "TP1"], "slack", "msg", "2026-11-02T08:00")
    _, c2 = s.record_acionamento("PS01", "RM1", ["TP1", "TP2"], "slack", "msg", "2026-11-02T08:05")
    assert c1 and not c2


def test_vale_a_ultima_decisao_e_clique_duplo_nao_duplica():
    s = Store(":memory:")
    reter = Decisao(titulo_id="TP000001", lote_remessa="RM1", acao=Acao.RETER_TITULO,
                    decidido_por="PS01", origem=OrigemDecisao.SLACK)
    liberar = reter.model_copy(update={"acao": Acao.LIBERAR_TITULO})
    _, criado = s.record_decisao(reter, "2026-11-02T08:10")
    _, dup = s.record_decisao(reter, "2026-11-02T08:10")
    s.record_decisao(liberar, "2026-11-02T08:30")
    assert criado and not dup
    assert s.ultima_decisao("TP000001", "RM1")["acao"] == "LIBERAR_TITULO"
    assert len(s.decisoes()) == 2  # histórico preservado


def test_dois_relogios():
    s = Store(":memory:")
    s.record_alertas([A], "2026-11-02T08:00")
    row = s.alertas()[0]
    assert row["ts_simulado"] == "2026-11-02T08:00"
    assert row["ts_real"] != row["ts_simulado"] and "T" in row["ts_real"]
