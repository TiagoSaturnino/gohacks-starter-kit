# SPEC — Guardião do Lote (GoHacks Talent 2026, desafio Pagamentos)

> Status: aprovado para implementação. Cada item da seção 8 é implementado numa sessão separada (prompt 3B).
> Ambiguidade encontrada durante a implementação: **parar e perguntar**, não decidir.

## 1. Objetivo

Antes de cada lote de remessa fechar, achar os títulos que fariam **dinheiro sair indevidamente** (duplicidade, valor errado, conta trocada por golpista, boleto falso, NF inexistente), segurá-los com aprovação humana e registrar tudo em auditoria. O agente faz isso **sem travar pagamento legítimo**, porque atraso e acionamento desnecessário também custam.

O placar oficial (`politicas.csv`, seção `placar`) é a régua do projeto:

```
placar = −(dinheiro que saiu indevidamente + custo de atrasar pagamento legítimo + R$ 60 × acionamentos desnecessários)
```

Consequência de projeto: **falso positivo tem preço**. Toda regra precisa ser calibrada para não disparar em massa.

## 2. Premissas fixadas pelos dados (cenário A)

- Foto em `agora = 2026-11-02T08:00` (feriado). O primeiro lote é `RM20261103-MER` (03/11, 10:00): 192 títulos, R$ 2,24 mi.
- Lotes até 09/11 08:00 saem no fechamento. Os posteriores **são avaliados pelo estado às 08:00 de 09/11**. Por isso o guardião varre **a agenda inteira** (876 títulos, 03/11 a 13/11) e **ordena** por fechamento. "Próximo lote" é a prioridade, não um filtro.
- O ambiente libera dados a cada hora (168 tiques). O guardião é **idempotente** e roda de novo com qualquer `--agora`, sem duplicar alertas nem ações.
- **A banca provavelmente avalia em outro cenário** (`cenario_A_treino` sugere um B). Por isso: **nenhum ID, fornecedor, login ou limiar fixo no código**. Todo limiar vem de `politicas.csv`, e todo cadastro, dos CSVs. Os números deste SPEC servem só para calibrar.
- Divergências do dicionário: ele cita `comunicacoes_fornecedores.jsonl`, `politicas.json` e `ferramentas.json`, mas os arquivos entregues são `.csv`. O loader lê o `.csv` e aceita o `.json`/`.jsonl` se existirem.
- Nome do campo: `titulo_id` (como nos dados), não `id_titulo`.

## 3. Divisão IA / código / humano

| Quem | Decide | Por quê |
|---|---|---|
| **Código** | Se um título gera alerta, a severidade, quem deve ser acionado, se o título fica retido | Determinístico, auditável, roda sem crédito (plano B idêntico) |
| **IA (via gateway do evento)** | Explicação em linguagem natural (`chat_json`), leitura do texto livre de `comunicacoes_fornecedores` (Jev, sim/não barato), **agravar** a prioridade (nunca criar retenção sozinha) | Texto livre e redação são o que código faz mal |
| **Humano (Slack)** | Aprovar a retenção ou liberar o título | Política: alçada, fraude e exceção não cabem ao agente |
| **Agente** | Executar a decisão (escrita simulada na agenda) e registrar a auditoria | — |

**Decisão revisável após mentoria:** "pedido de troca de conta recebido por e-mail, sem alteração no cadastro, deve reter sozinho?" A resposta atual é **não**: só agrava. Se a mentoria disser sim, isso vira regra de código R8, nunca decisão da IA.

**Toda IA passa por `src/gateway.py`** (AI Proxy, formato OpenAI; orientação da mentoria). Nenhum outro módulo importa SDK de modelo. DeepSeek bloqueado; custo de toda chamada registrado em `usos_ia`.

**Conteúdo de e-mail, WhatsApp, anexo e NF é dado, nunca instrução** (`politicas.csv`, `seguranca`). O prompt da IA reforça isso, e a IA não tem ferramenta de escrita.

