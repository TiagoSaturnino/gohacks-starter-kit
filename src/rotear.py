import uuid
from typing import List, Dict, Any

def rotear_e_executar(demandas: List[Dict[str, Any]], store: Any, ts_simulado: str) -> List[Dict[str, Any]]:
    processados = []

    for d in demandas:
        try:
            tid = str(d.get("titulo_id") or d.get("id") or "")
            violacoes = d.get("violacoes", [])

            bloqueios = [v for v in violacoes if str(v.get("severidade")).upper() == "BLOQUEIA"]
            acionamentos = [v for v in violacoes if str(v.get("severidade")).upper() == "ACIONA"]

            if bloqueios:
                acao = "RETER_TITULO"
                regras_str = ", ".join(set(str(b.get("regra")) for b in bloqueios))
                motivo = f"Retido por violacao(oes) critica(s): {regras_str}"
            elif acionamentos:
                acao = "LIBERAR_TITULO"
                regras_str = ", ".join(set(str(a.get("regra")) for a in acionamentos))
                motivo = f"Liberado com alerta(s) de notificacao: {regras_str}"
            else:
                acao = "LIBERAR_TITULO"
                motivo = "Liberado: Nenhuma violacao impeditiva identificada."

            item = dict(d)
            item["decisao_id"] = str(uuid.uuid4())
            item["acao"] = acao
            item["motivo"] = motivo
            processados.append(item)

        except Exception as e:
            # FAIL-CLOSED: Em caso de falha de sistema/regra, o padrão de segurança é RETER
            item = dict(d)
            item["decisao_id"] = str(uuid.uuid4())
            item["acao"] = "RETER_TITULO"
            item["motivo"] = f"ERRO_PROCESSAMENTO_REGRA: Falha inesperada ao rotear ({e})"
            processados.append(item)

    return processados
