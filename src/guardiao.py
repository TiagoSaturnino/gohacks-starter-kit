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
