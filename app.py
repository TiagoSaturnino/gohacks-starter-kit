"""Painel só leitura do Guardião do Lote (SPEC item 8).

    streamlit run app.py

Lê o que o guardião e o bot gravam: data/relatorio_executivo.json (placar da rodada) e
data/agenda_simulada.csv (status ao vivo, inclusive decisões tomadas no Slack).
Clique em "Atualizar" depois de uma decisão no Slack para vê-la aqui.
"""
import json
from pathlib import Path

import pandas as pd
import streamlit as st

MIN_POR_TITULO = 3  # premissa de conferência manual (mesma do pitch)
REL_VIVO, AGENDA_VIVA = Path("data/relatorio_executivo.json"), Path("data/agenda_simulada.csv")
REL_DEMO, AGENDA_DEMO = Path("demo/relatorio_executivo.json"), Path("demo/agenda_simulada.csv")
# Na nuvem (Streamlit Community Cloud) data/ não existe: os dados do desafio não são versionados.
# Sem a rodada local, o painel lê o snapshot da pasta demo/ (só agregados).
EM_DEMO = not REL_VIVO.exists() and REL_DEMO.exists()
REL, AGENDA = (REL_DEMO, AGENDA_DEMO) if EM_DEMO else (REL_VIVO, AGENDA_VIVA)


def brl(v: float) -> str:
    inteiro, cent = f"{abs(float(v)):,.2f}".split(".")
    return f"R$ {inteiro.replace(',', '.')},{cent}"


st.set_page_config(page_title="Guardião do Lote", page_icon="🛡️", layout="wide")

if not REL.exists():
    st.title("🛡️ Guardião do Lote")
    st.warning("Rode antes: python -m src.guardiao --agora 2026-11-02T08:00 --sem-ia --sem-slack")
    st.stop()

rel = json.loads(REL.read_text(encoding="utf-8"))
agenda = pd.read_csv(AGENDA, dtype=str, keep_default_na=False) if AGENDA.exists() else pd.DataFrame()

topo, botao = st.columns([5, 1])
topo.title("🛡️ Guardião do Lote")
topo.caption(f"Gogroup · contas a pagar · agenda de remessa às {rel.get('agora', '').replace('T', ' ')}")
botao.write("")
botao.button("🔄 Atualizar", width="stretch")
if EM_DEMO:
    st.info("Modo demonstração: snapshot de uma rodada do guardião (valores agregados). "
            "Para ver decisões ao vivo, rode o projeto localmente.")

retidos_agora = (int((agenda["status_guardiao"] == "retido").sum()) if "status_guardiao" in agenda
                 else int(rel.get("retidos", 0)))
liberados = agenda[agenda.get("status_guardiao", pd.Series(dtype=str)) == "liberado"] if not agenda.empty else agenda
total = int(rel.get("titulos_na_agenda", 0))
horas_antes = total * MIN_POR_TITULO / 60
horas_depois = rel.get("retidos", 0) * MIN_POR_TITULO / 60

c1, c2, c3, c4 = st.columns(4)
c1.metric("R$ protegido (estimado)", brl(rel.get("rs_protegido", 0)))
c2.metric("Custo máximo de falso positivo", brl(rel.get("custo_maximo_fp", 0)), help="Pior caso: se todos os retidos fossem legítimos.")
c3.metric("Retidos agora", f"{retidos_agora} de {total}",
          delta=f"{len(liberados)} liberado(s) pelo humano" if len(liberados) else None, delta_color="off")
c4.metric("Conferência por semana", f"{horas_antes:.0f} h → {horas_depois:.1f} h".replace(".", ","),
          help=f"Premissa: {MIN_POR_TITULO} min por título conferido à mão.")

st.divider()
esq, dir_ = st.columns([3, 2])
with esq:
    st.subheader("Retidos por lote")
    lotes = pd.DataFrame(rel.get("retidos_por_lote", []))
    if not lotes.empty:
        lotes["rótulo"] = lotes["lote"] + " · fecha " + lotes["fechamento"]
        st.bar_chart(lotes.set_index("rótulo")["valor"], color="#005CAC", height=300)
with dir_:
    st.subheader("Alertas por regra")
    regras = pd.DataFrame(list(rel.get("contagem_por_regra", {}).items()), columns=["regra", "títulos"])
    st.dataframe(regras, hide_index=True, width="stretch", height=300)

st.subheader("Decisões humanas")
if len(liberados):
    st.dataframe(liberados[["lote_remessa", "titulo_id", "valor", "status_guardiao", "decidido_por", "decidido_em"]],
                 hide_index=True, width="stretch")
else:
    st.info("Nenhuma decisão humana ainda. Sem resposta até o fechamento, os títulos seguem retidos.")

with st.expander("Ver títulos retidos e o motivo"):
    if not agenda.empty:
        st.dataframe(agenda[agenda["status_guardiao"] == "retido"][["lote_remessa", "titulo_id", "valor", "motivo"]],
                     hide_index=True, width="stretch")
