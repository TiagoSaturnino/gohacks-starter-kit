"""Configuração central lida de variáveis de ambiente (.env).

Tudo que muda entre ambientes (chave, modelo, cenário, limites, preços) fica aqui,
para que o restante do código não leia `os.getenv` espalhado.
"""
import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

PLACEHOLDER_KEY_PREFIX = "sk-ant-sua"
_TRUE_VALUES = ("true", "1", "yes", "sim")


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


def _env_optional_float(name: str) -> Optional[float]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


@dataclass(frozen=True)
class Settings:
    use_mock: bool
    api_key: str
    model: str
    timeout_s: float
    max_retries: int
    max_tokens: int
    max_batch_rows: int
    scenario: str
    executor: str
    audit_db: str
    manual_minutes_per_incident: int
    timezone: str = "America/Sao_Paulo"
    # Preços em USD por 1 milhão de tokens. Sem valor, o custo não é estimado
    # (preferimos não exibir número a exibir um preço desatualizado).
    price_input_per_mtok: Optional[float] = None
    price_output_per_mtok: Optional[float] = None
    cb_failure_threshold: int = 3
    cb_reset_s: float = 60.0

    @property
    def live_enabled(self) -> bool:
        """True somente com USE_MOCK=False e uma chave que não seja o placeholder."""
        has_key = bool(self.api_key) and not self.api_key.startswith(PLACEHOLDER_KEY_PREFIX)
        return (not self.use_mock) and has_key

    @property
    def pricing_configured(self) -> bool:
        return self.price_input_per_mtok is not None and self.price_output_per_mtok is not None


def load_settings() -> Settings:
    """Lê o ambiente a cada chamada (facilita testes e troca de .env)."""
    return Settings(
        use_mock=_env_bool("USE_MOCK", True),
        api_key=os.getenv("ANTHROPIC_API_KEY", "").strip(),
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5").strip(),
        timeout_s=_env_float("ANTHROPIC_TIMEOUT_S", 30.0),
        max_retries=_env_int("ANTHROPIC_MAX_RETRIES", 1),
        max_tokens=_env_int("ANTHROPIC_MAX_TOKENS", 1500),
        max_batch_rows=_env_int("MAX_BATCH_ROWS", 50),
        scenario=os.getenv("SCENARIO", "ecommerce").strip(),
        executor=os.getenv("EXECUTOR", "dry_run").strip().lower(),
        audit_db=os.getenv("AUDIT_DB", "data/audit.db").strip(),
        manual_minutes_per_incident=_env_int("MANUAL_MINUTES_PER_INCIDENT", 25),
        timezone=os.getenv("APP_TIMEZONE", "America/Sao_Paulo").strip() or "America/Sao_Paulo",
        price_input_per_mtok=_env_optional_float("PRICE_INPUT_PER_MTOK"),
        price_output_per_mtok=_env_optional_float("PRICE_OUTPUT_PER_MTOK"),
        cb_failure_threshold=max(1, _env_int("CB_FAILURE_THRESHOLD", 3)),
        cb_reset_s=_env_float("CB_RESET_S", 60.0),
    )
