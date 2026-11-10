import pandas as pd

from src.executors import AgendaSimuladaExecutor
from src.schemas import Acao, Decisao, OrigemDecisao


def test_agenda_simulada_nao_toca_no_original(tmp_path):
    original = tmp_path / "agenda_remessa.csv"
    original.write_text(
        "lote_remessa,titulo_id,valor\nRM1,TP000001,10.00\nRM1,TP000002,20.00\n", encoding="utf-8"
    )
    antes = original.read_text(encoding="utf-8")
    ex = AgendaSimuladaExecutor(str(original), str(tmp_path / "out" / "agenda_simulada.csv"))
    d = Decisao(titulo_id="TP000001", lote_remessa="RM1", acao=Acao.RETER_TITULO,
                decidido_por="guardiao", origem=OrigemDecisao.SILENCIO, motivo="sem decisão humana")
    assert ex.execute(1, d, "2026-11-03T10:00").status == "WRITTEN"
    df = pd.read_csv(tmp_path / "out" / "agenda_simulada.csv", dtype=str, keep_default_na=False)
    assert df.set_index("titulo_id").loc["TP000001", "status_guardiao"] == "retido"
    assert df.set_index("titulo_id").loc["TP000002", "status_guardiao"] == "no_lote"
    assert df.set_index("titulo_id").loc["TP000001", "valor"] == "10.00"  # dtype=str preserva
    assert original.read_text(encoding="utf-8") == antes
