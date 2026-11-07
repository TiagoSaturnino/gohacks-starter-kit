from src.config import load_settings


def test_defaults_are_offline_and_use_current_model(monkeypatch):
    for name in ("USE_MOCK", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "SCENARIO"):
        monkeypatch.delenv(name, raising=False)
    settings = load_settings()
    assert settings.use_mock is True
    assert settings.model == "claude-sonnet-5-5"
    assert settings.live_enabled is False


def test_live_requires_flag_off_and_real_looking_key(monkeypatch):
    monkeypatch.setenv("USE_MOCK", "False")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-sua-chave-aqui")
    assert load_settings().live_enabled is False  # placeholder
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-abc")
    assert load_settings().live_enabled is True
    monkeypatch.setenv("USE_MOCK", "True")
    assert load_settings().live_enabled is False


def test_invalid_numeric_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("MAX_BATCH_ROWS", "abc")
    assert load_settings().max_batch_rows == 50


def test_pricing_and_timezone_settings(monkeypatch):
    for name in ("PRICE_INPUT_PER_MTOK", "PRICE_OUTPUT_PER_MTOK", "APP_TIMEZONE"):
        monkeypatch.delenv(name, raising=False)
    settings = load_settings()
    assert settings.pricing_configured is False and settings.timezone == "America/Sao_Paulo"
    monkeypatch.setenv("PRICE_INPUT_PER_MTOK", "3")
    monkeypatch.setenv("PRICE_OUTPUT_PER_MTOK", "15")
    assert load_settings().pricing_configured is True
    monkeypatch.setenv("PRICE_INPUT_PER_MTOK", "abc")
    assert load_settings().pricing_configured is False
