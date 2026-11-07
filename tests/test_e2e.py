"""Teste ponta a ponta (SPEC §11): planta uma fraude nova nos dados reais e prova que o
guardião pega só o que deve. Sem ID fixo: o fornecedor e o lote são escolhidos por código."""
import shutil
from pathlib import Path

import pandas as pd
import pytest

from src.carga import carregar, politica
from src.checks import rodar_regras
from src.guardiao import MOTIVO_SILENCIO, executar
from src.store import Store

DADOS_REAIS = Path(__file__).resolve().parents[1] / "pagamentos"
AGORA = "2026-11-02T08:00"
pytestmark = pytest.mark.skipif(not DADOS_REAIS.exists(), reason="dados do desafio fora do git")


def _ler(pasta, nome):
    return pd.read_csv(pasta / f"{nome}.csv", dtype=str, keep_default_na=False)


def _gravar(df, pasta, nome):
    df.to_csv(pasta / f"{nome}.csv", index=False, encoding="utf-8")


def _rodar(dados, tmp, nome):
    saida = tmp / nome
    r = executar(AGORA, sem_ia=True, sem_slack=True, dados=str(dados), db=str(saida / "g.db"),
                 saida=str(saida))
    return r, saida


def _escolher_fornecedor(dados):
    """Fornecedor ativo, com título ted no primeiro lote a fechar e nenhum alerta."""
    base, pol = carregar(dados), politica(dados)
    pol.setdefault("guardiao", {})["dias_lancador_recente"] = 45
    alertas = rodar_regras(base.agenda, base, pol)
    com_alerta = set(base.agenda[base.agenda["titulo_id"].isin(alertas["titulo_id"])]["fornecedor_id"])
    ag = base.agenda.sort_values(["data_prevista", "horario_fechamento", "lote_remessa"])
    primeiro_lote = ag["lote_remessa"].iloc[0]
    ativos = set(base.fornecedores[base.fornecedores["status_cadastro"].str.lower() == "ativo"]["fornecedor_id"])
    candidatos = ag[(ag["lote_remessa"] == primeiro_lote) & (ag["forma_pagamento"] == "ted")
                    & ag["fornecedor_id"].isin(ativos) & ~ag["fornecedor_id"].isin(com_alerta)]
    assert not candidatos.empty, "nenhum fornecedor limpo com ted no primeiro lote"
    return sorted(candidatos["fornecedor_id"])[0]


def _plantar_fraude(dados, forn):
    fornecedores = _ler(dados, "fornecedores")
    i = fornecedores.index[fornecedores["fornecedor_id"] == forn][0]
    antigo = fornecedores.loc[i, ["banco", "agencia", "conta", "telefone_cadastrado"]].to_dict()
    novo = {"banco": "999", "agencia": "0001", "conta": "7777777-1"}
    tel_novo = "(11) 98888-0001"
    for campo, valor in novo.items():
        fornecedores.loc[i, campo] = valor
    fornecedores.loc[i, "telefone_cadastrado"] = tel_novo
    _gravar(fornecedores, dados, "fornecedores")

    alt = _ler(dados, "alteracoes_cadastro")
    proximo = max(int(x[2:]) for x in alt["alteracao_id"] if x[2:].isdigit()) + 1
    base_linha = {c: "" for c in alt.columns}
    linhas = [dict(base_linha, alteracao_id=f"AC{proximo}", data_hora="2026-10-26 09:00:00",
                   fornecedor_id=forn, campo="telefone", valor_anterior=antigo["telefone_cadastrado"],
                   valor_novo=tel_novo, alterado_por="cadastro_compras", origem="email",
                   confirmacao_registrada="não", confirmacao_como="")]
    for k, campo in enumerate(["banco", "agencia", "conta"], start=1):
        linhas.append(dict(base_linha, alteracao_id=f"AC{proximo + k}", data_hora="2026-10-31 10:00:00",
                           fornecedor_id=forn, campo=campo, valor_anterior=antigo[campo],
                           valor_novo=novo[campo], alterado_por="cadastro_compras", origem="email",
                           confirmacao_registrada="sim",
                           confirmacao_como=f"ligação para {tel_novo} confirmando a troca"))
    _gravar(pd.concat([alt, pd.DataFrame(linhas)], ignore_index=True), dados, "alteracoes_cadastro")

    agenda = _ler(dados, "agenda_remessa")
    ted = (agenda["fornecedor_id"] == forn) & (agenda["forma_pagamento"] == "ted")
    agenda.loc[ted, "destino_pagamento"] = f"ted {novo['banco']}/{novo['agencia']}/{novo['conta']}"
    _gravar(agenda, dados, "agenda_remessa")
    return set(agenda.loc[ted, "titulo_id"]), set(agenda.loc[(agenda["fornecedor_id"] == forn) & ~ted, "titulo_id"])


