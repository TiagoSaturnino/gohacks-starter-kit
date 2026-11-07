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
