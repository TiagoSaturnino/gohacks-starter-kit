from types import SimpleNamespace

import anthropic

from helpers import ML_LOG, FakeClient, make_settings, tool_response, valid_analysis
from src.analyzer import analyze_log, build_system_prompt, build_tool, estimate_cost
from src.resilience import CircuitBreaker
from src.schemas import ActionType


def status_error(cls, status):
    response = SimpleNamespace(
        status_code=status, headers={}, request=SimpleNamespace(method="POST", url="x")
    )
    return cls("erro simulado", response=response, body=None)


def run(client, log=ML_LOG, scenario=None, breaker=None, **settings):
    return analyze_log(
        log, scenario=scenario, settings=make_settings(**settings), client=client,
        breaker=breaker or CircuitBreaker(failure_threshold=99),
    )


# ---------------------------------------------------------------- modo offline
def test_offline_mode_replays_known_fixture(scenario):
    fixture = scenario.fixtures[0]
    outcome = analyze_log(fixture.log, scenario=scenario, settings=make_settings(use_mock=True))
    assert outcome.source == "MOCK"
    assert outcome.result.service_name == fixture.analysis.service_name


def test_offline_mode_never_invents_a_diagnosis(scenario):
    outcome = analyze_log("erro desconhecido 500", scenario=scenario, settings=make_settings(use_mock=True))
    assert outcome.source == "DEGRADED"
    assert outcome.result is None
    assert "fixture" in outcome.error


def test_placeholder_key_stays_offline(scenario):
    settings = make_settings(use_mock=False, api_key="sk-ant-sua-chave-aqui")
    outcome = analyze_log("qualquer coisa", scenario=scenario, settings=settings)
    assert outcome.source == "DEGRADED" and "fixture" in outcome.error


def test_empty_log_is_degraded(scenario):
    assert run(FakeClient(), log="   ", scenario=scenario).source == "DEGRADED"


# ---------------------------------------------------------------- modo live
def test_live_success_reports_measured_metadata(scenario):
    client = FakeClient(tool_response(valid_analysis(), input_tokens=120, output_tokens=60))
    outcome = run(client, scenario=scenario)
    assert outcome.source == "LIVE"
    assert outcome.meta.input_tokens == 120 and outcome.meta.output_tokens == 60
    assert outcome.meta.attempts == 1 and outcome.meta.model == "claude-sonnet-5-5"
    call = client.messages.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": "submit_log_analysis"}
    assert call["model"] == "claude-sonnet-5-5"


def test_pii_is_masked_before_reaching_the_model(scenario):
    log = (
        'Webhook failed. Payload: {"order_id": "GC-88310", "customer": '
        '{"name": "Maria Silva", "email": "carlos@example.com", "cpf": "123.456.789-09"}}'
    )
    analysis = valid_analysis(
        root_cause="Webhook do pedido GC-88310 falhou.",
        recommended_actions=[
            {"action_type": "ESCALATE_HUMAN", "description": "Escalar GC-88310.",
             "payload": {"order_id": "GC-88310"}}
        ],
    )
    client = FakeClient(tool_response(analysis))
    outcome = run(client, log=log, scenario=scenario)
    sent = client.messages.calls[0]["messages"][0]["content"]
    for secret in ("carlos@example.com", "123.456.789-09", "Maria Silva"):
        assert secret not in sent
    assert "[EMAIL_MASCARADO]" in sent and "[CPF_MASCARADO]" in sent
    assert outcome.meta.pii_masked == {"EMAIL": 1, "CPF": 1, "NOME": 1}


def test_model_cannot_waive_human_approval(scenario):
    analysis = valid_analysis(severity="MEDIUM", requires_human_intervention=False)
    analysis["recommended_actions"][0]["requires_human_approval"] = False
    outcome = run(FakeClient(tool_response(analysis)), scenario=scenario)
    assert outcome.source == "LIVE"
    assert outcome.result.requires_human_intervention is True
    assert outcome.result.recommended_actions[0].requires_human_approval is True


def test_tool_schema_hides_system_decided_fields_and_limits_actions(scenario):
    restricted = scenario.model_copy(
        update={"allowed_actions": [ActionType.LOG_ONLY, ActionType.ESCALATE_HUMAN]}
    )
    schema = build_tool(restricted)["input_schema"]
    assert "requires_human_intervention" not in schema["properties"]
    assert "requires_human_approval" not in schema["$defs"]["RecommendedAction"]["properties"]
    assert schema["$defs"]["ActionType"]["enum"] == ["LOG_ONLY", "ESCALATE_HUMAN"]


