"""Persistência mínima (SQLite, só biblioteca padrão): auditoria, outbox, triagem N2 e métricas.

* `decisions`: trilha de auditoria (quem decidiu o quê, quando, com qual payload). Há
  unicidade por (incidente, ação, hash do payload): a mesma ação não é decidida duas
  vezes, mesmo com clique duplo (idempotência no banco, não só na tela).
* `outbox`: ações aprovadas aguardando um worker externo (padrão outbox).
* `manual_triage`: fila de triagem manual N2, alimentada quando a análise falha
  (fail-closed). Uma linha por incidente.
* `analyses`: métricas reais de cada análise (latência, tokens, custo, origem).

Horários são gravados em ISO-8601 com offset do fuso local (Brasília por padrão).
É o primeiro degrau do desenho de produção (Postgres + fila): a interface fica, o
backend troca.
"""
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.timeutil import DEFAULT_TZ, iso_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    incident_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    approver TEXT NOT NULL,
    decision TEXT NOT NULL,
    source TEXT NOT NULL,
    execution_status TEXT,
    execution_detail TEXT,
    decision_seconds REAL,
    UNIQUE (incident_id, action_type, payload_hash)
);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    decision_id INTEGER NOT NULL UNIQUE,
    action_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING'
);
CREATE TABLE IF NOT EXISTS manual_triage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    incident_id TEXT NOT NULL UNIQUE,
    reason TEXT NOT NULL,
    detail TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING'
);
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    incident_id TEXT NOT NULL,
    scenario TEXT,
    source TEXT NOT NULL,
    reason TEXT,
    model TEXT,
    latency_ms INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    attempts INTEGER,
    severity TEXT,
    declared_confidence REAL,
    effective_confidence REAL
);
"""

# Colunas acrescentadas depois da primeira versão (bancos antigos são migrados).
MIGRATIONS = {"decisions": [("decision_seconds", "REAL")]}


def make_incident_id(log_text: str) -> str:
    """Identificador estável do incidente (hash do log normalizado)."""
    normalized = " ".join(log_text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def payload_hash(payload: Optional[Dict[str, Any]]) -> str:
    canonical = json.dumps(payload or {}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class Store:
    def __init__(self, path: str, tz_name: str = DEFAULT_TZ):
        self.path = str(path)
        self.tz_name = tz_name
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        for table, columns in MIGRATIONS.items():
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name, sql_type in columns:
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")

    def _now(self) -> str:
        return iso_now(self.tz_name)

    # ------------------------------------------------------------------ decisões
    def record_decision(
        self,
        incident_id: str,
        action_type: str,
        payload: Optional[Dict[str, Any]],
        approver: str,
        decision: str,
        source: str,
        decision_seconds: Optional[float] = None,
    ) -> Tuple[int, bool]:
        """Grava a decisão. Retorna (id, criada). Se já existir, `criada` é False."""
        digest = payload_hash(payload)
        status = "PENDING" if decision == "APPROVED" else "NOT_EXECUTED"
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO decisions "
                "(ts, incident_id, action_type, payload_hash, approver, decision, source, "
                "execution_status, decision_seconds) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (self._now(), incident_id, action_type, digest, approver, decision, source,
                 status, decision_seconds),
            )
            created = cursor.rowcount == 1
            row = conn.execute(
                "SELECT id FROM decisions WHERE incident_id=? AND action_type=? AND payload_hash=?",
                (incident_id, action_type, digest),
            ).fetchone()
            conn.commit()
        return int(row["id"]), created

    def get_decision(
        self, incident_id: str, action_type: str, payload: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM decisions WHERE incident_id=? AND action_type=? AND payload_hash=?",
                (incident_id, action_type, payload_hash(payload)),
            ).fetchone()
        return dict(row) if row else None

    def set_execution(self, decision_id: int, status: str, detail: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE decisions SET execution_status=?, execution_detail=? WHERE id=?",
                (status, detail, decision_id),
            )
            conn.commit()

    def decisions(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self._select("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------ outbox
    def enqueue(self, decision_id: int, action_type: str, payload: Optional[Dict[str, Any]]) -> int:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO outbox (ts, decision_id, action_type, payload_json) "
                "VALUES (?, ?, ?, ?)",
                (self._now(), decision_id, action_type, json.dumps(payload or {}, ensure_ascii=False)),
            )
            row = conn.execute(
                "SELECT id FROM outbox WHERE decision_id=?", (decision_id,)
            ).fetchone()
            conn.commit()
        return int(row["id"])

    def outbox(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self._select("SELECT * FROM outbox ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------ triagem N2
    def enqueue_manual_triage(
        self, incident_id: str, reason: str, detail: Optional[str] = None
    ) -> Tuple[int, bool]:
        """Registra o incidente na fila de triagem manual N2 (idempotente por incidente)."""
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO manual_triage (ts, incident_id, reason, detail) "
                "VALUES (?, ?, ?, ?)",
                (self._now(), incident_id, reason, detail),
            )
            created = cursor.rowcount == 1
            row = conn.execute(
                "SELECT id FROM manual_triage WHERE incident_id=?", (incident_id,)
            ).fetchone()
            conn.commit()
        return int(row["id"]), created

    def manual_triage(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self._select("SELECT * FROM manual_triage ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------ métricas
    def record_analysis(self, incident_id: str, scenario: Optional[str], data: Dict[str, Any]) -> int:
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "INSERT INTO analyses (ts, incident_id, scenario, source, reason, model, latency_ms, "
                "input_tokens, output_tokens, cost_usd, attempts, severity, declared_confidence, "
                "effective_confidence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    self._now(), incident_id, scenario, data.get("source"), data.get("reason"),
                    data.get("model"), data.get("latency_ms"), data.get("input_tokens"),
                    data.get("output_tokens"), data.get("cost_usd"), data.get("attempts"),
                    data.get("severity"), data.get("declared_confidence"),
                    data.get("effective_confidence"),
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def analyses(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self._select("SELECT * FROM analyses ORDER BY id DESC LIMIT ?", (limit,))

    def analytics(self) -> Dict[str, Any]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total, "
                "COALESCE(SUM(source='LIVE'),0) AS live, "
                "COALESCE(SUM(source='MOCK'),0) AS mock, "
                "COALESCE(SUM(source='DEGRADED'),0) AS degraded, "
                "AVG(CASE WHEN source='LIVE' THEN latency_ms END) AS avg_latency_live_ms, "
                "COALESCE(SUM(input_tokens),0) AS input_tokens, "
                "COALESCE(SUM(output_tokens),0) AS output_tokens, "
                "COALESCE(SUM(cost_usd),0) AS cost_usd FROM analyses"
            ).fetchone()
        return dict(row)

    def _select(self, sql: str, params: tuple) -> List[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
