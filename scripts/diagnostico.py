"""Prueba de SOLO LECTURA contra Odoo (usa el .env). No escribe ni imprime nada.

Uso:  .\\.venv\\Scripts\\python.exe -m scripts.diagnostico S2621473
"""
import json
import sys

from app import service
from app.config import get_settings
from app.odoo_client import OdooClient


def main(order: str):
    s = get_settings()
    if s.mode != "real":
        sys.exit("Pon ODOO_MODE=real en el .env para esta prueba.")
    odoo = OdooClient(s)
    print(f"Conectando a {s.odoo_url} (base {s.odoo_db}) como {s.odoo_user} ...")
    res = service.resolve(odoo, s, order_name=order)
    res.pop("sesion")
    print(json.dumps(res, indent=2, ensure_ascii=False))
    print("\n--- Campos de project.task relacionados con etapa/estado/factura ---")
    for k, v in odoo.describe_fields("project.task", ["stage", "state", "invoice", "closed", "done", "fold"]).items():
        print(f"{k:45} {v['type']:10} {v['string']}")
    print("\n--- Campos de tms.package relacionados con estado/factura ---")
    for k, v in odoo.describe_fields("tms.package", ["state", "invoice", "closed", "done"]).items():
        print(f"{k:45} {v['type']:10} {v['string']} {v.get('selection') or ''}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "S2621473")
