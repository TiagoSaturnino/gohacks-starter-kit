import argparse
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
from src.slack import enviar_alerta_slack

try:
    from src.config import DIAS_LANCADOR_RECENTE
except Exception:
    DIAS_LANCADOR_RECENTE = 30

def main():
    parser = argparse.ArgumentParser(description="Guardião de Demandas - Modo CLI / Offline")
    parser.add_argument("--agora", type=str, required=True, help="Data e hora ISO (ex: 2026-11-02T08:00)")
    parser.add_argument("--sem-ia", action="store_true", help="Executa no modo deterministico sem LLM")
    parser.add_argument("--sem-slack", action="store_true", help="Desativa integracao Slack")

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

            if "guardiao" not in pol or not isinstance(pol["guardiao"], dict):
                pol["guardiao"] = {}
            pol["guardiao"]["dias_lancador_recente"] = DIAS_LANCADOR_RECENTE

            if hasattr(base_dados, "agenda") and not base_dados.agenda.empty:
                alertas_raw = base_dados.agenda.to_dict(orient="records")

                df_alertas = rodar_regras(base_dados.agenda, base_dados, pol)
                alertas_violacoes = df_alertas.to_dict(orient="records") if hasattr(df_alertas, "to_dict") else []
                
                mapa_violacoes = {}
                for a in alertas_violacoes:
                    tid = str(a.get("titulo_id") or a.get("id") or "")
                    if tid:
                        mapa_violacoes.setdefault(tid, []).append(a)

                print(f"[INFO] Regras determinísticas executadas! {len(alertas_violacoes)} alertas mapeados para {len(mapa_violacoes)} títulos.")

                for item in alertas_raw:
                    tid = str(item.get("titulo_id") or item.get("id") or "")
                    item["violacoes"] = mapa_violacoes.get(tid, [])
        except Exception as e:
            print(f"[ERRO DETECTADO] Falha ao processar regras: {e}")
            import traceback
            traceback.print_exc()

    print(f"[INFO] Alertas/Demandas carregados: {len(alertas_raw)}")

    alertas_priorizados = priorizar_demandas(alertas_raw, datetime.fromisoformat(args.agora))
    processados = rotear_e_executar(alertas_priorizados, store, ts_simulado=args.agora)

    # Gravação na Agenda Simulada
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
                def __init__(self, val): self.value = val
                    
            class ObjDec:
                def __init__(self, t, l, a_str, mot):
                    self.titulo_id = t
                    self.lote_remessa = l
                    self.acao = Acao.RETER_TITULO if a_str == "RETER_TITULO" else Acao.LIBERAR_TITULO
                    self.motivo = mot
                    self.decidido_por = "GUARDIAO"
                    self.origem = EnumVal("REGRA_ESTATICA")

            dec = ObjDec(titulo_id, lote_remessa, acao_val, item.get("motivo", ""))
            res = executor.execute(item["decisao_id"], dec, args.agora)
            if res.status == "WRITTEN":
                escritas += 1

    retidos = [p for p in processados if p.get("acao") == "RETER_TITULO"]
    liberados = [p for p in processados if p.get("acao") == "LIBERAR_TITULO"]
    
    # DISPARO PARA O SLACK (se não for passado --sem-slack)
    slack_url = os.getenv("SLACK_WEBHOOK_URL", "")
    envios_slack = 0
    if not args.sem_slack and slack_url:
        print(f"[SLACK] Notificando retenções críticas no canal via Webhook...")
        # Envia os 5 primeiros títulos de maior prioridade/valor para não estourar rate-limit na demo
        for tit in retidos[:5]:
            if enviar_alerta_slack(slack_url, tit):
                envios_slack += 1
        print(f"[SLACK] {envios_slack} cards de retenção enviados com sucesso para o Slack!")
    elif not args.sem_slack and not slack_url:
        print("[SLACK AVISO] Argumento '--sem-slack' não fornecido, mas 'SLACK_WEBHOOK_URL' não está definida em .env")

    val_retido = sum(float(p.get("valor", 0) or 0) for p in retidos)
    val_liberado = sum(float(p.get("valor", 0) or 0) for p in liberados)

    relatorio = {
        "timestamp_execucao": args.agora,
        "total_demandas": len(processados),
        "total_retidos": len(retidos),
        "total_liberados": len(liberados),
        "valor_retido_brl": round(val_retido, 2),
        "valor_liberado_brl": round(val_liberado, 2),
        "alertas_slack_enviados": envios_slack,
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
    print(f" Títulos Retidos      : {len(retidos)} (R$ {val_retido:,.2f})")
    print(f" Títulos Liberados    : {len(liberados)} (R$ {val_liberado:,.2f})")
    print(f" Alertas no Slack     : {envios_slack} enviados")
    print(f" Relatório Salvo em   : {relatorio_path}")
    print("===========================================================================\n")

if __name__ == "__main__":
    main()
