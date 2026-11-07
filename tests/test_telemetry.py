from helpers import ML_LOG, FakeClient, make_settings, tool_response, valid_analysis
from src.analyzer import analyze_log
from src.resilience import CircuitBreaker
from src.store import Store
from src.telemetry import persist_outcome


def live(scenario, client):
    return analyze_log(
        ML_LOG, scenario=scenario, settings=make_settings(), client=client,
        breaker=CircuitBreaker(failure_threshold=99),
    )


def test_live_outcome_is_recorded_without_triage(tmp_path, scenario):
    store = Store(str(tmp_path / "a.db"))
    outcome = live(scenario, FakeClient(tool_response(valid_analysis(), input_tokens=120, output_tokens=60)))
    result = persist_outcome(store, "inc1", "ecommerce", outcome)
    assert result.triage_id is None and result.error is None
    row = store.analyses()[0]
    assert row["source"] == "LIVE" and row["input_tokens"] == 120 and row["severity"] == "HIGH"
    assert row["declared_confidence"] == 0.9 and row["effective_confidence"] == 0.8
    assert store.manual_triage() == []


def test_degraded_outcome_is_routed_to_manual_triage_once(tmp_path, scenario):
    store = Store(str(tmp_path / "a.db"))
    outcome = live(scenario, FakeClient(RuntimeError("falha")))
    first = persist_outcome(store, "inc1", "ecommerce", outcome)
    second = persist_outcome(store, "inc1", "ecommerce", outcome)
    assert first.triage_id and first.triage_created is True
    assert second.triage_id == first.triage_id and second.triage_created is False
    queue = store.manual_triage()
    assert len(queue) == 1 and queue[0]["reason"] == "UNEXPECTED"
    assert store.analytics()["degraded"] == 2  # cada execução vira uma linha de métrica


def test_persistence_failure_never_raises(tmp_path, scenario, monkeypatch):
    store = Store(str(tmp_path / "a.db"))

    def boom(*args, **kwargs):
        raise OSError("disco cheio")

    monkeypatch.setattr(store, "record_analysis", boom)
    outcome = live(scenario, FakeClient(tool_response(valid_analysis())))
    result = persist_outcome(store, "inc1", "ecommerce", outcome)
    assert result.error and "disco cheio" in result.error
