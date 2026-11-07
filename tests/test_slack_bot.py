"""Testes do slack_bot.py sem Slack: mensagens, clique, decisão na agenda e plano B."""
import re
from pathlib import Path

import pandas as pd
import pytest

from src import slack_bot as sb
from src.executors import AgendaSimuladaExecutor
from src.store import Store

DADOS_REAIS = Path(__file__).resolve().parents[1] / "pagamentos"


class _Base:
    def __init__(self, agenda, fornecedores, lotes=None):
        self.agenda, self.fornecedores = agenda, fornecedores
        self.lotes = lotes if lotes is not None else pd.DataFrame()


def _base():
    agenda = pd.DataFrame([
        {"titulo_id": "TP000001", "lote_remessa": "RM1", "fornecedor_id": "F1", "valor": 1000.0},
        {"titulo_id": "TP000002", "lote_remessa": "RM1", "fornecedor_id": "F1", "valor": 5000.0},
        {"titulo_id": "TP000003", "lote_remessa": "RM2", "fornecedor_id": "F2", "valor": 300.0},
    ])
    forn = pd.DataFrame([{"fornecedor_id": "F1", "categoria": "produto"},
                         {"fornecedor_id": "F2", "categoria": "frete"}])
    lotes = pd.DataFrame([{"lote_remessa": "RM1", "data_pagamento": "2026-11-03",
                           "horario_fechamento": "10:00"}])
    return _Base(agenda, forn, lotes)


def _alertas():
    return pd.DataFrame([
        {"titulo_id": "TP000001", "regra": "R2_valor_x_duplicata", "severidade": "BLOQUEIA",
         "evidencia": "título R$ 1.000,00 × duplicata 001", "valor": 1000.0},
        {"titulo_id": "TP000002", "regra": "R5_conta", "severidade": "BLOQUEIA",
         "evidencia": "conta trocada; CNPJ 85.779.751/0001-00", "valor": 5000.0},
        {"titulo_id": "TP000003", "regra": "R4_oc_x_nf", "severidade": "ACIONA",
         "evidencia": "OC pendente", "valor": 300.0},
    ])


def test_pendentes_so_bloqueia_ordenado_por_lote_e_valor():
    itens = sb.pendentes(_base(), _alertas())
    assert [p.titulo_id for p in itens] == ["TP000002", "TP000001"]  # ACIONA fica de fora
    assert itens[0].categoria == "produto" and itens[0].lote_remessa == "RM1"


