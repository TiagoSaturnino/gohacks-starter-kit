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

REQUIRES_APPROVAL_ACTIONS = frozenset({
    ActionType.REFRESH_TOKEN, ActionType.RETRY_WEBHOOK,
    ActionType.CLEAR_CACHE, ActionType.ESCALATE_HUMAN
})
HITL_SEVERITIES = frozenset({SeverityLevel.HIGH, SeverityLevel.CRITICAL})
LOW_CONFIDENCE_THRESHOLD = 0.6
PayloadValue = Union[str, int, float, bool]

class RecommendedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_type: ActionType = Field(description="Tipo de ação recomendada.")
    description: str = Field(max_length=300, description="Descrição sucinta.")
    payload: Optional[Dict[str, PayloadValue]] = Field(default=None, description="Parâmetros técnicos.")
    requires_human_approval: bool = Field(default=True, description="HITL em código.")

    @model_validator(mode="after")
    def enforce_hitl_policy(self):
        self.requires_human_approval = self.action_type in REQUIRES_APPROVAL_ACTIONS
        return self

class LogAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_name: str = Field(max_length=80, description="Serviço impactado.")
    severity: SeverityLevel = Field(description="Nível de severidade.")
    root_cause: str = Field(max_length=600, description="Causa raiz técnica.")
    confidence_score: float = Field(ge=0.0, le=1.0, description="Score de confiança.")
    recommended_actions: List[RecommendedAction] = Field(min_length=1, max_length=5)
    requires_human_intervention: bool = Field(default=True)

    @model_validator(mode="after")
    def sync_hitl_flags(self):
        force_review = self.severity in HITL_SEVERITIES or self.confidence_score < LOW_CONFIDENCE_THRESHOLD
        if force_review:
            for action in self.recommended_actions:
                action.requires_human_approval = True
        self.requires_human_intervention = force_review or any(a.requires_human_approval for a in self.recommended_actions)
        return self