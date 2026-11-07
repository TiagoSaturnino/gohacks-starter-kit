"""Cenários: tudo que muda de uma "dor" para outra, fora do código.

Um cenário é um arquivo JSON em `scenarios/` com o contexto do domínio, a régua de
severidade, as ações permitidas e fixtures (log + análise esperada) usadas pelo
modo offline (replay) e pelos testes. Trocar de desafio = criar um novo JSON.
"""
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas import ActionType, LogAnalysisResult, SeverityLevel

SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios"


class ScenarioNotFoundError(ValueError):
    pass


def normalize_text(text: str) -> str:
    """Normaliza espaços para comparar logs sem depender de indentação."""
    return " ".join(text.split())


class Fixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    log: str
    analysis: LogAnalysisResult


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    domain: str = Field(description="Contexto do domínio, inserido no system prompt.")
    severity_rubric: Dict[SeverityLevel, str]
    allowed_actions: List[ActionType] = Field(min_length=1)
    fixtures: List[Fixture] = Field(default_factory=list)

    @model_validator(mode="after")
    def fixtures_use_allowed_actions(self):
        allowed = set(self.allowed_actions)
        for fixture in self.fixtures:
            for action in fixture.analysis.recommended_actions:
                if action.action_type not in allowed:
                    raise ValueError(
                        f"fixture '{fixture.key}' usa ação fora de allowed_actions: "
                        f"{action.action_type.value}"
                    )
        return self

    def fixture_logs(self) -> Dict[str, str]:
        return {fixture.key: fixture.log for fixture in self.fixtures}

    def find_fixture(self, log_text: str) -> Optional[Fixture]:
        wanted = normalize_text(log_text)
        for fixture in self.fixtures:
            if normalize_text(fixture.log) == wanted:
                return fixture
        return None


def list_scenarios(directory: Optional[Path] = None) -> List[str]:
    base = Path(directory) if directory else SCENARIOS_DIR
    if not base.exists():
        return []
    return sorted(path.stem for path in base.glob("*.json"))


def load_scenario(name: str, directory: Optional[Path] = None) -> Scenario:
    base = Path(directory) if directory else SCENARIOS_DIR
    path = base / f"{name}.json"
    if not path.exists():
        available = ", ".join(list_scenarios(base)) or "nenhum"
        raise ScenarioNotFoundError(
            f"Cenário '{name}' não encontrado em {base}. Disponíveis: {available}."
        )
    # utf-8-sig tolera o BOM que alguns editores do Windows inserem.
    return Scenario.model_validate_json(path.read_text(encoding="utf-8-sig"))
