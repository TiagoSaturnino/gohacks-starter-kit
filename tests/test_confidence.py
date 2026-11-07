from src.confidence import assess_confidence, apply_confidence_policy
from src.schemas import LogAnalysisResult


def result(severity="MEDIUM", confidence=0.9, action="LOG_ONLY"):
    return LogAnalysisResult.model_validate(
        {
            "service_name": "Svc", "severity": severity, "root_cause": "c",
            "confidence_score": confidence,
            "recommended_actions": [{"action_type": action, "description": "d"}],
        }
    )


def test_no_adjustment_for_medium_log_only():
    assessment = assess_confidence(result())
    assert assessment.effective == assessment.declared == 0.9
    assert assessment.reasons == [] and not assessment.forced_review


def test_critical_and_high_have_caps():
    assert assess_confidence(result("CRITICAL", 0.99)).effective == 0.70
    assert assess_confidence(result("HIGH", 0.99)).effective == 0.80


def test_mutating_action_caps_confidence():
    assessment = assess_confidence(result("LOW", 0.99, action="REFRESH_TOKEN"))
    assert assessment.effective == 0.85
    assert any("altera sistemas" in reason for reason in assessment.reasons)


def test_escalate_human_is_not_treated_as_mutating():
    assert assess_confidence(result("LOW", 0.99, action="ESCALATE_HUMAN")).effective == 0.99


def test_penalties_for_warnings_and_repair():
    assessment = assess_confidence(result("LOW", 0.9), warnings_count=1, attempts=2)
    assert assessment.effective == 0.65  # 0.9 - 0.15 - 0.10
    assert not assessment.forced_review


def test_low_effective_confidence_forces_human_review():
    assert result("LOW", 0.7).requires_human_intervention is False  # sozinha, a nota declarada passa
    assessment = assess_confidence(result("LOW", 0.7), warnings_count=1, attempts=2)  # 0.45
    assert assessment.forced_review
    target = result("LOW", 0.7)
    apply_confidence_policy(target, assessment)
    assert target.requires_human_intervention is True
    assert target.recommended_actions[0].requires_human_approval is True


def test_effective_never_negative():
    assert assess_confidence(result("LOW", 0.1), warnings_count=3, attempts=2).effective == 0.0


def test_declared_value_is_preserved():
    item = result("CRITICAL", 0.99)
    assess_confidence(item)
    assert item.confidence_score == 0.99
