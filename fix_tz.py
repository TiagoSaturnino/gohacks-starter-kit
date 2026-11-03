import os

with open("src/store.py", "r", encoding="utf-8") as f:
    code = f.read()

# Substitui a função _now para usar o horário local/Brasília formatado
old_func = 'datetime.now(timezone.utc).isoformat(timespec="seconds")'
new_func = 'datetime.now().strftime("%Y-%m-%d %H:%M:%S")'

if old_func in code:
    code = code.replace(old_func, new_func)
    with open("src/store.py", "w", encoding="utf-8") as f:
        f.write(code)
    print("src/store.py atualizado para Horário Local!")
else:
    print("Função já atualizada ou formato diferente.")
