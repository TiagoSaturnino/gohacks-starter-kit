"""CLI do guardião: python -m src.guardiao --agora 2026-11-02T08:00 [--sem-ia] [--sem-slack]

Fluxo (SPEC §5): carga → regras → prioridade → roteamento (uma mensagem por pessoa por lote)
→ auditoria SQLite → retenção dos BLOQUEIA sem decisão humana ("sem decisão humana") →
explicação (IA via gateway, opcional) → placar. Rodar de novo não duplica nada.
Decisões humanas (Slack ou fila CLI) sempre prevalecem sobre a regra.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List, Optional

import pandas as pd

from src import relatorio
from src.carga import carregar, politica
from src.checks import rodar_regras
from src.config import load_settings
from src.executors import AgendaSimuladaExecutor
from src.priorizar import priorizar
from src.rotear import agrupar, pessoas_para
from src.schemas import Acao, Alerta, Decisao, OrigemDecisao, Severidade
from src.store import Store

ORIGENS_HUMANAS = {OrigemDecisao.SLACK.value, OrigemDecisao.CLI.value}
MOTIVO_SILENCIO = "sem decisão humana"
N_EXPLICACOES = 3


def _explicar(top: pd.DataFrame, alertas: pd.DataFrame, settings, store: Store) -> List[str]:
    """Explicação dos títulos mais urgentes. Sem IA ou com falha: cache ou texto-modelo."""
    from src.explicador import Explicador
    from src.gateway import IAProxy

    explicador = Explicador(IAProxy(settings), settings.cache_dir, store)
    saida = []
    for tid in top["titulo_id"]:
        linha = alertas[(alertas["titulo_id"] == tid)
                        & (alertas["severidade"] == Severidade.BLOQUEIA.value)].iloc[0]
        r = explicador.explicar(Alerta.model_validate(linha.to_dict()))
        saida.append(f"{tid} [{r.origem}]: {r.explicacao.resumo}")
    return saida


def executar(agora: str, sem_ia: bool = True, sem_slack: bool = True, dados: Optional[str] = None,
             db: str = "data/guardiao.db", saida: str = "data") -> dict:
    settings = load_settings()
    dados = dados or settings.dados_dir
    base, pol = carregar(dados), politica(dados)
    pol.setdefault("guardiao", {})["dias_lancador_recente"] = settings.dias_lancador_recente
    alertas = rodar_regras(base.agenda, base, pol)
    agora_ts = pd.Timestamp(agora)

    store = Store(db)
    lote_de = base.agenda.drop_duplicates("titulo_id").set_index("titulo_id")["lote_remessa"].to_dict()
    store.record_alertas([Alerta.model_validate(r) for r in alertas.to_dict("records")], agora, lote_de)

    prio = priorizar(alertas, base.agenda, agora_ts)
    categoria = base.fornecedores.drop_duplicates("fornecedor_id").set_index("fornecedor_id")["categoria"]
    forn_de = base.agenda.drop_duplicates("titulo_id").set_index("titulo_id")["fornecedor_id"]

    # Roteamento: BLOQUEIA e ACIONA acionam gente; AGRAVA (A1) só reordena.
    acionaveis = prio[prio["severidade"].isin([Severidade.BLOQUEIA.value, Severidade.ACIONA.value])]
    itens = [(t.titulo_id, t.lote_remessa,
              pessoas_para(t.regras, str(categoria.get(forn_de.get(t.titulo_id), "")), t.valor, pol))
             for t in acionaveis.itertuples(index=False)]
    canal = "cli" if sem_slack else "slack"
    for (pessoa, lote), titulos in agrupar(itens).items():
        store.record_acionamento(pessoa, lote, titulos, canal,
                                 f"{len(titulos)} título(s) com alerta no lote {lote}", agora)
    n_acionamentos = len({(a["pessoa_id"], a["lote_remessa"]) for a in store.acionamentos(10000)})

    # Retenção: BLOQUEIA sem decisão humana fica retido (fail-safe).
    saida_dir = Path(saida)
    executor = AgendaSimuladaExecutor(str(Path(dados) / "agenda_remessa.csv"),
                                      str(saida_dir / "agenda_simulada.csv"))
    retidos, liberados_humano = [], []
    for t in prio[prio["severidade"] == Severidade.BLOQUEIA.value].itertuples(index=False):
        ultima = store.ultima_decisao(t.titulo_id, t.lote_remessa)
        if ultima and ultima.get("origem") in ORIGENS_HUMANAS:
            (retidos if ultima["acao"] == Acao.RETER_TITULO.value else liberados_humano).append(t.titulo_id)
            continue
        decisao = Decisao(titulo_id=t.titulo_id, lote_remessa=t.lote_remessa, acao=Acao.RETER_TITULO,
                          decidido_por="guardiao", origem=OrigemDecisao.SILENCIO,
                          motivo=f"{MOTIVO_SILENCIO}: {', '.join(t.regras)}"[:300])
        decisao_id, criada = store.record_decisao(decisao, agora)
        if criada:
            res = executor.execute(decisao_id, decisao, agora)
            store.set_execution(decisao_id, res.status, res.detail)
        retidos.append(t.titulo_id)

    explicacoes: List[str] = []
    if not sem_ia:
        top = prio[prio["titulo_id"].isin(retidos)].head(N_EXPLICACOES)
        try:
            explicacoes = _explicar(top, alertas, settings, store)
        except Exception as erro:  # IA nunca derruba o guardião
            explicacoes = [f"(explicação indisponível: {type(erro).__name__})"]

    r = relatorio.calcular(base, pol, alertas, retidos, liberados_humano, n_acionamentos)
    r.update({"agora": agora, "modo": "deterministico" if sem_ia else "com_ia",
              "retidos_ids": sorted(retidos), "explicacoes": explicacoes})
    saida_dir.mkdir(parents=True, exist_ok=True)
    (saida_dir / "relatorio_executivo.json").write_text(
        json.dumps(r, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    relatorio.imprimir(r, agora, "SEM IA" if sem_ia else "COM IA (via gateway)", explicacoes)
    if not sem_slack:
        print(f"[SLACK] Para decidir no Slack: python -m src.slack_bot --agora {agora}")
    return r


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Guardião do lote de remessa")
    p.add_argument("--agora", required=True, help="ex.: 2026-11-02T08:00")
    p.add_argument("--sem-ia", action="store_true", help="explicações só do cache/texto-modelo")
    p.add_argument("--sem-slack", action="store_true", help="acionamentos registrados como fila CLI")
    p.add_argument("--dados", default=None, help="pasta dos CSVs (padrão: DADOS_DIR do .env)")
    p.add_argument("--db", default="data/guardiao.db")
    p.add_argument("--saida", default="data")
    a = p.parse_args(argv)
    executar(a.agora, a.sem_ia, a.sem_slack, a.dados, a.db, a.saida)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
