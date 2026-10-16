"""Comprueba los permisos del usuario de Odoo del .env, SIN escribir nada.

Uso:  .\\.venv\\Scripts\\python.exe -m scripts.comprobar_permisos

Sirve para validar el usuario técnico: debe tener todo lo marcado "debe SÍ" y nada de lo marcado "debe NO".
Con un usuario administrador saldrán muchos "debe NO" en rojo: es normal, por eso se cambia por uno técnico.
"""
import sys

from app.config import get_settings
from app.odoo_client import OdooClient


def main():
    s = get_settings()
    if s.mode != "real":
        sys.exit("Pon ODOO_MODE=real en el .env para esta comprobación.")
    print(f"Usuario: {s.odoo_user} en {s.odoo_url} (base {s.odoo_db})\n")
    bad = 0
    for model, op, expected, real in OdooClient(s).check_permissions():
        ok = expected == real
        bad += not ok
        print(f"{'OK  ' if ok else 'FALLA'}  {model:14} {op:7} debe {'SÍ' if expected else 'NO'}  tiene {'SÍ' if real else 'NO'}")
    print("\nTodo correcto." if not bad else f"\n{bad} permisos no coinciden con lo esperado.")


if __name__ == "__main__":
    main()