def test_system_prompt_carries_scenario_context(scenario):
    prompt = build_system_prompt(scenario)
    assert scenario.domain in prompt
    assert "CRITICAL" in prompt and "REFRESH_TOKEN" in prompt
    assert "não confiável" in prompt  # defesa contra prompt injection no log


def test_warning_for_unsupported_identifier_in_narrative(scenario):
    analysis = valid_analysis(root_cause="Falha do seller 8849201 no pedido 7777777.")
    outcome = run(FakeClient(tool_response(analysis)), scenario=scenario)
    assert outcome.source == "LIVE"
    assert "7777777" in outcome.warnings[0]


# ---------------------------------------------------------------- reparo e falhas
def test_one_repair_attempt_after_invalid_output(scenario):
    client = FakeClient(
        tool_response(valid_analysis(confidence_score=5), input_tokens=100, output_tokens=40),
        tool_response(valid_analysis(), input_tokens=130, output_tokens=60, tool_id="toolu_2"),
    )
    outcome = run(client, scenario=scenario)
    assert outcome.source == "LIVE" and outcome.meta.attempts == 2
    assert outcome.meta.input_tokens == 230 and outcome.meta.output_tokens == 100
    repair = client.messages.calls[1]["messages"][-1]["content"][0]
    assert repair["type"] == "tool_result" and repair["is_error"] is True
    assert repair["tool_use_id"] == "toolu_1"
    assert "confidence_score" in repair["content"]


def test_gives_up_after_second_invalid_output(scenario):
    bad = valid_analysis(confidence_score=5)
    outcome = run(FakeClient(tool_response(bad), tool_response(bad)), scenario=scenario)
    assert outcome.source == "DEGRADED" and outcome.result is None
    assert "rejeitada" in outcome.error


def test_invented_payload_id_triggers_repair_then_degrades(scenario):
    invented = valid_analysis()
    invented["recommended_actions"][0]["payload"] = {"seller_id": "1234567"}
    repaired = run(FakeClient(tool_response(invented), tool_response(valid_analysis())), scenario=scenario)
    assert repaired.source == "LIVE"
    persistent = run(FakeClient(tool_response(invented), tool_response(invented)), scenario=scenario)
    assert persistent.source == "DEGRADED" and "1234567" in persistent.error


def test_action_outside_scenario_is_rejected(scenario):
    restricted = scenario.model_copy(update={"allowed_actions": [ActionType.LOG_ONLY]})
    outcome = run(FakeClient(tool_response(valid_analysis()), tool_response(valid_analysis())), scenario=restricted)
    assert outcome.source == "DEGRADED" and "REFRESH_TOKEN" in outcome.error


def test_truncated_response_is_degraded(scenario):
    outcome = run(FakeClient(tool_response(valid_analysis(), stop_reason="max_tokens")), scenario=scenario)
    assert outcome.source == "DEGRADED" and "truncada" in outcome.error


def test_missing_tool_call_is_degraded(scenario):
    text_only = SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text="oi")],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    outcome = run(FakeClient(text_only), scenario=scenario)
    assert outcome.source == "DEGRADED" and "ferramenta" in outcome.error


def test_api_error_is_degraded_without_fake_diagnosis(scenario):
    # O objeto `request` não é usado pelo nosso código; evita depender da lib HTTP do SDK.
    error = anthropic.APIConnectionError(
        request=SimpleNamespace(method="POST", url="https://api.anthropic.com/v1/messages")
    )
    outcome = run(FakeClient(error), scenario=scenario)
    assert outcome.source == "DEGRADED" and outcome.result is None
    assert outcome.reason == "CONNECTION" and "Anthropic" in outcome.error


def test_unexpected_exception_is_degraded(scenario):
    outcome = run(FakeClient(RuntimeError("boom")), scenario=scenario)
    assert outcome.source == "DEGRADED" and outcome.reason == "UNEXPECTED"
    assert "boom" in outcome.error_detail


# ---------------------------------------------------------------- v3: falhas classificadas
def test_rate_limit_is_classified_and_blocks_automation(scenario):
    outcome = run(FakeClient(status_error(anthropic.RateLimitError, 429)), scenario=scenario)
    assert outcome.reason == "RATE_LIMIT" and "429" in outcome.error
    assert outcome.automation_blocked and outcome.result is None


def test_timeout_is_classified_with_configured_limit(scenario):
    error = anthropic.APITimeoutError(request=SimpleNamespace(method="POST", url="x"))
    outcome = run(FakeClient(error), scenario=scenario, timeout_s=7.0)
    assert outcome.reason == "TIMEOUT" and "7s" in outcome.error


