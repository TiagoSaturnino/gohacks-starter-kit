"""Centro de operações: triagem + RCA com guardrails e aprovação humana (HITL).

Execute com:  streamlit run app.py
"""
import logging
import time

import pandas as pd
import streamlit as st

from src.analyzer import AnalysisOutcome, analyze_log, build_tool
from src.config import Settings, load_settings
from src.executors import build_executor
from src.pii import mask_pii
from src.scenarios import Scenario, list_scenarios, load_scenario
from src.store import Store, make_incident_id
from src.telemetry import persist_outcome
from src.timeutil import format_local, now_local

logger = logging.getLogger("gohacks.app")

st.set_page_config(page_title="GoHacks - Triagem & RCA", page_icon="⚡", layout="wide")

SEVERITY_BADGE = {
    "CRITICAL": ("red", "Crítico"),
    "HIGH": ("orange", "Alto"),
    "MEDIUM": ("yellow", "Médio"),
    "LOW": ("green", "Baixo"),
}
SOURCE_BADGE = {
    "LIVE": ("green", "Live API"),
    "MOCK": ("blue", "Replay offline"),
    "DEGRADED": ("red", "Fail-closed"),
}
MODE_SINGLE = "Análise individual"
MODE_BATCH = "Lote (CSV)"


def badge(text: str, color: str) -> str:
    return f":{color}-badge[{text}]"


@st.cache_resource
def get_store(path: str, tz_name: str) -> Store:
    return Store(path, tz_name)


def read_csv_flexible(uploaded) -> pd.DataFrame:
    try:
        return pd.read_csv(uploaded)
    except UnicodeDecodeError:
        uploaded.seek(0)
        return pd.read_csv(uploaded, encoding="latin-1")


# --------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------
def render_sidebar(settings: Settings, executor):
    st.sidebar.title("⚙️ Configuração")

    if settings.live_enabled:
        st.sidebar.success(f"🟢 Live API · {settings.model}")
    else:
        st.sidebar.info("🟡 Modo offline: replay de fixtures do cenário")

    names = list_scenarios()
    if not names:
        st.error("Nenhum cenário encontrado na pasta `scenarios/`.")
        st.stop()
    default_index = names.index(settings.scenario) if settings.scenario in names else 0
    scenario_name = st.sidebar.selectbox("Cenário", names, index=default_index, key="scenario")
    try:
        scenario = load_scenario(scenario_name)
    except Exception as error:  # noqa: BLE001 - JSON de cenário inválido
        st.error(f"Cenário '{scenario_name}' inválido: {error}")
        st.stop()

    mode = st.sidebar.radio("Modo de operação", [MODE_SINGLE, MODE_BATCH], key="mode")

    st.sidebar.markdown("---")
    operator = st.sidebar.text_input(
        "Operador (registrado na auditoria)", key="operator", placeholder="seu nome"
    )
    manual_minutes = st.sidebar.number_input(
        "Premissa: minutos por incidente na triagem manual",
        min_value=1,
        value=settings.manual_minutes_per_incident,
        key="manual_minutes",
        help="Base do cálculo de redução do tempo de diagnóstico. O tempo da análise é medido.",
    )
    st.sidebar.markdown("---")
    exec_label = "simulado (dry-run)" if executor.name == "dry_run" else "outbox (worker externo)"
    st.sidebar.caption(f"Execução: {exec_label}")
    st.sidebar.caption(f"Horários em {settings.timezone}")
    if not settings.pricing_configured:
        st.sidebar.caption("Custo: defina PRICE_INPUT_PER_MTOK e PRICE_OUTPUT_PER_MTOK no .env.")
    return scenario, operator.strip(), int(manual_minutes), mode


# --------------------------------------------------------------------------
# Aba 1: diagnóstico e ações
# --------------------------------------------------------------------------
def render_fail_closed(state):
    outcome: AnalysisOutcome = state["outcome"]
    persisted = state["persisted"]
    st.error(
        "🚨 **Política de segurança acionada (fail-closed).** "
        "A análise não pôde ser concluída e nenhuma automação foi liberada."
    )
    st.markdown(f"**Motivo:** {outcome.error}")
    lines = ["🔒 Automação **bloqueada**: nenhuma ação é oferecida nem executada."]
    if persisted.triage_id:
        suffix = "" if persisted.triage_created else " (já estava na fila)"
        lines.append(
            f"📨 Incidente **registrado na fila de triagem manual N2** (#{persisted.triage_id}){suffix}. "
            "Veja a aba de auditoria."
        )
    else:
        lines.append(
            "⚠️ Não foi possível registrar o incidente na fila N2 "
            f"({persisted.error or 'erro desconhecido'}). Encaminhe manualmente."
        )
    st.markdown("\n".join(f"- {line}" for line in lines))
    with st.expander("Detalhes técnicos"):
        st.code(f"reason={outcome.reason}\n{outcome.error_detail or ''}")


