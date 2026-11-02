import os
from typing import Literal, Optional
import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError
from src.schemas import LogAnalysisResult
from src.mocks import get_mock_analysis

load_dotenv()

class AnalysisOutcome(BaseModel):
    source: Literal["LIVE", "MOCK", "DEGRADED"]
    result: Optional[LogAnalysisResult] = None
    error: Optional[str] = None

SYSTEM_PROMPT = """
Você é um Especialista Sênior em Triagem de Logs e RCA (Root Cause Analysis) para o ecossistema GoGroup.
Sua missão é analisar logs brutos de erro e retornar uma estrutura rígida JSON.

Regras de Segurança:
1. Analise apenas os dados contidos no log. Não invente serviços, URLs ou IDs não presentes no texto.
2. Identifique o serviço, causa raiz, severidade e ações corretivas necessárias.
"""

def analyze_log(log_text: str) -> AnalysisOutcome:
    use_mock = os.getenv("USE_MOCK", "True").lower() in ("true", "1", "yes")
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    model_name = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")

    if use_mock or not api_key or api_key.startswith("sk-ant-sua"):
        return AnalysisOutcome(
            source="MOCK",
            result=get_mock_analysis(log_text)
        )

    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=15.0)
        
        response = client.messages.create(
            model=model_name,
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": f"Analise o seguinte log de erro desestruturado:\n\n<log>\n{log_text}\n</log>"}
            ],
            tools=[{
                "name": "submit_log_analysis",
                "description": "Envia o resultado estruturado da análise de causa raiz.",
                "input_schema": LogAnalysisResult.model_json_schema()
            }],
            tool_choice={"type": "tool", "name": "submit_log_analysis"}
        )

        if response.stop_reason == "max_tokens":
            return AnalysisOutcome(
                source="DEGRADED",
                error="A resposta do modelo foi truncada por limite de tokens. Encaminhado para análise manual."
            )

        for content in response.content:
            if content.type == "tool_use" and content.name == "submit_log_analysis":
                validated_result = LogAnalysisResult.model_validate(content.input)
                return AnalysisOutcome(
                    source="LIVE",
                    result=validated_result
                )

        return AnalysisOutcome(
            source="DEGRADED",
            error="O modelo não disparou a ferramenta de saída estruturada."
        )

    except ValidationError as ve:
        return AnalysisOutcome(
            source="DEGRADED",
            error=f"Falha na validação do Schema Pydantic: {str(ve)[:120]}"
        )
    except anthropic.APIError as ae:
        return AnalysisOutcome(
            source="DEGRADED",
            error=f"Erro de comunicação com a API Anthropic: {ae.message}"
        )
    except Exception as e:
        return AnalysisOutcome(
            source="DEGRADED",
            error=f"Erro inesperado no pipeline de triagem: {str(e)[:100]}"
        )
