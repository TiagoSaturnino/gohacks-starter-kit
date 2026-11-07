"""Contratos Pydantic v2 do Guardião do Lote.

Princípio herdado do kit: o modelo *propõe*, o código *decide*. Aqui ficou mais forte:
quem gera alerta e decide retenção são as regras determinísticas (checks.py). A IA só
devolve `ExplicacaoAlerta`, que não tem nenhum campo capaz de liberar, reter ou
reduzir prioridade — o único efeito possível é `agravar`.
"""
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Severidade(str, Enum):
    BLOQUEIA = "BLOQUEIA"  # perda provável se sair: retém no silêncio humano
    ACIONA = "ACIONA"      # exceção de política: avisa o dono e segue no lote
    AGRAVA = "AGRAVA"      # nunca gera alerta sozinha: só sobe a prioridade


class Acao(str, Enum):
    RETER_TITULO = "RETER_TITULO"
    LIBERAR_TITULO = "LIBERAR_TITULO"
    ACIONAR_PESSOA = "ACIONAR_PESSOA"
    LOG_ONLY = "LOG_ONLY"


class OrigemDecisao(str, Enum):
    SLACK = "slack"
    CLI = "cli"                # fila de triagem manual (plano B do Slack)
    SILENCIO = "silencio"      # ninguém respondeu até o fechamento
    AUTOMATICO = "automatico"  # título sem alerta segue no lote


class Alerta(BaseModel):
    """Saída de cada regra de checks.py (uma linha do DataFrame de alertas)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    titulo_id: str = Field(pattern=r"^TP\d{6}$")
    regra: str = Field(min_length=2, max_length=60)
    severidade: Severidade
    evidencia: str = Field(min_length=5, max_length=500)
    valor: float = Field(ge=0)


class ExplicacaoAlerta(BaseModel):
    """Única coisa que a IA pode devolver. Enviada como schema ao modelo."""

    model_config = ConfigDict(extra="forbid")

    titulo_id: str = Field(description="Repita exatamente o titulo_id do alerta.")
    resumo: str = Field(
        min_length=10,
        max_length=300,
        description="Explicação do risco em até 2 frases, para quem vai aprovar. Só fatos da evidência.",
    )
    comunicacoes_citadas: List[str] = Field(
        default_factory=list,
        max_length=5,
        description="IDs EM... das comunicações que sustentam o resumo (vazio se nenhuma).",
    )
    agravar: bool = Field(
        default=False,
        description="True só se alguma comunicação indicar risco maior (ex.: pedido de troca de conta).",
    )

    @field_validator("comunicacoes_citadas")
    @classmethod
    def ids_de_comunicacao(cls, value: List[str]) -> List[str]:
        for item in value:
            if not (item.startswith("EM") and item[2:].isdigit()):
                raise ValueError(f"'{item}' não é um id de comunicação (EM + dígitos)")
        return value


class Decisao(BaseModel):
    model_config = ConfigDict(extra="forbid")

    titulo_id: str = Field(pattern=r"^TP\d{6}$")
    lote_remessa: str = Field(min_length=3, max_length=40)
    acao: Acao
    decidido_por: str = Field(min_length=1, max_length=80)
    origem: OrigemDecisao
    motivo: Optional[str] = Field(default=None, max_length=300)
