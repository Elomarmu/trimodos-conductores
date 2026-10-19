@echo off
rem Arranca el servidor contra el Odoo 18 de pruebas. Para parar: Ctrl+C
rem Uso:  iniciar-v18.cmd                      (usa .env.odoo18)
rem       iniciar-v18.cmd .env.odoo18-tecnico  (usa ese archivo, p. ej. el usuario t?cnico)
cd /d "%~dp0"
set ENV_FILE=.env.odoo18
if not "%~1"=="" set ENV_FILE=%~1
echo Odoo 18 de pruebas con el archivo %ENV_FILE%. Abre http://localhost:8000 en el navegador. Para parar el servidor pulsa Ctrl+C.
".venv\Scripts\python.exe" -m uvicorn app.main:app --reload --port 8000
