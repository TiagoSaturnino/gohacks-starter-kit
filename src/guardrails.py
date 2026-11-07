"""Guardrails semânticos: o que o schema Pydantic não consegue garantir.

O schema valida a *forma* da saída. Aqui validamos a *ancoragem* no log original
(identificadores citados precisam existir no texto) e a política do cenário
(só ações permitidas).
"""
import re
from dataclasses import dataclass, field
from typing import Iterable, List

from src.schemas import LogAnalysisResult

# Token "tipo identificador": sequência de [A-Za-z0-9_-] com ao menos 4 dígitos
# (ex.: 8849201, GC-88310, MLB1298491). Endpoints e números pequenos não entram.
_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]*\d[A-Za-z0-9_\-]*")
MIN_ID_DIGITS = 4


def extract_identifiers(text: str) -> List[str]:
    """Extrai tokens com cara de identificador (>= 4 dígitos)."""
    found = []
    for token in _TOKEN_RE.findall(text):
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
    violations: List[str] = field(default_factory=list)  # bloqueiam o resultado
    warnings: List[str] = field(default_factory=list)  # mostram aviso, não bloqueiam

    @property
    def ok(self) -> bool:
        return not self.violations


def check_result(result: LogAnalysisResult, allowed_actions, source_text: str) -> GuardrailReport:
    """Aplica as regras de ancoragem e de ações permitidas.

    - ação fora do catálogo do cenário  -> violação
    - identificador no payload sem lastro no log -> violação (payload dispara automação)
    - identificador em root_cause/description sem lastro -> aviso
    """
    report = GuardrailReport()
    allowed = set(allowed_actions)

    for index, action in enumerate(result.recommended_actions, start=1):
        if action.action_type not in allowed:
            report.violations.append(
                f"ação #{index} ({action.action_type.value}) não é permitida neste cenário"
            )
        if action.payload:
            payload_values = [str(value) for value in action.payload.values()]
            missing = ungrounded_identifiers(payload_values, source_text)
            if missing:
                report.violations.append(
                    f"payload da ação #{index} cita valores que não aparecem no log: {', '.join(missing)}"
                )

    narrative = [result.root_cause] + [action.description for action in result.recommended_actions]
    missing_text = ungrounded_identifiers(narrative, source_text)
    if missing_text:
        report.warnings.append(
            "Identificadores citados na análise sem lastro no log: " + ", ".join(missing_text)
        )
    return report
