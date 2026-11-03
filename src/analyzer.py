import time
import logging
from typing import Optional, Literal
from pydantic import BaseModel, Field
import anthropic
from src.config import Settings, load_settings
from src.schemas import LogAnalysisResult
from src.scenarios import Scenario, load_scenario
from src.pii import mask_pii
from src.guardrails import check_result

TOOL_NAME = "submit_log_analysis"

class AnalysisMeta(BaseModel):
    model: Optional[str] = None
    latency_ms: int = 0
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    attempts: int = 0
    pii_masked: dict = Field(default_factory=dict)

class AnalysisOutcome(BaseModel):
    source: Literal["LIVE", "MOCK", "DEGRADED"]
    result: Optional[LogAnalysisResult] = None
    error: Optional[str] = None
    warnings: list = Field(default_factory=list)
    meta: AnalysisMeta = Field(default_factory=AnalysisMeta)

def analyze_log(log_text: str, scenario: Optional[Scenario] = None, settings: Optional[Settings] = None, client=None) -> AnalysisOutcome:
    settings = settings or load_settings()
    scenario = scenario or load_scenario(settings.scenario)
    if not log_text or not log_text.strip():
        return AnalysisOutcome(source="DEGRADED", error="Log vazio.")
    
    if client is None and not settings.live_enabled:
        fixture = scenario.find_fixture(log_text)
        if fixture is None:
            return AnalysisOutcome(source="DEGRADED", error=f"Modo offline: sem fixture para este log no cenário '{scenario.name}'.")
        return AnalysisOutcome(source="MOCK", result=fixture.analysis)
    
    return AnalysisOutcome(source="DEGRADED", error="Live API indisponível ou não configurada.")