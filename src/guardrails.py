import re
from dataclasses import dataclass, field
from typing import Iterable, List
from src.schemas import LogAnalysisResult

_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]*\d[A-Za-z0-9_\-]*")

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
    return report