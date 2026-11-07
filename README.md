# ⚡ GoHacks Starter Kit — Triagem de incidentes e RCA com IA

Kit-base para o **GoHacks Talent 2026**. Recebe um log/incidente, devolve um diagnóstico
estruturado (causa raiz, severidade, ações propostas) e **só executa algo depois da
aprovação de um operador**. O domínio (e-commerce, TI, dados…) fica em um arquivo de
**cenário**, então adaptar o kit a uma nova "dor" não exige mexer no motor.

> Status honesto: é um **MVP**. Não há stream, fila nem integração real com sistemas de
> terceiros; a execução de ações é **simulada** ou **enfileirada em uma outbox local**.
> Veja "MVP vs. produção" abaixo.

## O que o sistema garante (e como)

| Garantia | Como é aplicada | Onde |
|---|---|---|
| Saída estruturada e tipada | Tool calling forçado + Pydantic v2 (`extra="forbid"`, limites de tamanho, payload plano) | `src/schemas.py` |
| Aprovação humana não depende do LLM | `requires_human_approval` é **derivado em código** (tipo de ação, severidade HIGH/CRITICAL, confiança efetiva < 60%). Os campos nem aparecem no schema enviado ao modelo | `src/schemas.py`, `src/analyzer.py` |
| Confiança reavaliada pelo código | A nota **declarada** pelo modelo vira entrada: tetos por severidade (CRITICAL 70%, HIGH 80%) e por ação que altera sistemas (85%), penalidades por ID sem lastro (-15 p.p.) e por saída reparada (-10 p.p.). A UI mostra declarada **e** efetiva | `src/confidence.py` |
| Menos alucinação de identificadores | **Ancoragem**: IDs no `payload` precisam existir no log; senão a saída é rejeitada (1 reparo, depois `DEGRADED`). IDs inventados na narrativa geram aviso | `src/guardrails.py` |
| Só ações permitidas pelo cenário | O enum da tool é restringido ao cenário e a saída é revalidada | `src/analyzer.py` |
| Dados pessoais não saem crus | CPF, CNPJ, e-mail, telefone, cartão (Luhn) e chaves JSON de nome são mascarados antes da chamada | `src/pii.py` |
| **Fail-closed** | Qualquer falha (API fora, 429, timeout, saída inválida, erro inesperado) vira `DEGRADED` com código de motivo: automação bloqueada e incidente **gravado na fila de triagem manual N2** (verificável na aba de auditoria). A tela nunca exibe traceback | `src/analyzer.py`, `src/telemetry.py`, `app.py` |
| Resiliência a API instável | Timeout e retries configuráveis + **circuit breaker** (após 3 falhas seguidas, as próximas análises falham rápido por 60 s em vez de esperar o timeout de cada linha do lote) | `src/resilience.py` |
| Observabilidade e custo | Latência real (s), tokens reais, tentativas e custo estimado por análise, persistidos em SQLite e agregados na aba de auditoria. Log estruturado (JSON) | `src/analyzer.py`, `src/store.py` |
| Auditoria e idempotência | SQLite com quem decidiu, quando (**horário de Brasília com offset**) e hash do payload. `UNIQUE(incidente, ação, hash)` impede decisão duplicada mesmo com clique duplo | `src/store.py` |

> "Guardrail" aqui reduz risco, não o elimina. A **confiança efetiva é uma política
> conservadora, não uma calibração estatística**: calibrar de verdade exige um conjunto
> rotulado para comparar confiança declarada com acerto real. A ancoragem cobre
> identificadores, não a correção da causa raiz.

## Como rodar (Windows / PowerShell)