def test_auth_and_server_errors_are_classified(scenario):
    auth = run(FakeClient(status_error(anthropic.AuthenticationError, 401)), scenario=scenario)
    assert auth.reason == "AUTH" and "ANTHROPIC_API_KEY" in auth.error
    server = run(FakeClient(status_error(anthropic.InternalServerError, 500)), scenario=scenario)
    assert server.reason == "API_ERROR" and "500" in server.error


def test_degraded_never_exposes_raw_exception_in_user_message(scenario):
    outcome = run(FakeClient(status_error(anthropic.RateLimitError, 429)), scenario=scenario)
    assert "erro simulado" not in outcome.error  # detalhe técnico fica em error_detail
    assert "erro simulado" in outcome.error_detail


# ---------------------------------------------------------------- v3: circuit breaker
def test_circuit_breaker_opens_and_skips_the_api(scenario):
    breaker = CircuitBreaker(failure_threshold=2, reset_after_s=60)
    failing = lambda: FakeClient(status_error(anthropic.RateLimitError, 429))
    for _ in range(2):
        assert run(failing(), scenario=scenario, breaker=breaker).reason == "RATE_LIMIT"
    untouched = FakeClient(tool_response(valid_analysis()))
    outcome = run(untouched, scenario=scenario, breaker=breaker)
    assert outcome.reason == "CIRCUIT_OPEN" and "Circuit breaker" in outcome.error
    assert untouched.messages.calls == []  # falhou rápido, sem chamar a API


def test_successful_call_resets_failure_count(scenario):
    breaker = CircuitBreaker(failure_threshold=2)
    run(FakeClient(status_error(anthropic.RateLimitError, 429)), scenario=scenario, breaker=breaker)
    run(FakeClient(tool_response(valid_analysis())), scenario=scenario, breaker=breaker)
    run(FakeClient(status_error(anthropic.RateLimitError, 429)), scenario=scenario, breaker=breaker)
    assert breaker.state == "CLOSED"


def test_invalid_model_output_does_not_trip_the_breaker(scenario):
    breaker = CircuitBreaker(failure_threshold=1)
    bad = valid_analysis(confidence_score=5)
    run(FakeClient(tool_response(bad), tool_response(bad)), scenario=scenario, breaker=breaker)
    assert breaker.state == "CLOSED"


# ---------------------------------------------------------------- v3: confiança efetiva e custo
def test_live_outcome_reports_declared_and_effective_confidence(scenario):
    outcome = run(FakeClient(tool_response(valid_analysis(confidence_score=0.95))), scenario=scenario)
    assert outcome.confidence.declared == 0.95
    assert outcome.confidence.effective == 0.80  # severidade HIGH limita a 80%
    assert outcome.confidence.reasons


def test_repair_and_warning_lower_effective_confidence_and_force_review(scenario):
    client = FakeClient(
        tool_response(valid_analysis(confidence_score=5)),
        tool_response(valid_analysis(severity="MEDIUM", confidence_score=0.7,
                                     root_cause="Falha do seller 8849201 no pedido 7777777.")),
    )
    outcome = run(client, scenario=scenario)
    assert outcome.source == "LIVE" and outcome.meta.attempts == 2
    # declarada 0.70 (sem teto aplicável) - 0.15 (id sem lastro) - 0.10 (reparo) = 0.45 < 0.6
    assert outcome.confidence.effective == 0.45
    assert outcome.confidence.forced_review is True
    assert outcome.result.requires_human_intervention is True


def test_cost_is_estimated_only_when_prices_are_configured(scenario):
    free = run(FakeClient(tool_response(valid_analysis(), input_tokens=1000, output_tokens=500)), scenario=scenario)
    assert free.meta.cost_usd is None
    priced = run(
        FakeClient(tool_response(valid_analysis(), input_tokens=1_000_000, output_tokens=500_000)),
        scenario=scenario, price_input_per_mtok=3.0, price_output_per_mtok=15.0,
    )
    assert priced.meta.cost_usd == 10.5  # 3.0 + 0.5 * 15.0


def test_estimate_cost_helper():
    settings = make_settings(price_input_per_mtok=2.0, price_output_per_mtok=10.0)
    assert estimate_cost(500_000, 100_000, settings) == 2.0
    assert estimate_cost(1, 1, make_settings()) is None


def test_offline_replay_also_gets_effective_confidence(scenario):
    fixture = scenario.fixtures[0]  # HIGH, 0.95 declarado
    outcome = analyze_log(fixture.log, scenario=scenario, settings=make_settings(use_mock=True))
    assert outcome.confidence.declared == 0.95 and outcome.confidence.effective == 0.80
