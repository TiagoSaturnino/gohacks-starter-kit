import openai
import httpx
import pytest
from pydantic import BaseModel

from src.ia_proxy import IAProxy, extrair_json, validar_modelo
from tests.conftest import FakeHttp, FakeOpenAI


class Saida(BaseModel):
    resposta: str


def test_sem_token_nao_chama_rede(settings, breaker):
    gw = IAProxy(settings, breaker=breaker)
    r = gw.chat_json("s", "u", Saida)
    assert not r.ok and r.reason == "IA_DESLIGADA"


def test_deepseek_bloqueado():
    with pytest.raises(ValueError):
        validar_modelo("deepseek-v4-pro")


def test_extrai_json_com_cerca_e_texto():
    assert extrair_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extrair_json('Claro! {"a": 2} fim') == {"a": 2}


def test_chat_ok_registra_uso(settings, breaker):
    fake = FakeOpenAI([{"resposta": "pong"}])
    gw = IAProxy(settings, client=fake, breaker=breaker)
    r = gw.chat_json("s", "u", Saida)
    assert r.ok and r.data == {"resposta": "pong"}
    assert r.uso.input_tokens == 100 and r.uso.cost_usd is None  # chat: sem preço por chamada
    assert gw.sessao.chamadas == 1


def test_reparo_depois_de_saida_invalida(settings, breaker):
    fake = FakeOpenAI(["não é json", {"resposta": "ok"}])
    r = IAProxy(settings, client=fake, breaker=breaker).chat_json("s", "u", Saida)
    assert r.ok and r.uso.attempts == 2
    assert "Saída rejeitada" in fake.chamadas[1]["messages"][-1]["content"]


def test_guardrail_reprova_duas_vezes_vira_invalid_output(settings, breaker):
    fake = FakeOpenAI([{"resposta": "x"}, {"resposta": "x"}])
    r = IAProxy(settings, client=fake, breaker=breaker).chat_json(
        "s", "u", Saida, validar=lambda obj: ["inventou ID"]
    )
    assert not r.ok and r.reason == "INVALID_OUTPUT"


def test_falhas_abrem_o_breaker(settings, breaker):
    req = httpx.Request("POST", "https://x")
    erro = openai.APIConnectionError(request=req)
    fake = FakeOpenAI([erro, erro, {"resposta": "nunca"}])
    gw = IAProxy(settings, client=fake, breaker=breaker)
    assert gw.chat_json("s", "u", Saida).reason == "CONNECTION"
    assert gw.chat_json("s", "u", Saida).reason == "CONNECTION"
    assert gw.chat_json("s", "u", Saida).reason == "CIRCUIT_OPEN"
    assert len(fake.chamadas) == 2  # a 3a nem chegou ao IAProxy


def test_jev_ok_com_custo(settings, breaker):
    http = FakeHttp({"model": "jev-1.14.0", "answers": {"troca_conta": {"type": "noul", "noul": 0.93}},
                     "usage": {"input_tokens": 280, "output_tokens": 10, "estimated_cost_usd": 0.0000118}})
    gw = IAProxy(settings, http=http, breaker=breaker)
    r = gw.jev({"mensagem": "x"}, {"troca_conta": {"type": "noul", "instructions": "?"}})
    assert r.ok and r.data["troca_conta"]["noul"] == 0.93
    assert r.uso.cost_usd == pytest.approx(0.0000118)
    assert http.chamadas[0]["url"].endswith("/systemone")


def test_jev_429_classificado(settings, breaker):
    http = FakeHttp({"error": "cota"}, status=429)
    r = IAProxy(settings, http=http, breaker=breaker).jev({}, {})
    assert not r.ok and r.reason == "RATE_LIMIT"


def test_token_nunca_aparece_no_erro(monkeypatch, tmp_path, breaker):
    monkeypatch.setenv("USE_IA", "True")
    monkeypatch.setenv("AI_PROXY_API_KEY", "gw-tok-segredo123")
    from src.config import load_settings

    http = FakeHttp({"error": "x"}, status=401)
    r = IAProxy(load_settings(), http=http, breaker=breaker).jev({}, {})
    assert r.reason == "AUTH"
    assert "segredo123" not in (r.error_detail or "") + r.model_dump_json()
