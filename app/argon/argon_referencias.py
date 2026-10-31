"""Productos y proveedores de las tablas de la Excel ARGON a partir de su referencia.

En la Excel se rellena solo la referencia (lo que se ve en Odoo):
- «Ref. producto»: el código entre corchetes del producto ([2250] LLAGOSTA-AMBERES → 2250).
- «Ref./NIF proveedor»: la referencia del contacto o su NIF/CIF.
Con eso el proceso busca en Odoo el nombre y el ID, y prepara las escrituras para completar la Excel
(las hace n8n a las 5:00). Si una fila tiene ID pero no referencia, se rellena la referencia.

Solo lee de Odoo (product.product y res.partner, search_read).
"""
from __future__ import annotations

import re

from openpyxl.utils import column_index_from_string, get_column_letter

import argon_facturacion as F

REF_PRODUCTO = "Ref. producto"
REF_PROVEEDOR = "Ref./NIF proveedor"
FILA_CAB = 3

# hoja → (columna de nombre de producto, columna ID producto) y (columna nombre proveedor, columna ID proveedor)
PRODUCTO = {"TARIFAS": ("Producto en Odoo", "ID producto"), "PRODUCTOS": ("Producto en Odoo", "ID producto")}
PROVEEDOR = {"TARIFAS": ("Proveedor", "ID proveedor"), "PRODUCTOS": ("Proveedor por defecto", "ID proveedor"),
             "CONDUCTORES": (None, "ID proveedor"), "RESERVAS": ("Proveedor", "ID proveedor")}


def _txt(v) -> str:
    if v is None or v is False:  # Odoo devuelve False en los campos vacíos
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _ref_producto(v) -> str:
    """Admite «2250» o «[2250] LLAGOSTA-AMBERES»."""
    t = _txt(v)
    m = re.match(r"^\[([^\]]+)\]", t)
    return (m.group(1) if m else t).strip()


def _nif(v) -> str:
    t = re.sub(r"[\s\-.]", "", _txt(v)).upper()
    return t[2:] if re.match(r"^[A-Z]{2}[0-9A-Z]", t) and t[:2] == "ES" else t


def _columna(hoja: str, nombre: str, escrituras: list) -> str:
    """Letra de la columna; si no existe, se crea a la derecha de la tabla (cabecera en la fila 3)."""
    cols = F.COLS.setdefault(hoja, {})
    if nombre not in cols:
        ultima = max((column_index_from_string(x) for x in cols.values()), default=0)
        cols[nombre] = get_column_letter(ultima + 1)
        escrituras.append({"hoja": hoja, "celda": f"{cols[nombre]}{FILA_CAB}", "valor": nombre})
    return cols[nombre]


def _filas(tablas: dict, hoja: str) -> list:
    t = {"TARIFAS": tablas["tarifas"], "PRODUCTOS": list(tablas["productos"].values()),
         "CONDUCTORES": list(tablas["conductores"].values()), "RESERVAS": tablas["reservas"]}[hoja]
    return [f for f in t if f.get("_fila")]


