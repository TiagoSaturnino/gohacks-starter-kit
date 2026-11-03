import pytest
from src.schemas import LogAnalysisResult, RecommendedAction

def test_mutating_action_requires_approval():
    action = RecommendedAction(action_type="REFRESH_TOKEN", description="x", requires_human_approval=False)
    assert action.requires_human_approval is True