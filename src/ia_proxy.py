"""IAProxy de IA: a ÚNICA porta do projeto para modelos de linguagem.

Tudo passa pelo AI Proxy do evento (formato OpenAI, https://ai-proxy.gogroupbr.com/v1):
* `chat_json`  -> texto estruturado (explicação de alerta), validado por Pydantic,
                  com 1 tentativa de reparo devolvendo o erro ao modelo;
* `jev`        -> decisão tipada barata (~0,5 s; classificar, sim/não, pontuar),
                  usada para ler comunicações de fornecedores em lote.

Garantias herdadas do kit e mantidas aqui:
* nunca levanta exceção por falha de rede/API: devolve `GatewayResult(ok=False, reason=...)`;
* circuit breaker compartilhado: após N falhas seguidas, falha rápido (plano B imediato);
* custo/uso de TODA chamada é registrado (regra do evento). O proxy não informa preço
  por chamada de chat: mostramos tokens e "sem preço por chamada", nunca um valor inventado.
  O Jev informa `estimated_cost_usd`, e esse valor é registrado.
* o token nunca aparece em log, print ou mensagem de erro.

Uso rápido (PowerShell):  python -m src.ia_proxy --ping
"""
import argparse
import json
import logging
import re
import time
from functools import lru_cache
from typing import Any, Callable, Dict, List, Literal, Optional, Type

import httpx
import openai
from pydantic import BaseModel, Field, ValidationError

from src.config import Settings, load_settings
from src.resilience import CircuitBreaker, get_breaker

logger = logging.getLogger("guardiao.IAProxy")

MAX_ATTEMPTS = 2  # 1 tentativa + 1 reparo
MODELOS_BLOQUEADOS = ("deepseek",)  # regra do evento


class UsoIA(BaseModel):
    endpoint: Literal["chat", "jev"] = "chat"
    modelo: Optional[str] = None
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: Optional[float] = None  # só o Jev informa
    attempts: int = 0

    def linha_custo(self, sessao: "SessaoIA") -> str:
        preco = f"US$ {self.cost_usd:.7f}" if self.cost_usd is not None else "sem preço por chamada"
        return (
            f"[custo] {self.modelo or '?'} · entrada {self.input_tokens} tok · saída {self.output_tokens} tok"
            f" (raciocínio {self.reasoning_tokens}) · {preco} | sessão: {sessao.chamadas} chamadas,"
            f" {sessao.tokens} tok, US$ {sessao.cost_usd:.7f} (só Jev)"
        )


class SessaoIA(BaseModel):
    chamadas: int = 0
    tokens: int = 0
    cost_usd: float = 0.0

    def somar(self, uso: UsoIA) -> None:
        self.chamadas += 1
        self.tokens += uso.input_tokens + uso.output_tokens
        self.cost_usd += uso.cost_usd or 0.0


class GatewayResult(BaseModel):
    ok: bool
    data: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None        # IA_DESLIGADA, CIRCUIT_OPEN, RATE_LIMIT, TIMEOUT, INVALID_OUTPUT...
    error_detail: Optional[str] = None  # curto, sem segredos
    uso: UsoIA = Field(default_factory=UsoIA)


def validar_modelo(model: str) -> str:
    if any(model.lower().startswith(prefixo) for prefixo in MODELOS_BLOQUEADOS):
        raise ValueError(
            f"Modelo '{model}' está fora do evento. Use 'latest', 'glm-5.3' ou 'kimi-for-coding'."
        )
    return model


