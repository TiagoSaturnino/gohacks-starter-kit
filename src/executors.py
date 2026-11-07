"""Executores: o que acontece DEPOIS da aprovação humana.

* `DryRunExecutor`: não faz nenhuma chamada externa (padrão do MVP).
* `OutboxExecutor`: enfileira a ação na tabela `outbox`; um worker externo faria a
  chamada real (padrão outbox). Nenhum executor aqui chama sistemas de terceiros.

Para integrar um sistema real, implemente `Executor.execute` e registre em
`build_executor`.
"""
from dataclasses import dataclass
from typing import Protocol

from src.config import Settings
from src.schemas import RecommendedAction
from src.store import Store


@dataclass(frozen=True)
class ExecutionResult:
    status: str  # SIMULATED | QUEUED
    detail: str


class Executor(Protocol):
    name: str

    def execute(self, decision_id: int, action: RecommendedAction) -> ExecutionResult: ...


class DryRunExecutor:
    name = "dry_run"

    def execute(self, decision_id: int, action: RecommendedAction) -> ExecutionResult:
        return ExecutionResult(
            status="SIMULATED",
            detail="Execução simulada: nenhuma chamada externa foi feita.",
        )


class OutboxExecutor:
    name = "outbox"

    def __init__(self, store: Store):
        self.store = store

    def execute(self, decision_id: int, action: RecommendedAction) -> ExecutionResult:
        outbox_id = self.store.enqueue(decision_id, action.action_type.value, action.payload)
        return ExecutionResult(
            status="QUEUED",
            detail=f"Ação enfileirada na outbox (#{outbox_id}); um worker externo executaria a chamada real.",
        )


def build_executor(settings: Settings, store: Store) -> Executor:
    if settings.executor == "outbox":
        return OutboxExecutor(store)
    return DryRunExecutor()
