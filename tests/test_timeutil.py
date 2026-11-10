from datetime import datetime, timedelta, timezone

from src.timeutil import BRT_FIXED, format_local, get_tz, iso_now


def test_iso_now_has_brasilia_offset():
    assert iso_now("America/Sao_Paulo").endswith("-03:00")


def test_unknown_timezone_falls_back_to_fixed_brt():
    assert get_tz("Nao/Existe") is BRT_FIXED
    assert iso_now("Nao/Existe").endswith("-03:00")


def test_format_local_converts_utc_rows_to_brasilia():
    assert format_local("2026-11-06T04:32:10+00:00", "America/Sao_Paulo") == "06/11/2026 01:32:10"


def test_format_local_keeps_offset_aware_local_values():
    assert format_local("2026-11-06T01:32:10-03:00") == "06/11/2026 01:32:10"


def test_format_local_handles_legacy_naive_and_garbage():
    assert format_local("2026-11-06 01:32:10") == "06/11/2026 01:32:10"
    assert format_local("nao-e-data") == "nao-e-data"
    assert format_local(None) == ""
