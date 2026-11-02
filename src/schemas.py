from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict, model_validator

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

MUTATING_ACTIONS = {
    ActionType.REFRESH_TOKEN,
    ActionType.RETRY_WEBHOOK,
    ActionType.CLEAR_CACHE
}

class RecommendedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    
    action_type: ActionType = Field(description="Tipo de ação recomendada")
    description: str = Field(description="Descrição detalhada da ação")
    payload: Optional[Dict[str, Any]] = Field(default=None, description="Payload técnico em JSON")
    requires_human_approval: bool = Field(
        default=True, 
        description="Se a ação exige aprovação humana para ser executada"
    )

    @model_validator(mode="after")
    def enforce_hitl_policy(self):
        if self.action_type in MUTATING_ACTIONS:
            self.requires_human_approval = True
        return self

class LogAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    
    service_name: str = Field(description="Nome do serviço impactado (ex: Mercado Livre API, WooCommerce)")
    severity: SeverityLevel = Field(description="Nível de severidade do incidente")
    root_cause: str = Field(description="Explicação sucinta e técnica da causa raiz")
    confidence_score: float = Field(description="Score de confiança do diagnóstico (0.0 a 1.0)")
    requires_human_intervention: bool = Field(
        default=True, 
        description="Indica se há ações que exigem intervenção humana"
    )
    recommended_actions: List[RecommendedAction] = Field(description="Lista de ações corretivas")

    @model_validator(mode="after")
    def sync_global_intervention_flag(self):
        self.requires_human_intervention = any(
            action.requires_human_approval for action in self.recommended_actions
        )
        return self
