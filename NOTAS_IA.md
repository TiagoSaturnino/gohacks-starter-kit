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
