import os
import json
import anthropic
from dotenv import load_dotenv
from pydantic import ValidationError
from src.schemas import LogAnalysisResult
from src.mocks import get_mock_analysis

load_dotenv()

SYSTEM_PROMPT = """
Você é um Especialista Sênior em Triagem de Logs e RCA (Root Cause Analysis) para o ecossistema GoGroup.
Sua missão é analisar logs brutos de erro e retornar uma estrutura rígida JSON.

Regras de Segurança:
1. Analise apenas os dados contidos no log. Não invente serviços ou IDs não presentes no texto.
2. Identifique o serviço, causa raiz, severidade e ações corretivas necessárias.
"""

def analyze_log(log_text: str) -> LogAnalysisResult:
    use_mock = os.getenv("USE_MOCK", "True").lower() in ("true", "1", "yes")
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    model_name = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")

    if use_mock or not api_key or api_key.startswith("sk-ant-sua"):
        return get_mock_analysis(log_text)

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

        for content in response.content:
            if content.type == "tool_use" and content.name == "submit_log_analysis":
                return LogAnalysisResult.model_validate(content.input)

        raise ValueError("O modelo não retornou a ferramenta esperada.")

    except (anthropic.APIError, ValidationError, Exception) as e:
        fallback_result = get_mock_analysis(log_text)
        fallback_result.root_cause += f" (Nota: Análise gerada em modo de contingência devido a erro: {str(e)[:50]})"
        return fallback_result
