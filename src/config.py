import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

PLACEHOLDER_KEY_PREFIX = "sk-ant-sua"
_TRUE_VALUES = ("true", "1", "yes", "sim")

def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in _TRUE_VALUES

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

    @property
    def live_enabled(self) -> bool:
        has_key = bool(self.api_key) and not self.api_key.startswith(PLACEHOLDER_KEY_PREFIX)
        return (not self.use_mock) and has_key

def load_settings() -> Settings:
    return Settings(
        use_mock=_env_bool("USE_MOCK", True),
        api_key=os.getenv("ANTHROPIC_API_KEY", "").strip(),
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5").strip(),
        timeout_s=_env_float("ANTHROPIC_TIMEOUT_S", 30.0),
        max_retries=_env_int("ANTHROPIC_MAX_RETRIES", 2),
        max_tokens=_env_int("ANTHROPIC_MAX_TOKENS", 1500),
        max_batch_rows=_env_int("MAX_BATCH_ROWS", 50),
        scenario=os.getenv("SCENARIO", "ecommerce").strip(),
        executor=os.getenv("EXECUTOR", "dry_run").strip().lower(),
        audit_db=os.getenv("AUDIT_DB", "data/audit.db").strip(),
        manual_minutes_per_incident=_env_int("MANUAL_MINUTES_PER_INCIDENT", 25),
    )