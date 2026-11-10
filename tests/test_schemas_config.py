import pytest
from pydantic import ValidationError

from src.config import load_settings
from src.schemas import ExplicacaoAlerta


def test_ia_nao_tem_campo_para_liberar_ou_reter():
    campos = set(ExplicacaoAlerta.model_fields)
    assert campos == {"titulo_id", "resumo", "comunicacoes_citadas", "agravar"}
    with pytest.raises(ValidationError):
        ExplicacaoAlerta(titulo_id="TP1", resumo="explicação qualquer", liberar=True)


def test_comunicacao_citada_precisa_ser_em():
    with pytest.raises(ValidationError):
        ExplicacaoAlerta(titulo_id="TP1", resumo="explicação qualquer", comunicacoes_citadas=["FP4615"])


def test_ia_desligada_por_padrao(monkeypatch):
    monkeypatch.delenv("USE_IA", raising=False)
    monkeypatch.setenv("AI_PROXY_API_KEY", "gw-tok-x")
    assert load_settings().ia_enabled is False
    monkeypatch.setenv("USE_IA", "True")
    s = load_settings()
    assert s.ia_enabled and s.max_tokens >= 2000
