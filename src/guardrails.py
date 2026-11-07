"""Guardrails semânticos: o que o schema Pydantic não consegue garantir.

Ancoragem: a explicação da IA só pode citar identificadores (TP..., FP..., EM...,
números de NF, valores com 4+ dígitos) que existam na evidência do alerta ou no texto
das comunicações enviadas. Qualquer identificador inventado rejeita a saída (1 reparo,
depois texto-modelo).
"""
import re
from dataclasses import dataclass, field
from typing import Iterable, List

from src.schemas import Alerta, ExplicacaoAlerta

# Token "tipo identificador": sequência de [A-Za-z0-9_-] com ao menos 4 dígitos.
_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]*\d[A-Za-z0-9_\-]*")
MIN_ID_DIGITS = 4


def extract_identifiers(text: str) -> List[str]:
    """Extrai tokens com cara de identificador (>= 4 dígitos)."""
    found = []
    for token in _TOKEN_RE.findall(text):
        token = token.strip("-_")
        if sum(char.isdigit() for char in token) >= MIN_ID_DIGITS and token not in found:
            found.append(token)
    return found


def ungrounded_identifiers(texts: Iterable[str], source_text: str) -> List[str]:
    """Identificadores presentes em `texts` mas ausentes em `source_text`."""
    haystack = source_text.lower()
    missing: List[str] = []
    for text in texts:
        for token in extract_identifiers(text):
            if token.lower() not in haystack and token not in missing:
                missing.append(token)
    return missing


@dataclass
class GuardrailReport:
    violations: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def check_explicacao(expl: ExplicacaoAlerta, alerta: Alerta, fonte: str) -> GuardrailReport:
    """`fonte` = evidência do alerta + texto (mascarado) das comunicações enviadas ao modelo."""
    report = GuardrailReport()
    if expl.titulo_id != alerta.titulo_id:
        report.violations.append(
            f"titulo_id '{expl.titulo_id}' diferente do alerta '{alerta.titulo_id}'"
        )
    for em in expl.comunicacoes_citadas:
        if em not in fonte:
            report.violations.append(f"comunicação {em} não foi enviada ao modelo")
    missing = ungrounded_identifiers([expl.resumo], fonte)
    if missing:
        report.violations.append(
            "resumo cita identificadores sem lastro na evidência: " + ", ".join(missing)
        )
    return report
