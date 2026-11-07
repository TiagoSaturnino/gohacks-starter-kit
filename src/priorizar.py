from datetime import datetime
from typing import List, Dict, Any

PESOS_SEVERIDADE = {
    "BLOQUEIA": 100,
    "ACIONA": 40,
    "AGRAVA": 10,
}

def priorizar_demandas(demandas: List[Dict[str, Any]], agora: datetime) -> List[Dict[str, Any]]:
    def calcular_score(d: Dict[str, Any]) -> float:
        violacoes = d.get("violacoes", [])
        
        # Maior severidade entre as violações do título
        max_peso = 0
        for v in violacoes:
            sev = str(v.get("severidade", "")).upper()
            max_peso = max(max_peso, PESOS_SEVERIDADE.get(sev, 0))
        
        # Urgência por horário de fechamento
        horario_str = d.get("horario_fechamento")
        score_urgencia = 0.0
        if horario_str:
            try:
                # Exemplo de parsing simples ou fallback de minutos
                h, m = map(int, horario_str.split(":")[:2])
                ref_min = agora.hour * 60 + agora.minute
                alvo_min = h * 60 + m
                diff = alvo_min - ref_min
                if diff > 0:
                    score_urgencia = max(0.0, 120.0 - diff) # Mais urgente quanto menor a diferença
            except Exception:
                score_urgencia = 0.0

        valor = float(d.get("valor", 0) or 0)
        score_valor = min(valor / 10000.0, 50.0) # Cap para valor financeiro

        return max_peso + score_urgencia + score_valor

    for d in demandas:
        d["prioridade_score"] = calcular_score(d)

    return sorted(demandas, key=lambda x: x["prioridade_score"], reverse=True)