def render_kpis(outcome: AnalysisOutcome, manual_minutes: int):
    res, conf, meta = outcome.result, outcome.confidence, outcome.meta
    c1, c2, c3, c4 = st.columns(4)
    c1.caption("Serviço impactado")
    c1.markdown(f"### {res.service_name}")
    c2.metric("Confiança declarada (modelo)", f"{conf.declared:.0%}")
    c3.metric(
        "Confiança efetiva (política)",
        f"{conf.effective:.0%}",
        delta=f"{(conf.effective - conf.declared) * 100:+.0f} p.p.",
        delta_color="off",
    )
    live = outcome.source == "LIVE"
    seconds = meta.latency_ms / 1000
    c4.metric("Tempo de análise (medido)" if live else "Tempo de análise",
              f"{seconds:.2f}s" if live else "n/d (offline)")

    d1, d2, d3 = st.columns(3)
    if live:
        manual_s = manual_minutes * 60
        reduction = max(0.0, (1 - seconds / manual_s) * 100)
        d1.metric(
            "Redução do tempo de diagnóstico",
            f"{reduction:.2f}%",
            delta=f"{max(manual_s - seconds, 0):.0f}s a menos que a premissa de {manual_minutes} min",
            delta_color="off",
        )
        if meta.cost_usd is not None:
            d2.metric("Custo estimado da análise", f"US$ {meta.cost_usd:.4f}")
        else:
            d2.metric("Custo estimado da análise", "defina os preços")
        d3.metric("Tokens (entrada / saída)", f"{meta.input_tokens} / {meta.output_tokens}")
    else:
        d1.metric("Redução do tempo de diagnóstico", "n/d (offline)")
        d2.metric("Custo estimado da análise", "n/d (offline)")
        d3.metric("Tokens (entrada / saída)", "n/d (offline)")
    st.caption(
        "Redução = (premissa manual − tempo medido da análise) ÷ premissa manual. "
        "Mede o tempo de diagnóstico, não o MTTR completo (que inclui aprovação e execução)."
    )


def render_approval_controls(action, idx, state, store, executor, operator, settings):
    incident_id = state["incident_id"]
    decision = store.get_decision(incident_id, action.action_type.value, action.payload)

    if decision:
        who = decision["approver"]
        when = format_local(decision["ts"], settings.timezone)
        if decision["decision"] == "APPROVED":
            st.success(
                f"✅ Aprovada por **{who}** em {when} · "
                f"{decision['execution_status']}: {decision['execution_detail'] or 'aguardando execução'}"
            )
        else:
            st.warning(f"⛔ Rejeitada por **{who}** em {when}.")
        if decision.get("decision_seconds") is not None:
            st.caption(f"Tempo entre a análise e a decisão humana: {decision['decision_seconds']:.1f}s")
        return

    if not action.requires_human_approval:
        st.info("Registrada para acompanhamento; não requer aprovação nem execução.")
        return

    if not operator:
        st.caption("Informe o operador na barra lateral para aprovar ou rejeitar.")
    col_ok, col_no, _ = st.columns([1, 1, 4])
    approve = col_ok.button("✅ Aprovar", key=f"approve_{incident_id}_{idx}", disabled=not operator)
    reject = col_no.button("⛔ Rejeitar", key=f"reject_{incident_id}_{idx}", disabled=not operator)
    if approve or reject:
        decision_name = "APPROVED" if approve else "REJECTED"
        elapsed = time.time() - state["analyzed_at"]
        decision_id, created = store.record_decision(
            incident_id, action.action_type.value, action.payload,
            operator, decision_name, state["outcome"].source, decision_seconds=elapsed,
        )
        if created and approve:
            execution = executor.execute(decision_id, action)
            store.set_execution(decision_id, execution.status, execution.detail)
        st.rerun()


