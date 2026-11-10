<<<<<<< HEAD
"""Quem é acionado por título (SPEC §5, passo 5), a partir de politicas.csv.

- Fraude (R5 conta alterada, R6 boleto divergente): coordenadora de CP (primeira pessoa da
  política) + PS08, como na SPEC.
- Acima da alçada do aprovador: a pessoa de alcadas.diretora_financeira.
- Sempre: o dono da categoria do fornecedor (tabela da SPEC §5).
Agrupamento: UMA mensagem por pessoa por lote (`agrupar`).
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Set, Tuple

# Dono por categoria (SPEC §5). Valores de domínio, não IDs de fornecedor/título.
DONO_POR_CATEGORIA = {
    "produto": "PS04", "embalagens": "PS04",
    "insumos_capa": "PS05", "servicos": "PS05",
    "marketing_agencia": "PS06", "influenciador": "PS06",
    "frete": "PS07",
    "aluguel": "PS10", "utilidades": "PS10",
    "ti_saas": "PS11",
}
PREFIXOS_FRAUDE = ("R5", "R6")
ANALISTA_FRAUDE = "PS08"


def _pessoas_validas(pol: Dict[str, Any]) -> List[str]:
    pessoas = (pol.get("acionamento_humano") or {}).get("pessoas") or []
    return [str(p.get("pessoa_id")) for p in pessoas if p.get("pessoa_id")]


def pessoas_para(regras: Iterable[str], categoria: str, valor: float, pol: Dict[str, Any]) -> Set[str]:
    validas = _pessoas_validas(pol)
    coordenadora = validas[0] if validas else "PS01"
    diretoria = (pol.get("alcadas") or {}).get("diretora_financeira") or {}
    limiar = float(diretoria.get("aprova_acima_de", float("inf")))
    alvo: Set[str] = set()
    if any(str(r).startswith(PREFIXOS_FRAUDE) for r in regras):
        alvo |= {coordenadora, ANALISTA_FRAUDE}
    if valor > limiar and diretoria.get("pessoa_id"):
        alvo.add(str(diretoria["pessoa_id"]))
    alvo.add(DONO_POR_CATEGORIA.get(str(categoria), coordenadora))
    return {p for p in alvo if not validas or p in validas} or {coordenadora}


def agrupar(itens: Iterable[Tuple[str, str, Set[str]]]) -> Dict[Tuple[str, str], List[str]]:
    """[(titulo_id, lote, pessoas)] -> {(pessoa, lote): [titulos]} — uma mensagem por par."""
    grupos: Dict[Tuple[str, str], List[str]] = {}
    for tid, lote, pessoas in itens:
        for p in sorted(pessoas):
            grupos.setdefault((p, lote), []).append(tid)
    return {k: sorted(set(v)) for k, v in sorted(grupos.items())}
=======
﻿from typing import List, Dict, Any
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
>>>>>>> 0befe969dce2620aeabb47a698aea167f6cf1d50
