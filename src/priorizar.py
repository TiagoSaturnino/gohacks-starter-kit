"""Prioridade dos títulos alertados (SPEC §5, passo 4).

score = peso da severidade × valor × urgência, com urgência = 1 / (horas até o fechamento + 1).
Dedup por titulo_id: vale a maior severidade do título. A1 (AGRAVA) nunca retém sozinha.
"""
from __future__ import annotations

import pandas as pd

PESO_SEVERIDADE = {"BLOQUEIA": 100.0, "ACIONA": 10.0, "AGRAVA": 1.0}
ORDEM_SEVERIDADE = ["BLOQUEIA", "ACIONA", "AGRAVA"]


def fechamento(agenda: pd.DataFrame) -> pd.Series:
    """Momento de fechamento do lote de cada título (data_prevista + horario_fechamento)."""
    data = pd.to_datetime(agenda["data_prevista"], errors="coerce").dt.normalize()
    hora = pd.to_timedelta(agenda["horario_fechamento"].astype(str).str.slice(0, 5) + ":00",
                           errors="coerce").fillna(pd.Timedelta(0))
    return pd.Series((data + hora).values, index=agenda["titulo_id"].values)


def priorizar(alertas: pd.DataFrame, agenda: pd.DataFrame, agora: pd.Timestamp) -> pd.DataFrame:
    """Uma linha por título alertado: severidade máxima, regras, valor, lote, fechamento, score."""
    colunas = ["titulo_id", "severidade", "regras", "valor", "lote_remessa", "fechamento", "score"]
    if alertas is None or alertas.empty:
        return pd.DataFrame(columns=colunas)
    ag = agenda.drop_duplicates("titulo_id").set_index("titulo_id")
    fecha = fechamento(agenda.drop_duplicates("titulo_id"))
    linhas = []
    for tid, g in alertas.groupby("titulo_id", sort=False):
        sevs = set(g["severidade"].astype(str).str.upper())
        sev = next(s for s in ORDEM_SEVERIDADE if s in sevs) if sevs & set(ORDEM_SEVERIDADE) else "AGRAVA"
        valor = float(ag.loc[tid, "valor"]) if tid in ag.index else float(g["valor"].iloc[0])
        quando = fecha.get(tid, pd.NaT)
        horas = max((quando - agora).total_seconds() / 3600, 0) if pd.notna(quando) else 999.0
        linhas.append({
            "titulo_id": tid, "severidade": sev, "regras": sorted(set(g["regra"])), "valor": valor,
            "lote_remessa": ag.loc[tid, "lote_remessa"] if tid in ag.index else None,
            "fechamento": quando, "score": PESO_SEVERIDADE[sev] * valor / (horas + 1),
        })
    return (pd.DataFrame(linhas, columns=colunas)
            .sort_values(["fechamento", "score"], ascending=[True, False], kind="stable")
            .reset_index(drop=True))