def classify_error(error: Exception, timeout_s: float) -> tuple:
    """Traduz exceções do SDK OpenAI / httpx em (código, mensagem)."""
    if isinstance(error, openai.RateLimitError):
        return "RATE_LIMIT", "Limite de requisições do IAProxy (HTTP 429)."
    if isinstance(error, (openai.APITimeoutError, httpx.TimeoutException)):
        return "TIMEOUT", f"O IAProxy não respondeu em {timeout_s:.0f}s."
    if isinstance(error, (openai.APIConnectionError, httpx.TransportError)):
        return "CONNECTION", "Falha de conexão com o IAProxy."
    if isinstance(error, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return "AUTH", "Token do IAProxy inválido ou sem permissão (verifique AI_PROXY_API_KEY)."
    status = getattr(error, "status_code", None)
    if status is None and isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
    if status == 429:
        return "RATE_LIMIT", "Limite de requisições do IAProxy (HTTP 429)."
    if status in (401, 403):
        return "AUTH", "Token do IAProxy inválido ou sem permissão (verifique AI_PROXY_API_KEY)."
    if status is not None:
        return "API_ERROR", f"O IAProxy retornou HTTP {status}."
    return "API_ERROR", "Erro inesperado no IAProxy."


_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def extrair_json(texto: str) -> Dict[str, Any]:
    """Aceita JSON puro, cercado por ``` ou com texto em volta (pega o primeiro objeto)."""
    limpo = _JSON_FENCE_RE.sub("", (texto or "").strip())
    try:
        return json.loads(limpo)
    except json.JSONDecodeError:
        inicio, fim = limpo.find("{"), limpo.rfind("}")
        if inicio == -1 or fim <= inicio:
            raise
        return json.loads(limpo[inicio : fim + 1])


@lru_cache(maxsize=2)
def _build_client(base_url: str, api_key: str, timeout_s: float, max_retries: int) -> openai.OpenAI:
    return openai.OpenAI(base_url=base_url, api_key=api_key, timeout=timeout_s, max_retries=max_retries)


class IAProxy:
    """`client` (OpenAI) e `http` (httpx) são injetáveis para testes, sem token real."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        client: Any = None,
        http: Any = None,
        breaker: Optional[CircuitBreaker] = None,
    ):
        self.settings = settings or load_settings()
        self.model = validar_modelo(self.settings.model)
        self.online = client is not None or http is not None or self.settings.ia_enabled
        self._client = client
        self._http = http
        self.breaker = breaker or get_breaker(self.settings.cb_failure_threshold, self.settings.cb_reset_s)
        self.sessao = SessaoIA()

    # ------------------------------------------------------------------ infra
    def _openai(self) -> Any:
        if self._client is None:
            s = self.settings
            self._client = _build_client(s.proxy_base_url, s.proxy_api_key, s.timeout_s, s.max_retries)
        return self._client

    def _httpx(self) -> Any:
        if self._http is None:
            self._http = httpx.Client(timeout=self.settings.timeout_s)
        return self._http

    def _fail(self, reason: str, message: str, uso: UsoIA, detail: Optional[str] = None) -> GatewayResult:
        logger.warning("IAProxy %s: %s", reason, message)
        return GatewayResult(ok=False, reason=reason, error_detail=(detail or message)[:200], uso=uso)

    def _registrar(self, uso: UsoIA) -> None:
        self.sessao.somar(uso)
        logger.info(uso.linha_custo(self.sessao))

    def _pre_checagem(self, uso: UsoIA) -> Optional[GatewayResult]:
        if not self.online:
            return GatewayResult(ok=False, reason="IA_DESLIGADA", error_detail="USE_IA=False ou sem token", uso=uso)
        if not self.breaker.allow():
            return self._fail(
                "CIRCUIT_OPEN",
                f"Circuit breaker aberto; nova tentativa em {self.breaker.retry_in_s()}s.",
                uso,
            )
        return None

    # ------------------------------------------------------------------ chat estruturado
    def chat_json(
        self,
        system: str,
        user: str,
        schema: Type[BaseModel],
        validar: Optional[Callable[[BaseModel], List[str]]] = None,
    ) -> GatewayResult:
        """Pede um objeto JSON no formato de `schema`; `validar` aplica guardrails extras.

        Não usa `response_format` nem tool calling: nem todo modelo do proxy aceita.
        O contrato é garantido por Pydantic + reparo, que funcionam em qualquer modelo.
        """
        uso = UsoIA(endpoint="chat", modelo=self.model)
        bloqueio = self._pre_checagem(uso)
        if bloqueio:
            return bloqueio

        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        sistema = (
            f"{system}\n\nResponda SOMENTE com um objeto JSON válido (sem markdown, sem texto antes ou "
            f"depois) que siga este JSON Schema:\n{schema_json}"
        )
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": sistema},
            {"role": "user", "content": user},
        ]
        started = time.perf_counter()
        problemas: List[str] = []

        for attempt in range(1, MAX_ATTEMPTS + 1):
            uso.attempts = attempt
            try:
                resp = self._openai().chat.completions.create(
                    model=self.model, messages=messages, max_tokens=self.settings.max_tokens
                )
            except Exception as error:  # noqa: BLE001 - última barreira: nunca derruba o guardião
                reason, message = classify_error(error, self.settings.timeout_s)
                if reason in {"RATE_LIMIT", "TIMEOUT", "CONNECTION", "AUTH", "API_ERROR"}:
                    self.breaker.record_failure()
                uso.latency_ms = int((time.perf_counter() - started) * 1000)
                return self._fail(reason, message, uso, detail=f"{type(error).__name__}")

            self.breaker.record_success()
            usage = getattr(resp, "usage", None)
            uso.input_tokens += getattr(usage, "prompt_tokens", 0) or 0
            uso.output_tokens += getattr(usage, "completion_tokens", 0) or 0
            details = getattr(usage, "completion_tokens_details", None)
            uso.reasoning_tokens += getattr(details, "reasoning_tokens", 0) or 0
            uso.modelo = getattr(resp, "model", None) or self.model
            uso.latency_ms = int((time.perf_counter() - started) * 1000)

            choice = resp.choices[0]
            if getattr(choice, "finish_reason", None) == "length":
                self._registrar(uso)
                return self._fail("TRUNCATED", "Resposta cortada por limite de tokens.", uso)

            conteudo = choice.message.content or ""
            try:
                obj = schema.model_validate(extrair_json(conteudo))
                problemas = validar(obj) if validar else []
            except (json.JSONDecodeError, ValidationError) as error:
                problemas = [f"Saída fora do schema: {str(error)[:300]}"]

            if not problemas:
                self._registrar(uso)
                return GatewayResult(ok=True, data=obj.model_dump(mode="json"), uso=uso)

            logger.warning("chat_json tentativa %d rejeitada: %s", attempt, "; ".join(problemas))
            if attempt < MAX_ATTEMPTS:
                messages.append({"role": "assistant", "content": conteudo})
                messages.append(
                    {
                        "role": "user",
                        "content": "Saída rejeitada: " + "; ".join(problemas)
                        + ". Corrija e responda de novo só com o JSON, usando apenas fatos e IDs fornecidos.",
                    }
                )

        self._registrar(uso)
        return self._fail("INVALID_OUTPUT", "Saída rejeitada pelos guardrails: " + "; ".join(problemas), uso)

    # ------------------------------------------------------------------ Jev (decisão tipada)
    def jev(self, state: Any, questions: Dict[str, Any]) -> GatewayResult:
        """POST /v1/systemone. Devolve `data` = answers. Custo só por entrada (estimado pelo proxy)."""
        uso = UsoIA(endpoint="jev", modelo="jev-latest", attempts=1)
        bloqueio = self._pre_checagem(uso)
        if bloqueio:
            return bloqueio

        started = time.perf_counter()
        try:
            resp = self._httpx().post(
                f"{self.settings.proxy_base_url.rstrip('/')}/systemone",
                headers={
                    "Authorization": f"Bearer {self.settings.proxy_api_key}",
                    "Content-Type": "application/json",
                },
                json={"state": state, "questions": questions},
            )
            resp.raise_for_status()
            body = resp.json()
        except Exception as error:  # noqa: BLE001
            reason, message = classify_error(error, self.settings.timeout_s)
            self.breaker.record_failure()
            uso.latency_ms = int((time.perf_counter() - started) * 1000)
            return self._fail(reason, message, uso, detail=type(error).__name__)

        self.breaker.record_success()
        usage = body.get("usage") or {}
        uso.modelo = body.get("model") or "jev-latest"
        uso.input_tokens = int(usage.get("input_tokens") or 0)
        uso.output_tokens = int(usage.get("output_tokens") or 0)
        custo = usage.get("estimated_cost_usd")
        if custo is None:
            custo = resp.headers.get("x-estimated-cost-usd")
        uso.cost_usd = float(custo) if custo is not None else None
        uso.latency_ms = int((time.perf_counter() - started) * 1000)
        self._registrar(uso)
        answers = body.get("answers")
        if not isinstance(answers, dict):
            return self._fail("INVALID_OUTPUT", "Resposta do Jev sem 'answers'.", uso)
        return GatewayResult(ok=True, data=answers, uso=uso)


# ---------------------------------------------------------------------- teste de fumaça
class _Pong(BaseModel):
    resposta: str = Field(max_length=20)


def _main() -> int:
    parser = argparse.ArgumentParser(description="Teste de fumaça do IAProxy de IA.")
    parser.add_argument("--ping", action="store_true", help="chama o chat e imprime a linha de custo")
    parser.add_argument("--jev", action="store_true", help="faz uma pergunta sim/não ao Jev")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    gw = IAProxy()
    if not gw.online:
        print("IA desligada: defina AI_PROXY_API_KEY e USE_IA=True no .env.")
        return 1
    if args.ping or not args.jev:
        r = gw.chat_json("Você é um teste de conectividade.", "Responda com resposta='pong'.", _Pong)
        print("chat:", "OK" if r.ok else f"FALHOU ({r.reason}: {r.error_detail})", r.data or "")
    if args.jev:
        r = gw.jev(
            {"texto": "Prezados, mudamos de banco. Favor depositar na nova conta a partir de hoje."},
            {"troca_conta": {"type": "noul", "instructions": "A mensagem pede troca de dados bancários?"}},
        )
        print("jev:", "OK" if r.ok else f"FALHOU ({r.reason}: {r.error_detail})", r.data or "")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
