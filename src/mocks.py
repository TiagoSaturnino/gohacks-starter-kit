from src.schemas import LogAnalysisResult, SeverityLevel, ActionType, RecommendedAction

MOCK_LOGS = {
    "mercadolivre_oauth_401": """
    [2026-11-01T14:23:10Z] ERROR [GoGroup-Integrator] MercadoLivre API HTTP 401 Unauthorized
    Endpoint: POST /items/MLB1298491/stock
    Payload: {"seller_id": "8849201", "quantity": 15}
    Response: {"error": "invalid_token", "message": "The access token provided has expired."}
    """,
    "sp_api_504_timeout": """
    [2026-11-01T15:10:05Z] ERROR [GoGroup-AmazonSync] Amazon SP-API Gateway Timeout HTTP 504
    Endpoint: GET /orders/v0/orders
    Message: Connection timed out after 30000ms while fetching seller orders.
    """,
    "webhook_null_cpf": """
    [2026-11-01T16:05:40Z] ERROR [GoGroup-Checkout] Webhook delivery failed.
    Payload: {"order_id": "GC-88310", "customer": {"name": "Maria Silva", "cpf": null}, "total": 149.90}
    Message: ValidationError: customer.cpf cannot be null. Event sent to DLQ.
    """
}

def get_mock_analysis(log_text: str) -> LogAnalysisResult:
    log_lower = log_text.lower()
    
    if "401" in log_lower or "token" in log_lower or "mercadolivre" in log_lower:
        return LogAnalysisResult(
            service_name="Mercado Livre API",
            severity=SeverityLevel.HIGH,
            root_cause="Token OAuth2 do vendedor 8849201 expirado durante sincronização de estoque.",
            confidence_score=0.98,
            recommended_actions=[
                RecommendedAction(
                    action_type=ActionType.REFRESH_TOKEN,
                    description="Executar refresh do token de acesso do Mercado Livre",
                    payload={"seller_id": "8849201", "endpoint": "/oauth/token"},
                    requires_human_approval=True
                )
            ]
        )
    elif "504" in log_lower or "timeout" in log_lower or "amazon" in log_lower:
        return LogAnalysisResult(
            service_name="Amazon SP-API Gateway",
            severity=SeverityLevel.MEDIUM,
            root_cause="Timeout de conexão (504) ao consultar pedidos da Amazon SP-API.",
            confidence_score=0.92,
            recommended_actions=[
                RecommendedAction(
                    action_type=ActionType.RETRY_WEBHOOK,
                    description="Reprocessar consulta de pedidos com exponential backoff",
                    payload={"retry_attempt": 1, "service": "amazon_sp_api"},
                    requires_human_approval=True
                )
            ]
        )
    else:
        return LogAnalysisResult(
            service_name="GoGroup Checkout System",
            severity=SeverityLevel.CRITICAL,
            root_cause="Falha no payload do webhook: campo obrigatório customer.cpf veio nulo.",
            confidence_score=0.95,
            recommended_actions=[
                RecommendedAction(
                    action_type=ActionType.ESCALATE_HUMAN,
                    description="Notificar equipe de cadastro para correção do CPF do cliente",
                    payload={"order_id": "GC-88310", "issue": "MISSING_CPF"},
                    requires_human_approval=False
                )
            ]
        )
