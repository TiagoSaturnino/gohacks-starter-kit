import os
from dotenv import load_dotenv
from anthropic import Anthropic
from src.schemas import LogAnalysisResult, SeverityLevel, ActionType, RecommendedAction

load_dotenv()

SYSTEM_PROMPT = """
Você é um Engenheiro de Confiabilidade (SRE) e Arquiteto de IA Sênior do e-commerce GoGroup.
Sua tarefa é analisar logs de erro de integração (Mercado Livre, Amazon, Webhooks, Sentry) e realizar Root Cause Analysis (RCA).

Regras Rígidas:
1. Identifique a causa raiz exata sem especulações.
2. Defina o nível de severidade adequado (LOW, MEDIUM, HIGH, CRITICAL).
3. Toda ação que altere dados, faça chamadas de reativação ou possa ter impacto financeiro DEVE ter `requires_human_approval = True` (Human-in-the-Loop).
"""

def analyze_log(log_text: str) -> LogAnalysisResult:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    use_mock = os.getenv("USE_MOCK", "True").lower() in ("true", "1", "yes")

    # Se estiver em modo Mock ou sem chave válida, usa o fallback estruturado
    if use_mock or not api_key or api_key.startswith("sk-ant-sua"):
        return _mock_analyzer(log_text)

    client = Anthropic(api_key=api_key)

    # Definindo a Tool com o JSON Schema do Pydantic
    tools = [
        {
            "name": "report_log_analysis",
            "description": "Reporta o resultado detalhado da análise de causa raiz (RCA) do log de erro.",
            "input_schema": LogAnalysisResult.model_json_schema()
        }
    ]

    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        tools=tools,
        tool_choice={"type": "tool", "name": "report_log_analysis"},
        messages=[
            {"role": "user", "content": f"Analise o seguinte log de erro:\n\n{log_text}"}
        ]
    )

    # Extrai os argumentos estruturados retornados pela Tool
    for block in response.content:
        if block.type == "tool_use" and block.name == "report_log_analysis":
            return LogAnalysisResult.model_validate(block.input)

    raise ValueError("A LLM não retornou a análise no formato esperado.")

def _mock_analyzer(log_text: str) -> LogAnalysisResult:
    """Motor simulação offline com inteligência lógica pré-definida."""
    log_lower = log_text.lower()

    if "mercadolivre" in log_lower or "401" in log_lower:
        return LogAnalysisResult(
            service_name="Mercado Livre API",
            summary="Token de acesso OAuth2 expirado na sincronização de pedidos.",
            root_cause="A requisição GET /orders/search falhou com erro 401 Unauthorized pois o token expirou às 14:00:00Z e o refresh token automático não foi disparado.",
            severity=SeverityLevel.HIGH,
            recommended_actions=[
                RecommendedAction(
                    action_type=ActionType.REFRESH_TOKEN,
                    description="Executar OAuth Token Refresh para o seller_id 8849201.",
                    requires_human_approval=True,
                    payload={"seller_id": "8849201", "endpoint": "/oauth/token"}
                ),
                RecommendedAction(
                    action_type=ActionType.NOTIFY_TEAM,
                    description="Notificar equipe de integrações via Slack sobre a falha de autorização.",
                    requires_human_approval=False,
                    payload={"channel": "#alerts-integrations"}
                )
            ],
            requires_human_intervention=True,
            confidence_score=0.98
        )
    elif "amazon" in log_lower or "504" in log_lower:
        return LogAnalysisResult(
            service_name="Amazon SP-API",
            summary="Timeout de gateway de 30s no endpoint de inventário FBA.",
            root_cause="A chamada /fba/inventory/v1 excedeu o limite de tempo (30005ms) causando bloqueio na reconciliação de estoque de 450 SKUs.",
            severity=SeverityLevel.CRITICAL,
            recommended_actions=[
                RecommendedAction(
                    action_type=ActionType.RETRY_WEBHOOK,
                    description="Agendar reexecução do job de sincronização de inventário com backoff exponencial.",
                    requires_human_approval=True,
                    payload={"job_id": "amazon_fba_sync_450", "max_retries": 3}
                )
            ],
            requires_human_intervention=True,
            confidence_score=0.95
        )
    else:
        return LogAnalysisResult(
            service_name="Webhook Receiver",
            summary="Falha de validação de Schema no webhook de pedido recebido.",
            root_cause="Payload rejeitado pela camada Pydantic pois o campo obrigatório 'customer.cpf' veio nulo na ordem GC-99211.",
            severity=SeverityLevel.MEDIUM,
            recommended_actions=[
                RecommendedAction(
                    action_type=ActionType.ESCALATE_HUMAN,
                    description="Encaminhar pedido GC-99211 para a fila de tratamento manual de dados de clientes.",
                    requires_human_approval=True,
                    payload={"order_id": "GC-99211", "missing_field": "customer.cpf"}
                )
            ],
            requires_human_intervention=True,
            confidence_score=0.92
        )
