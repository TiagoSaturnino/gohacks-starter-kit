"""Liga o resultado da análise à persistência: métricas + roteamento fail-closed.

Quando a análise falha (DEGRADED), o incidente é registrado na fila de triagem manual
N2. Assim a mensagem "roteado para triagem manual" mostrada ao operador é verdadeira
e verificável na aba de auditoria.
"""
import logging
from dataclasses import dataclass
from typing import Optional

from src.analyzer import AnalysisOutcome
from src.store import Store

logger = logging.getLogger("gohacks.telemetry")


@dataclass
class PersistResult:
    triage_id: Optional[int] = None
    triage_created: bool = False
    error: Optional[str] = None


def persist_outcome(
    store: Store, incident_id: str, scenario_id: str, outcome: AnalysisOutcome
) -> PersistResult:
    """Grava a métrica da análise e, se DEGRADED, enfileira na triagem manual N2.

    Nunca levanta exceção: falha de persistência não pode derrubar a tela.
    """
    result = PersistResult()
    meta = outcome.meta
    try:
        store.record_analysis(
            incident_id,
            scenario_id,
            {
                "source": outcome.source,
                "reason": outcome.reason,
                "model": meta.model,
                "latency_ms": meta.latency_ms,
                "input_tokens": meta.input_tokens,
                "output_tokens": meta.output_tokens,
                "cost_usd": meta.cost_usd,
                "attempts": meta.attempts,
                "severity": outcome.result.severity.value if outcome.result else None,
                "declared_confidence": outcome.confidence.declared if outcome.confidence else None,
                "effective_confidence": outcome.confidence.effective if outcome.confidence else None,
            },
        )
        if outcome.automation_blocked:
            result.triage_id, result.triage_created = store.enqueue_manual_triage(
                incident_id, outcome.reason or "UNKNOWN", outcome.error_detail or outcome.error
            )
    except Exception as error:  # noqa: BLE001 - persistência é best-effort
        logger.exception("falha ao persistir análise")
        result.error = f"{type(error).__name__}: {error}"
    return result
