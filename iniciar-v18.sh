#!/usr/bin/env bash
# Mac: arranca el servidor contra el Odoo 18 de pruebas (igual que iniciar-v18.cmd en Windows). Para parar: Ctrl+C
# Uso:  ./iniciar-v18.sh                      (usa .env.odoo18)
#       ./iniciar-v18.sh .env.odoo18-tecnico  (usa ese archivo, p. ej. el usuario técnico)
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then echo "Falta preparar el proyecto: ejecuta primero ./preparar.sh"; exit 1; fi
export ENV_FILE="${1:-.env.odoo18}"
if [ ! -f "$ENV_FILE" ]; then echo "No existe el archivo $ENV_FILE en la carpeta del proyecto."; exit 1; fi
echo "Odoo 18 de pruebas con el archivo $ENV_FILE. Abre http://localhost:8000 en el navegador. Para parar el servidor pulsa Ctrl+C."
exec .venv/bin/python -m uvicorn app.main:app --reload --port 8000
