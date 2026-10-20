#!/usr/bin/env bash
# Mac: arranca el servidor con el modo que indique el archivo .env (igual que iniciar.cmd en Windows). Para parar: Ctrl+C
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then echo "Falta preparar el proyecto: ejecuta primero ./preparar.sh"; exit 1; fi
echo "Abre http://localhost:8000 en el navegador. Para parar el servidor pulsa Ctrl+C."
exec .venv/bin/python -m uvicorn app.main:app --reload --port 8000