def render_diagnosis(state, store, executor, operator, manual_minutes, settings):
    outcome: AnalysisOutcome = state["outcome"]
    if outcome.automation_blocked:
        render_fail_closed(state)
        return

    res = outcome.result
    sev_color, sev_label = SEVERITY_BADGE.get(res.severity.value, ("gray", res.severity.value))
    src_color, src_label = SOURCE_BADGE[outcome.source]
    hitl = (
        badge("Revisão humana obrigatória", "red")
        if res.requires_human_intervention
        else badge("Sem aprovação exigida", "green")
    )
    env = badge("Execução SIMULADA", "gray") if executor.name == "dry_run" else badge("Execução via OUTBOX", "violet")
    st.markdown(
        " ".join([badge(f"Severidade: {sev_label}", sev_color), badge(src_label, src_color), hitl, env])
    )
    if outcome.source == "MOCK":
        st.info("ℹ️ Replay de fixture (modo offline): não houve chamada ao modelo.")
    for warning in outcome.warnings:
        st.warning(f"⚠️ {warning}")

    render_kpis(outcome, manual_minutes)

    st.markdown("### 🔍 Diagnóstico")
    box = {"CRITICAL": st.error, "HIGH": st.warning}.get(res.severity.value, st.info)
    box(f"**Causa raiz:** {res.root_cause}")

    st.markdown("### 🛠️ Ações recomendadas e aprovação humana (HITL)")
    for idx, action in enumerate(res.recommended_actions):
        label = f"Ação #{idx + 1}: {action.action_type.value} — {action.description}"
        with st.expander(label, expanded=True):
            policy = (
                badge("Exige aprovação humana", "red")
                if action.requires_human_approval
                else badge("Registro apenas", "green")
            )
            st.markdown(policy)
            if action.payload:
                st.caption("Payload técnico:")
                st.json(action.payload)
            render_approval_controls(action, idx, state, store, executor, operator, settings)


# --------------------------------------------------------------------------
# Aba 2: segurança, PII e schema
# --------------------------------------------------------------------------
def render_security(log_text: str, state, scenario: Scenario, settings: Settings):
    masked = mask_pii(log_text)
    sent_note = (
        "único conteúdo enviado à API" if settings.live_enabled
        else "prévia do que seria enviado (modo offline não envia nada)"
    )
    left, right = st.columns(2)
    left.markdown("**Log original** (permanece local)")
    left.code(log_text or "(vazio)", language="text", wrap_lines=True)
    right.markdown(f"**Log mascarado** ({sent_note})")
    right.code(masked.text or "(vazio)", language="text", wrap_lines=True)

    if masked.counts:
        st.dataframe(
            pd.DataFrame([{"Tipo": k, "Ocorrências": v} for k, v in masked.counts.items()]),
            width="stretch",
        )
    else:
        st.caption("Nenhum dado pessoal detectado por regex.")
    st.caption("Limitação: nomes em texto livre não são detectados por regex (ver README).")

    st.markdown("---")
    st.markdown("#### Schema estrito (Pydantic v2)")
    if not state or state["outcome"].automation_blocked:
        st.info("Sem saída estruturada: nada foi aceito pelo schema (ou a análise ainda não rodou).")
    else:
        outcome = state["outcome"]
        st.success(
            "✅ Saída validada: tipos, limites de tamanho, campos extras proibidos, payload plano "
            "e ações permitidas pelo cenário."
        )
        conf = outcome.confidence
        st.markdown(
            f"**Confiança:** declarada {conf.declared:.0%} → efetiva {conf.effective:.0%}"
            + (" · revisão humana forçada" if conf.forced_review else "")
        )
        for reason in conf.reasons or ["Sem ajustes por política."]:
            st.caption(f"• {reason}")
        st.json(outcome.result.model_dump(mode="json"))

    with st.expander("Schema JSON enviado ao modelo (tool)"):
        st.caption(
            "Os campos requires_human_approval / requires_human_intervention não aparecem aqui: "
            "são decididos em código, não pelo modelo."
        )
        st.json(build_tool(scenario)["input_schema"])


# --------------------------------------------------------------------------
# Aba 3: auditoria e outbox
# --------------------------------------------------------------------------
def _table(rows, columns, tz_name):
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame = frame[[c for c in columns if c in frame.columns]].copy()
    if "ts" in frame.columns:
        frame["ts"] = frame["ts"].map(lambda value: format_local(value, tz_name))
    return frame


