from helpers import ML_LOG, valid_analysis
from src.guardrails import check_result, extract_identifiers, ungrounded_identifiers
from src.schemas import ActionType, LogAnalysisResult

ALL = set(ActionType)


def test_extract_identifiers_keeps_only_id_like_tokens():
    tokens = extract_identifiers("seller 8849201 item MLB1298491 /oauth/token retry 3 order GC-88310")
    assert "8849201" in tokens and "MLB1298491" in tokens and "GC-88310" in tokens
    assert "3" not in tokens and "/oauth/token" not in tokens


def test_ungrounded_identifiers_detects_invented_ids():
    assert ungrounded_identifiers(["seller 8849201"], ML_LOG) == []
    assert ungrounded_identifiers(["seller 9999999"], ML_LOG) == ["9999999"]


def test_grounded_result_passes():
    result = LogAnalysisResult.model_validate(valid_analysis())
    report = check_result(result, ALL, ML_LOG)
    assert report.ok and not report.warnings


def test_invented_payload_value_is_a_violation():
    data = valid_analysis()
    data["recommended_actions"][0]["payload"] = {"seller_id": "1234567"}
    report = check_result(LogAnalysisResult.model_validate(data), ALL, ML_LOG)
    assert not report.ok
    assert "1234567" in report.violations[0]


def test_invented_id_in_narrative_is_only_a_warning():
    data = valid_analysis(root_cause="Falha no pedido 7777777 do seller 8849201.")
    report = check_result(LogAnalysisResult.model_validate(data), ALL, ML_LOG)
    assert report.ok
    assert "7777777" in report.warnings[0]


def test_action_outside_allowed_set_is_a_violation():
    result = LogAnalysisResult.model_validate(valid_analysis())
    report = check_result(result, {ActionType.LOG_ONLY}, ML_LOG)
    assert not report.ok
    assert "REFRESH_TOKEN" in report.violations[0]