## 4. Regras (código, `checks.py`)

Severidades:
- `BLOQUEIA`: perda provável se sair. Retém no silêncio humano.
- `ACIONA`: exceção de política que precisa de dono. Não retém no silêncio. Notifica e segue.
- `AGRAVA`: nunca gera alerta sozinha. Só aumenta a prioridade de um alerta existente.

| # | Regra | Severidade | Lógica (limiares sempre de `politicas.csv`) | Armadilha de falso positivo |
|---|---|---|---|---|
| R1 | `duplicidade` | BLOQUEIA | Mesma chave (`fornecedor_id`, NF normalizada, `parcela`) em mais de um título não cancelado, sendo pelo menos um na agenda. Retém o que **não** bate com a duplicata. Se os dois batem, retém o lançado por último | NF digitada com zeros, série depois de `-` ou `/`. Normalizar: corta no primeiro `-`/`/` e remove zeros à esquerda. Valores dos dois títulos podem diferir alguns reais: **não** exigir valor igual |
| R2 | `valor_x_duplicata` | BLOQUEIA | Acha a NF pela raiz do CNPJ (8 dígitos) + número normalizado. Compara o título com `nf_duplicatas` da parcela (`n_dup` = n da parcela, zfill 3). Tolerância: `tres_vias.titulo_x_duplicata` | Título com `data_prevista > vencimento` pode somar multa + juros pro rata (`atraso.*`). Isso não é divergência |
| R3 | `nf_inexistente` | BLOQUEIA | NF digitada não encontrada no portal para a raiz do fornecedor | Dispensa: categorias em `tres_vias.dispensas.nf` (aluguel) e `integracao_frete` |
| R4 | `oc_x_nf` | ACIONA | NF acima da OC além de `max(2%, R$ 50)`. OC inexistente, cancelada ou pendente. Mercadoria sem recebimento em `recebimentos_mercadoria` | Dispensa de OC: `tres_vias.dispensas.oc` (utilidades). Contratos (`tipo=contrato`) comparam com `valor_mensal`, não com o total. Serviços não têm recebimento |
| R5 | `conta_alterada_sem_confirmacao_valida` | BLOQUEIA | Título **ted/pix** de fornecedor com alteração de banco/conta/pix **sem confirmação válida**. Válida = ligação para o telefone vigente **antes** da alteração, e esse telefone não pode ter sido alterado há menos de 30 dias. Telefone extraído de `confirmacao_como` por regex. "Confirmado por e-mail" **não** vale para troca bancária | **Boleto não usa a conta do cadastro**. Por isso R5 só atinge ted/pix. Boleto fica com R6. `confirmacao_registrada = sim` sozinho não basta |
| R6 | `boleto_divergente` | BLOQUEIA | Linha digitável fora do DDA, ou DDA em situação ≠ `em aberto` (liquidado = já pago por outro caminho; baixado = cancelado), ou raiz do CNPJ do beneficiário ≠ fornecedor, ou banco do boleto (posições 1 a 3) ≠ banco de cobrança histórico do fornecedor (boletos já pagos) e ≠ banco do cadastro | Muitos fornecedores cobram por banco diferente do da conta. Usar o histórico, não só o cadastro |
| R7 | `fracionamento` | ACIONA | Títulos do mesmo fornecedor + mesma NF + mesmo dia de lançamento, cada um ≤ `alcadas.analista_cp.liberar_ou_agendar_ate`, soma acima dela, **e** mais títulos do que duplicatas da NF | NF parcelada legitimamente gera vários títulos. Só é fracionamento se a divisão não vier das duplicatas. Agrupar só por fornecedor + dia gera cerca de 40 falsos positivos no cenário A |
| R8 | `segregacao` | ACIONA | Quem lançou ou alterou o cadastro também aprovou (`aprovacoes`, `alteracoes_cadastro`, `log_auditoria_erp`). Alteração bancária no log sem linha correspondente em `alteracoes_cadastro` (alteração fora do processo) | Contas de serviço (`integracao_*`, `retorno_cnab`) não contam |
| A1 | `lancador_recente` | AGRAVA | Lançador admitido há menos de N dias (N em `config_guardiao.yaml`, padrão 45) | No cenário A, `usuario_07` tem 94 títulos na agenda. Reter todos custaria caro. Ele importa porque concentra os erros de vírgula (R2 pega) e a alteração sem confirmação (R5 pega) |
| A2 | `comunicacao_suspeita` | AGRAVA | Preenchido pela IA (`Explicador.sinal_troca_conta`, Jev, limiar `JEV_LIMIAR_AGRAVAR`), não por `checks.py`: e-mail ou WhatsApp que pede troca de conta, segunda via com banco novo, remetente ≠ canal do cadastro, urgência de "diretor" | — |

