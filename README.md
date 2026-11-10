# 🛡️ Guardião de Pagamentos — AI Governance & Resilient Batch Engine

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)
![Pytest](https://img.shields.io/badge/Tests-129%20Passed-success?logo=pytest)
![Architecture](https://img.shields.io/badge/Architecture-Modular%20%2F%20Clean-orange)
![License](https://img.shields.io/badge/License-MIT-green)

> Engine autônoma em Python voltada para governança financeira, detecção de riscos em lotes de remessa bancária, priorização por horário de fechamento e workflows de *Human-in-the-Loop* via Slack.

---

## 💡 O Problema & A Solução

Processar lotes de pagamento de forma puramente manual ou sem validações estritas expõe a empresa a duplicidades, fraudes e falhas operacionais críticas ("abrir a torneira" de dinheiro por erro). 

O **Guardião de Pagamentos** resolve esse gargalo através de 4 pilares:
1. **Regras Determinísticas Offline:** Validação estrita de regras de negócio (R1 a R8 + A1) para identificar inconsistências e duplicidades de títulos.
2. **Severidade Granular & Fail-Closed:** Apenas severidades do tipo `BLOQUEIA` retêm pagamentos. Em caso de falha de sistema, o motor adota *fail-closed* por segurança.
3. **Priorização por Urgência & Impacto:** Classifica demandas cruzando o horário limite de fechamento do lote com o valor financeiro consolidado em R\$.
4. **Agente com Humano no Circuito (*Human-in-the-Loop*):** Disparo de alertas interativos estruturados no Slack via Block Kit para aprovação gerencial.

---

## 📐 Arquitetura do Sistema

```mermaid
graph TD
    A[Agenda de Remessa / CSV] --> B[src/carga.py: Ingestão & Políticas]
    B --> C[src/checks.py: Regras Determinísticas]
    C --> D[src/priorizar.py: Scoring & Urgência]
    D --> E[src/rotear.py: Fail-Closed & Decisão]
    E -->|Bloqueia| F[src/slack.py: Alerta Block Kit / Slack]
    E -->|Salva| G[data/guardiao.db: SQLite Audit Trail]
    E -->|Gera| H[app.py: Streamlit Dashboard Executivo]
```
🛠️ Funcionalidades PrincipaisMóduloResponsabilidade Técnicasrc/checks.pyAplicação de regras estáticas de conformidade e detecção de riscos.src/priorizar.pyCálculo de score considerando o horário de fechamento e valor financeiro (R$).src/rotear.pyLógica de decisão baseada em severidade (BLOQUEIA vs ACIONA) com fail-closed.src/slack.pyIntegração via Webhook com Slack (Block Kit e botões interativos).src/guardiao.pyOrquestrador CLI central e consolidação de relatórios executivos em JSON.app.pyInterface interativa em Streamlit para auditoria executiva e filtros de lotes.🚀 Como Executar o Projeto LocalmentePré-requisitosPython 3.10+GitPasso a PassoClonar o repositório:Bashgit clone [https://github.com/TiagoSaturnino/gohacks-starter-kit.git](https://github.com/TiagoSaturnino/gohacks-starter-kit.git)
cd gohacks-starter-kit
Criar e ativar o ambiente virtual:Bashpython -m venv .venv
# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1
# Linux/Mac:
source .venv/bin/activate
Instalar dependências:Bashpip install -r requirements.txt
Configurar variáveis de ambiente (Opcional - Slack):
Crie um arquivo .env na raiz do projeto:Snippet de códigoSLACK_WEBHOOK_URL="sua_url_do_webhook_aqui"
Executar a suíte de testes:Bashpytest
Rodar o pipeline do Guardião (Modo CLI):Bashpython -m src.guardiao --agora 2026-11-02T08:00 --sem-ia --sem-slack
Iniciar a interface Streamlit:Bashpython -m streamlit run app.py
🧪 Cobertura de TestesO projeto conta com suíte de testes unitários e de integração automatizados via pytest:Bashroot@career-ops:# pytest -v
========================== test session starts ==========================
tests/test_checks.py ........................................... [PASS]
tests/test_e2e.py .............................................. [PASS]
tests/test_gateway.py .......................................... [PASS]
========================== 129 passed in 14.91s ==========================