def completar(tablas: dict, odoo) -> dict:
    """Completa nombre e ID desde la referencia (y la referencia desde el ID). Devuelve escrituras y avisos."""
    esc, avisos = [], []

    def poner(hoja, fila, columna, valor):
        if columna is None:
            return
        letra = _columna(hoja, columna, esc)
        if _txt(fila.get(columna)) != _txt(valor):
            fila[columna] = valor
            esc.append({"hoja": hoja, "celda": f"{letra}{fila['_fila']}", "valor": valor})

    # ---- productos
    filas_p = [(h, f) for h in PRODUCTO for f in _filas(tablas, h)]
    for h, _ in filas_p:
        _columna(h, REF_PRODUCTO, esc)
    def ref_p(h, f):  # la columna de referencia o, si está vacía y no hay ID, «[2250]» escrito en el nombre
        r = _ref_producto(f.get(REF_PRODUCTO))
        if not r and not F._int(f.get(PRODUCTO[h][1])) and _txt(f.get(PRODUCTO[h][0])).startswith("["):
            r = _ref_producto(f.get(PRODUCTO[h][0]))
        return r

    refs = {r for h, f in filas_p if (r := ref_p(h, f))}
    ids = {i for h, f in filas_p if not _txt(f.get(REF_PRODUCTO)) and (i := F._int(f.get(PRODUCTO[h][1])))}
    por_ref, por_id = {}, {}
    if refs:
        for p in odoo("product.product", "search_read", [[["default_code", "in", sorted(refs)]]],
                      fields=["default_code", "display_name"]):
            por_ref.setdefault(p["default_code"], []).append(p)
    if ids:
        por_id = {p["id"]: p for p in odoo("product.product", "search_read", [[["id", "in", sorted(ids)]]],
                                           fields=["default_code", "display_name"])}
    for h, f in filas_p:
        nombre, col_id = PRODUCTO[h]
        r = ref_p(h, f)
        if r:
            c = por_ref.get(r, [])
            if len(c) == 1:
                poner(h, f, REF_PRODUCTO, r)
                poner(h, f, col_id, c[0]["id"])
                poner(h, f, nombre, c[0]["display_name"])
            else:
                avisos.append({"hoja": h, "celda": f"{F.COLS[h][REF_PRODUCTO]}{f['_fila']}", "texto":
                               f"producto [{r}] " + ("no existe en Odoo" if not c else f"repetido en Odoo ({len(c)} productos)")})
        elif (i := F._int(f.get(col_id))) and i in por_id:
            if por_id[i]["default_code"]:
                poner(h, f, REF_PRODUCTO, por_id[i]["default_code"])
            if not _txt(f.get(nombre)):
                poner(h, f, nombre, por_id[i]["display_name"])

    # ---- proveedores
    filas_v = [(h, f) for h in PROVEEDOR for f in _filas(tablas, h)]
    for h, _ in filas_v:
        _columna(h, REF_PROVEEDOR, esc)
    refs = {r for h, f in filas_v if (r := _txt(f.get(REF_PROVEEDOR)))}
    ids = {i for h, f in filas_v if not _txt(f.get(REF_PROVEEDOR)) and (i := F._int(f.get(PROVEEDOR[h][1])))}
    campos = ["ref", "vat", "display_name", "parent_id", "supplier_rank"]
    cand = []
    if refs:
        nifs = sorted({x for r in refs for x in (_nif(r), "ES" + _nif(r))})
        cand = odoo("res.partner", "search_read", [["|", ["ref", "in", sorted(refs)], ["vat", "in", nifs]]], fields=campos)
    por_id = {p["id"]: p for p in odoo("res.partner", "search_read", [[["id", "in", sorted(ids)]]], fields=campos)} if ids else {}

    def buscar(r):
        c = [p for p in cand if _txt(p["ref"]) == r] or [p for p in cand if p["vat"] and _nif(p["vat"]) == _nif(r)]
        if len(c) > 1:  # mismo NIF en varias direcciones: la empresa principal, mejor si ya es proveedor
            c = [p for p in c if not p["parent_id"]] or c
            c = [p for p in c if p["supplier_rank"]] if len([p for p in c if p["supplier_rank"]]) == 1 else c
        return c

    for h, f in filas_v:
        nombre, col_id = PROVEEDOR[h]
        r = _txt(f.get(REF_PROVEEDOR))
        if r:
            c = buscar(r)
            if len(c) == 1:
                poner(h, f, col_id, c[0]["id"])
                poner(h, f, nombre, c[0]["display_name"])
            else:
                avisos.append({"hoja": h, "celda": f"{F.COLS[h][REF_PROVEEDOR]}{f['_fila']}", "texto":
                               f"proveedor {r} " + ("no existe en Odoo" if not c else f"repetido en Odoo ({len(c)} contactos)")})
        elif (i := F._int(f.get(col_id))) and i in por_id:
            p = por_id[i]
            if _txt(p["ref"]) or p["vat"]:
                poner(h, f, REF_PROVEEDOR, _txt(p["ref"]) or _nif(p["vat"]))
            if nombre and not _txt(f.get(nombre)):
                poner(h, f, nombre, p["display_name"])
    return {"escrituras": esc, "avisos": avisos}
