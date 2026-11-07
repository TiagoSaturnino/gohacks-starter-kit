"""Configuração central lida de variáveis de ambiente (.env).

Tudo que muda entre ambientes (token, modelo, dados, relógio simulado, limites) fica
aqui, para que o restante do código não leia `os.getenv` espalhado.

A IA passa sempre pelo IAProxy do evento (AI Proxy, formato OpenAI). Limiares de
negócio (alçadas, tolerâncias, custos) NÃO ficam aqui: vêm de politicas.csv.
"""
import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

_TRUE_VALUES = ("true", "1", "yes", "sim")
DEFAULT_PROXY_URL = "https://ai-proxy.gogroupbr.com/v1"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    # --- IAProxy de IA
    use_ia: bool
    proxy_api_key: str
    proxy_base_url: str
    model: str
    timeout_s: float
    max_retries: int
    max_tokens: int
    jev_limiar_agravar: float
    cb_failure_threshold: int
    cb_reset_s: float
    cache_dir: str
    # --- guardião
    dados_dir: str
    agora: str
    dias_lancador_recente: int
    executor: str
    audit_db: str
    timezone: str = "America/Sao_Paulo"
    # --- Slack (item 6 do SPEC)
    slack_bot_token: str = ""
    slack_app_token: str = ""
    slack_canal: str = ""

    @property
    def ia_enabled(self) -> bool:
        """True somente com USE_IA=True e um token gw-tok configurado."""
        return self.use_ia and bool(self.proxy_api_key)

    @property
    def slack_enabled(self) -> bool:
        return bool(self.slack_bot_token and self.slack_app_token and self.slack_canal)


def load_settings() -> Settings:
    """Lê o ambiente a cada chamada (facilita testes e troca de .env)."""
    return Settings(
        use_ia=_env_bool("USE_IA", False),
        proxy_api_key=os.getenv("AI_PROXY_API_KEY", "").strip(),
        proxy_base_url=os.getenv("AI_PROXY_BASE_URL", DEFAULT_PROXY_URL).strip(),
        model=os.getenv("AI_MODEL", "latest").strip(),
        timeout_s=_env_float("AI_TIMEOUT_S", 30.0),
        max_retries=_env_int("AI_MAX_RETRIES", 1),
        max_tokens=max(2000, _env_int("AI_MAX_TOKENS", 2000)),  # extras raciocinam antes de responder
        jev_limiar_agravar=_env_float("JEV_LIMIAR_AGRAVAR", 0.7),
        cb_failure_threshold=max(1, _env_int("CB_FAILURE_THRESHOLD", 3)),
        cb_reset_s=_env_float("CB_RESET_S", 60.0),
        cache_dir=os.getenv("CACHE_DIR", "cache_explicacoes").strip(),
        dados_dir=os.getenv("DADOS_DIR", "pagamentos").strip(),
        agora=os.getenv("AGORA", "2026-11-02T08:00").strip(),
        dias_lancador_recente=_env_int("DIAS_LANCADOR_RECENTE", 45),
        executor=os.getenv("EXECUTOR", "agenda_simulada").strip().lower(),
        audit_db=os.getenv("AUDIT_DB", "data/audit.db").strip(),
        timezone=os.getenv("APP_TIMEZONE", "America/Sao_Paulo").strip() or "America/Sao_Paulo",
        slack_bot_token=os.getenv("SLACK_BOT_TOKEN", "").strip(),
        slack_app_token=os.getenv("SLACK_APP_TOKEN", "").strip(),
        slack_canal=os.getenv("SLACK_CANAL", "").strip(),
    )
