r"""Prueba completa de un viaje de PR contra el Odoo 18 DE PRUEBAS: foto → lectura → (confirmación) → línea en el
pedido del mes → orden de transporte en PDF (se guarda junto a la foto).

Solo funciona contra stinsa18 (pruebas). Pide confirmación antes de grabar cada viaje.
Uso (PowerShell, con la clave de Claude en .env y el usuario técnico en .env.odoo18-tecnico):
    $env:ENV_FILE=".env.odoo18-tecnico"; .\.venv\Scripts\python.exe -X utf8 -m scripts.probar_registro_pr foto1.jpg [foto2.png ...]
"""
import json
import mimetypes
import sys
from pathlib import Path

from dotenv import load_dotenv

from app.config import get_settings
from app.odoo_client import OdooClient
from app.pr import pr_lector as L
from app.pr import pr_odoo as O
from app.pr import pr_reglas as R
from app.pr.pr_api import OdooPR

load_dotenv()  # la clave de Claude (ANTHROPIC_API_KEY) está en el .env normal; no pisa lo ya cargado


def pregunta(texto: str) -> bool:
    return input(f"{texto} (s/n): ").strip().lower() in ("s", "si", "sí")


def main(args):
    if not args:
        print(__doc__)
        return 1
    s = get_settings()
    if s.mode != "real" or "stinsa18" not in s.odoo_url:
        print(f"Esta prueba solo se hace contra el Odoo 18 de pruebas (stinsa18). Ahora apunta a: {s.odoo_url or '(nada)'}")
        print('Pon antes:  $env:ENV_FILE=".env.odoo18-tecnico"')
        return 2
    odoo, cli = OdooPR(s), OdooClient(s)
    print(f"Odoo: {s.odoo_url} · usuario {s.odoo_user}")
    for ruta in map(Path, args):
        print(f"\n=== {ruta.name}: leyendo con Claude…")
        leido = L.leer_albaran(ruta.read_bytes(), mimetypes.guess_type(ruta.name)[0] or "image/jpeg")
        v = R.limpiar(leido).a_dict()
        veh = O.comprobar_vehiculos(odoo, v["tractora"], v["remolque"])
        for clave in ("tractora", "remolque"):
            if not veh[clave]["existe"] and len(veh[clave]["parecidas"]) == 1:
                otra = veh[clave]["parecidas"][0]
                if pregunta(f"La {clave} {v[clave]} no está en Flota. ¿Es {otra}?"):
                    v[clave] = otra
        veh = O.comprobar_vehiculos(odoo, v["tractora"], v["remolque"])
        print(json.dumps({k: v[k] for k in ("albaran", "fecha", "hora", "tractora", "remolque", "peso_kg", "bultos",
                                             "codigo_paper", "contenedor", "toneladas", "avisos", "pendientes")},
                         ensure_ascii=False, indent=1))
        print("Punto de carga:", v["punto"] and v["punto"]["nombre"], "· Colaborador:", veh.get("colaborador") or "(no encontrado)")
        if leido.get("dudas"):
            print("Dudas de la lectura:", leido["dudas"])
        if not pregunta("¿Grabar este viaje en el Odoo 18 de pruebas?"):
            continue
        try:
            r = O.registrar(odoo, v)
        except O.ErrorPR as e:
            print("NO SE HA GRABADO:", e)
            continue
        print(json.dumps(r, ensure_ascii=False))
        if r.get("duplicado"):
            print("Ese albarán ya estaba registrado: no se ha creado nada nuevo.")
        adj = cli.print_order(r["tarea_id"], R.EMPRESA_ID)
        pdf = cli.get_pdf(adj["id"], adj.get("access_token"))
        if pdf:
            destino = ruta.with_name(f"orden-{r['bulto']}.pdf")
            destino.write_bytes(pdf)
            print("Orden de transporte guardada en:", destino)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
