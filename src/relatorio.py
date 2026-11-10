"""Placar estimado (SPEC §7). Tudo sai de politicas.csv; nada de premissa inventada.

- R$ protegido: títulos BLOQUEIA retidos, sem repetição. Se o título só tem R2, conta só a
  diferença paga a mais (título − duplicata), não o título inteiro.
- Custo máximo de FP: Σ valor × (multa + juros_ao_mes × 7/30) + custo de relacionamento da
  categoria. Pior caso, se todos os retidos fossem legítimos.
- Acionamentos: quantidade × custo_por_acionamento_desnecessario (teto).
- Placar = protegido − custo de FP dos liberados pelo humano − acionamentos desnecessários
  (aproximação: um por título liberado pelo humano).
"""
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from src.carga import Base
from src.checks import _casar_duplicata, _titulos

REGRA_SO_DIFERENCA = "R2_valor_x_duplicata"


def _brl(v: float) -> str:
    inteiro, cent = f"{abs(v):,.2f}".split(".")
    return f"{'-' if v < 0 else ''}R$ {inteiro.replace(',', '.')},{cent}"


def _custo_fp(valor: float, categoria: str, pol: Dict[str, Any]) -> float:
    atraso = pol.get("atraso") or {}
    rel = atraso.get("custo_de_relacionamento_por_titulo_atrasado") or {}
    return valor * (float(atraso.get("multa", 0)) + float(atraso.get("juros_ao_mes", 0)) * 7 / 30) \
        + float(rel.get(categoria, 0) or 0)


def calcular(base: Base, pol: Dict[str, Any], alertas: pd.DataFrame, retidos: List[str],
             liberados_humano: List[str], n_acionamentos: int) -> Dict[str, Any]:
    ag = base.agenda.drop_duplicates("titulo_id").set_index("titulo_id")
    cat = base.fornecedores.drop_duplicates("fornecedor_id").set_index("fornecedor_id")["categoria"]
    bloq = alertas[alertas["severidade"].astype(str).str.upper() == "BLOQUEIA"] if not alertas.empty else alertas
    regras_por_titulo = bloq.groupby("titulo_id")["regra"].apply(set).to_dict() if not bloq.empty else {}

    dup = _casar_duplicata(_titulos(base.agenda, base), base).drop_duplicates("titulo_id")
    valor_dup = dup.set_index("titulo_id")["valor_dup"]

    protegido, custo_fp_max = 0.0, 0.0
    por_lote: Dict[str, Dict[str, Any]] = {}
    for tid in retidos:
        valor = float(ag.loc[tid, "valor"])
        categoria = str(cat.get(ag.loc[tid, "fornecedor_id"], ""))
        if regras_por_titulo.get(tid) == {REGRA_SO_DIFERENCA} and pd.notna(valor_dup.get(tid)):
            protegido += max(valor - float(valor_dup[tid]), 0.0)
        else:
            protegido += valor
        custo_fp_max += _custo_fp(valor, categoria, pol)
        lote = str(ag.loc[tid, "lote_remessa"])
        item = por_lote.setdefault(lote, {"lote": lote, "titulos": 0, "valor": 0.0,
                                          "fechamento": f"{ag.loc[tid, 'data_prevista']:%d/%m} "
                                                        f"{ag.loc[tid, 'horario_fechamento']}"})
        item["titulos"] += 1
        item["valor"] += valor

    custo_acion = float((pol.get("acionamento_humano") or {}).get("custo_por_acionamento_desnecessario", 0))
    custo_fp_liberados = sum(
        _custo_fp(float(ag.loc[t, "valor"]), str(cat.get(ag.loc[t, "fornecedor_id"], "")), pol)
        for t in liberados_humano if t in ag.index)
    placar = protegido - custo_fp_liberados - custo_acion * len(liberados_humano)

    contagem = (alertas.groupby("regra")["titulo_id"].nunique().sort_index().to_dict()
                if not alertas.empty else {})
    return {
        "titulos_na_agenda": int(len(ag)),
        "titulos_com_alerta": int(alertas["titulo_id"].nunique()) if not alertas.empty else 0,
        "contagem_por_regra": {k: int(v) for k, v in contagem.items()},
        "retidos": len(retidos),
        "valor_de_face_retido": round(sum(float(ag.loc[t, "valor"]) for t in retidos), 2),
        "retidos_por_lote": sorted(por_lote.values(), key=lambda x: x["lote"]),
        "liberados_pelo_humano": len(liberados_humano),
        "rs_protegido": round(protegido, 2),
        "custo_maximo_fp": round(custo_fp_max, 2),
        "acionamentos": n_acionamentos,
        "custo_teto_acionamentos": round(n_acionamentos * custo_acion, 2),
        "placar_estimado": round(placar, 2),
    }


def imprimir(r: Dict[str, Any], agora: str, modo: str, explicacoes: List[str]) -> None:
    linha = "=" * 74
    print(f"\n{linha}\n GUARDIÃO DO LOTE · {agora} · {modo}\n{linha}")
    print(f" Agenda: {r['titulos_na_agenda']} títulos · {r['titulos_com_alerta']} com algum alerta")
    print("\n Alertas por regra (títulos):")
    for regra, n in r["contagem_por_regra"].items():
        print(f"   {regra:<44} {n:>4}")
    print(f"\n Retidos por lote ({r['retidos']} títulos, {_brl(r['valor_de_face_retido'])} em valor de face):")
    for l in r["retidos_por_lote"]:
        print(f"   {l['lote']:<18} fecha {l['fechamento']:<12} {l['titulos']:>3} títulos  {_brl(l['valor']):>16}")
    if explicacoes:
        print("\n Por que reter (explicação dos mais urgentes):")
        for e in explicacoes:
            print(f"   • {e}")
    print(f"\n PLACAR ESTIMADO")
    print(f"   R$ protegido (R2 só pela diferença) {_brl(r['rs_protegido']):>20}")
    print(f"   Custo máximo de falso positivo      {_brl(r['custo_maximo_fp']):>20}  (pior caso)")
    print(f"   Acionamentos                        {r['acionamentos']:>8} msgs  teto {_brl(r['custo_teto_acionamentos'])}")
    print(f"   Liberados pelo humano               {r['liberados_pelo_humano']:>8}")
    print(f"   Placar estimado                     {_brl(r['placar_estimado']):>20}")
    print(f" Títulos sem alerta seguem no lote normalmente.\n{linha}\n")
