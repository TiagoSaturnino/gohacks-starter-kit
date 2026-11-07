import json
from pathlib import Path
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Guardião de Pagamentos", layout="wide")

st.title("🛡️ Painel do Guardião de Pagamentos")
st.caption("Consolidação de Auditoria Executiva & Agente Autônomo - Hackathon GoHacks")

relatorio_path = Path("data/relatorio_executivo.json")
agenda_sim_path = Path("data/agenda_simulada.csv")

if relatorio_path.exists():
    try:
        rel = json.loads(relatorio_path.read_text(encoding="utf-8"))
        col1, col2, col3, col4 = st.columns(4)
        
        v_ret = rel.get("valor_retido_brl", 0)
        v_lib = rel.get("valor_liberado_brl", 0)

        col1.metric("Total Processado", f"{rel.get('total_demandas', 0)} títulos")
        col2.metric("Títulos Retidos", f"{rel.get('total_retidos', 0)}", delta=f"R$ {v_ret:,.2f} Preservados", delta_color="inverse")
        col3.metric("Títulos Liberados", f"{rel.get('total_liberados', 0)}", delta=f"R$ {v_lib:,.2f} Aprovados")
        col4.metric("Modo de Execução", rel.get("modo_execucao", "OFFLINE"))
    except Exception as e:
        st.error(f"Erro ao ler relatório: {e}")

st.markdown("---")

if agenda_sim_path.exists():
    try:
        df = pd.read_csv(agenda_sim_path)
        col_status = "status_guardiao" if "status_guardiao" in df.columns else df.columns[0]
        
        filtro = st.radio(
            "Filtrar visão:",
            ["Todos os Registros", "Retidos (Bloqueados)", "Liberados (Aprovados)"],
            horizontal=True
        )
        
        if filtro == "Retidos (Bloqueados)":
            df_exibir = df[df[col_status].astype(str).str.contains("RETER|BLOQUE|RETIDO", case=False, na=False)]
        elif filtro == "Liberados (Aprovados)":
            df_exibir = df[df[col_status].astype(str).str.contains("LIBERAR|APROV|LIBERADO", case=False, na=False)]
        else:
            df_exibir = df

        st.subheader(f"📋 Agenda Simulada ({len(df_exibir)} de {len(df)} exibições)")
        st.dataframe(df_exibir, use_container_width=True, height=500)
    except Exception as e:
        st.error(f"Erro ao carregar a agenda simulada: {e}")
else:
    st.warning("Arquivo 'data/agenda_simulada.csv' não encontrado.")
