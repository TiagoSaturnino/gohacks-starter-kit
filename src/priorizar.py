from datetime import datetime
from typing import List, Dict, Any

def priorizar_demandas(demandas: List[Dict[str, Any]], data_referencia: datetime = None) -> List[Dict[str, Any]]:
    pesos_severidade = {
        "CRITICO": 1,
        "ALTO": 2,
        "MEDIO": 3,
        "BAIXO": 4
    }

    def chave_ordenacao(item: Dict[str, Any]):
        sev = str(item.get("severidade", "BAIXO")).upper()
        peso_sev = pesos_severidade.get(sev, 5)

        valor = item.get("valor", 0.0)
        try:
            valor = float(valor)
        except (ValueError, TypeError):
            valor = 0.0

        return (peso_sev, -valor)

    return sorted(demandas, key=chave_ordenacao)
