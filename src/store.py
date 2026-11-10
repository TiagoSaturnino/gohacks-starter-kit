"""Persistência (SQLite, só biblioteca padrão): alertas, acionamentos, decisões, outbox,
fila de triagem manual e uso do IAProxy de IA.

Dois relógios, de propósito:
* `ts_real`: hora de verdade, com offset (auditoria: quando o sistema gravou).
* `ts_simulado`: o `agora` do cenário (lógica: a decisão veio antes do fechamento?).
Sem essa separação, o teste ponta a ponta dependeria do dia em que roda.

Idempotência:
* `alertas`: UNIQUE(titulo_id, regra, evidencia_hash) -> rodar o guardião de novo não duplica.
* `acionamentos`: UNIQUE(pessoa_id, lote_remessa, titulos_hash) -> a mesma mensagem não sai 2x.
* `decisoes`: guarda o HISTÓRICO; vale a última por (titulo_id, lote_remessa). O clique
  duplo é barrado por UNIQUE(titulo_id, lote_remessa, acao, decidido_por, ts_simulado).
  (O kit original travava por ação: RETER e LIBERAR do mesmo título conviviam.)
"""
import hashlib
import json
import sqlite3
from contextlib import closing, nullcontext
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.schemas import Alerta, Decisao
from src.timeutil import DEFAULT_TZ, iso_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS alertas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_real TEXT NOT NULL,
    ts_simulado TEXT NOT NULL,
    titulo_id TEXT NOT NULL,
    lote_remessa TEXT,
    regra TEXT NOT NULL,
    severidade TEXT NOT NULL,
    evidencia TEXT NOT NULL,
    evidencia_hash TEXT NOT NULL,
    valor REAL NOT NULL,
    UNIQUE (titulo_id, regra, evidencia_hash)
);
CREATE TABLE IF NOT EXISTS acionamentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_real TEXT NOT NULL,
    ts_simulado TEXT NOT NULL,
    pessoa_id TEXT NOT NULL,
    lote_remessa TEXT NOT NULL,
    titulos_json TEXT NOT NULL,
    titulos_hash TEXT NOT NULL,
    canal TEXT NOT NULL,
    mensagem TEXT NOT NULL,
    UNIQUE (pessoa_id, lote_remessa, titulos_hash)
);
CREATE TABLE IF NOT EXISTS decisoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_real TEXT NOT NULL,
    ts_simulado TEXT NOT NULL,
    titulo_id TEXT NOT NULL,
    lote_remessa TEXT NOT NULL,
    acao TEXT NOT NULL,
    decidido_por TEXT NOT NULL,
    origem TEXT NOT NULL,
    motivo TEXT,
    execution_status TEXT,
    execution_detail TEXT,
    UNIQUE (titulo_id, lote_remessa, acao, decidido_por, ts_simulado)
);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_real TEXT NOT NULL,
    decisao_id INTEGER NOT NULL UNIQUE,
    acao TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING'
);
CREATE TABLE IF NOT EXISTS manual_triage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_real TEXT NOT NULL,
    chave TEXT NOT NULL UNIQUE,
    reason TEXT NOT NULL,
    detail TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING'
);
CREATE TABLE IF NOT EXISTS usos_ia (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_real TEXT NOT NULL,
    chave TEXT,
    endpoint TEXT NOT NULL,
    origem TEXT NOT NULL,
    motivo TEXT,
    modelo TEXT,
    latency_ms INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    reasoning_tokens INTEGER,
    cost_usd REAL,
    attempts INTEGER
);
"""


def short_hash(value: Any) -> str:
    canonical = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class Store:
    def __init__(self, path: str, tz_name: str = DEFAULT_TZ):
        self.path = str(path)
        self.tz_name = tz_name
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._mem: Optional[sqlite3.Connection] = None
        if self.path == ":memory:":  # uma conexão só, senão cada chamada veria um banco vazio
            self._mem = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem.row_factory = sqlite3.Row
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    # ------------------------------------------------------------------ infra
    def _conn(self):
        if self._mem is not None:
            return nullcontext(self._mem)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return closing(conn)

    def _now(self) -> str:
        return iso_now(self.tz_name)

    def _select(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    # ------------------------------------------------------------------ alertas
    def record_alertas(
        self, alertas: Iterable[Alerta], ts_simulado: str, lote_por_titulo: Optional[Dict[str, str]] = None
    ) -> int:
        """Grava alertas; devolve quantos eram novos (re-execução não duplica)."""
        lote_por_titulo = lote_por_titulo or {}
        novos = 0
        with self._conn() as conn:
            for a in alertas:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO alertas (ts_real, ts_simulado, titulo_id, lote_remessa, regra, "
                    "severidade, evidencia, evidencia_hash, valor) VALUES (?,?,?,?,?,?,?,?,?)",
                    (self._now(), ts_simulado, a.titulo_id, lote_por_titulo.get(a.titulo_id), a.regra,
                     a.severidade.value, a.evidencia, short_hash(a.evidencia), a.valor),
                )
                novos += cur.rowcount
            conn.commit()
        return novos

    def alertas(self, limit: int = 1000) -> List[Dict[str, Any]]:
        return self._select("SELECT * FROM alertas ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------ acionamentos
    def record_acionamento(
        self, pessoa_id: str, lote_remessa: str, titulos: List[str], canal: str, mensagem: str, ts_simulado: str
    ) -> Tuple[int, bool]:
        """Uma mensagem por pessoa por lote. Retorna (id, criado)."""
        ordenados = sorted(set(titulos))
        digest = short_hash(ordenados)
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO acionamentos (ts_real, ts_simulado, pessoa_id, lote_remessa, "
                "titulos_json, titulos_hash, canal, mensagem) VALUES (?,?,?,?,?,?,?,?)",
                (self._now(), ts_simulado, pessoa_id, lote_remessa, json.dumps(ordenados), digest, canal, mensagem),
            )
            created = cur.rowcount == 1
            row = conn.execute(
                "SELECT id FROM acionamentos WHERE pessoa_id=? AND lote_remessa=? AND titulos_hash=?",
                (pessoa_id, lote_remessa, digest),
            ).fetchone()
            conn.commit()
        return int(row["id"]), created

    def acionamentos(self, limit: int = 200) -> List[Dict[str, Any]]:
        return self._select("SELECT * FROM acionamentos ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------ decisões
    def record_decisao(self, decisao: Decisao, ts_simulado: str) -> Tuple[int, bool]:
        status = "PENDING" if decisao.acao.value in ("RETER_TITULO", "LIBERAR_TITULO") else "NOT_EXECUTED"
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO decisoes (ts_real, ts_simulado, titulo_id, lote_remessa, acao, "
                "decidido_por, origem, motivo, execution_status) VALUES (?,?,?,?,?,?,?,?,?)",
                (self._now(), ts_simulado, decisao.titulo_id, decisao.lote_remessa, decisao.acao.value,
                 decisao.decidido_por, decisao.origem.value, decisao.motivo, status),
            )
            created = cur.rowcount == 1
            row = conn.execute(
                "SELECT id FROM decisoes WHERE titulo_id=? AND lote_remessa=? AND acao=? AND decidido_por=? "
                "AND ts_simulado=?",
                (decisao.titulo_id, decisao.lote_remessa, decisao.acao.value, decisao.decidido_por, ts_simulado),
            ).fetchone()
            conn.commit()
        return int(row["id"]), created

    def ultima_decisao(self, titulo_id: str, lote_remessa: str) -> Optional[Dict[str, Any]]:
        rows = self._select(
            "SELECT * FROM decisoes WHERE titulo_id=? AND lote_remessa=? ORDER BY ts_simulado DESC, id DESC LIMIT 1",
            (titulo_id, lote_remessa),
        )
        return rows[0] if rows else None

    def set_execution(self, decisao_id: int, status: str, detail: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE decisoes SET execution_status=?, execution_detail=? WHERE id=?", (status, detail, decisao_id)
            )
            conn.commit()

    def decisoes(self, limit: int = 200) -> List[Dict[str, Any]]:
        return self._select("SELECT * FROM decisoes ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------ outbox
    def enqueue(self, decisao_id: int, acao: str, payload: Optional[Dict[str, Any]]) -> int:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO outbox (ts_real, decisao_id, acao, payload_json) VALUES (?,?,?,?)",
                (self._now(), decisao_id, acao, json.dumps(payload or {}, ensure_ascii=False)),
            )
            row = conn.execute("SELECT id FROM outbox WHERE decisao_id=?", (decisao_id,)).fetchone()
            conn.commit()
        return int(row["id"])

    def outbox(self, limit: int = 200) -> List[Dict[str, Any]]:
        return self._select("SELECT * FROM outbox ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------ triagem manual (plano B do Slack)
    def enqueue_manual_triage(self, chave: str, reason: str, detail: Optional[str] = None) -> Tuple[int, bool]:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO manual_triage (ts_real, chave, reason, detail) VALUES (?,?,?,?)",
                (self._now(), chave, reason, detail),
            )
            created = cur.rowcount == 1
            row = conn.execute("SELECT id FROM manual_triage WHERE chave=?", (chave,)).fetchone()
            conn.commit()
        return int(row["id"]), created

    def manual_triage(self, limit: int = 200) -> List[Dict[str, Any]]:
        return self._select("SELECT * FROM manual_triage ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------ uso do IAProxy de IA
    def record_uso_ia(self, chave: Optional[str], data: Dict[str, Any]) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO usos_ia (ts_real, chave, endpoint, origem, motivo, modelo, latency_ms, input_tokens, "
                "output_tokens, reasoning_tokens, cost_usd, attempts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (self._now(), chave, data.get("endpoint", "chat"), data.get("origem", "?"), data.get("motivo"),
                 data.get("modelo"), data.get("latency_ms"), data.get("input_tokens"), data.get("output_tokens"),
                 data.get("reasoning_tokens"), data.get("cost_usd"), data.get("attempts")),
            )
            conn.commit()
            return int(cur.lastrowid)

    def resumo_ia(self) -> Dict[str, Any]:
        rows = self._select(
            "SELECT COUNT(*) AS chamadas, COALESCE(SUM(origem='LIVE'),0) AS live, "
            "COALESCE(SUM(origem='CACHE'),0) AS cache, COALESCE(SUM(origem='MODELO'),0) AS texto_modelo, "
            "COALESCE(SUM(input_tokens),0) AS input_tokens, COALESCE(SUM(output_tokens),0) AS output_tokens, "
            "COALESCE(SUM(cost_usd),0) AS cost_usd FROM usos_ia"
        )
        return rows[0]