Requer **Python 3.11+** (`python --version`).

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1      # se bloquear: Set-ExecutionPolicy -Scope Process Bypass
pip install -r requirements-dev.txt
Copy-Item .env.example .env       # edite o .env se tiver chave
pytest                            # testes (não precisam de chave)
streamlit run app.py
```

### Modos de operação

- **Offline (padrão, `USE_MOCK=True`)**: replay das fixtures do cenário. Logs fora das
  fixtures retornam "sem fixture" (fail-closed), não um diagnóstico inventado.
- **Live**: defina `ANTHROPIC_API_KEY` e `USE_MOCK=False`. O modelo vem de
  `ANTHROPIC_MODEL` (padrão `claude-sonnet-5-5`). Nunca commite o `.env`.

### Variáveis úteis (`.env`)

| Variável | Para quê |
|---|---|
| `PRICE_INPUT_PER_MTOK`, `PRICE_OUTPUT_PER_MTOK` | Preços em USD por 1 milhão de tokens (da página oficial de preços). Sem eles, o app mostra tokens e latência reais mas não estima custo |
| `MANUAL_MINUTES_PER_INCIDENT` | Premissa da triagem manual (também editável na barra lateral) |
| `APP_TIMEZONE` | Fuso de gravação/exibição (padrão `America/Sao_Paulo`) |
| `ANTHROPIC_TIMEOUT_S`, `ANTHROPIC_MAX_RETRIES` | Timeout (30 s) e retries do SDK (1) |
| `CB_FAILURE_THRESHOLD`, `CB_RESET_S` | Circuit breaker (3 falhas, 60 s) |
| `EXECUTOR` | `dry_run` (simulado) ou `outbox` (enfileira ação aprovada) |

## Interface (3 abas)

1. **🩺 Diagnóstico e ações (HITL)**: severidade em cores (Crítico vermelho, Alto laranja,
   Médio amarelo, Baixo verde), origem do resultado, causa raiz, confiança declarada vs.
   efetiva, tempo medido, **redução do tempo de diagnóstico**, custo e botões de
   aprovar/rejeitar com feedback imediato.
2. **🔒 Segurança, PII e schema**: log original × log mascarado, itens mascarados, JSON
   validado pelo Pydantic, composição da confiança e o schema enviado ao modelo.
3. **🗂️ Auditoria e outbox**: decisões humanas, outbox, fila de triagem N2 e métricas
   de análise, atualizadas a cada 5 s.

> **Sobre a métrica de tempo:** `redução = (premissa manual − tempo medido da análise) ÷
> premissa manual`. A premissa é sua (barra lateral) e o tempo é medido. Isso mede o tempo
> de **diagnóstico**, não o MTTR completo, que inclui aprovação e execução. O tempo entre a
> análise e a decisão humana também é registrado na auditoria.

## Estrutura

```text
├── app.py                  # UI Streamlit (abas, lote, rede de segurança)
├── scenarios/
│   └── ecommerce.json      # domínio, régua de severidade, ações permitidas, fixtures
├── src/
│   ├── config.py           # variáveis de ambiente
│   ├── schemas.py          # contrato Pydantic + política HITL
│   ├── scenarios.py        # carregamento de cenários e replay de fixtures
│   ├── pii.py              # máscara de dados pessoais
│   ├── guardrails.py       # ancoragem e ações permitidas
│   ├── confidence.py       # confiança efetiva (política)
│   ├── resilience.py       # circuit breaker
│   ├── analyzer.py         # chamada ao modelo, reparo, falhas classificadas, custo
│   ├── telemetry.py        # métricas + roteamento fail-closed para a fila N2
│   ├── store.py            # SQLite: auditoria, outbox, fila N2, métricas
│   ├── executors.py        # DryRun / Outbox (pós-aprovação)
│   └── timeutil.py         # fuso local com offset
└── tests/                  # pytest (cliente Anthropic falso) + fluxo da UI
```

## Como adaptar a uma nova dor (≈30 min)

1. Copie `scenarios/ecommerce.json` para `scenarios/<nova_dor>.json`.
2. Reescreva `domain` (contexto), `severity_rubric` (o que é CRITICAL/HIGH/… nessa dor) e
   `allowed_actions` (subconjunto de `ActionType`).
3. Grave 2–4 `fixtures` (log + análise esperada). O teste
   `test_every_fixture_is_grounded_in_its_own_log` valida se são coerentes.
4. Rode `pytest`, escolha o cenário na barra lateral (ou `SCENARIO=<nova_dor>` no `.env`).
5. Precisa de uma ação nova (ex.: reiniciar serviço)? Acrescente em `ActionType`
   (`src/schemas.py`) e, se mutar algo, em `REQUIRES_APPROVAL_ACTIONS` e `MUTATING_ACTIONS`.
6. Para chamar um sistema real, implemente `Executor.execute` em `src/executors.py`.

## MVP vs. produção

| Tema | MVP (este repositório) | Desenho para produção |
|---|---|---|
| Estado de incidentes | análise em `st.session_state`; decisões, fila N2 e métricas em SQLite | Postgres (histórico/SLA) + Redis (travas) |
| Processamento | síncrono por requisição; lote sequencial (máx. 50) com circuit breaker | fila (SQS/RabbitMQ/Celery) + workers + DLQ |
| Execução | simulada ou outbox local | worker consumindo a outbox, com lock distribuído |
| Segredos | `.env` local | Secrets Manager / Vault |
| PII | regex + Luhn/checksum | + NER (Microsoft Presidio) para nomes em texto livre |
| Identidade do operador | texto livre | SSO / RBAC |
| Observabilidade | log JSON + métricas na UI | OpenTelemetry, dashboard, alertas |
| Qualidade do modelo | confiança efetiva por política | golden set + avaliação em CI + calibração real |

## Limitações conhecidas

- Nomes em texto livre não são detectados pela máscara de PII.
- Confiança efetiva é política, não calibração estatística; não há golden set em CI.
- O custo depende dos preços que você configurar; não há tabela embutida (evita número
  desatualizado).
- O modo live foi testado com cliente falso (sem chamar a API); faça um teste manual com
  sua chave antes da demonstração.
- Não há autenticação: o campo "Operador" apenas identifica quem aprova.
- A tabela de auditoria usa SQLite local: em hospedagem efêmera (ex.: Streamlit Cloud) o
  arquivo some a cada reinício.