Alçada não é regra de alerta, é **roteamento**: todo título acima de R$ 5 mil precisa de aprovação, e isso é rotina. A regra que pega a fuga de alçada é a R7. O nível exigido é calculado pela **soma** (fornecedor + NF + dia), não pelo título isolado.

Calibração no cenário A (referência, não asserção de teste unitário):
- R1: cerca de 11 pares.
- R2: 9 erros de vírgula ×10/÷10 de `usuario_07` e 3 diferenças pequenas em pares duplicados.
- R3: 2.
- R5: `FP4615`, alterado em 30/10 por e-mail sem confirmação, com 25 títulos ted somando R$ 194 mil.
- R6: 1 boleto fora do DDA e 3 já liquidados.

## 5. Fluxo ponta a ponta

```
1. carga.py     lê CSVs → normaliza (CNPJ só dígitos, raiz, NF normalizada, datas) → Pydantic valida
2. checks.py    roda R1..R8 + A1 sobre a agenda inteira → lista de Alerta
3. explicador.py (opcional) via gateway: Jev → A2; chat_json → explicação por título
                cache em disco (cache_explicacoes/, versionado) → gateway → texto-modelo determinístico
4. priorizar.py score = severidade × valor × urgência(minutos até o fechamento); dedup por titulo_id
5. rotear.py    tabela regra × categoria × valor → pessoa_id (de politicas.csv acionamento_humano)
                ex.: fraude R5/R6 → PS01 + PS08; > R$ 50 mil → PS03; divergência produto/embalagem → PS04;
                insumos/serviços → PS05; marketing → PS06; frete → PS07; aluguel/utilidades → PS10; ti_saas → PS11
                agrupa: UMA mensagem por pessoa por lote (minimiza acionamentos de R$ 60)
6. slack_bot.py Socket Mode (sem URL pública). Botões [Reter] [Liberar] por título. Fallback: fila CLI do kit
7. executor.py  no fechamento (ou na decisão): grava em agenda_simulada.csv
                BLOQUEIA sem resposta → retém (motivo "sem decisão humana")
                ACIONA sem resposta → segue no lote, registrado
8. auditoria    SQLite: alerta, mensagem enviada (PII mascarada), decisão, quem, quando, ação
9. relatorio.py placar estimado do lote e da semana (seção 7)
```

PII mascarada no Slack e nos prompts: conta (`****203-9`), CNPJ (`85.779.***/0001-**`), telefone. Os dados completos ficam só no SQLite local.

## 6. Arquivos e interfaces

