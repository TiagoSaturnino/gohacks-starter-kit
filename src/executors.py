"""Executores: o que acontece DEPOIS da decisão (humana ou por silêncio no fechamento).

* `DryRunExecutor`: não grava nada (testes).
* `OutboxExecutor`: enfileira na tabela `outbox` (padrão outbox do kit).
* `AgendaSimuladaExecutor`: escrita simulada pedida no SPEC. Mantém `agenda_simulada.csv`
  = agenda_remessa.csv + status_guardiao/motivo/decidido_por/decidido_em.
  O agenda_remessa.csv original NUNCA é alterado.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pandas as pd

from src.config import Settings
from src.schemas import Acao, Decisao
from src.store import Store

STATUS_POR_ACAO = {Acao.RETER_TITULO: "retido", Acao.LIBERAR_TITULO: "liberado"}
COLUNAS_GUARDIAO = ["status_guardiao", "motivo", "decidido_por", "decidido_em"]


@dataclass(frozen=True)
class ExecutionResult:
    status: str  # SIMULATED | QUEUED | WRITTEN | SKIPPED
    detail: str


class Executor(Protocol):
    name: str

    def execute(self, decisao_id: int, decisao: Decisao, ts_simulado: str) -> ExecutionResult: ...


class DryRunExecutor:
    name = "dry_run"

    def execute(self, decisao_id: int, decisao: Decisao, ts_simulado: str) -> ExecutionResult:
        return ExecutionResult("SIMULATED", "Execução simulada: nada foi gravado.")


class OutboxExecutor:
    name = "outbox"

    def __init__(self, store: Store):
        self.store = store

    def execute(self, decisao_id: int, decisao: Decisao, ts_simulado: str) -> ExecutionResult:
        outbox_id = self.store.enqueue(decisao_id, decisao.acao.value, decisao.model_dump(mode="json"))
        return ExecutionResult("QUEUED", f"Decisão enfileirada na outbox (#{outbox_id}).")


class AgendaSimuladaExecutor:
    name = "agenda_simulada"

    def __init__(self, agenda_original: str, agenda_simulada: str):
        self.original = Path(agenda_original)
        self.simulada = Path(agenda_simulada)

    def _carregar(self) -> pd.DataFrame:
        if self.simulada.exists():
            return pd.read_csv(self.simulada, dtype=str, keep_default_na=False)
        df = pd.read_csv(self.original, dtype=str, keep_default_na=False)
        for col in COLUNAS_GUARDIAO:
            df[col] = ""
        df["status_guardiao"] = "no_lote"
        return df

    def execute(self, decisao_id: int, decisao: Decisao, ts_simulado: str) -> ExecutionResult:
        status = STATUS_POR_ACAO.get(decisao.acao)
        if status is None:
            return ExecutionResult("SKIPPED", f"{decisao.acao.value} não altera a agenda.")
        df = self._carregar()
        alvo = (df["titulo_id"] == decisao.titulo_id) & (df["lote_remessa"] == decisao.lote_remessa)
        if not alvo.any():
            return ExecutionResult("SKIPPED", f"{decisao.titulo_id} não está em {decisao.lote_remessa}.")
        df.loc[alvo, "status_guardiao"] = status
        df.loc[alvo, "motivo"] = decisao.motivo or ""
        df.loc[alvo, "decidido_por"] = f"{decisao.decidido_por} ({decisao.origem.value})"
        df.loc[alvo, "decidido_em"] = ts_simulado
        self.simulada.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.simulada.with_suffix(".tmp")
        df.to_csv(tmp, index=False, encoding="utf-8")
        tmp.replace(self.simulada)  # troca atômica: nunca deixa o CSV pela metade
        return ExecutionResult("WRITTEN", f"{decisao.titulo_id} -> {status} em {self.simulada.name}.")


def build_executor(settings: Settings, store: Store) -> Executor:
    if settings.executor == "outbox":
        return OutboxExecutor(store)
    if settings.executor == "dry_run":
        return DryRunExecutor()
    return AgendaSimuladaExecutor(
        str(Path(settings.dados_dir) / "agenda_remessa.csv"),
        str(Path("data") / "agenda_simulada.csv"),
    )
