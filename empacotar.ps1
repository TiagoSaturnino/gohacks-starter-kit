# Gera ..\gohacks-atual.zip com o estado atual da pasta (sobrescreve o anterior)
$destino = Join-Path (Split-Path $PSScriptRoot -Parent) "gohacks-atual.zip"
Remove-Item $destino -ErrorAction SilentlyContinue
tar -a -cf $destino -C $PSScriptRoot --exclude=.venv --exclude=.env --exclude=.git `
    --exclude=.pytest_cache --exclude=__pycache__ --exclude="backup_*" .
Write-Host "Pronto: $destino"
