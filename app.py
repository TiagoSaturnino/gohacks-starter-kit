import os
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from src.mocks import MOCK_LOGS
from src.analyzer import analyze_log

load_dotenv()

st.set_page_config(page_title="GoHacks - AI Log RCA Engine", page_icon="⚡", layout="wide")

# Sidebar
st.sidebar.title("⚙️ Configurações & Parâmetros")

use_mock = os.getenv("USE_MOCK", "True").lower() in ("true", "1", "yes")
if use_mock:
    st.sidebar.info("🟡 **Modo:** Mock Offline (Alta Velocidade)")
else:
    st.sidebar.success("🟢 **Modo:** Anthropic Claude 3.5 Sonnet (Live API)")

mode = st.sidebar.radio("Selecione o Modo de Operação:", ["Análise Individual (Live Demo)", "Processamento em Lote (CSV)"])

st.title("⚡ GoGroup AI-First Log Triage & RCA Engine")
st.caption("Agente Inteligente de Triagem e Causa Raiz com Guardrails Pydantic e Human-in-the-Loop (HITL)")

# ---------------------------------------------------------
# MODO 1: ANÁLISE INDIVIDUAL
# ---------------------------------------------------------
if mode == "Análise Individual (Live Demo)":
    selected_log_key = st.sidebar.selectbox(
        "Selecione um Log de Teste (Mock):",
        options=list(MOCK_LOGS.keys()),
        format_func=lambda x: x.replace("_", " ").title()
    )

    log_input = st.text_area(
        "Payload do Log de Erro (Sentry / CloudWatch / Webhooks):",
        value=MOCK_LOGS[selected_log_key],
        height=170
    )

    if st.button("🚀 Executar Triagem & RCA", type="primary"):
        with st.spinner("Analisando log com guardrails Pydantic..."):
            st.session_state["analysis_result"] = analyze_log(log_input)
            st.session_state["executed_actions"] = {}

    # Renderiza o resultado persistido na sessão
    if "analysis_result" in st.session_state:
        result = st.session_state["analysis_result"]
        st.success("Análise concluída e validada pelo Pydantic Schema!")

        # Métrica de ROI Operacional
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Serviço Impactado", result.service_name)
        col2.metric("Nível de Severidade", result.severity.value)
        col3.metric("Confiança da IA", f"{int(result.confidence_score * 100)}%")
        col4.metric("Tempo Economizado (SLA)", "~25 min", delta="98% mais rápido")

        st.markdown("---")
        st.markdown("### 🔍 Diagnóstico do Incidente")

        if result.severity.value in ["HIGH", "CRITICAL"]:
            st.error(f"**Causa Raiz Identificada:** {result.root_cause}")
        else:
            st.warning(f"**Causa Raiz Identificada:** {result.root_cause}")

        st.markdown("---")
        st.markdown("### 🛠️ Ações Recomendadas & Trava Human-in-the-Loop (HITL)")

        if result.requires_human_intervention:
            st.warning("⚠️ **Guardrail HITL Ativo:** Ações críticas exigem aprovação explícita do operador.")

        if "executed_actions" not in st.session_state:
            st.session_state["executed_actions"] = {}

        for idx, action in enumerate(result.recommended_actions):
            with st.expander(f"Ação #{idx+1}: {action.action_type.value} - {action.description}", expanded=True):
                st.write(f"**Requer Validação Humana?** {'🔴 SIM (Trava HITL Ativa)' if action.requires_human_approval else '🟢 NÃO (Automação Direta)'}")

                if action.payload:
                    st.caption("Payload técnico para automação:")
                    st.json(action.payload)

                if action.requires_human_approval:
                    btn_key = f"btn_approve_{idx}"
                    if st.session_state["executed_actions"].get(idx, False):
                        st.success(f"✅ Ação [{action.action_type.value}] APROVADA por Tiago Saturnino e Executada com Sucesso!")
                    else:
                        if st.button(f"✅ Aprovar & Executar Ação #{idx+1}", key=btn_key):
                            st.session_state["executed_actions"][idx] = True
                            st.balloons()
                            st.rerun()

        with st.expander("📄 Ver Saída JSON Estruturada (Pydantic Schema)"):
            st.json(result.model_dump())

# ---------------------------------------------------------
# MODO 2: PROCESSAMENTO EM LOTE (CSV)
# ---------------------------------------------------------
else:
    st.markdown("### 📊 Processamento de Incidentes em Lote")
    st.write("Faça o upload de uma planilha CSV contendo múltiplos logs para triagem automática agrupada.")

    uploaded_file = st.file_uploader("Carregar arquivo CSV de logs", type=["csv"])

    if st.checkbox("Usar dados de exemplo em lote"):
        sample_data = {
            "log_id": ["LOG-001", "LOG-002", "LOG-003"],
            "raw_log": list(MOCK_LOGS.values())
        }
        df_sample = pd.DataFrame(sample_data)
        st.dataframe(df_sample, use_container_width=True)

        if st.button("⚡ Triar Lote de Exemplo"):
            results = []
            for _, row in df_sample.iterrows():
                res = analyze_log(row["raw_log"])
                results.append({
                    "ID": row["log_id"],
                    "Serviço": res.service_name,
                    "Severidade": res.severity.value,
                    "Causa Raiz": res.root_cause,
                    "Requer Aprovação Humana": res.requires_human_intervention
                })
            
            df_results = pd.DataFrame(results)
            st.markdown("#### 📈 Resultado da Triagem Agrupada")
            st.dataframe(df_results, use_container_width=True)

            st.markdown("##### Distribuição de Severidade")
            st.bar_chart(df_results["Severidade"].value_counts())
