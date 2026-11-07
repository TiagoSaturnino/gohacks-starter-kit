# NOTAS_IA — o que a IA gerou e o que foi verificado

## Item 1 — carga.py + test_carga.py (2026-11-07)

**Gerado pela IA (Claude):** `src/carga.py` (loader csv/json/jsonl, normalizações de CNPJ, raiz, NF,
datas e valores, `politica()` aninhado, validação Pydantic de agenda e contas a pagar) e
`tests/test_carga.py` (25 testes).

**Decidido pelo humano antes do código:** `politica()` aninhado (dicts + listas); Pydantic já no
item 1 para agenda e contas; tolerâncias escritas em prosa ficam para o item 2.

**Verificado:**
- `pytest`: 67 passed (42 do kit + 25 novos).
- Cenário A real: 876 títulos na agenda, 7.120 em contas a pagar, 0 linhas inválidas, carga em ~0,3 s.
- Variantes de NF observadas nos dados (`99999`, `999999999`, `99999-9`, `99999/9`, `9999`...) cobertas por teste.
- `grep` por `FP[0-9]{4}`, `TP[0-9]{6}` e `usuario_` em `src/carga.py`: nada.
- Linha inválida sai de `agenda`/`contas` e vai para `Base.invalidos` (sem traceback).

**Pendente para o item 4:** o guardião precisa mandar `Base.invalidos` para `Store.enqueue_manual_triage`.

## Item 2 — checks.py R1 a R3 + test_checks.py (2026-11-07)

**Gerado pela IA (Claude):** `src/checks.py` (R1 duplicidade, R2 valor × duplicata, R3 NF
inexistente, `conferir_valor`, `proximo_dia_util`, `rodar_regras`), extração da tolerância em
`src/carga.py` (`reais_na_prosa`, `tolerancia_titulo_x_duplicata`) e 29 testes novos
(19 em `test_checks.py`, 10 em `test_carga.py`).

**Decidido pelo humano antes do código:**
- Tolerância título × duplicata: regex na prosa de `politicas.csv` (exatamente um "R$ ..."),
  derivada em `pol['tres_vias']['tolerancia_titulo_x_duplicata']`; a carga falha alto se ilegível.
- R2 e atraso: carência até o próximo dia útil após o vencimento da duplicata (feriados com
  `afeta_remessa = sim`). Depois disso, vale a duplicata ou a fórmula exata
  duplicata × (1 + multa + juros × dias/30), sempre ± tolerância.
- R1 em casos de borda: o legítimo é o 1º lançado que bate (ou o 1º lançado, se nenhum bate);
  os demais títulos do grupo que estão na agenda são retidos.

**Verificado:**
- `pytest`: 96 passed (67 anteriores + 29 novos).
- Cenário A: R1 = 13, R2 = 11 (8 erros de vírgula + 3 diferenças pequenas em pares
  duplicados), R3 = 2; 23 títulos distintos, R$ 277.462,20; carga + regras em ~0,6 s.
- Divergência com a calibração da SPEC: ela cita 9 erros de vírgula e cerca de 11 pares; os dados
  da agenda têm 8 e 13. Há mais erros de vírgula entre os títulos `aberto`, que ainda não estão na agenda.
- Nenhum dos 233 títulos com `data_prevista` depois do vencimento da duplicata leva multa: a
  carência não gera falso positivo no cenário A.
- `grep` por `FP[0-9]{4}`, `TP[0-9]{6}` e `usuario_` em `src/checks.py` e `src/carga.py`: nada.

**Interpretações sem caso no cenário A (revisar se a mentoria discordar):**
- R2: NF achada sem a duplicata da parcela gera alerta (o título não tem com o que bater).
- R3: NF vazia fora das dispensas gera alerta ("título sem número de NF").
- `integracao_frete` é a única constante de domínio em `checks.py`, citada na SPEC e ausente de `politicas.csv`.
- `regra` sai como `R1_duplicidade`, `R2_valor_x_duplicata` e `R3_nf_inexistente` (código + nome).

**Pendente para o item 7:** o relatório deve contar, para a R2, só a diferença paga a mais, não o
`valor` do título (SPEC §7).

## Item 3 — checks.py R4 a R8 + A1 + test_checks.py (2026-11-07)

**Gerado pela IA (Claude):** em `src/checks.py`, as regras R4 OC × NF, R5 conta alterada sem
confirmação válida, R6 boleto divergente, R7 fracionamento, R8 segregação e A1 lançador recente,
todas na lista `REGRAS`. Em `src/carga.py`, a extração estrita de mais dois limiares escritos em prosa:
`tolerancia_nf_x_oc` ("2% ou R$ 50,00") e `dias_telefone_recente` ("há menos de 30 dias").
Testes: 33 novos (30 em `test_checks.py`, 3 em `test_carga.py`). O helper `montar()` agora aceita
as tabelas do item 3.