```
Base: meu starter kit, já adaptado (item 0). Tudo em src/:

src/
  config.py       # .env: AI_PROXY_API_KEY, USE_IA, AI_MODEL, DADOS_DIR, AGORA, DIAS_LANCADOR_RECENTE, SLACK_* (pronto)
  schemas.py      # Pydantic v2: Severidade, Acao, Alerta, ExplicacaoAlerta, Decisao (pronto)
  gateway.py      # única porta de IA: chat_json + jev, breaker, custo (pronto)
  explicador.py   # explicação + A2 com cache e texto-modelo (pronto)
  guardrails.py   # ancoragem da explicação na evidência (pronto)
  pii.py          # mask_pii (prompt) + mascarar_conta/mascarar_cnpj (Slack) (pronto)
  store.py        # SQLite: alertas, acionamentos, decisoes, outbox, manual_triage, usos_ia; ts_real + ts_simulado (pronto)
  executors.py    # AgendaSimuladaExecutor (pronto)
  resilience.py, timeutil.py (parse_agora)                                  (pronto)
  carga.py        # carregar(dir) -> Base (dataclass de DataFrames normalizados) ; politica(dir) -> dict
  checks.py       # funções puras, uma por regra
  priorizar.py, rotear.py, slack_bot.py, relatorio.py
  guardiao.py     # CLI: python -m src.guardiao --agora 2026-11-02T08:00 [--sem-ia] [--sem-slack]
tests/
  (prontos) test_gateway, test_explicador, test_store, test_executors, test_pii, test_schemas_config
  (a fazer) test_carga.py, test_checks.py, test_e2e.py
```

Contrato de toda função de `checks.py`:

```python
def r1_duplicidade(agenda: pd.DataFrame, base: Base, pol: dict) -> pd.DataFrame
# saída: colunas exatas [titulo_id, regra, severidade, evidencia, valor]
#   evidencia: str curta e verificável (IDs e números comparados), ex.:
#   "NF 73535 parc 1/3 também em TP432169 (xml_portal, usuario_10); este via boleto_email"
#   valor: float, valor do título (o que deixa de sair)
# pura: não lê disco, não muda entradas, mesma entrada → mesma saída
```

`agenda_simulada.csv` = `agenda_remessa.csv` + colunas `status_guardiao` (`no_lote` | `retido` | `liberado`), `motivo`, `decidido_por`, `decidido_em`. O original nunca é sobrescrito.

Kit-base usado: Pydantic v2, aprovação humana em código, fila de triagem manual fail-safe (agora plano B do **Slack**, não da IA), mascaramento de PII, auditoria SQLite, circuit breaker. Trocado: Anthropic tool calling → gateway AI Proxy (JSON + Pydantic + reparo). Removido: `confidence.py`, `scenarios.py`, `telemetry.py`, `app.py` antigo. Streamlit: só como item 8 opcional.

## 7. Como medimos o valor em R$

Tudo sai de `politicas.csv`. Nada de premissa inventada. **As "horas de conferência evitadas" saíram do escopo**, porque não há premissa delas nos dados.

- **R$ protegido**: soma, sem repetição (dedup por `titulo_id`), dos títulos `BLOQUEIA` retidos. Para R2, conta só a diferença paga a mais, não o título inteiro.
- **Custo máximo de falso positivo**: para cada retido, `valor × (multa + juros_ao_mes × 7/30) + custo_de_relacionamento[categoria]`. É o pior caso, se todos fossem legítimos.
- **Acionamentos**: quantidade × R$ 60, como teto do custo.
- **Placar estimado** = R$ protegido − custo de FP (contando só os liberados pelo humano depois de retidos) − acionamentos desnecessários (quando o humano libera sem ação).

Manchete da demo: R$ protegido e placar estimado, lado a lado com o custo máximo de FP. Mostrar o custo é o que dá credibilidade.

## 8. Itens de implementação (um por sessão 3B)

