import pytest
from pydantic import ValidationError

from src.schemas import LogAnalysisResult, RecommendedAction


def build(severity="MEDIUM", confidence=0.9, actions=None):
    return LogAnalysisResult.model_validate(
        {
            "service_name": "Svc",
            "severity": severity,
            "root_cause": "causa",
            "confidence_score": confidence,
            "recommended_actions": (
                actions
                if actions is not None
                else [{"action_type": "LOG_ONLY", "description": "registrar"}]
            ),
        }
    )


def test_mutating_action_always_requires_approval_even_if_model_says_false():
    action = RecommendedAction(
        action_type="REFRESH_TOKEN", description="x", requires_human_approval=False
    )
    assert action.requires_human_approval is True


def test_log_only_low_severity_high_confidence_needs_no_approval():
    result = build(severity="LOW")
    assert result.recommended_actions[0].requires_human_approval is False
    assert result.requires_human_intervention is False


@pytest.mark.parametrize("severity", ["HIGH", "CRITICAL"])
def test_high_severity_forces_review_on_every_action(severity):
    result = build(severity=severity)
    assert result.recommended_actions[0].requires_human_approval is True
    assert result.requires_human_intervention is True


def test_low_confidence_forces_review():
    result = build(severity="LOW", confidence=0.5)
    assert result.recommended_actions[0].requires_human_approval is True
    assert result.requires_human_intervention is True


def test_global_flag_is_derived_not_trusted():
    data = {
        "service_name": "Svc", "severity": "MEDIUM", "root_cause": "c",
        "confidence_score": 0.9, "requires_human_intervention": False,
        "recommended_actions": [{"action_type": "CLEAR_CACHE", "description": "limpar"}],
    }
    assert LogAnalysisResult.model_validate(data).requires_human_intervention is True


@pytest.mark.parametrize("score", [-0.1, 1.5])
def test_confidence_bounds(score):
    with pytest.raises(ValidationError):
        build(confidence=score)


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        RecommendedAction(action_type="LOG_ONLY", description="x", surprise="y")


def test_payload_must_be_flat_and_scalar():
    with pytest.raises(ValidationError):
        RecommendedAction(action_type="LOG_ONLY", description="x", payload={"a": {"b": 1}})


def test_payload_size_limit():
    big = {f"k{i}": i for i in range(9)}
    with pytest.raises(ValidationError):
        RecommendedAction(action_type="LOG_ONLY", description="x", payload=big)


def test_at_least_one_action_required():
    with pytest.raises(ValidationError):
        build(actions=[])
    with pytest.raises(ValidationError):
        LogAnalysisResult.model_validate(
            {"service_name": "s", "severity": "LOW", "root_cause": "c",
             "confidence_score": 0.9, "recommended_actions": []}
        )
