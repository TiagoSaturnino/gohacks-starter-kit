import streamlit as st
from src.mocks import MOCK_LOGS
from src.analyzer import analyze_log

st.set_page_config(page_title="GoHacks - AI Log RCA", page_icon="⚡", layout="wide")

st.title("⚡ GoGroup AI-First Log Triage & RCA Engine")
st.caption("Agente Inteligente de Triagem e Causa Raiz com Trava Human-in-the-Loop (HITL)")

st.sidebar.header("⚙️ Configurações da Demo")
selected_log_key = st.sidebar.selectbox(
    "Selecione um Log de Teste (Mock):",
    options=list(MOCK_LOGS.keys()),
    format_func=lambda x: x.replace("_", " ").title()
)

log_input = st.text_area(
    "Payload do Log de Erro (Sentry / CloudWatch / Webhooks):",
    value=MOCK_LOGS[selected_log_key],
    height=180
)

if st.button("🚀 Executar Triagem & RCA", type="primary"):
    with st.spinner("Processando log e validando guardrails..."):
        result = analyze_log(log_input)

    st.success("Análise Pydantic concluída com sucesso!")

    col1, col2, col3 = st.columns(3)
    col1.metric("Serviço Impactado", result.service_name)
    col2.metric("Nível de Severidade", result.severity.value)
    col3.metric("Confiança do Modelo", f"{int(result.confidence_score * 100)}%")

    st.markdown("---")
    st.markdown("### 🔍 Diagnóstico do Incidente")
    st.subheader(result.summary)
    st.info(f"**Causa Raiz Identificada:** {result.root_cause}")

    st.markdown("---")
    st.markdown("### 🛠️ Ações Recomendadas & Trava Human-in-the-Loop")

    if result.requires_human_intervention:
        st.warning("⚠️ **Guardrail Ativo:** Esta ocorrência exige aprovação explícita de um operador humano para execução de ações de mitigação.")

    for idx, action in enumerate(result.recommended_actions):
        with st.expander(f"Ação #{idx+1}: {action.action_type.value} - {action.description}", expanded=True):
            st.write(f"**Requer Validação Humana?** {'🔴 SIM (HITL Guardrail)' if action.requires_human_approval else '🟢 NÃO (Automação Direta)'}")
            
            if action.payload:
                st.caption("Payload técnico de execução:")
                st.json(action.payload)

            if action.requires_human_approval:
                if st.button(f"✅ Aprovar & Executar Ação #{idx+1}", key=f"btn_approve_{idx}"):
                    st.balloons()
                    st.success(f"Ação [{action.action_type.value}] APROVADA por Tiago Saturnino e enviada para a fila de execução!")

    with st.expander("📄 Ver Saída JSON Estruturada (Pydantic Schema)"):
        st.json(result.model_dump())
