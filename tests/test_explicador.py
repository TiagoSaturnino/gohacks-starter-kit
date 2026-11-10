from src.explicador import Explicador, texto_modelo
from src.gateway import IAProxy
from src.schemas import Alerta, Severidade
from tests.conftest import FakeHttp, FakeOpenAI

ALERTA = Alerta(
    titulo_id="TP431502", regra="R5_conta_alterada", severidade=Severidade.BLOQUEIA,
    evidencia="FP4615 trocou banco/conta em 2026-10-30 via email sem confirmação; destino ted 905/0001/4471203-9",
    valor=1180.86,
)
COMUNICACAO = {"comunicacao_id": "EM570999", "data_hora": "2026-10-30 10:00", "canal": "email",
               "remetente": "financeiro@x.com", "assunto": "Nova conta",
               "texto": "Mudamos de banco, pague na conta 4471203-9. Urgente!", "anexos": ""}


def _expl(cache, respostas=None, http=None, settings=None, breaker=None):
    client = FakeOpenAI(respostas) if respostas is not None else None
    gw = IAProxy(settings, client=client, http=http, breaker=breaker)
    return Explicador(gw, str(cache)), client


def test_sem_ia_usa_texto_modelo_e_nao_muda_nada(settings, breaker, tmp_path):
    exp, _ = _expl(tmp_path, settings=settings, breaker=breaker)
    r = exp.explicar(ALERTA, [COMUNICACAO])
    assert r.origem == "MODELO" and r.motivo == "IA_DESLIGADA"
    assert r.explicacao == texto_modelo(ALERTA)


def test_live_grava_cache_e_segunda_vez_nao_chama(settings, breaker, tmp_path):
    ok = {"titulo_id": "TP431502", "resumo": "Conta trocada em 2026-10-30 por e-mail, sem ligação de confirmação.",
          "comunicacoes_citadas": ["EM570999"], "agravar": True}
    exp, client = _expl(tmp_path, [ok], settings=settings, breaker=breaker)
    assert exp.explicar(ALERTA, [COMUNICACAO]).origem == "LIVE"
    r2 = exp.explicar(ALERTA, [COMUNICACAO])
    assert r2.origem == "CACHE" and r2.explicacao.agravar is True
    assert len(client.chamadas) == 1


def test_pii_nao_vai_ao_modelo(settings, breaker, tmp_path):
    ok = {"titulo_id": "TP431502", "resumo": "Conta trocada sem confirmação válida.", "agravar": False}
    exp, client = _expl(tmp_path, [ok], settings=settings, breaker=breaker)
    exp.explicar(ALERTA, [COMUNICACAO])
    enviado = client.chamadas[0]["messages"][1]["content"]
    assert "4471203-9" not in enviado and "financeiro@x.com" not in enviado


def test_id_inventado_cai_no_texto_modelo(settings, breaker, tmp_path):
    ruim = {"titulo_id": "TP431502", "resumo": "Igual ao TP999999 pago ontem.", "agravar": True}
    exp, _ = _expl(tmp_path, [ruim, ruim], settings=settings, breaker=breaker)
    r = exp.explicar(ALERTA, [COMUNICACAO])
    assert r.origem == "MODELO" and r.motivo == "INVALID_OUTPUT"
    assert r.explicacao.agravar is False  # IA reprovada não agrava nada


def test_jev_sinal_troca_conta(settings, breaker, tmp_path):
    http = FakeHttp({"answers": {"troca_conta": {"type": "noul", "noul": 0.91}}, "usage": {"input_tokens": 50}})
    exp, _ = _expl(tmp_path, http=http, settings=settings, breaker=breaker)
    assert exp.sinal_troca_conta(COMUNICACAO) == 0.91
    assert "4471203-9" not in str(http.chamadas[0]["json"])


def test_jev_sem_ia_devolve_none(settings, breaker, tmp_path):
    exp, _ = _expl(tmp_path, settings=settings, breaker=breaker)
    assert exp.sinal_troca_conta(COMUNICACAO) is None


def test_markdown_bloqueia_tem_estrutura_e_acao_curta():
    from src.explicador import MAX_LINHAS_ACAO, ResultadoExplicacao, formatar_markdown
    r = ResultadoExplicacao(origem="MODELO", explicacao=texto_modelo(ALERTA))
    md = formatar_markdown(ALERTA, r)
    assert md.startswith("### 🔴 TP431502")
    assert "**Regra:** `R5_conta_alterada`" in md and "**Por que:**" in md
    acao = md.split("**Ação recomendada**\n")[1].splitlines()
    assert 1 <= len(acao) <= MAX_LINHAS_ACAO and all(l.startswith("- ") for l in acao)


def test_markdown_escapa_cifrao_e_achata_quebras():
    from src.explicador import ResultadoExplicacao, formatar_markdown
    from src.schemas import ExplicacaoAlerta
    e = ExplicacaoAlerta(titulo_id="TP431502", resumo="Conta trocada.\n\n# Pague R$ 10 ou R$ 20", comunicacoes_citadas=["EM570999"], agravar=True)
    md = formatar_markdown(ALERTA, ResultadoExplicacao(origem="LIVE", explicacao=e))
    assert "\\$" in md and "R$ 1.180,86".replace("$", "\\$") in md
    assert "\n# Pague" not in md and "`EM570999`" in md and "Agravante" in md


def test_markdown_liberado_usa_verde():
    from src.explicador import ResultadoExplicacao, formatar_markdown
    r = ResultadoExplicacao(origem="CACHE", explicacao=texto_modelo(ALERTA))
    assert formatar_markdown(ALERTA, r, liberado=True).startswith("### 🟢")
