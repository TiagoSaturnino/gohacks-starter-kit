"""Fakes do IAProxy: nenhum teste usa rede nem token real."""
import json
from types import SimpleNamespace

import pytest

from src.config import load_settings
from src.resilience import CircuitBreaker


class FakeOpenAI:
    """Imita client.chat.completions.create devolvendo respostas em sequência."""

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.chamadas = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.chamadas.append(kwargs)
        item = self.respostas.pop(0)
        if isinstance(item, Exception):
            raise item
        conteudo = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        return SimpleNamespace(
            model="gpt-fake",
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=conteudo))],
            usage=SimpleNamespace(
                prompt_tokens=100, completion_tokens=20,
                completion_tokens_details=SimpleNamespace(reasoning_tokens=5),
            ),
        )


class FakeHttp:
    def __init__(self, body, status=200):
        self.body, self.status, self.chamadas = body, status, []

    def post(self, url, headers=None, json=None):
        import httpx

        self.chamadas.append({"url": url, "headers": headers, "json": json})
        request = httpx.Request("POST", url)
        return httpx.Response(self.status, json=self.body, request=request)


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv("USE_IA", "False")
    monkeypatch.setenv("AI_PROXY_API_KEY", "")
    monkeypatch.setenv("AI_MODEL", "latest")
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    return load_settings()


@pytest.fixture
def breaker():
    return CircuitBreaker(failure_threshold=2, reset_after_s=60)