@st.fragment(run_every="5s")
def audit_panel(store: Store, tz_name: str):
    stats = store.analytics()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Análises registradas", int(stats["total"]))
    c2.metric("Fail-closed (sem análise)", int(stats["degraded"]))
    avg = stats["avg_latency_live_ms"]
    c3.metric("Latência média (LIVE)", f"{avg / 1000:.2f}s" if avg else "n/d")
    c4.metric("Custo acumulado (USD)", f"{stats['cost_usd']:.4f}" if stats["cost_usd"] else "n/d")
    st.caption(
        f"Atualizado às {now_local(tz_name).strftime('%H:%M:%S')} · atualização automática a cada 5 s"
    )

    sections = [
        ("Decisões humanas (trilha de auditoria)", store.decisions(),
         ["ts", "incident_id", "action_type", "decision", "approver", "execution_status",
          "decision_seconds", "source"], "Nenhuma decisão registrada ainda."),
        ("Outbox (ações aprovadas aguardando worker)", store.outbox(),
         ["ts", "decision_id", "action_type", "payload_json", "status"],
         "Outbox vazia (use EXECUTOR=outbox para enfileirar ações aprovadas)."),
        ("Fila de triagem manual N2 (fail-closed)", store.manual_triage(),
         ["ts", "incident_id", "reason", "detail", "status"], "Nenhum incidente na fila N2."),
        ("Análises (latência, tokens e custo reais)", store.analyses(),
         ["ts", "source", "reason", "latency_ms", "input_tokens", "output_tokens", "cost_usd",
          "severity", "declared_confidence", "effective_confidence"], "Nenhuma análise ainda."),
    ]
    for title, rows, columns, empty_message in sections:
        st.markdown(f"**{title}**")
        if rows:
            st.dataframe(_table(rows, columns, tz_name), width="stretch", hide_index=True)
        else:
            st.caption(empty_message)


# --------------------------------------------------------------------------
# Análise individual (abas)
# --------------------------------------------------------------------------
def render_single(settings, scenario: Scenario, store, executor, operator, manual_minutes):
    fixtures = scenario.fixture_logs()
    selected = None
    if fixtures:
        selected = st.sidebar.selectbox(
            "Log de exemplo",
            list(fixtures.keys()),
            format_func=lambda key: key.replace("_", " ").title(),
            key=f"fixture_{scenario.id}",
        )
    log_input = st.text_area(
        "Log do incidente (Sentry / CloudWatch / webhook):",
        value=fixtures.get(selected, ""),
        height=150,
        key=f"log_input_{scenario.id}_{selected}",
    )

    if st.button("🚀 Executar triagem e RCA", type="primary", key="run_single"):
        with st.spinner("Analisando..."):
            outcome = analyze_log(log_input, scenario=scenario, settings=settings)
        incident_id = make_incident_id(log_input)
        persisted = persist_outcome(store, incident_id, scenario.id, outcome)
        st.session_state["single"] = {
            "outcome": outcome,
            "incident_id": incident_id,
            "log": log_input,
            "analyzed_at": time.time(),
            "persisted": persisted,
        }

    state = st.session_state.get("single")
    tab_diag, tab_sec, tab_audit = st.tabs(
        ["🩺 Diagnóstico e ações (HITL)", "🔒 Segurança, PII e schema", "🗂️ Auditoria e outbox"]
    )
    with tab_diag:
        if state:
            render_diagnosis(state, store, executor, operator, manual_minutes, settings)
        else:
            st.info("Escolha um log e clique em **Executar triagem e RCA**.")
    with tab_sec:
        render_security(state["log"] if state else log_input, state, scenario, settings)
    with tab_audit:
        audit_panel(store, settings.timezone)


# --------------------------------------------------------------------------
# Lote
# --------------------------------------------------------------------------
def outcome_to_row(row_id, outcome: AnalysisOutcome) -> dict:
    meta = outcome.meta
    base = {
        "ID": row_id,
        "Fonte": outcome.source,
        "Motivo": outcome.reason or "",
        "Latência (ms)": meta.latency_ms,
        "Tokens": (meta.input_tokens or 0) + (meta.output_tokens or 0),
        "Custo (USD)": meta.cost_usd,
    }
    if outcome.result is None:
        return {
            **base, "Serviço": "N/A", "Severidade": "UNKNOWN", "Confiança efetiva": None,
            "Causa raiz / erro": f"FALHA: {outcome.error}", "Requer aprovação": True, "Avisos": "",
        }
    result = outcome.result
    return {
        **base, "Serviço": result.service_name, "Severidade": result.severity.value,
        "Confiança efetiva": outcome.confidence.effective, "Causa raiz / erro": result.root_cause,
        "Requer aprovação": result.requires_human_intervention, "Avisos": " | ".join(outcome.warnings),
    }


