"""Comprobación de COMPATIBILIDAD con una versión de Odoo (p. ej. la 18). SOLO LECTURA: no escribe ni imprime nada.

Uso (con ENV_FILE apuntando al entorno a probar):
    $env:ENV_FILE=".env.odoo18"; .\.venv\Scripts\python.exe -X utf8 -m scripts.comprobar_compatibilidad
"""
import sys

from app.config import get_settings
from app.odoo_client import (ATTACHMENT_FIELDS, ATTACHMENT_MODEL, EDITABLE_FIELDS, GOODS_MODEL, PACKAGE_FIELDS, PACKAGE_MODEL,
                             REPORT_NAME, TASK_FIELDS, TASK_MODEL, OdooClient)


def main():
    s = get_settings()
    if s.mode != "real":
        sys.exit("Pon ODOO_MODE=real en el archivo de entorno.")
    o = OdooClient(s)
    print(f"Servidor: {s.odoo_url}  base: {s.odoo_db}  usuario: {s.odoo_user}")
    print("Versión de Odoo:", o.server_version())
    fallos = 0

    def check(ok, texto):
        nonlocal fallos
        fallos += not ok
        print(("  OK    " if ok else "  FALLA ") + texto)

    o._login()
    check(True, "las credenciales (contraseña o clave API) sirven para JSON-RPC")
    print("\nCompañías visibles:", [(c["id"], c["name"]) for c in (lambda: (o.s.company_ids.clear(), o.list_companies())[1])()])

    print("\nCampos que usa la app:")
    for model, wanted in ((TASK_MODEL, TASK_FIELDS + ["tasks_ids"][:0]), (PACKAGE_MODEL, PACKAGE_FIELDS), (ATTACHMENT_MODEL, ATTACHMENT_FIELDS)):
        try:
            have = o.describe_fields(model, [""]) if model in (TASK_MODEL, PACKAGE_MODEL) else None
        except Exception as e:  # noqa: BLE001
            check(False, f"{model}: no se puede leer ({e})")
            continue
        if have is None:
            continue
        missing = [f for f in wanted if f not in have]
        check(not missing, f"{model}: {len(wanted) - len(missing)}/{len(wanted)} campos" + (f"  FALTAN: {missing}" if missing else ""))
    check(EDITABLE_FIELDS <= set(o.describe_fields(PACKAGE_MODEL, [""])), f"{PACKAGE_MODEL}: existen los 4 campos editables")
    try:
        o.search_goods("a", 1)
        check(True, f"{GOODS_MODEL}: name_search funciona")
    except Exception as e:  # noqa: BLE001
        check(False, f"{GOODS_MODEL}: {e}")

    rep = o.find_report()
    check(bool(rep), f"existe el informe {REPORT_NAME}" + (f" (modelo {rep[0]['model']})" if rep else ""))
    check(o.check_web_login(), "las MISMAS credenciales abren sesión web (necesario para imprimir el PDF)")

    print("\nPermisos:")
    for model, op, expected, real in o.check_permissions():
        print(f"  {'ok   ' if expected == real else 'dif. '} {model:14} {op:7} esperado {'SÍ' if expected else 'NO'}  tiene {'SÍ' if real else 'NO'}")
    print("\nTodo correcto." if not fallos else f"\n{fallos} comprobaciones fallan.")


if __name__ == "__main__":
    main()
