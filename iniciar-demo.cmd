@echo off
rem Arranca el servidor en MODO SIMULADO (no toca Odoo). Para parar: Ctrl+C
cd /d "%~dp0"
set ODOO_MODE=mock
set DRY_RUN=false
echo Abre http://localhost:8000 en el navegador. Para parar el servidor pulsa Ctrl+C.
".venv\Scripts\python.exe" -m uvicorn app.main:app --reload --port 8000