def render_batch(settings, scenario: Scenario, store: Store):
    st.markdown("### 📊 Processamento em lote")
    st.write(
        f"CSV com a coluna `raw_log` ou `log` (máx. {settings.max_batch_rows} linhas; "
        "logs idênticos são analisados uma única vez). Linhas sem análise vão para a fila N2."
    )

    uploaded = st.file_uploader("Carregar CSV de logs", type=["csv"], key="batch_upload")
    frame = None
    if uploaded is not None:
        try:
            frame = read_csv_flexible(uploaded)
        except Exception as error:  # noqa: BLE001 - mostrar erro amigável
            st.error(f"Erro ao ler o CSV: {error}")
    elif st.checkbox("Usar exemplos do cenário", key="batch_sample"):
        fixtures = scenario.fixture_logs()
        frame = pd.DataFrame({"log_id": list(fixtures.keys()), "raw_log": list(fixtures.values())})

    if frame is not None:
        if len(frame) > settings.max_batch_rows:
            st.warning(f"O lote excede {settings.max_batch_rows} linhas; processando as primeiras.")
            frame = frame.head(settings.max_batch_rows)
        st.dataframe(frame.head(), width="stretch")

        column = next((name for name in ("raw_log", "log") if name in frame.columns), None)
        if column is None:
            st.error("O CSV precisa ter uma coluna chamada `raw_log` ou `log`.")
        elif st.button("⚡ Executar triagem do lote", key="run_batch"):
            rows, cache = [], {}
            progress = st.progress(0.0)
            total = len(frame)
            for position, (_, record) in enumerate(frame.iterrows(), start=1):
                text = str(record[column])
                key = " ".join(text.split())
                if key not in cache:
                    outcome = analyze_log(text, scenario=scenario, settings=settings)
                    persist_outcome(store, make_incident_id(text), scenario.id, outcome)
                    cache[key] = outcome
                row_id = record["log_id"] if "log_id" in frame.columns else f"LOG-{position}"
                rows.append(outcome_to_row(row_id, cache[key]))
                progress.progress(position / total)
            st.session_state["batch"] = pd.DataFrame(rows)

    results = st.session_state.get("batch")
    if results is not None:
        st.markdown("#### 📈 Resultado")
        live = results[results["Fonte"] == "LIVE"]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Processados", len(results))
        c2.metric("Sem análise (DEGRADED)", int((results["Fonte"] == "DEGRADED").sum()))
        c3.metric(
            "Latência média (LIVE)",
            f"{live['Latência (ms)'].mean() / 1000:.2f}s" if len(live) else "n/d",
        )
        costs = live["Custo (USD)"].dropna()
        c4.metric("Custo estimado (LIVE)", f"US$ {costs.sum():.4f}" if len(costs) else "n/d")
        st.dataframe(results, width="stretch")
        st.markdown("##### Distribuição de severidade")
        distribution = (
            results["Severidade"].value_counts().rename_axis("Severidade").reset_index(name="Qtd")
        )
        st.bar_chart(distribution, x="Severidade", y="Qtd")
        st.download_button(
            "⬇️ Baixar resultados (CSV)",
            results.to_csv(index=False).encode("utf-8-sig"),
            file_name="triagem_lote.csv",
            mime="text/csv",
            key="download_batch",
        )


# --------------------------------------------------------------------------
# Main (com rede de segurança: a tela nunca mostra traceback bruto)
# --------------------------------------------------------------------------
def main():
    settings = load_settings()
    store = get_store(settings.audit_db, settings.timezone)
    executor = build_executor(settings, store)
    scenario, operator, manual_minutes, mode = render_sidebar(settings, executor)

    st.title("⚡ Centro de operações: triagem e RCA com IA")
    st.caption(
        f"Cenário: {scenario.name} · schema Pydantic + ancoragem no log + confiança efetiva "
        "+ aprovação humana + fail-closed"
    )

    if mode == MODE_SINGLE:
        render_single(settings, scenario, store, executor, operator, manual_minutes)
    else:
        render_batch(settings, scenario, store)


def run():
    try:
        main()
    except Exception as error:  # noqa: BLE001
        # st.rerun()/st.stop() são implementados com exceções: precisam passar.
        if type(error).__name__ in ("RerunException", "StopException", "ScriptControlException"):
            raise
        logger.exception("falha inesperada na interface")
        st.error(
            "🚨 Falha inesperada na interface. Política fail-closed: nenhuma ação foi executada. "
            "Recarregue a página; o detalhe técnico está no log do servidor."
        )
        with st.expander("Detalhes técnicos"):
            st.code(f"{type(error).__name__}: {error}")


run()
