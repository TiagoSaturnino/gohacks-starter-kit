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
