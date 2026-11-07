"""Utilitários de teste: cliente Anthropic falso e payloads prontos."""
from types import SimpleNamespace

from src.config import Settings

TOOL_NAME = "submit_log_analysis"


def make_settings(**overrides) -> Settings:
    values = dict(
        use_mock=False,
        api_key="sk-ant-teste-nao-e-uma-chave-real",
        model="claude-sonnet-5-5",
        timeout_s=5.0,
        max_retries=0,
        max_tokens=1500,
        max_batch_rows=50,
        scenario="ecommerce",
        executor="dry_run",
        audit_db=":memory:",
        manual_minutes_per_incident=25,
    )
    values.update(overrides)
    return Settings(**values)


class FakeMessages:
    def __init__(self, items):
        self.items = list(items)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    """Imita `anthropic.Anthropic` para testar o caminho live sem chave."""

    def __init__(self, *items):
        self.messages = FakeMessages(items)


def tool_response(payload, stop_reason="tool_use", input_tokens=100, output_tokens=50, tool_id="toolu_1"):
    block = SimpleNamespace(type="tool_use", id=tool_id, name=TOOL_NAME, input=payload)
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[block],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def valid_analysis(**overrides):
    """Análise válida e ancorada no log do fixture `mercadolivre_oauth_401`."""
    data = {
        "service_name": "Mercado Livre API",
        "severity": "HIGH",
        "root_cause": "O token do seller 8849201 expirou (HTTP 401) ao atualizar o item MLB1298491.",
        "confidence_score": 0.9,
        "recommended_actions": [
            {
                "action_type": "REFRESH_TOKEN",
                "description": "Renovar o token do seller 8849201.",
                "payload": {"seller_id": "8849201", "endpoint": "/oauth/token"},
            }
        ],
    }
    data.update(overrides)
    return data


ML_LOG = (
    "[2026-11-01T14:23:10Z] ERROR [GoGroup-Integrator] MercadoLivre API HTTP 401 Unauthorized\n"
    "Endpoint: POST /items/MLB1298491/stock\n"
    'Payload: {"seller_id": "8849201", "quantity": 15}\n'
    'Response: {"error": "invalid_token", "message": "The access token provided has expired."}'
)
