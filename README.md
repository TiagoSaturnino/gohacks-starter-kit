# Guardião do Lote — GoHacks Talent 2026 (desafio Pagamentos)

Antes de cada lote de remessa fechar, acha os títulos que fariam dinheiro sair
indevidamente (duplicidade, valor errado, conta trocada por golpista, boleto falso),
pede decisão humana no Slack e registra tudo em auditoria. Construído sobre o meu
kit-base (Pydantic v2, aprovação humana em código, PII mascarada, fila manual
fail-safe, auditoria SQLite). Especificação completa: `SPEC.md`.

## Quem decide o quê

| Quem | Decide | Onde |
|---|---|---|
| Código | se o título gera alerta, severidade, quem acionar, se fica retido | `src/checks.py` (itens 2–3 do SPEC) |
| IA, via gateway | só a explicação e o sinal "agravar"; o schema nem tem campo para reter/liberar | `src/explicador.py`, `src/schemas.py` |
| Humano (Slack) | reter ou liberar | item 6 do SPEC |
| Agente | executa a decisão na agenda simulada e audita | `src/executors.py`, `src/store.py` |

## Garantias

| Garantia | Como | Onde |
|---|---|---|
| Uma porta só para IA | Todo modelo passa pelo AI Proxy do evento (formato OpenAI): `chat_json` para texto, Jev para decisão tipada barata. DeepSeek bloqueado | `src/gateway.py` |
| Custo visível | Toda chamada registra tokens (chat) ou US$ estimado (Jev) em `usos_ia` e no log `[custo]`. Chat não tem preço por chamada: não inventamos | `src/gateway.py`, `src/store.py` |
| IA não inventa | Saída validada por Pydantic + ancoragem: IDs citados precisam estar na evidência. 1 reparo, depois texto-modelo | `src/guardrails.py` |
| Plano B sem crédito | Cache em disco → gateway → texto-modelo. Breaker abre após 3 falhas. Retenções não mudam sem IA | `src/explicador.py`, `src/resilience.py` |
| Dados bancários não saem crus | Linha digitável, chave de acesso, conta, destino e pix mascarados no prompt; máscara parcial no Slack | `src/pii.py` |
| Re-execução segura | Alertas e acionamentos idempotentes; vale a última decisão por título e lote; dois relógios (real e simulado) | `src/store.py` |
| Original intocado | Escrita simulada em `data/agenda_simulada.csv` | `src/executors.py` |

## Rodar (Windows / PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env      # coloque o AI_PROXY_API_KEY e USE_IA=True para usar IA
pytest
python -m src.gateway --ping --jev   # teste de fumaça do gateway (mostra a linha [custo])
```

Os CSVs do desafio ficam em `./pagamentos` (fora do git).
