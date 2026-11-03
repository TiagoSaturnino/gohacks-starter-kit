from pathlib import Path
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
    return Scenario.model_validate_json(path.read_text(encoding="utf-8-sig"))