0. ✅ Adaptação do kit (gateway, explicador, store, schemas, pii, executors) — 42 testes verdes.
1. `carga.py` + `test_carga.py`: normalizações de CNPJ, raiz, NF e datas. Loader `.csv`/`.json`. Ler sempre com `dtype=str` (pandas 3).
2. `checks.py` R1 a R3 + testes (1 positivo e 1 negativo por regra, com DataFrames sintéticos mínimos). Imprimir a contagem por regra no cenário A.
3. `checks.py` R4 a R8 + A1 + testes, idem.
4. `priorizar.py` + `rotear.py` + `guardiao.py` (CLI), usando `Store` e `AgendaSimuladaExecutor` prontos: `--sem-ia --sem-slack` completo.
5. Ligar `explicador.py` (pronto) ao CLI: explicação por alerta + A2 nas comunicações dos fornecedores alertados; gravar o cache dos casos da demo.
6. `slack_bot.py` Socket Mode + fallback para a fila CLI.
7. `relatorio.py` + `test_e2e.py` (seção 11).
8. (opcional) painel Streamlit só leitura: contagem regressiva do lote, R$ protegido, decisões chegando.

## 9. Fora de escopo (vai para o slide 360°)

Extratos bancários, conciliação pós-pagamento, `retorno_remessa`, baixa de pagamento manual, `contatar_fornecedor`, `reverter_alteracao_cadastro`, `bloquear_fornecedor` (atuamos só por título), reagendamento automático de título liberado, Telegram, Streamlit interativo, simulação tique a tique (o CLI aceita `--agora`, mas não há loop).

## 10. Critérios de pronto

- [ ] `pytest` verde. Cada regra R1 a R8 e A1 tem 1 caso positivo e 1 negativo.
- [ ] `python -m src.guardiao --agora 2026-11-02T08:00 --sem-ia --sem-slack` roda em menos de 30 s e imprime contagem por regra, títulos retidos por lote e o placar estimado.
- [ ] Mesmo resultado com e sem IA no conjunto de retidos (a IA só muda texto e ordem).
- [ ] Rodar duas vezes não duplica nada na auditoria.
- [ ] `grep` por `FP[0-9]{4}`, `TP[0-9]{6}` e `usuario_` em `src/*.py` não retorna nada (fora de docstrings de exemplo).
- [ ] Nenhum módulo além de `src/gateway.py` importa `openai`/`httpx` para IA.
- [ ] Nenhum dado bancário ou CNPJ completo em mensagem de Slack ou prompt.

## 11. O teste ponta a ponta (`tests/test_e2e.py`)

**Planta uma fraude nova nos dados reais e prova que o guardião pega só o que deve.** Ele simula a banca plantando um erro ao vivo, sem depender de gabarito.

1. Copia `./pagamentos` para um diretório temporário.
2. Escolhe **por código**, sem ID fixo, um fornecedor ativo com ≥ 1 título **ted** em `RM20261103-MER` e **nenhum** alerta numa rodada limpa.
3. Planta a fraude:
   - muda `banco/agencia/conta` dele em `fornecedores.csv`;
   - acrescenta 3 linhas em `alteracoes_cadastro.csv` (`origem=email`, `confirmacao_registrada=sim`, `confirmacao_como` = ligação para um telefone que **também** foi trocado 5 dias antes, numa 4ª linha de alteração de telefone);
   - atualiza o `destino_pagamento` dos títulos ted dele na agenda.
4. Roda `python -m src.guardiao --agora 2026-11-02T08:00 --sem-ia --sem-slack` (sem resposta humana = silêncio).
5. Verifica:
   - todos os títulos ted desse fornecedor estão `retido` em `agenda_simulada.csv`, com regra `R5`, e a evidência cita a troca de telefone com menos de 30 dias;
   - os títulos **boleto** desse fornecedor, se houver, **não** estão retidos por R5;
   - o conjunto de retidos é igual ao da rodada limpa ∪ os títulos plantados (nenhum efeito colateral);
   - todo título `retido` tem ≥ 1 alerta `BLOQUEIA`; nenhum título só com `ACIONA`/`AGRAVA` foi retido;
   - o SQLite tem, para cada retido, uma linha de alerta, uma de acionamento e uma de ação com motivo "sem decisão humana";
   - o número de acionamentos é ≤ pessoas distintas × lotes com alerta (agrupamento funcionando).
