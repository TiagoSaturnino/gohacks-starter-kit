"""Mascaramento de dados pessoais (LGPD) antes de enviar texto a um LLM.

Cobre CPF, CNPJ, e-mail, telefone BR, cartão (validado por Luhn), valores de chaves
JSON de nome e, para Pagamentos, dados bancários: linha digitável (47 dígitos, contém
agência e conta), chave de acesso de NF (44 dígitos), destino "banco/agência/conta",
conta isolada (6 a 8 dígitos + DV) e chave pix aleatória (UUID).

`mask_pii` substitui tudo (para prompts). `mascarar_conta` e `mascarar_cnpj` fazem
máscara parcial legível (para mensagens no Slack), mantendo o suficiente para a
pessoa reconhecer o fornecedor sem expor o dado inteiro. Limitação conhecida: nomes soltos em texto livre NÃO são
detectados por regex; para isso seria preciso NER (ex.: Microsoft Presidio).
"""
import re
from dataclasses import dataclass, field
from typing import Dict

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
CNPJ_RE = re.compile(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)")
CPF_RE = re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)")
PHONE_RE = re.compile(r"(?<!\d)(?:\+55\s?)?\(?\d{2}\)?\s?9?\d{4}-\d{4}(?!\d)")
LINHA_DIGITAVEL_RE = re.compile(r"(?<!\d)\d{47}(?!\d)")
CHAVE_ACESSO_RE = re.compile(r"(?<!\d)\d{44}(?!\d)")
DESTINO_BANCARIO_RE = re.compile(r"(?<![\d/])\d{3}/\d{4}/\d{4,8}-[\dXx](?![\d])")
# 6 a 8 dígitos + DV: não pega NF com série ("73535-1" tem 5 dígitos antes do traço).
CONTA_RE = re.compile(r"(?<![\d/.-])\d{6,8}-[\dXx](?![\d/])")
UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
NAME_KEY_RE = re.compile(
    r'("(?:name|nome|full_name|customer_name|nome_cliente)"\s*:\s*")([^"]+)(")',
    re.IGNORECASE,
)


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _luhn_ok(number: str) -> bool:
    total, parity = 0, len(number) % 2
    for index, char in enumerate(number):
        digit = int(char)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _cpf_ok(cpf: str) -> bool:
    if len(cpf) != 11 or len(set(cpf)) == 1:
        return False
    for size in (9, 10):
        total = sum(int(cpf[i]) * (size + 1 - i) for i in range(size))
        check = (total * 10) % 11 % 10
        if check != int(cpf[size]):
            return False
    return True


def _cnpj_ok(cnpj: str) -> bool:
    if len(cnpj) != 14 or len(set(cnpj)) == 1:
        return False
    first = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    second = [6] + first
    for weights, position in ((first, 12), (second, 13)):
        total = sum(int(digit) * weight for digit, weight in zip(cnpj[:position], weights))
        rest = total % 11
        check = 0 if rest < 2 else 11 - rest
        if check != int(cnpj[position]):
            return False
    return True


@dataclass
class MaskResult:
    text: str
    counts: Dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def mask_pii(text: str) -> MaskResult:
    """Substitui dados pessoais por marcadores `[TIPO_MASCARADO]`."""
    counts: Dict[str, int] = {}

    def bump(kind: str) -> None:
        counts[kind] = counts.get(kind, 0) + 1

    def sub_email(match):
        bump("EMAIL")
        return "[EMAIL_MASCARADO]"

    def sub_card(match):
        raw = match.group(0)
        digits = _digits(raw)
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            bump("CARTAO")
            return "[CARTAO_MASCARADO]"
        return raw

    def sub_cnpj(match):
        raw = match.group(0)
        formatted = any(sep in raw for sep in ".-/")
        if formatted or _cnpj_ok(_digits(raw)):
            bump("CNPJ")
            return "[CNPJ_MASCARADO]"
        return raw

    def sub_cpf(match):
        raw = match.group(0)
        formatted = any(sep in raw for sep in ".-")
        if formatted or _cpf_ok(_digits(raw)):
            bump("CPF")
            return "[CPF_MASCARADO]"
        return raw

    def sub_phone(match):
        bump("TELEFONE")
        return "[TELEFONE_MASCARADO]"

    def sub_name(match):
        bump("NOME")
        return f"{match.group(1)}[NOME_MASCARADO]{match.group(3)}"

    def fixed(kind: str, marker: str):
        def _sub(match):
            bump(kind)
            return marker
        return _sub

    # Dados bancários primeiro: os números longos contêm trechos que pareceriam CNPJ/CPF.
    masked = LINHA_DIGITAVEL_RE.sub(fixed("LINHA_DIGITAVEL", "[LINHA_DIGITAVEL_MASCARADA]"), text)
    masked = CHAVE_ACESSO_RE.sub(fixed("CHAVE_ACESSO", "[CHAVE_ACESSO_MASCARADA]"), masked)
    masked = UUID_RE.sub(fixed("CHAVE_PIX", "[CHAVE_PIX_MASCARADA]"), masked)
    masked = DESTINO_BANCARIO_RE.sub(fixed("DESTINO_BANCARIO", "[DESTINO_BANCARIO_MASCARADO]"), masked)
    masked = EMAIL_RE.sub(sub_email, masked)
    masked = CNPJ_RE.sub(sub_cnpj, masked)
    masked = CPF_RE.sub(sub_cpf, masked)
    masked = PHONE_RE.sub(sub_phone, masked)  # antes do cartão: +55 85 99999-8888 passa no Luhn por acaso
    masked = CARD_RE.sub(sub_card, masked)
    masked = CONTA_RE.sub(fixed("CONTA", "[CONTA_MASCARADA]"), masked)
    masked = NAME_KEY_RE.sub(sub_name, masked)
    return MaskResult(text=masked, counts=counts)


def mascarar_conta(conta: str) -> str:
    """'4471203-9' -> '****203-9'. Mantém os 3 últimos dígitos e o DV."""
    raw = str(conta or "").strip()
    if not raw:
        return ""
    corpo, _, dv = raw.partition("-")
    visivel = corpo[-3:]
    sufixo = f"{visivel}-{dv}" if dv else visivel
    return "****" + sufixo


def mascarar_cnpj(cnpj: str) -> str:
    """'85.779.751/0001-00' -> '85.779.***/0001-**'. Mantém a raiz parcial e a filial."""
    d = _digits(str(cnpj or ""))
    if len(d) != 14:
        return "[CNPJ_MASCARADO]"
    return f"{d[0:2]}.{d[2:5]}.***/{d[8:12]}-**"
