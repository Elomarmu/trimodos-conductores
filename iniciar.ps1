# Arranca el servidor en local. Para pararlo: Ctrl+C en esta terminal.
Set-Location $PSScriptRoot
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
