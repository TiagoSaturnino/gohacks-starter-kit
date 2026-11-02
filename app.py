import os
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from src.mocks import MOCK_LOGS
from src.analyzer import analyze_log

load_dotenv()

st.set_page_config(page_title="GoHacks - AI Log RCA Engine", page_icon="⚡", layout="wide")

st.sidebar.title("⚙️ Configurações & Parâmetros")

use_mock = os.getenv("USE_MOCK", "True").lower() in ("true", "1", "yes")
if use_mock:
    st.sidebar.info("🟡 **Modo Configurado:** Mock Offline (Demonstração)")
else:
    st.sidebar.success("🟢 **Modo Configurado:** Anthropic Live API")

mode = st.sidebar.radio("Selecione o Modo de Operação:", ["Análise Individual (Live Demo)", "Processamento em Lote (CSV)"])

st.title("⚡ GoGroup AI-First Log Triage & RCA Engine")
st.caption("Agente Inteligente de Triagem com Guardrails Pydantic e Human-in-the-Loop (HITL)")

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
            st.session_state["outcome"] = analyze_log(log_input)
            st.session_state["executed_actions"] = {}

    if "outcome" in st.session_state:
        outcome = st.session_state["outcome"]
        
        if outcome.source == "DEGRADED":
            st.error(f"🚨 **Falha no Pipeline Operacional:** {outcome.error}")
            st.warning("O incidente foi automaticamente encaminhado para triagem manual de nível N2.")
        else:
            result = outcome.result
            if outcome.source == "MOCK":
                st.info("ℹ️ Resultado gerado via Engine Mock Offline.")
            else:
                st.success("🟢 Análise concluída em Tempo Real (Live API) e validada via Pydantic!")

            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Serviço Impactado", result.service_name)
            col2.metric("Nível de Severidade", result.severity.value)
            col3.metric("Confiança Estimada", f"{int(result.confidence_score * 100)}%")
            col4.metric("Redução de SLA", "~25 min (Estimado)")

            st.markdown("---")
            st.markdown("### 🔍 Diagnóstico do Incidente")

            if result.severity.value in ["HIGH", "CRITICAL"]:
                st.error(f"**Causa Raiz Identificada:** {result.root_cause}")
            else:
                st.warning(f"**Causa Raiz Identificada:** {result.root_cause}")

            st.markdown("---")
            st.markdown("### 🛠️ Ações Recomendadas & Trava Human-in-the-Loop (HITL)")

            if result.requires_human_intervention:
                st.warning("⚠️ **Guardrail HITL Ativo:** Este incidente exige aprovação explícita do operador.")

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
                            st.success(f"✅ Ação [{action.action_type.value}] - APROVAÇÃO REGISTRADA (Execução Simulada)")
                        else:
                            if st.button(f"✅ Aprovar & Simular Ação #{idx+1}", key=btn_key):
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
    st.write("Faça o upload de uma planilha CSV contendo a coluna `raw_log` ou `log` (Limite: 50 itens por lote).")

    uploaded_file = st.file_uploader("Carregar arquivo CSV de logs", type=["csv"])
    df_to_process = None

    if uploaded_file is not None:
        try:
            df_to_process = pd.read_csv(uploaded_file)
            if len(df_to_process) > 50:
                st.warning("O lote excede 50 itens. Processando apenas as primeiras 50 linhas para proteção do sistema.")
                df_to_process = df_to_process.head(50)
            st.success(f"Arquivo CSV carregado! Total de linhas a processar: {len(df_to_process)}")
            st.dataframe(df_to_process.head(), use_container_width=True)
        except Exception as e:
            st.error(f"Erro ao ler arquivo CSV: {e}")

    elif st.checkbox("Usar dados de exemplo em lote"):
        sample_data = {
            "log_id": ["LOG-001", "LOG-002", "LOG-003"],
            "raw_log": list(MOCK_LOGS.values())
        }
        df_to_process = pd.DataFrame(sample_data)
        st.dataframe(df_to_process, use_container_width=True)

    if df_to_process is not None:
        log_column = "raw_log" if "raw_log" in df_to_process.columns else ("log" if "log" in df_to_process.columns else None)

        if not log_column:
            st.error("O CSV precisa conter uma coluna chamada `raw_log` ou `log`.")
        else:
            if st.button("⚡ Executar Triagem no Lote"):
                results = []
                progress_bar = st.progress(0)
                total = len(df_to_process)

                for idx, row in df_to_process.iterrows():
                    log_text = str(row[log_column])
                    outcome = analyze_log(log_text)
                    
                    if outcome.source == "DEGRADED":
                        results.append({
                            "ID": row.get("log_id", f"LOG-{idx+1}"),
                            "Fonte": "DEGRADED",
                            "Serviço": "N/A",
                            "Severidade": "UNKNOWN",
                            "Causa Raiz": f"FALHA: {outcome.error}",
                            "Requer Aprovação": True
                        })
                    else:
                        res = outcome.result
                        results.append({
                            "ID": row.get("log_id", f"LOG-{idx+1}"),
                            "Fonte": outcome.source,
                            "Serviço": res.service_name,
                            "Severidade": res.severity.value,
                            "Causa Raiz": res.root_cause,
                            "Requer Aprovação": res.requires_human_intervention
                        })
                    progress_bar.progress((idx + 1) / total)

                df_results = pd.DataFrame(results)
                st.session_state["batch_results"] = df_results

    if "batch_results" in st.session_state:
        df_results = st.session_state["batch_results"]
        st.markdown("#### 📈 Resultado da Triagem Agrupada")
        st.dataframe(df_results, use_container_width=True)

        st.markdown("##### Distribuição de Severidade")
        st.bar_chart(df_results["Severidade"].value_counts())
