# Arranca el servidor en MODO SIMULADO (no toca Odoo, ignora el modo del .env). Parar: Ctrl+C.
Set-Location $PSScriptRoot
$env:ODOO_MODE = "mock"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
