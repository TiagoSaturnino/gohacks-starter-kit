"""Confiança efetiva: o código reavalia a nota autodeclarada pelo modelo.

LLMs tendem a ser hiperconfiantes. Aqui a nota declarada vira apenas uma entrada: o
sistema aplica TETOS por política (severidade alta, ações que alteram sistemas) e
PENALIDADES por sinais objetivos (identificador sem lastro no log, necessidade de
reparo da saída). Se a confiança efetiva ficar abaixo do limiar, a revisão humana
passa a ser obrigatória.

Atenção ao vocabulário: isto é uma política conservadora, NÃO uma calibração
estatística. Calibrar de verdade exige um conjunto rotulado (golden set) para comparar
confiança declarada com acerto real; ver README, seção "Limitações".
"""
from typing import List

from pydantic import BaseModel, Field

from src.schemas import (
    LOW_CONFIDENCE_THRESHOLD,
    MUTATING_ACTIONS,
    LogAnalysisResult,
    SeverityLevel,
)

SEVERITY_CAPS = {SeverityLevel.CRITICAL: 0.70, SeverityLevel.HIGH: 0.80}
MUTATING_CAP = 0.85
WARNING_PENALTY = 0.15
REPAIR_PENALTY = 0.10


class ConfidenceAssessment(BaseModel):
    declared: float
    effective: float
    reasons: List[str] = Field(default_factory=list)
    forced_review: bool = False


def assess_confidence(
    result: LogAnalysisResult, warnings_count: int = 0, attempts: int = 1
) -> ConfidenceAssessment:
    declared = result.confidence_score
    effective = declared
    reasons: List[str] = []

    cap = SEVERITY_CAPS.get(result.severity)
    if cap is not None and effective > cap:
        effective = cap
        reasons.append(f"Severidade {result.severity.value}: teto de {cap:.0%}")

    if any(a.action_type in MUTATING_ACTIONS for a in result.recommended_actions) and effective > MUTATING_CAP:
        effective = MUTATING_CAP
        reasons.append(f"Ação que altera sistemas: teto de {MUTATING_CAP:.0%}")

    if warnings_count:
        effective -= WARNING_PENALTY
        reasons.append(f"Identificador citado sem lastro no log: -{WARNING_PENALTY:.0%}")

    if attempts > 1:
        effective -= REPAIR_PENALTY
        reasons.append(f"Saída precisou de reparo (tentativa {attempts}): -{REPAIR_PENALTY:.0%}")

    effective = round(max(effective, 0.0), 2)
    return ConfidenceAssessment(
        declared=declared,
        effective=effective,
        reasons=reasons,
        forced_review=effective < LOW_CONFIDENCE_THRESHOLD,
    )


def apply_confidence_policy(result: LogAnalysisResult, assessment: ConfidenceAssessment) -> None:
    if assessment.forced_review:
        result.force_review()
