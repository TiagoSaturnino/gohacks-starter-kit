from typing import List, Dict, Any
from pathlib import Path
from src.store import Store
from src.carga import carregar
from src.checks import rodar_regras

def converter_para_serializavel(d: Dict[str, Any]) -> Dict[str, Any]:
    limpo = {}
    for k, v in d.items():
        if hasattr(v, "isoformat"):
            limpo[k] = v.isoformat()
        elif hasattr(v, "item"):
            limpo[k] = v.item()
        else:
            limpo[k] = str(v) if v is not None else None
    return limpo

def rotear_e_executar(alertas_ordenados: List[Dict[str, Any]], store: Store, ts_simulado: str) -> List[Dict[str, Any]]:
    decisoes_registradas = []
    alertas_mapeados = {}
    pasta_pagamentos = Path("pagamentos")

    if pasta_pagamentos.exists():
        try:
            base = carregar(pasta_pagamentos)
            if hasattr(base, "agenda") and not base.agenda.empty:
                df_alertas = rodar_regras(base.agenda, base, base.politica)
                if hasattr(df_alertas, "to_dict"):
                    for alt in df_alertas.to_dict(orient="records"):
                        t_id = str(alt.get("titulo_id") or alt.get("id") or "")
                        if t_id:
                            alertas_mapeados[t_id] = alt
        except Exception:
            pass

    for item in alertas_ordenados:
        item_serial = converter_para_serializavel(item)
        titulo_id = str(item_serial.get("titulo_id") or item_serial.get("id") or "SEM_ID")
        lote_remessa = str(item_serial.get("lote_remessa") or item_serial.get("lote") or "LOTE_DEFAULT")

        alerta_obj = alertas_mapeados.get(titulo_id) or (item_serial if item_serial.get("regra") else None)
        
        if alerta_obj:
            regra = alerta_obj.get("regra") if isinstance(alerta_obj, dict) else getattr(alerta_obj, "regra", "R_RISCO")
            sev_raw = alerta_obj.get("severidade") if isinstance(alerta_obj, dict) else getattr(alerta_obj, "severidade", "BLOQUEIA")
            sev_str = sev_raw.value if hasattr(sev_raw, "value") else str(sev_raw)

            acao = "RETER_TITULO"
            motivo = f"Retido por regra determinística [{regra}] (severidade {sev_str})"
        else:
            acao = "LIBERAR_TITULO"
            motivo = "Liberado para pagamento: nenhuma irregularidade encontrada nas regras determinísticas."

        class ObjDecisao:
            def __init__(self, t_id, l_rem, ac, dec_por, orig, mot):
                self.titulo_id = t_id
                self.lote_remessa = l_rem
                self.acao = type("EnumVal", (), {"value": ac})()
                self.decidido_por = dec_por
                self.origem = type("EnumVal", (), {"value": orig})()
                self.motivo = mot

        decisao = ObjDecisao(
            t_id=titulo_id,
            l_rem=lote_remessa,
            ac=acao,
            dec_por="GUARDIAO_DETERMINISTICO",
            orig="REGRA_ESTATICA",
            mot=motivo
        )

        decisao_id, criada = store.record_decisao(decisao, ts_simulado=ts_simulado)
        if criada:
            store.enqueue(decisao_id=decisao_id, acao=acao, payload=item_serial)

        decisoes_registradas.append({
            "decisao_id": decisao_id,
            "titulo_id": titulo_id,
            "lote_remessa": lote_remessa,
            "acao": acao,
            "motivo": motivo,
            "criada": criada
        })

    return decisoes_registradas
