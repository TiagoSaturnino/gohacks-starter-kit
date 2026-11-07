import json

import pytest
from pydantic import ValidationError

from helpers import make_settings
from src.analyzer import analyze_log
from src.guardrails import check_result
from src.scenarios import ScenarioNotFoundError, list_scenarios, load_scenario


def test_ecommerce_scenario_loads(scenario):
    assert scenario.id == "ecommerce"
    assert len(scenario.fixtures) >= 3
    assert "ecommerce" in list_scenarios()


def test_every_fixture_is_grounded_in_its_own_log(scenario):
    for fixture in scenario.fixtures:
        report = check_result(fixture.analysis, scenario.allowed_actions, fixture.log)
        assert report.ok, (fixture.key, report.violations)
        assert not report.warnings, (fixture.key, report.warnings)


def test_find_fixture_ignores_whitespace_differences(scenario):
    fixture = scenario.fixtures[0]
    indented = "\n".join("    " + line for line in fixture.log.splitlines()) + "\n"
    assert scenario.find_fixture(indented) is fixture
    assert scenario.find_fixture("outro log qualquer") is None


def test_missing_scenario_raises_clear_error(tmp_path):
    with pytest.raises(ScenarioNotFoundError) as error:
        load_scenario("nao_existe", directory=tmp_path)
    assert "nao_existe" in str(error.value)


def _custom_scenario(tmp_path, **overrides):
    data = {
        "id": "ti_ops",
        "name": "TI Ops (teste)",
        "domain": "Estações Windows e impressoras de uma unidade.",
        "severity_rubric": {"HIGH": "Usuário sem impressão", "LOW": "Aviso"},
        "allowed_actions": ["ESCALATE_HUMAN", "LOG_ONLY"],
        "fixtures": [
            {
                "key": "spooler_down",
                "log": "Spooler stopped on host PC-4432",
                "analysis": {
                    "service_name": "Print Spooler",
                    "severity": "HIGH",
                    "root_cause": "O serviço Spooler parou no host PC-4432.",
                    "confidence_score": 0.9,
                    "recommended_actions": [
                        {
                            "action_type": "ESCALATE_HUMAN",
                            "description": "Acionar o suporte N2 para o host PC-4432.",
                            "payload": {"host": "PC-4432"},
                        }
                    ],
                },
            }
        ],
    }
    data.update(overrides)
    path = tmp_path / "ti_ops.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8-sig")  # com BOM
    return path


def test_new_pain_is_just_a_new_json_file(tmp_path):
    _custom_scenario(tmp_path)
    scenario = load_scenario("ti_ops", directory=tmp_path)  # tolera BOM
    outcome = analyze_log(
        "Spooler stopped on host PC-4432", scenario=scenario,
        settings=make_settings(use_mock=True),
    )
    assert outcome.source == "MOCK"
    assert outcome.result.service_name == "Print Spooler"


def test_fixture_with_action_outside_allowed_list_is_rejected(tmp_path):
    _custom_scenario(tmp_path, allowed_actions=["LOG_ONLY"])
    with pytest.raises(ValidationError):
        load_scenario("ti_ops", directory=tmp_path)
