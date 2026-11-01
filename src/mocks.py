MOCK_LOGS = {
    "mercadolivre_auth_error": """
2026-11-01T14:22:10Z [ERROR] [MercadoLivreService] Request failed for endpoint GET /orders/search.
HTTP Status: 401 Unauthorized
Response Body: {"message": "invalid_token", "error": "not_authorized", "status": 401}
Context: Sync job failed for seller_id 8849201. Access token expired at 2026-11-01T14:00:00Z.
""",
    "amazon_sp_api_timeout": """
2026-11-01T14:25:33Z [CRITICAL] [AmazonSyncService] Timeout waiting for Amazon SP-API endpoint /fba/inventory/v1.
HTTP Status: 504 Gateway Timeout
Duration: 30005ms
Stacktrace:
  File "amazon_client.py", line 142, in fetch_inventory
    raise TimeoutError("Amazon SP-API did not respond within 30s")
Context: Inventory reconciliation blocked for 450 SKUs.
""",
    "webhook_payload_invalid": """
2026-11-01T14:28:01Z [WARNING] [WebhookReceiver] Failed to process incoming webhook from Marketplace Store.
Error: ValidationError (Pydantic) - Field 'customer.cpf' is required but received null.
Payload Received: {"order_id": "GC-99211", "customer": {"name": "Carlos Silva", "email": "carlos@example.com"}, "total": 149.90}
Action: Webhook discarded and pushed to DLQ (Dead Letter Queue).
"""
}

def get_mock_log(log_key: str) -> str:
    return MOCK_LOGS.get(log_key, "Log não encontrado.")
