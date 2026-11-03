import re
from dataclasses import dataclass, field
from typing import Dict

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
CNPJ_RE = re.compile(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)")
CPF_RE = re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)")
PHONE_RE = re.compile(r"(?<!\d)(?:\+55\s?)?\(?\d{2}\)?\s?9?\d{4}-\d{4}(?!\d)")
NAME_KEY_RE = re.compile(r'("(?:name|nome|full_name|customer_name|nome_cliente)"\s*:\s*")([^"]+)(")', re.IGNORECASE)

def _digits(val: str) -> str:
    return re.sub(r"\D", "", val)

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
    return MaskResult(text=t, counts=counts)