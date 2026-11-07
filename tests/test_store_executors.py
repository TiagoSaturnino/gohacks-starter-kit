import sqlite3

from helpers import make_settings
from src.executors import DryRunExecutor, OutboxExecutor, build_executor
from src.schemas import RecommendedAction
from src.store import Store, make_incident_id, payload_hash

ACTION = RecommendedAction(
    action_type="REFRESH_TOKEN", description="renovar", payload={"seller_id": "8849201"}
)


def test_incident_id_ignores_whitespace():
    assert make_incident_id("a  b\nc") == make_incident_id("a b c")
    assert make_incident_id("a b c") != make_incident_id("a b d")


def test_payload_hash_is_order_independent():
    assert payload_hash({"a": 1, "b": 2}) == payload_hash({"b": 2, "a": 1})
    assert payload_hash(None) == payload_hash({})


def test_decision_is_idempotent(tmp_path):
    store = Store(str(tmp_path / "audit.db"))
    first_id, first_created = store.record_decision("inc1", "REFRESH_TOKEN", ACTION.payload, "Tiago", "APPROVED", "MOCK")
    second_id, second_created = store.record_decision("inc1", "REFRESH_TOKEN", ACTION.payload, "Outra", "APPROVED", "MOCK")
    assert first_created is True and second_created is False
    assert first_id == second_id
    stored = store.get_decision("inc1", "REFRESH_TOKEN", ACTION.payload)
    assert stored["approver"] == "Tiago"  # a primeira decisão prevalece
    assert len(store.decisions()) == 1


def test_different_payload_is_a_different_decision(tmp_path):
    store = Store(str(tmp_path / "audit.db"))
    store.record_decision("inc1", "REFRESH_TOKEN", {"seller_id": "1"}, "A", "APPROVED", "LIVE")
    _, created = store.record_decision("inc1", "REFRESH_TOKEN", {"seller_id": "2"}, "A", "APPROVED", "LIVE")
    assert created is True


def test_decisions_persist_across_store_instances(tmp_path):
    path = str(tmp_path / "audit.db")
    Store(path).record_decision("inc1", "REFRESH_TOKEN", None, "Tiago", "REJECTED", "MOCK")
    assert Store(path).get_decision("inc1", "REFRESH_TOKEN", None)["decision"] == "REJECTED"


def test_dry_run_executor_makes_no_external_call():
    result = DryRunExecutor().execute(1, ACTION)
    assert result.status == "SIMULATED"


def test_outbox_executor_enqueues_once(tmp_path):
    store = Store(str(tmp_path / "audit.db"))
    decision_id, _ = store.record_decision("inc1", "REFRESH_TOKEN", ACTION.payload, "Tiago", "APPROVED", "MOCK")
    executor = OutboxExecutor(store)
    first = executor.execute(decision_id, ACTION)
    second = executor.execute(decision_id, ACTION)
    assert first.status == "QUEUED"
    assert first.detail == second.detail
    rows = store.outbox()
    assert len(rows) == 1 and rows[0]["status"] == "PENDING"


def test_set_execution_is_visible_in_decision(tmp_path):
    store = Store(str(tmp_path / "audit.db"))
    decision_id, _ = store.record_decision("inc1", "REFRESH_TOKEN", None, "T", "APPROVED", "MOCK")
    store.set_execution(decision_id, "SIMULATED", "ok")
    assert store.get_decision("inc1", "REFRESH_TOKEN", None)["execution_status"] == "SIMULATED"


def test_build_executor_follows_settings(tmp_path):
    store = Store(str(tmp_path / "audit.db"))
    assert build_executor(make_settings(executor="dry_run"), store).name == "dry_run"
    assert build_executor(make_settings(executor="outbox"), store).name == "outbox"
    assert build_executor(make_settings(executor="qualquer"), store).name == "dry_run"


# ---------------------------------------------------------------- v3
def test_timestamps_are_local_with_explicit_offset(tmp_path):
    store = Store(str(tmp_path / "audit.db"))
    store.record_decision("inc1", "REFRESH_TOKEN", None, "T", "APPROVED", "MOCK")
    assert store.decisions()[0]["ts"].endswith("-03:00")
    utc_store = Store(str(tmp_path / "utc.db"), tz_name="UTC")
    utc_store.record_decision("inc1", "REFRESH_TOKEN", None, "T", "APPROVED", "MOCK")
    assert utc_store.decisions()[0]["ts"].endswith("+00:00")


