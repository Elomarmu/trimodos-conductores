@echo off
rem Arranca el servidor apuntando al Odoo 18 de pruebas (usa el archivo .env.odoo18). Para parar: Ctrl+C
cd /d "%~dp0"
set ENV_FILE=.env.odoo18
echo Odoo 18 de pruebas. Abre http://localhost:8000 en el navegador. Para parar el servidor pulsa Ctrl+C.
".venv\Scripts\python.exe" -m uvicorn app.main:app --reload --port 8000
