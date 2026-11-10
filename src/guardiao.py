<<<<<<< HEAD
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
=======
﻿import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from src.store import Store
from src.carga import carregar, politica
from src.checks import rodar_regras
from src.priorizar import priorizar_demandas
from src.rotear import rotear_e_executar
from src.executors import AgendaSimuladaExecutor
from src.schemas import Acao

# Tenta carregar a configuração de DIAS_LANCADOR_RECENTE do projeto
try:
    from src.config import DIAS_LANCADOR_RECENTE
except Exception:
    DIAS_LANCADOR_RECENTE = 30

def main():
    parser = argparse.ArgumentParser(description="Guardião de Demandas - Modo CLI / Offline")
    parser.add_argument("--agora", type=str, required=True, help="Data e hora de referência ISO (ex: 2026-11-02T08:00)")
    parser.add_argument("--sem-ia", action="store_true", help="Executa no modo determinístico sem chamadas a LLM")
    parser.add_argument("--sem-slack", action="store_true", help="Desativa integrações de envio via Slack")

    args = parser.parse_args()

    os.makedirs("data", exist_ok=True)
    db_path = "data/guardiao.db"
    store = Store(db_path)

    alertas_raw = []
    pasta_pagamentos = Path("pagamentos")

    if pasta_pagamentos.exists():
        try:
            base_dados = carregar(pasta_pagamentos)
            pol = politica(pasta_pagamentos)

            # Injeção exigida pela regra A1 no checks.py
            if "guardiao" not in pol or not isinstance(pol["guardiao"], dict):
                pol["guardiao"] = {}
            pol["guardiao"]["dias_lancador_recente"] = DIAS_LANCADOR_RECENTE

            if hasattr(base_dados, "agenda") and not base_dados.agenda.empty:
                alertas_raw = base_dados.agenda.to_dict(orient="records")

                # Executa a suíte completa de regras determinísticas (R1-R8 + A1)
                df_alertas = rodar_regras(base_dados.agenda, base_dados, pol)
                
                alertas_violacoes = df_alertas.to_dict(orient="records") if hasattr(df_alertas, "to_dict") else []
                mapa_violacoes = {}
                for a in alertas_violacoes:
                    tid = str(a.get("titulo_id") or a.get("id") or "")
                    if tid:
                        mapa_violacoes[tid] = a

                print(f"[INFO] Regras determinísticas executadas com sucesso! {len(mapa_violacoes)} violações/alertas gerados.")

                # Associa os alertas aos registros da agenda
                for item in alertas_raw:
                    tid = str(item.get("titulo_id") or item.get("id") or "")
                    if tid in mapa_violacoes:
                        viol = mapa_violacoes[tid]
                        item["regra"] = viol.get("regra")
                        item["severidade"] = viol.get("severidade")
                        item["evidencia"] = viol.get("evidencia")
        except Exception as e:
            print(f"[ERRO DETECTADO] Falha ao processar regras: {e}")
            import traceback
            traceback.print_exc()

    if not alertas_raw and Path("demandas.json").exists():
        try:
            with open("demandas.json", "r", encoding="utf-8") as f:
                alertas_raw = json.load(f)
        except Exception as e:
            print(f"[AVISO] Não foi possível ler demandas.json: {e}")

    print(f"[INFO] Alertas/Demandas carregados: {len(alertas_raw)}")

    alertas_priorizados = priorizar_demandas(alertas_raw, datetime.fromisoformat(args.agora))
    print(f"[INFO] Alertas priorizados a processar: {len(alertas_priorizados)}")

    processados = rotear_e_executar(alertas_priorizados, store, ts_simulado=args.agora)

    # Gravação na Agenda Simulada (Item 6)
    agenda_orig = str(Path("pagamentos") / "agenda_remessa.csv")
    agenda_sim = str(Path("data") / "agenda_simulada.csv")
    escritas = 0
    if Path(agenda_orig).exists():
        executor = AgendaSimuladaExecutor(agenda_orig, agenda_sim)
        for item in processados:
            titulo_id = item["titulo_id"]
            lote_remessa = item.get("lote_remessa", "LOTE_01")
            acao_val = item["acao"]
            
            class EnumVal:
                def __init__(self, val):
                    self.value = val
                    
            class ObjDec:
                def __init__(self, t, l, a_str):
                    self.titulo_id = t
                    self.lote_remessa = l
                    self.acao = Acao.RETER_TITULO if a_str == "RETER_TITULO" else Acao.LIBERAR_TITULO
                    self.motivo = item.get("motivo", "Processado via Guardião")
                    self.decidido_por = "GUARDIAO"
                    self.origem = EnumVal("REGRA_ESTATICA")

            dec = ObjDec(titulo_id, lote_remessa, acao_val)
            res = executor.execute(item["decisao_id"], dec, args.agora)
            if res.status == "WRITTEN":
                escritas += 1
        print(f"[EXECUTOR] Agenda Simulada atualizada em '{agenda_sim}' ({escritas} registros gravados).")

    tot_retidos = sum(1 for p in processados if p.get("acao") == "RETER_TITULO")
    tot_liberados = sum(1 for p in processados if p.get("acao") == "LIBERAR_TITULO")
    
    relatorio = {
        "timestamp_execucao": args.agora,
        "total_demandas": len(processados),
        "total_retidos": tot_retidos,
        "total_liberados": tot_liberados,
        "decisoes_sqlite": len(store.decisoes()),
        "outbox_sqlite": len(store.outbox()),
        "agenda_simulada_csv": agenda_sim if Path(agenda_sim).exists() else None,
        "modo_execucao": "DETERMINISTICO_OFFLINE" if args.sem_ia else "HYBRID_AI"
    }

    relatorio_path = Path("data/relatorio_executivo.json")
    relatorio_path.write_text(json.dumps(relatorio, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n=================== PAINEL DE AUDITORIA & CONSOLIDAÇÃO ===================")
    print(f" Data/Hora Referência : {args.agora}")
    print(f" Total Processado     : {len(processados)} demandas")
    print(f" Titulos Retidos      : {tot_retidos}")
    print(f" Titulos Liberados    : {tot_liberados}")
    print(f" Histórico SQLite     : {len(store.decisoes())} decisões salvas ({db_path})")
    print(f" Fila Outbox SQLite   : {len(store.outbox())} itens enfileirados")
    print(f" Relatório Salvo em   : {relatorio_path}")
    print("===========================================================================\n")

    print(f"[SUCESSO] Processamento do Guardião concluído com sucesso!")

if __name__ == "__main__":
    main()
>>>>>>> 0befe969dce2620aeabb47a698aea167f6cf1d50
