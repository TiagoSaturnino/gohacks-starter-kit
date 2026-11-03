import os
import subprocess
import json

FILES = {
    ".env.example": """# Copie para .env
ANTHROPIC_API_KEY=sk-ant-sua-chave-aqui
USE_MOCK=True
ANTHROPIC_MODEL=claude-sonnet-5-5
SCENARIO=ecommerce
EXECUTOR=dry_run
AUDIT_DB=data/audit.db
MANUAL_MINUTES_PER_INCIDENT=25""",

    ".gitignore": """venv/
.venv/
.env
__pycache__/
.streamlit/
.pytest_cache/
data/
*.db""",

    "pytest.ini": """[pytest]
pythonpath = .
testpaths = tests""",

    "requirements.txt": """streamlit==1.66.1
anthropic==2.4.0
pydantic==2.14.2
python-dotenv==1.2.4
pandas==3.1.0""",

    "requirements-dev.txt": """-r requirements.txt
pytest==9.2.0""",

    "scenarios/ecommerce.json": json.dumps({
        "id": "ecommerce",
        "name": "Integrações de e-commerce (Mercado Livre, Amazon SP-API, webhooks)",
        "domain": "Operação de integrações de e-commerce de um grupo de varejo: sincronização de pedidos, estoque e preço com marketplaces (Mercado Livre, Amazon SP-API) e webhooks de checkout.",
        "severity_rubric": {
            "CRITICAL": "Pedidos ou faturamento bloqueados ou dados de clientes expostos.",
            "HIGH": "Sincronização de pedidos, estoque ou preço interrompida.",
            "MEDIUM": "Falha intermitente ou degradação com retentativa possível.",
            "LOW": "Aviso sem impacto operacional imediato."
        },
        "allowed_actions": ["REFRESH_TOKEN", "RETRY_WEBHOOK", "CLEAR_CACHE", "ESCALATE_HUMAN", "LOG_ONLY"],
        "fixtures": [
            {
                "key": "mercadolivre_oauth_401",
                "log": "[2026-11-01T14:23:10Z] ERROR [GoGroup-Integrator] MercadoLivre API HTTP 401 Unauthorized\nEndpoint: POST /items/MLB1298491/stock\nPayload: {\"seller_id\": \"8849201\", \"quantity\": 15}\nResponse: {\"error\": \"invalid_token\", \"message\": \"The access token provided has expired.\"}",
                "analysis": {
                    "service_name": "Mercado Livre API",
                    "severity": "HIGH",
                    "root_cause": "O access token OAuth2 do seller 8849201 expirou (HTTP 401 invalid_token) ao atualizar o estoque do item MLB1298491.",
                    "confidence_score": 0.95,
                    "recommended_actions": [
                        {
                            "action_type": "REFRESH_TOKEN",
                            "description": "Renovar o token de acesso do seller 8849201.",
                            "payload": {"seller_id": "8849201", "endpoint": "/oauth/token"}
                        }
                    ]
                }
            },
            {
                "key": "sp_api_504_timeout",
                "log": "[2026-11-01T15:10:05Z] ERROR [GoGroup-AmazonSync] Amazon SP-API Gateway Timeout HTTP 504\nEndpoint: GET /orders/v0/orders\nMessage: Connection timed out after 30000ms while fetching seller orders.",
                "analysis": {
                    "service_name": "Amazon SP-API",
                    "severity": "MEDIUM",
                    "root_cause": "A consulta GET /orders/v0/orders excedeu 30000ms e retornou 504 Gateway Timeout.",
                    "confidence_score": 0.9,
                    "recommended_actions": [
                        {
                            "action_type": "RETRY_WEBHOOK",
                            "description": "Reexecutar a consulta de pedidos com backoff exponencial.",
                            "payload": {"service": "amazon_sp_api", "retry_attempt": 1}
                        }
                    ]
                }
            },
            {
                "key": "webhook_null_cpf",
                "log": "[2026-11-01T16:05:40Z] ERROR [GoGroup-Checkout] Webhook delivery failed.\nPayload: {\"order_id\": \"GC-88310\", \"customer\": {\"name\": \"Maria Silva\", \"cpf\": null}, \"total\": 149.90}\nMessage: ValidationError: customer.cpf cannot be null. Event sent to DLQ.",
                "analysis": {
                    "service_name": "GoGroup Checkout",
                    "severity": "CRITICAL",
                    "root_cause": "O webhook do pedido GC-88310 foi rejeitado porque customer.cpf veio nulo; evento enviado à DLQ.",
                    "confidence_score": 0.95,
                    "recommended_actions": [
                        {
                            "action_type": "ESCALATE_HUMAN",
                            "description": "Encaminhar o pedido GC-88310 à equipe de cadastro para correção do CPF.",
                            "payload": {"order_id": "GC-88310", "issue": "MISSING_CPF"}
                        }
                    ]
                }
            }
        ]
    }, indent=2),

    "src/config.py": """import os
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
    )""",

    "src/schemas.py": """from enum import Enum
from typing import Dict, List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field, model_validator

class SeverityLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

class ActionType(str, Enum):
    REFRESH_TOKEN = "REFRESH_TOKEN"
    RETRY_WEBHOOK = "RETRY_WEBHOOK"
    CLEAR_CACHE = "CLEAR_CACHE"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"
    LOG_ONLY = "LOG_ONLY"

REQUIRES_APPROVAL_ACTIONS = frozenset({
    ActionType.REFRESH_TOKEN, ActionType.RETRY_WEBHOOK,
    ActionType.CLEAR_CACHE, ActionType.ESCALATE_HUMAN
})
HITL_SEVERITIES = frozenset({SeverityLevel.HIGH, SeverityLevel.CRITICAL})
LOW_CONFIDENCE_THRESHOLD = 0.6
PayloadValue = Union[str, int, float, bool]

class RecommendedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_type: ActionType = Field(description="Tipo de ação recomendada.")
    description: str = Field(max_length=300, description="Descrição sucinta.")
    payload: Optional[Dict[str, PayloadValue]] = Field(default=None, description="Parâmetros técnicos.")
    requires_human_approval: bool = Field(default=True, description="HITL em código.")

    @model_validator(mode="after")
    def enforce_hitl_policy(self):
        self.requires_human_approval = self.action_type in REQUIRES_APPROVAL_ACTIONS
        return self

class LogAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_name: str = Field(max_length=80, description="Serviço impactado.")
    severity: SeverityLevel = Field(description="Nível de severidade.")
    root_cause: str = Field(max_length=600, description="Causa raiz técnica.")
    confidence_score: float = Field(ge=0.0, le=1.0, description="Score de confiança.")
    recommended_actions: List[RecommendedAction] = Field(min_length=1, max_length=5)
    requires_human_intervention: bool = Field(default=True)

    @model_validator(mode="after")
    def sync_hitl_flags(self):
        force_review = self.severity in HITL_SEVERITIES or self.confidence_score < LOW_CONFIDENCE_THRESHOLD
        if force_review:
            for action in self.recommended_actions:
                action.requires_human_approval = True
        self.requires_human_intervention = force_review or any(a.requires_human_approval for a in self.recommended_actions)
        return self""",

    "src/pii.py": """import re
from dataclasses import dataclass, field
from typing import Dict

EMAIL_RE = re.compile(r"[\\w.+-]+@[\\w-]+(?:\\.[\\w-]+)+")
CARD_RE = re.compile(r"(?<!\\d)(?:\\d[ -]?){12,18}\\d(?!\\d)")
CNPJ_RE = re.compile(r"(?<!\\d)\\d{2}\\.?\\d{3}\\.?\\d{3}/?\\d{4}-?\\d{2}(?!\\d)")
CPF_RE = re.compile(r"(?<!\\d)\\d{3}\\.?\\d{3}\\.?\\d{3}-?\\d{2}(?!\\d)")
PHONE_RE = re.compile(r"(?<!\\d)(?:\\+55\\s?)?\\(?\\d{2}\\)?\\s?9?\\d{4}-\\d{4}(?!\\d)")
NAME_KEY_RE = re.compile(r'("(?:name|nome|full_name|customer_name|nome_cliente)"\\s*:\\s*")([^"]+)(")', re.IGNORECASE)

def _digits(val: str) -> str:
    return re.sub(r"\\D", "", val)

def _luhn_ok(num: str) -> bool:
    total, parity = 0, len(num) % 2
    for idx, char in enumerate(num):
        d = int(char)
        if idx % 2 == parity:
            d *= 2
            if d > 9: d -= 9
        total += d
    return total % 10 == 0

@dataclass
class MaskResult:
    text: str
    counts: Dict[str, int] = field(default_factory=dict)

def mask_pii(text: str) -> MaskResult:
    counts: Dict[str, int] = {}
    def bump(k): counts[k] = counts.get(k, 0) + 1
    
    t = EMAIL_RE.sub(lambda m: (bump("EMAIL"), "[EMAIL_MASCARADO]")[1], text)
    t = CNPJ_RE.sub(lambda m: (bump("CNPJ"), "[CNPJ_MASCARADO]")[1], t)
    t = CPF_RE.sub(lambda m: (bump("CPF"), "[CPF_MASCARADO]")[1], t)
    t = PHONE_RE.sub(lambda m: (bump("TELEFONE"), "[TELEFONE_MASCARADO]")[1], t)
    t = CARD_RE.sub(lambda m: (bump("CARTAO"), "[CARTAO_MASCARADO]")[1] if _luhn_ok(_digits(m.group(0))) else m.group(0), t)
    t = NAME_KEY_RE.sub(lambda m: (bump("NOME"), f'{m.group(1)}[NOME_MASCARADO]{m.group(3)}')[1], t)
    return MaskResult(text=t, counts=counts)""",

    "src/guardrails.py": """import re
from dataclasses import dataclass, field
from typing import Iterable, List
from src.schemas import LogAnalysisResult

_TOKEN_RE = re.compile(r"[A-Za-z0-9_\\-]*\\d[A-Za-z0-9_\\-]*")

def extract_identifiers(text: str) -> List[str]:
    return [t for t in _TOKEN_RE.findall(text) if sum(c.isdigit() for c in t) >= 4]

def ungrounded_identifiers(texts: Iterable[str], source_text: str) -> List[str]:
    haystack = source_text.lower()
    missing = []
    for text in texts:
        for token in extract_identifiers(text):
            if token.lower() not in haystack and token not in missing:
                missing.append(token)
    return missing

@dataclass
class GuardrailReport:
    violations: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    @property
    def ok(self) -> bool: return not self.violations

def check_result(result: LogAnalysisResult, allowed_actions, source_text: str) -> GuardrailReport:
    report = GuardrailReport()
    allowed = set(allowed_actions)
    for idx, action in enumerate(result.recommended_actions, start=1):
        if action.action_type not in allowed:
            report.violations.append(f"Ação #{idx} ({action.action_type.value}) não permitida no cenário")
        if action.payload:
            missing = ungrounded_identifiers([str(v) for v in action.payload.values()], source_text)
            if missing:
                report.violations.append(f"Payload da ação #{idx} possui IDs não presentes no log: {', '.join(missing)}")
    return report""",

    "src/scenarios.py": """from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field
from src.schemas import ActionType, LogAnalysisResult, SeverityLevel

SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios"

class Fixture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    log: str
    analysis: LogAnalysisResult

class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    domain: str
    severity_rubric: Dict[SeverityLevel, str]
    allowed_actions: List[ActionType]
    fixtures: List[Fixture] = Field(default_factory=list)

    def fixture_logs(self) -> Dict[str, str]:
        return {f.key: f.log for f in self.fixtures}

    def find_fixture(self, log_text: str) -> Optional[Fixture]:
        wanted = " ".join(log_text.split())
        for f in self.fixtures:
            if " ".join(f.log.split()) == wanted:
                return f
        return None

def list_scenarios() -> List[str]:
    return sorted(p.stem for p in SCENARIOS_DIR.glob("*.json")) if SCENARIOS_DIR.exists() else []

def load_scenario(name: str) -> Scenario:
    path = SCENARIOS_DIR / f"{name}.json"
    if not path.exists():
        raise ValueError(f"Cenário '{name}' não existe.")
    return Scenario.model_validate_json(path.read_text(encoding="utf-8-sig"))""",

    "src/analyzer.py": """import time
import logging
from typing import Optional, Literal
from pydantic import BaseModel, Field
import anthropic
from src.config import Settings, load_settings
from src.schemas import LogAnalysisResult
from src.scenarios import Scenario, load_scenario
from src.pii import mask_pii
from src.guardrails import check_result

TOOL_NAME = "submit_log_analysis"

class AnalysisMeta(BaseModel):
    model: Optional[str] = None
    latency_ms: int = 0
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    attempts: int = 0
    pii_masked: dict = Field(default_factory=dict)

class AnalysisOutcome(BaseModel):
    source: Literal["LIVE", "MOCK", "DEGRADED"]
    result: Optional[LogAnalysisResult] = None
    error: Optional[str] = None
    warnings: list = Field(default_factory=list)
    meta: AnalysisMeta = Field(default_factory=AnalysisMeta)

def analyze_log(log_text: str, scenario: Optional[Scenario] = None, settings: Optional[Settings] = None, client=None) -> AnalysisOutcome:
    settings = settings or load_settings()
    scenario = scenario or load_scenario(settings.scenario)
    if not log_text or not log_text.strip():
        return AnalysisOutcome(source="DEGRADED", error="Log vazio.")
    
    if client is None and not settings.live_enabled:
        fixture = scenario.find_fixture(log_text)
        if fixture is None:
            return AnalysisOutcome(source="DEGRADED", error=f"Modo offline: sem fixture para este log no cenário '{scenario.name}'.")
        return AnalysisOutcome(source="MOCK", result=fixture.analysis)
    
    return AnalysisOutcome(source="DEGRADED", error="Live API indisponível ou não configurada.")""",

    "src/store.py": """import sqlite3, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List
from contextlib import closing

SCHEMA = \"\"\"
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    incident_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    approver TEXT NOT NULL,
    decision TEXT NOT NULL,
    source TEXT NOT NULL,
    execution_status TEXT,
    execution_detail TEXT,
    UNIQUE (incident_id, action_type, payload_hash)
);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    decision_id INTEGER NOT NULL UNIQUE,
    action_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING'
);
\"\"\"

def make_incident_id(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()[:12]

def payload_hash(payload: Optional[Dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(payload or {}, sort_keys=True).encode()).hexdigest()[:16]

class Store:
    def __init__(self, path: str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def record_decision(self, incident_id: str, action_type: str, payload: Optional[Dict], approver: str, decision: str, source: str) -> Tuple[int, bool]:
        digest = payload_hash(payload)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "INSERT OR IGNORE INTO decisions (ts, incident_id, action_type, payload_hash, approver, decision, source) VALUES (?,?,?,?,?,?,?)",
                (now, incident_id, action_type, digest, approver, decision, source)
            )
            created = cur.rowcount == 1
            row = conn.execute("SELECT id FROM decisions WHERE incident_id=? AND action_type=? AND payload_hash=?", (incident_id, action_type, digest)).fetchone()
            conn.commit()
            return int(row["id"]), created

    def get_decision(self, incident_id: str, action_type: str, payload: Optional[Dict]) -> Optional[Dict]:
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM decisions WHERE incident_id=? AND action_type=? AND payload_hash=?", (incident_id, action_type, payload_hash(payload))).fetchone()
            return dict(row) if row else None

    def set_execution(self, decision_id: int, status: str, detail: str):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("UPDATE decisions SET execution_status=?, execution_detail=? WHERE id=?", (status, detail, decision_id))
            conn.commit()

    def decisions(self) -> List[Dict]:
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM decisions ORDER BY id DESC").fetchall()]

    def outbox(self) -> List[Dict]:
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM outbox ORDER BY id DESC").fetchall()]""",

    "src/executors.py": """from dataclasses import dataclass
from src.schemas import RecommendedAction
from src.store import Store

@dataclass(frozen=True)
class ExecutionResult:
    status: str
    detail: str

class DryRunExecutor:
    name = "dry_run"
    def execute(self, decision_id: int, action: RecommendedAction) -> ExecutionResult:
        return ExecutionResult("SIMULATED", "Execução simulada: nenhuma chamada externa feita.")

def build_executor(settings, store: Store):
    return DryRunExecutor()""",

    "tests/helpers.py": """from src.config import Settings

def make_settings(**kwargs) -> Settings:
    vals = dict(use_mock=True, api_key="", model="claude-sonnet-5-5", timeout_s=5.0, max_retries=0, max_tokens=1500, max_batch_rows=50, scenario="ecommerce", executor="dry_run", audit_db=":memory:", manual_minutes_per_incident=25)
    vals.update(kwargs)
    return Settings(**vals)""",

    "tests/conftest.py": """import pytest
from src.scenarios import load_scenario

@pytest.fixture
def scenario():
    return load_scenario("ecommerce")""",

    "tests/test_schemas.py": """import pytest
from src.schemas import LogAnalysisResult, RecommendedAction

def test_mutating_action_requires_approval():
    action = RecommendedAction(action_type="REFRESH_TOKEN", description="x", requires_human_approval=False)
    assert action.requires_human_approval is True""",

    "app.py": """import pandas as pd
import streamlit as st
from src.config import load_settings
from src.scenarios import load_scenario, list_scenarios
from src.analyzer import analyze_log
from src.store import Store, make_incident_id
from src.executors import build_executor

st.set_page_config(page_title="GoHacks - Triagem & RCA", page_icon="⚡", layout="wide")

def main():
    settings = load_settings()
    store = Store(settings.audit_db)
    executor = build_executor(settings, store)
    
    st.title("⚡ GoHacks AI Engine - Triagem & RCA")
    scenario_names = list_scenarios()
    if not scenario_names:
        st.error("Nenhum cenário encontrado.")
        return
    
    scenario = load_scenario(scenario_names[0])
    st.caption(f"Cenário Ativo: {scenario.name}")
    
    fixtures = scenario.fixture_logs()
    selected_key = st.selectbox("Selecione um Log de Teste:", list(fixtures.keys()))
    log_text = st.text_area("Payload do Log:", value=fixtures[selected_key], height=150)
    
    if st.button("🚀 Executar Triagem & RCA", type="primary"):
        outcome = analyze_log(log_text, scenario=scenario, settings=settings)
        if outcome.source == "DEGRADED":
            st.error(f"🚨 {outcome.error}")
        else:
            res = outcome.result
            st.success(f"Análise Concluída ({outcome.source})")
            st.write(f"**Serviço:** {res.service_name} | **Severidade:** {res.severity.value}")
            st.write(f"**Causa Raiz:** {res.root_cause}")

if __name__ == "__main__":
    main()"""
}

def apply():
    try:
        subprocess.run(["git", "checkout", "-b", "refactor/v2-estrutura"], check=False)
    except Exception:
        pass

    for path, content in FILES.items():
        dir_name = os.path.dirname(path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content.strip())
        print(f"  ok  {path}")

    if os.path.exists("src/mocks.py"):
        os.remove("src/mocks.py")
        print("  removido  src/mocks.py")

    print("\n✅ Estrutura V2 aplicada com sucesso!")

if __name__ == "__main__":
    apply()
