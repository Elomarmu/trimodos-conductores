#!/usr/bin/env bash
# Mac: prepara el proyecto (solo la primera vez, o tras cambiar requirements): crea .venv e instala lo necesario.
# En Windows se hace a mano (ver README, apartado 1).
set -euo pipefail
cd "$(dirname "$0")"

# El python3 que trae el Mac es antiguo (3.9) y la app necesita 3.10 o más nuevo: se busca uno que valga.
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Falta Python 3.10 o más nuevo (el que trae el Mac es antiguo)."
  echo "Instálalo con Homebrew:  brew install python@3.12"
  echo "o descárgalo de https://www.python.org/downloads/ y vuelve a ejecutar ./preparar.sh"
  exit 1
fi

# Si la carpeta del proyecto se comparte con Windows, su .venv no sirve en el Mac: no se borra, se avisa.
if [ -d .venv ] && [ ! -x .venv/bin/python ]; then
  echo "Ya hay una carpeta .venv que no es de Mac (¿creada en Windows?). No se toca."
  echo "Si este proyecto es solo del Mac, bórrala y vuelve a ejecutar ./preparar.sh"
  exit 1
fi

echo "Usando $("$PY" --version) ($(command -v "$PY"))"
"$PY" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements-dev.txt
echo
echo "Listo. Para arrancar la app:"
echo "  ./iniciar-demo.sh   datos simulados (no toca Odoo)"
echo "  ./iniciar.sh        el modo que diga el archivo .env"