def test_guardiao_pega_a_fraude_plantada_e_so_ela(tmp_path):
    dados = tmp_path / "pagamentos"
    shutil.copytree(DADOS_REAIS, dados)

    limpo, _ = _rodar(dados, tmp_path, "limpo")
    forn = _escolher_fornecedor(dados)
    ted, outros = _plantar_fraude(dados, forn)
    assert ted

    r, saida = _rodar(dados, tmp_path, "fraude")
    agenda = pd.read_csv(saida / "agenda_simulada.csv", dtype=str, keep_default_na=False)
    status = agenda.set_index("titulo_id")["status_guardiao"]
    store = Store(str(saida / "g.db"))
    alertas = pd.DataFrame(store.alertas(10000))

    # 1. todos os ted do fornecedor retidos, por R5, citando a troca de telefone recente
    assert all(status[t] == "retido" for t in ted)
    r5 = alertas[alertas["regra"].str.startswith("R5") & alertas["titulo_id"].isin(ted)]
    assert set(r5["titulo_id"]) == ted
    assert r5["evidencia"].str.contains("telefone trocado").all()

    # 2. boletos (e outras formas) do fornecedor não são retidos por R5
    assert not (alertas["regra"].str.startswith("R5") & alertas["titulo_id"].isin(outros)).any()

    # 3. nenhum efeito colateral: retidos = rodada limpa ∪ plantados
    assert set(r["retidos_ids"]) == set(limpo["retidos_ids"]) | ted

    # 4. todo retido tem BLOQUEIA; nada retido só com ACIONA/AGRAVA
    bloq = set(alertas[alertas["severidade"] == "BLOQUEIA"]["titulo_id"])
    retidos = set(status[status == "retido"].index)
    assert retidos <= bloq

    # 5. SQLite: alerta, acionamento e ação "sem decisão humana" para cada retido
    acion = pd.DataFrame(store.acionamentos(10000))
    acionados = {t for js in acion["titulos_json"] for t in pd.read_json(pd.io.common.StringIO(js), typ="series")}
    decisoes = pd.DataFrame(store.decisoes(10000))
    silencio = set(decisoes[decisoes["motivo"].str.startswith(MOTIVO_SILENCIO)]["titulo_id"])
    for t in retidos:
        assert t in set(alertas["titulo_id"]) and t in acionados and t in silencio

    # 6. agrupamento: acionamentos ≤ pessoas distintas × lotes com alerta
    assert len(acion) <= acion["pessoa_id"].nunique() * acion["lote_remessa"].nunique()


def test_rodar_duas_vezes_nao_duplica_auditoria(tmp_path):
    dados = tmp_path / "pagamentos"
    shutil.copytree(DADOS_REAIS, dados)
    _, saida = _rodar(dados, tmp_path, "x")
    s = Store(str(saida / "g.db"))
    antes = (len(s.alertas(10000)), len(s.acionamentos(10000)), len(s.decisoes(10000)))
    _rodar(dados, tmp_path, "x")
    assert (len(s.alertas(10000)), len(s.acionamentos(10000)), len(s.decisoes(10000))) == antes
