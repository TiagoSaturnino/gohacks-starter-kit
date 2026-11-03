import os

app_code = '''import pandas as pd
import streamlit as st
from src.config import load_settings
from src.scenarios import load_scenario, list_scenarios
from src.analyzer import analyze_log
from src.store import Store, make_incident_id
from src.executors import build_executor
from src.pii import mask_pii

st.set_page_config(page_title="GoHacks - Triagem & RCA Engine", page_icon="⚡", layout="wide")

@st.cache_resource
def get_store(path: str) -> Store:
    return Store(path)

def main():
    settings = load_settings()
    store = get_store(settings.audit_db)
    executor = build_executor(settings, store)

    st.sidebar.title("⚙️ Configuração & Parâmetros")
    
    if settings.live_enabled:
        st.sidebar.success(f"🟢 Live API · {settings.model}")
    else:
        st.sidebar.info("🟡 Modo Offline: Replay de Fixtures")

    scenario_names = list_scenarios()
    if not scenario_names:
        st.error("Nenhum cenário encontrado na pasta scenarios/.")
        return

    scenario_name = st.sidebar.selectbox("Cenário de Domínio", scenario_names)
    scenario = load_scenario(scenario_name)

    mode = st.sidebar.radio("Modo de Operação", ["Análise Individual (Live Demo)", "Processamento em Lote (CSV)"])
    
    st.sidebar.markdown("---")
    operator = st.sidebar.text_input("Operador (HITL)", value="Tiago Saturnino", help="Nome registrado na trilha de auditoria")

    st.title("⚡ GoGroup AI-First Log Triage & RCA Engine")
    st.caption(f"Cenário Ativo: **{scenario.name}** · Guardrails Pydantic + Ancoragem de Identificadores + HITL em Código")

    if mode == "Análise Individual (Live Demo)":
        fixtures = scenario.fixture_logs()
        selected_key = st.sidebar.selectbox(
            "Selecione um Log de Teste:",
            list(fixtures.keys()),
            format_func=lambda x: x.replace("_", " ").title()
        )
        
        log_input = st.text_area(
            "Payload do Log de Erro (Sentry / CloudWatch / Webhooks):",
            value=fixtures[selected_key],
            height=160
        )

        with st.expander("🔒 Visualizar Payload Sanitizado (PII Masking)"):
            masked = mask_pii(log_input)
            st.code(masked.text)
            if masked.counts:
                st.caption(f"Itens mascarados via LGPD: {masked.counts}")

        if st.button("🚀 Executar Triagem & RCA", type="primary"):
            with st.spinner("Analisando log com guardrails V2..."):
                outcome = analyze_log(log_input, scenario=scenario, settings=settings)
                incident_id = make_incident_id(log_input)
                st.session_state["analysis_outcome"] = outcome
                st.session_state["incident_id"] = incident_id

        if "analysis_outcome" in st.session_state:
            outcome = st.session_state["analysis_outcome"]
            incident_id = st.session_state["incident_id"]

            if outcome.source == "DEGRADED":
                st.error(f"🚨 **Análise Indisponível:** {outcome.error}")
                st.warning("O incidente foi redirecionado para triagem manual N2.")
            else:
                res = outcome.result
                if outcome.source == "MOCK":
                    st.info("ℹ️ Resultado gerado via Replay de Fixture (Modo Offline).")
                else:
                    st.success("🟢 Análise em tempo real validada pelo Schema Pydantic!")

                for warning in outcome.warnings:
                    st.warning(f"⚠️ {warning}")

                # Métricas
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Serviço Impactado", res.service_name)
                c2.metric("Severidade", res.severity.value)
                c3.metric("Confiança (Modelo)", f"{int(res.confidence_score * 100)}%")
                c4.metric("SLA Estimado", "~25 min", delta="98% mais rápido")

                st.markdown("---")
                st.markdown("### 🔍 Diagnóstico do Incidente")
                if res.severity.value in ["HIGH", "CRITICAL"]:
                    st.error(f"**Causa Raiz Identificada:** {res.root_cause}")
                else:
                    st.warning(f"**Causa Raiz Identificada:** {res.root_cause}")

                st.markdown("---")
                st.markdown("### 🛠️ Ações Recomendadas & Trava Human-in-the-Loop (HITL)")
                
                if res.requires_human_intervention:
                    st.warning("⚠️ **Guardrail HITL Ativo:** Exige aprovação explícita antes de qualquer execução em sistemas.")

                for idx, action in enumerate(res.recommended_actions):
                    with st.expander(f"Ação #{idx+1}: {action.action_type.value} - {action.description}", expanded=True):
                        st.write(f"**Política de Segurança:** {'🔴 Exige Aprovação Humana (HITL)' if action.requires_human_approval else '🟢 Automação Direta'}")
                        
                        if action.payload:
                            st.caption("Payload técnico para automação:")
                            st.json(action.payload)

                        # Estado de decisão no SQLite
                        decision = store.get_decision(incident_id, action.action_type.value, action.payload)
                        
                        if decision:
                            if decision["decision"] == "APPROVED":
                                st.success(f"✅ Aprovada por **{decision['approver']}** em {decision['ts']} · Status: {decision['execution_status']}")
                            else:
                                st.error(f"⛔ Rejeitada por **{decision['approver']}** em {decision['ts']}")
                        else:
                            col_app, col_rej, _ = st.columns([1, 1, 3])
                            if col_app.button(f"✅ Aprovar Ação #{idx+1}", key=f"btn_app_{idx}"):
                                dec_id, created = store.record_decision(incident_id, action.action_type.value, action.payload, operator, "APPROVED", outcome.source)
                                if created:
                                    exec_res = executor.execute(dec_id, action)
                                    store.set_execution(dec_id, exec_res.status, exec_res.detail)
                                st.balloons()
                                st.rerun()
                            
                            if col_rej.button(f"⛔ Rejeitar Ação #{idx+1}", key=f"btn_rej_{idx}"):
                                store.record_decision(incident_id, action.action_type.value, action.payload, operator, "REJECTED", outcome.source)
                                st.rerun()

                with st.expander("📄 Ver Saída JSON Estruturada (Pydantic Schema)"):
                    st.json(res.model_dump())

        st.markdown("---")
        with st.expander("🗂️ Trilha de Auditoria SQLite"):
            decisions = store.decisions()
            if decisions:
                st.dataframe(pd.DataFrame(decisions), use_container_width=True)
            else:
                st.caption("Nenhuma aprovação registrada ainda.")

if __name__ == "__main__":
    main()
'''

with open("app.py", "w", encoding="utf-8") as f:
    f.write(app_code)
print("app.py atualizado com a interface completa V2!")
