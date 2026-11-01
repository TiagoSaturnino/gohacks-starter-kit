from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class SeverityLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

class ActionType(str, Enum):
    REFRESH_TOKEN = "REFRESH_TOKEN"
    RETRY_WEBHOOK = "RETRY_WEBHOOK"
    CLEAR_CACHE = "CLEAR_CACHE"
    NOTIFY_TEAM = "NOTIFY_TEAM"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"

class RecommendedAction(BaseModel):
    action_type: ActionType = Field(
        ..., 
        description="Tipo de ação corretiva a ser tomada."
    )
    description: str = Field(
        ..., 
        description="Explicação sucinta do que esta ação fará."
    )
    requires_human_approval: bool = Field(
        True, 
        description="Trava HITL: True se a ação alterar dados ou reativar serviços críticos."
    )
    payload: Optional[Dict[str, Any]] = Field(
        None, 
        description="Parâmetros técnicos para automação (ex: webhook_id, endpoint)."
    )

class LogAnalysisResult(BaseModel):
    service_name: str = Field(
        ..., 
        description="Nome do serviço ou integração impactada (ex: MercadoLivre API, Sentry, Webhook Marketplace)."
    )
    summary: str = Field(
        ..., 
        description="Resumo do incidente em uma frase direta."
    )
    root_cause: str = Field(
        ..., 
        description="Análise detalhada da causa raiz identificada no log."
    )
    severity: SeverityLevel = Field(
        ..., 
        description="Nível de severidade do impacto no e-commerce."
    )
    recommended_actions: List[RecommendedAction] = Field(
        ..., 
        description="Lista de ações recomendadas para mitigar ou resolver o problema."
    )
    requires_human_intervention: bool = Field(
        ..., 
        description="Trava global: True se qualquer ação exigir validação humana antes da execução."
    )
    confidence_score: float = Field(
        ..., 
        ge=0.0, 
        le=1.0, 
        description="Nível de confiança da análise da IA (de 0.0 a 1.0)."
    )