**Decidido pelo humano antes do código:**
- R5: o "antes" vale para o **telefone**, não para a ligação. Vale a ligação para o telefone que
  estava no cadastro N dias antes da alteração, mesmo feita depois da gravação no ERP. Há 4 trocas
  assim no cenário A, 10 títulos ted (R$ 47,2 mil) que não são retidos.
- R4: recebimento só é exigido de NF achada no portal com modelo 55 (NF-e de mercadoria). NF fora
  do portal fica com a R3, sem alerta duplicado.
- A1: o tempo de casa é medido na **data de lançamento** do título. N vem de
  `pol['guardiao']['dias_lancador_recente']`, que o guardião (item 4) injeta a partir do config;
  se faltar, a regra levanta ValueError.
- R8: segue a letra da SPEC. "Alterou dados bancários e lançou título do mesmo fornecedor" fica
  fora: seriam 11 ACIONA no cenário A, 6 deles de trocas confirmadas.

**Verificado:**
- `pytest`: 129 passed (96 anteriores + 33 novos).
- Cenário A: R4 = 2 (1 OC cancelada, 1 pendente), R5 = 25 (R$ 194.440,08, um único fornecedor,
  troca em 30/10 por e-mail sem confirmação), R6 = 4 (1 fora do DDA e com beneficiário de outra
  raiz, 3 já liquidados), R7 = 0, R8 = 0, A1 = 161 (2 lançadores admitidos em outubro).
  R1 a R3 não mudaram (13 / 11 / 2). No total, 52 títulos distintos com BLOQUEIA (R$ 642.811,33)
  e 54 com alerta. Carga + regras em ~0,4 s.
- R5 e R6 batem com a calibração da SPEC (25 títulos / R$ 194 mil; 1 fora do DDA + 3 liquidados).
- Todo telefone citado em confirmação de troca bancária é o do cadastro vigente. O de R5 depende só
  do fornecedor sem confirmação.
- R4: nenhuma NF acima da OC além da tolerância. 52 títulos estão acima da OC **dentro** da folga.
  Isso confirma que a folga max(2%, R$ 50) evita falso positivo.
- R7: 548 grupos fornecedor + NF + dia passam da alçada pela soma, todos com o número de títulos
  igual ao de duplicatas (parcelamento legítimo). Agrupar só por fornecedor + dia daria 75 grupos com
  título na agenda.
- R8: nenhum título da agenda tem aprovação ainda (as aprovações saem no lote). O log do ERP
  bate 1:1 com `alteracoes_cadastro`, casado pelo id `AC…` do detalhe.
- `grep` por `FP[0-9]{4}`, `TP[0-9]{6}` e `usuario_` em `src/*.py`: nada.
- Evidências sem CNPJ, conta ou linha digitável. Telefone mascarado como `(85) ****-0134`.

**Interpretações sem caso no cenário A (revisar se a mentoria discordar):**
- R5: vale a **última** troca dos campos que definem o destino (ted ← banco/agência/conta;
  pix ← chave_pix), sem janela de idade. Troca de chave pix não afeta ted, e vice-versa.
- R5: confirmação sem a palavra "ligação" (ex.: "confirmado por e-mail") ou sem telefone legível é inválida.
- R4, R6 e R8 juntam os motivos numa linha por título (`;`), para não duplicar alerta do mesmo título na mesma regra.
- R6: o histórico de bancos de cobrança vem dos boletos `pago` do fornecedor. Fornecedor sem
  histórico só passa se o banco do boleto for o do cadastro.
- R7: conta todos os títulos não cancelados do grupo (pagos inclusive) e alerta só os da agenda.
  NF fora do portal fica com a R3.
- R8: alteração bancária do log fora do processo atinge só os títulos cuja forma usa o campo
  alterado (mesmo mapa da R5).
- Constantes de domínio em `checks.py` (vindas do dicionário de dados, não são IDs nem limiares):
  `STATUS_OC_INVALIDOS`, `MODELOS_COM_RECEBIMENTO` (55), `SITUACAO_DDA_PAGAVEL`,
  `PERFIS_SERVICO` (integracao), `CAMPOS_DESTINO`.

**Pendente para o item 4:** o guardião deve injetar `pol['guardiao']['dias_lancador_recente']` a
partir de `config.dias_lancador_recente` antes de `rodar_regras()`. O priorizador deve garantir que
A1 (AGRAVA) nunca gere alerta sozinha nem retenção.
