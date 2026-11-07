"""Contrato de saída do modelo (Pydantic v2) e política de aprovação humana (HITL).

Princípio: o modelo *propõe*; o código *decide* o que exige aprovação. Os campos
`requires_human_approval` e `requires_human_intervention` existem no resultado, mas
são sempre recalculados pelos validators abaixo, nunca confiados à saída do LLM.
"""
from enum import Enum
from typing import Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SeverityLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ActionType(str, Enum):
    REFRESH_TOKEN = "REFRESH_TOKEN"
    RETRY_WEBHOOK = "RETRY_WEBHOOK"
    CLEAR_CACHE = "CLEAR_CACHE"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"
    LOG_ONLY = "LOG_ONLY"


# Ações que sempre exigem aprovação de um operador.
REQUIRES_APPROVAL_ACTIONS = frozenset(
    {
        ActionType.REFRESH_TOKEN,
        ActionType.RETRY_WEBHOOK,
        ActionType.CLEAR_CACHE,
        ActionType.ESCALATE_HUMAN,
    }
)

# Ações que alteram sistemas (usadas para limitar a confiança efetiva).
MUTATING_ACTIONS = frozenset(
    {
        ActionType.REFRESH_TOKEN,
        ActionType.RETRY_WEBHOOK,
        ActionType.CLEAR_CACHE,
    }
)

# Severidades em que o incidente inteiro passa por revisão humana.
HITL_SEVERITIES = frozenset({SeverityLevel.HIGH, SeverityLevel.CRITICAL})

# Abaixo disso, o diagnóstico é tratado como incerto e exige revisão humana.
LOW_CONFIDENCE_THRESHOLD = 0.6

PayloadValue = Union[str, int, float, bool]


class RecommendedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: ActionType = Field(description="Tipo de ação recomendada.")
    description: str = Field(
        max_length=300, description="O que a ação fará, em uma frase."
    )
    payload: Optional[Dict[str, PayloadValue]] = Field(
        default=None,
        description=(
            "Parâmetros técnicos em objeto plano (chave -> texto/número/booleano). "
            "Use apenas identificadores presentes no log."
        ),
    )
    requires_human_approval: bool = Field(
        default=True,
        description="Definido pelo sistema; não é decisão do modelo.",
    )

    @model_validator(mode="after")
    def enforce_hitl_policy(self):
        # Derivado do tipo da ação; qualquer valor vindo do modelo é descartado.
        # O resultado agregado (LogAnalysisResult) pode elevar para True por
        # severidade alta ou baixa confiança.
        self.requires_human_approval = self.action_type in REQUIRES_APPROVAL_ACTIONS
        return self

    @model_validator(mode="after")
    def limit_payload_size(self):
        if self.payload is not None and len(self.payload) > 8:
            raise ValueError("payload excede 8 chaves")
        return self


class LogAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_name: str = Field(
        max_length=80, description="Serviço ou integração impactada."
    )
    severity: SeverityLevel = Field(description="Severidade conforme a régua do cenário.")
    root_cause: str = Field(
        max_length=600, description="Causa raiz técnica, baseada apenas no log."
    )
    confidence_score: float = Field(
        ge=0.0, le=1.0, description="Confiança do diagnóstico (0.0 a 1.0)."
    )
    recommended_actions: List[RecommendedAction] = Field(
        min_length=1, max_length=5, description="Ações propostas."
    )
    requires_human_intervention: bool = Field(
        default=True,
        description="Definido pelo sistema; não é decisão do modelo.",
    )

    @model_validator(mode="after")
    def sync_hitl_flags(self):
        force_review = (
            self.severity in HITL_SEVERITIES
            or self.confidence_score < LOW_CONFIDENCE_THRESHOLD
        )
        if force_review:
            for action in self.recommended_actions:
                action.requires_human_approval = True
        self.requires_human_intervention = force_review or any(
            action.requires_human_approval for action in self.recommended_actions
        )
        return self

    def force_review(self) -> None:
        """Exige revisão humana em todas as ações (usado pela política de confiança efetiva)."""
        for action in self.recommended_actions:
            action.requires_human_approval = True
        self.requires_human_intervention = True
