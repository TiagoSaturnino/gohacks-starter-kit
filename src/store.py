import sqlite3, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List
from contextlib import closing

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
"""

def make_incident_id(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()[:12]

def payload_hash(payload: Optional[Dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(payload or {}, sort_keys=True).encode()).hexdigest()[:16]

class Store:
    def __init__(self, path: str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def record_decision(self, incident_id: str, action_type: str, payload: Optional[Dict], approver: str, decision: str, source: str) -> Tuple[int, bool]:
        digest = payload_hash(payload)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "INSERT OR IGNORE INTO decisions (ts, incident_id, action_type, payload_hash, approver, decision, source) VALUES (?,?,?,?,?,?,?)",
                (now, incident_id, action_type, digest, approver, decision, source)
            )
            created = cur.rowcount == 1
            row = conn.execute("SELECT id FROM decisions WHERE incident_id=? AND action_type=? AND payload_hash=?", (incident_id, action_type, digest)).fetchone()
            conn.commit()
            return int(row["id"]), created

    def get_decision(self, incident_id: str, action_type: str, payload: Optional[Dict]) -> Optional[Dict]:
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM decisions WHERE incident_id=? AND action_type=? AND payload_hash=?", (incident_id, action_type, payload_hash(payload))).fetchone()
            return dict(row) if row else None

    def set_execution(self, decision_id: int, status: str, detail: str):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("UPDATE decisions SET execution_status=?, execution_detail=? WHERE id=?", (status, detail, decision_id))
            conn.commit()

    def decisions(self) -> List[Dict]:
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM decisions ORDER BY id DESC").fetchall()]

    def outbox(self) -> List[Dict]:
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM outbox ORDER BY id DESC").fetchall()]