def test_decision_seconds_and_initial_execution_status(tmp_path):
    store = Store(str(tmp_path / "audit.db"))
    store.record_decision("inc1", "REFRESH_TOKEN", None, "T", "APPROVED", "MOCK", decision_seconds=12.5)
    store.record_decision("inc1", "RETRY_WEBHOOK", None, "T", "REJECTED", "MOCK")
    approved = store.get_decision("inc1", "REFRESH_TOKEN", None)
    rejected = store.get_decision("inc1", "RETRY_WEBHOOK", None)
    assert approved["decision_seconds"] == 12.5 and approved["execution_status"] == "PENDING"
    assert rejected["execution_status"] == "NOT_EXECUTED"


def test_double_click_creates_a_single_row(tmp_path):
    store = Store(str(tmp_path / "audit.db"))
    outcomes = [
        store.record_decision("inc1", "REFRESH_TOKEN", {"seller_id": "8849201"}, "T", "APPROVED", "LIVE")
        for _ in range(5)
    ]
    assert [created for _, created in outcomes] == [True, False, False, False, False]
    assert len({decision_id for decision_id, _ in outcomes}) == 1
    assert len(store.decisions()) == 1


def test_manual_triage_queue_is_idempotent_per_incident(tmp_path):
    store = Store(str(tmp_path / "audit.db"))
    first_id, first_created = store.enqueue_manual_triage("inc1", "RATE_LIMIT", "429")
    second_id, second_created = store.enqueue_manual_triage("inc1", "TIMEOUT", "outro")
    assert first_created is True and second_created is False and first_id == second_id
    rows = store.manual_triage()
    assert len(rows) == 1 and rows[0]["reason"] == "RATE_LIMIT" and rows[0]["status"] == "PENDING"


def test_analytics_aggregates_real_metrics(tmp_path):
    store = Store(str(tmp_path / "audit.db"))
    store.record_analysis("a", "ecommerce", {"source": "LIVE", "latency_ms": 2000, "input_tokens": 100,
                                              "output_tokens": 50, "cost_usd": 0.01})
    store.record_analysis("b", "ecommerce", {"source": "LIVE", "latency_ms": 4000, "input_tokens": 200,
                                              "output_tokens": 70, "cost_usd": 0.02})
    store.record_analysis("c", "ecommerce", {"source": "DEGRADED", "reason": "RATE_LIMIT", "latency_ms": 10})
    stats = store.analytics()
    assert stats["total"] == 3 and stats["live"] == 2 and stats["degraded"] == 1
    assert stats["avg_latency_live_ms"] == 3000
    assert stats["input_tokens"] == 300 and stats["output_tokens"] == 120
    assert round(stats["cost_usd"], 4) == 0.03


def test_analytics_on_empty_database(tmp_path):
    stats = Store(str(tmp_path / "audit.db")).analytics()
    assert stats["total"] == 0 and stats["avg_latency_live_ms"] is None and stats["cost_usd"] == 0


def test_legacy_database_is_migrated_in_place(tmp_path):
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, "
        "incident_id TEXT NOT NULL, action_type TEXT NOT NULL, payload_hash TEXT NOT NULL, "
        "approver TEXT NOT NULL, decision TEXT NOT NULL, source TEXT NOT NULL, "
        "execution_status TEXT, execution_detail TEXT, UNIQUE (incident_id, action_type, payload_hash));"
        "INSERT INTO decisions (ts, incident_id, action_type, payload_hash, approver, decision, source) "
        "VALUES ('2026-11-02T17:00:00+00:00', 'old', 'REFRESH_TOKEN', 'h', 'Tiago', 'APPROVED', 'MOCK');"
    )
    conn.commit()
    conn.close()
    store = Store(path)  # não deve falhar nem perder a linha antiga
    assert store.decisions()[0]["incident_id"] == "old"
    store.record_decision("new", "REFRESH_TOKEN", None, "T", "APPROVED", "MOCK", decision_seconds=1.0)
    assert len(store.decisions()) == 2
