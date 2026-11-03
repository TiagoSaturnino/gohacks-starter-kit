from dataclasses import dataclass
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
    return DryRunExecutor()