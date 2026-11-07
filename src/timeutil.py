"""Datas e horas: grava com offset explícito, exibe no fuso local.

Decisão de projeto: o banco guarda ISO-8601 *com offset* (ex.: 2026-11-06T01:32:10-03:00).
Isso é legível para o operador (Horário de Brasília) e continua sem ambiguidade, ao
contrário de um horário "local" sem fuso, que muda de significado conforme o servidor.

No Windows o módulo `zoneinfo` precisa do pacote `tzdata` (está no requirements.txt).
Se mesmo assim o fuso não for encontrado, cai para UTC-3 fixo (o Brasil não tem horário
de verão desde 2019).
"""
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TZ = "America/Sao_Paulo"
BRT_FIXED = timezone(timedelta(hours=-3), "BRT")


def get_tz(name: str = DEFAULT_TZ) -> tzinfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return BRT_FIXED


def now_local(name: str = DEFAULT_TZ) -> datetime:
    return datetime.now(get_tz(name))


def iso_now(name: str = DEFAULT_TZ) -> str:
    return now_local(name).isoformat(timespec="seconds")


def format_local(value: Optional[str], name: str = DEFAULT_TZ, fmt: str = "%d/%m/%Y %H:%M:%S") -> str:
    """Converte um ISO-8601 (com ou sem offset) para o fuso local de exibição."""
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    tz = get_tz(name)
    if parsed.tzinfo is None:  # linhas antigas, gravadas sem fuso
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz).strftime(fmt)
