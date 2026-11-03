from src.config import Settings

def make_settings(**kwargs) -> Settings:
    vals = dict(use_mock=True, api_key="", model="claude-sonnet-5-5", timeout_s=5.0, max_retries=0, max_tokens=1500, max_batch_rows=50, scenario="ecommerce", executor="dry_run", audit_db=":memory:", manual_minutes_per_incident=25)
    vals.update(kwargs)
    return Settings(**vals)