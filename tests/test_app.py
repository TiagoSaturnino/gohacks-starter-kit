"""Testes de fluxo da UI com o harness oficial do Streamlit (sem navegador)."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import src.analyzer as analyzer_module
from src.store import Store

APP = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "audit.db")


@pytest.fixture
def app(db_path, monkeypatch):
    monkeypatch.setenv("USE_MOCK", "True")
    monkeypatch.setenv("SCENARIO", "ecommerce")
    monkeypatch.setenv("EXECUTOR", "dry_run")
    monkeypatch.setenv("AUDIT_DB", db_path)
    test = AppTest.from_file(APP, default_timeout=30)
    test.run()
    assert not test.exception
    return test


def button_with_prefix(app, prefix):
    return next(b for b in app.button if b.key and b.key.startswith(prefix))


def all_markdown(app):
    return " ".join(item.value for item in app.markdown)


def test_main_screen_has_three_tabs(app):
    labels = [tab.label for tab in app.tabs]
    assert labels == [
        "🩺 Diagnóstico e ações (HITL)",
        "🔒 Segurança, PII e schema",
        "🗂️ Auditoria e outbox",
    ]


def test_offline_replay_shows_badges_and_both_confidences(app):
    app.button(key="run_single").click().run()
    assert not app.exception
    text = all_markdown(app)
    assert "orange-badge[Severidade: Alto]" in text  # HIGH em laranja
    assert "Execução SIMULADA" in text and "Revisão humana obrigatória" in text
    labels = [metric.label for metric in app.metric]
    assert "Confiança declarada (modelo)" in labels and "Confiança efetiva (política)" in labels
    assert any("replay" in item.value.lower() for item in app.info)


def test_approval_flow_records_local_time_and_decision_seconds(app, db_path):
    app.button(key="run_single").click().run()
    assert button_with_prefix(app, "approve_").disabled  # sem operador não aprova
    app.sidebar.text_input(key="operator").set_value("Tiago").run()
    button_with_prefix(app, "approve_").click().run()
    assert not app.exception
    assert any("Aprovada por" in item.value and "SIMULATED" in item.value for item in app.success)

    rows = Store(db_path).decisions()
    assert len(rows) == 1 and rows[0]["approver"] == "Tiago" and rows[0]["decision"] == "APPROVED"
    assert rows[0]["ts"].endswith("-03:00") and rows[0]["decision_seconds"] is not None


def test_unknown_log_is_fail_closed_and_routed_to_manual_triage(app, db_path):
    app.text_area[0].set_value("erro totalmente desconhecido ABC-99999").run()
    app.button(key="run_single").click().run()
    assert any("fail-closed" in item.value for item in app.error)
    assert "fila de triagem manual N2" in all_markdown(app)
    assert not any(b.key and b.key.startswith("approve_") for b in app.button)  # automação bloqueada
    queue = Store(db_path).manual_triage()
    assert len(queue) == 1 and queue[0]["reason"] == "NO_FIXTURE"


def test_unexpected_crash_shows_friendly_message_not_a_traceback(app, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("falha simulada")

    monkeypatch.setattr(analyzer_module, "analyze_log", boom)
    app.run()  # reexecuta o script já com o patch
    app.button(key="run_single").click().run()
    assert not app.exception
    assert any("Falha inesperada" in item.value for item in app.error)


def test_batch_mode_with_scenario_samples(app, db_path):
    app.sidebar.radio(key="mode").set_value("Lote (CSV)").run()
    app.checkbox(key="batch_sample").check().run()
    app.button(key="run_batch").click().run()
    assert not app.exception
    labels = [metric.label for metric in app.metric]
    assert "Processados" in labels and "Sem análise (DEGRADED)" in labels
    assert len(Store(db_path).analyses()) >= 4
