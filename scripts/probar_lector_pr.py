"""Prueba la lectura de albaranes de PR (o del listado del tren) con Claude. No toca Odoo.

Uso (con ANTHROPIC_API_KEY en el .env):
    python -X utf8 -m scripts.probar_lector_pr albaran1.jpg albaran2.pdf ...
    python -X utf8 -m scripts.probar_lector_pr --tren LISTADO_UTIS.pdf
"""
import json
import mimetypes
import sys
from pathlib import Path

from dotenv import load_dotenv

from app.pr import pr_lector as L
from app.pr import pr_reglas as R

load_dotenv()


def main(args):
    tren = "--tren" in args
    rutas = [Path(a) for a in args if a != "--tren"]
    if not rutas:
        print(__doc__)
        return 1
    archivos = [(p.read_bytes(), mimetypes.guess_type(p.name)[0] or "image/jpeg") for p in rutas]
    if tren:
        print(json.dumps(R.limpiar_tren(L.leer_tren(archivos)), ensure_ascii=False, indent=1))
        return 0
    for p, (datos, tipo) in zip(rutas, archivos):
        leido = L.leer_albaran(datos, tipo)
        v = R.limpiar(leido)
        print(f"\n=== {p.name}")
        print(json.dumps(v.a_dict(), ensure_ascii=False, indent=1))
        if leido.get("dudas"):
            print("Dudas:", leido["dudas"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
