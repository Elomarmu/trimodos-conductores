@echo off
rem Arranca el servidor con el modo que indique el archivo .env. Para parar: Ctrl+C
cd /d "%~dp0"
echo Abre http://localhost:8000 en el navegador. Para parar el servidor pulsa Ctrl+C.
".venv\Scripts\python.exe" -m uvicorn app.main:app --reload --port 8000
