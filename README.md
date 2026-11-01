# ⚡ GoHacks Starter Kit 2026 - GoGroup AI-First Triage & RCA Engine

Repositório base desenvolvido para a trilha de **Tecnologia, Engenharia e Dados** do **GoHacks Talent 2026**.

## 🎯 Proposta de Valor & ROI
Agente inteligente de Triagem e Análise de Causa Raiz (RCA) para integrações de e-commerce (Mercado Livre, Amazon SP-API, Webhooks). 
Reduz o **MTTR (Mean Time to Resolution)** de horas para segundos, eliminando análises manuais repetitivas e garantindo segurança operacional.

## 🛡️ Pilares AI-First do GoGroup Implementados
1. **Zero Alucinação (Pydantic v2 Guardrails):** Toda saída do modelo é estritamente validada por tipos estruturados.
2. **Human-in-the-Loop (HITL):** Ações de mitigação críticas exigem aprovação explícita de um operador humano.
3. **Resiliência & Custo Zero (Mock Fallback):** Execução híbrida (Anthropic Claude 3.5 Sonnet + modo Offline).

## 🚀 Arquitetura do Repositório

```text
gohacks-starter-kit/
├── .env.example          # Modelo de variáveis de ambiente
├── .gitignore            # Proteção contra vazamento de tokens
├── requirements.txt      # Dependências com versões travadas
├── app.py                # Interface gráfica Streamlit com Live Demo & HITL
└── src/
    ├── schemas.py        # Guardrails e Schemas Pydantic v2
    ├── analyzer.py       # Motor Claude 3.5 Sonnet com Tool Calling
    └── mocks.py          # Payloads de teste de alta fidelidade