def test_evidencia_vai_mascarada_para_o_slack():
    itens = sb.pendentes(_base(), _alertas())
    texto = str(sb.montar_mensagens("RM1", itens))
    assert "85.779.751/0001-00" not in texto
    assert not re.search(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", texto)


def test_mensagem_tem_botoes_por_titulo_e_respeita_limite_de_blocos():
    p = sb.pendentes(_base(), _alertas())[0]
    itens = [sb.Pendente(f"TP{i:06d}", "RM1", "F1", "produto", 10.0 + i, p.motivos) for i in range(45)]
    msgs = sb.montar_mensagens("RM1", itens, "03/11 10:00")
    assert len(msgs) == 3 and all(len(m["blocks"]) <= 50 for m in msgs)
    acoes = [b for m in msgs for b in m["blocks"] if b["type"] == "actions"]
    assert len(acoes) == 45
    ids = {e["action_id"] for e in acoes[0]["elements"]}
    assert ids == {sb.ACTION_RETER, sb.ACTION_LIBERAR}
    assert acoes[0]["elements"][0]["value"] == "TP000000|RM1"
    assert "fecha 03/11 10:00" in msgs[0]["text"] and "(1/3)" in msgs[0]["text"]


def test_registrar_clique_troca_so_os_botoes_do_titulo_clicado():
    itens = sb.pendentes(_base(), _alertas())
    blocos = sb.montar_mensagens("RM1", itens)[0]["blocks"]
    novos = sb.registrar_clique(blocos, "dec|TP000002|RM1", "✅ liberado")
    assert [b["type"] for b in novos].count("actions") == 1
    assert any(b["type"] == "context" for b in novos)


def _executor(tmp_path):
    original = tmp_path / "agenda_remessa.csv"
    pd.DataFrame([{"lote_remessa": "RM1", "titulo_id": "TP000001", "valor": "1000.00"}]).to_csv(
        original, index=False)
    return AgendaSimuladaExecutor(str(original), str(tmp_path / "agenda_simulada.csv"))


def test_clique_liberar_grava_decisao_e_agenda_e_repeticao_nao_duplica(tmp_path):
    store, executor = Store(":memory:"), _executor(tmp_path)
    r1 = sb.aplicar_decisao(store, executor, "TP000001", "RM1", sb.ACTION_LIBERAR, "renata",
                            "2026-11-02T08:00")
    r2 = sb.aplicar_decisao(store, executor, "TP000001", "RM1", sb.ACTION_LIBERAR, "renata",
                            "2026-11-02T08:00")
    assert r1["status"] == "WRITTEN" and r2["status"] == "DUPLICADA"
    agenda = pd.read_csv(tmp_path / "agenda_simulada.csv", dtype=str)
    assert agenda.loc[0, "status_guardiao"] == "liberado"
    assert agenda.loc[0, "decidido_por"] == "renata (slack)"
    assert len(store.decisoes()) == 1 and store.decisoes()[0]["origem"] == "slack"


def test_titulo_com_decisao_humana_sai_dos_pendentes(tmp_path):
    store, executor = Store(":memory:"), _executor(tmp_path)
    sb.aplicar_decisao(store, executor, "TP000001", "RM1", sb.ACTION_RETER, "renata", "2026-11-02T08:00")
    assert [p.titulo_id for p in sb.pendentes(_base(), _alertas(), store)] == ["TP000002"]


def test_plano_b_manda_pendentes_para_triagem_sem_duplicar():
    store = Store(":memory:")
    itens = sb.pendentes(_base(), _alertas())
    assert sb.enviar_para_triagem(store, itens, "slack_nao_configurado") == 2
    assert sb.enviar_para_triagem(store, itens, "slack_nao_configurado") == 0
    assert len(store.manual_triage()) == 2


@pytest.mark.skipif(not DADOS_REAIS.exists(), reason="dados do desafio fora do git")
def test_sem_tokens_o_bot_cai_no_plano_b(tmp_path, monkeypatch):
    for var in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_CANAL"):
        monkeypatch.setenv(var, "")
    monkeypatch.setenv("DADOS_DIR", str(DADOS_REAIS))
    db = tmp_path / "g.db"
    assert sb.main(["--agora", "2026-11-02T08:00", "--db", str(db)]) == 1
    assert len(Store(str(db)).manual_triage()) > 0


@pytest.mark.skipif(not DADOS_REAIS.exists(), reason="dados do desafio fora do git")
def test_filtro_de_lote_no_plano_b(tmp_path, monkeypatch):
    for var in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_CANAL"):
        monkeypatch.setenv(var, "")
    monkeypatch.setenv("DADOS_DIR", str(DADOS_REAIS))
    db = tmp_path / "g.db"
    sb.main(["--agora", "2026-11-02T08:00", "--db", str(db), "--lote", "RM20261103-MER"])
    chaves = [t["chave"] for t in Store(str(db)).manual_triage()]
    assert chaves and all(c.endswith("|RM20261103-MER") for c in chaves)


@pytest.mark.skipif(not DADOS_REAIS.exists(), reason="dados do desafio fora do git")
def test_token_com_formato_errado_cai_no_plano_b_sem_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xapp-trocado")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-1")
    monkeypatch.setenv("SLACK_CANAL", "C123")
    monkeypatch.setenv("DADOS_DIR", str(DADOS_REAIS))
    assert sb.main(["--agora", "2026-11-02T08:00", "--db", str(tmp_path / "g.db")]) == 1
    assert "xoxb-" in capsys.readouterr().out
