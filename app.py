import os
import streamlit as st
from dotenv import load_dotenv
from src.mocks import MOCK_LOGS
from src.analyzer import analyze_log

load_dotenv()

st.set_page_config(page_title="GoHacks - AI Log RCA", page_icon="⚡", layout="wide")

# Barra Lateral (Sidebar)
st.sidebar.title("⚙️ Configurações da Demo")

use_mock = os.getenv("USE_MOCK", "True").lower() in ("true", "1", "yes")
if use_mock:
    st.sidebar.info("🟡 **Modo:** Mock Offline (Alta Velocidade)")
else:
    st.sidebar.success("🟢 **Modo:** Anthropic Claude 3.5 Sonnet (Live API)")

selected_log_key = st.sidebar.selectbox(
    "Selecione um Log de Teste (Mock):",
    options=list(MOCK_LOGS.keys()),
    format_func=lambda x: x.replace("_", " ").title()
)

# Conteúdo Principal
st.title("⚡ GoGroup AI-First Log Triage & RCA Engine")
st.caption("Agente Inteligente de Triagem e Causa Raiz com Guardrail Human-in-the-Loop (HITL)")

log_input = st.text_area(
    "Payload do Log de Erro (Sentry / CloudWatch / Webhooks):",
    value=MOCK_LOGS[selected_log_key],
    height=170
)

if st.button("🚀 Executar Triagem & RCA", type="primary"):
    with st.spinner("Analisando log com guardrails Pydantic..."):
        result = analyze_log(log_input)

    st.success("Análise concluída e validada pelo Pydantic Schema!")

    # Linha de Métricas
    col1, col2, col3 = st.columns(3)
    col1.metric("Serviço Impactado", result.service_name)
    col2.metric("Nível de Severidade", result.severity.value)
    col3.metric("Confiança da IA", f"{int(result.confidence_score * 100)}%")

    st.markdown("---")
    st.markdown("### 🔍 Diagnóstico do Incidente")
    
    # Destaque de Severidade por Cor
    if result.severity.value in ["HIGH", "CRITICAL"]:
        st.error(f"**Causa Raiz Identificada:** {result.root_cause}")
    else:
        st.warning(f"**Causa Raiz Identificada:** {result.root_cause}")

    st.markdown("---")
    st.markdown("### 🛠️ Ações Recomendadas & Trava Human-in-the-Loop (HITL)")

    if result.requires_human_intervention:
        st.warning("⚠️ **Guardrail HITL Ativo:** Ações críticas exigem aprovação explícita do operador antes da execução.")

    for idx, action in enumerate(result.recommended_actions):
        with st.expander(f"Ação #{idx+1}: {action.action_type.value} - {action.description}", expanded=True):
            st.write(f"**Requer Validação Humana?** {'🔴 SIM (Trava HITL Ativa)' if action.requires_human_approval else '🟢 NÃO (Automação Direta)'}")
            
            if action.payload:
                st.caption("Payload técnico para automação:")
                st.json(action.payload)

            if action.requires_human_approval:
                if st.button(f"✅ Aprovar & Executar Ação #{idx+1}", key=f"btn_approve_{idx}"):
                    st.balloons()
                    st.success(f"Ação [{action.action_type.value}] APROVADA por Tiago Saturnino e enviada para execução!")

    with st.expander("📄 Ver Saída JSON Estruturada (Pydantic Schema)"):
        st.json(result.model_dump())
