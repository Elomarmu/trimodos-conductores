"""Productos, proveedores, ubicaciones y cuentas analíticas de las tablas de la Excel ARGON a partir de lo que se ve en
Odoo (nadie tiene que buscar un ID).

En la Excel se rellena (cabecera amarilla) la referencia **o** el nombre:
- Producto: «Ref. producto» (código entre corchetes: [2250] … → 2250) o «Producto en Odoo» (nombre).
- Proveedor: «Ref./NIF proveedor» (referencia del contacto o NIF/CIF) o el nombre del proveedor.
- Ubicación: «Ref. Odoo» o «Nombre en Odoo» (contacto de Odoo de la terminal o planta).
- Cuenta analítica: «Cuenta analítica» (código o nombre, varias separadas por comas o «+»).
El proceso busca en Odoo y completa el resto (nombre, referencia e ID); n8n lo escribe en la Excel a las 5:00.
Manda la referencia; si no hay, el nombre; si tampoco, el ID que ya hubiera. Si no lo encuentra o hay varios iguales,
lo avisa en el correo con enlace a la celda y a los candidatos de Odoo.

Solo lee de Odoo (search_read de product.product, res.partner y account.analytic.account).
"""
from __future__ import annotations

import re

from openpyxl.utils import column_index_from_string, get_column_letter

import argon_facturacion as F

REF_PRODUCTO = "Ref. producto"
REF_PROVEEDOR = "Ref./NIF proveedor"
REF_UBICACION, NOMBRE_UBICACION = "Ref. Odoo", "Nombre en Odoo"
CUENTA, CUENTA_ID = "Cuenta analítica", "Cuenta analítica (ID)"
FILA_CAB = 3

# hoja → (columna nombre, columna ID)
PRODUCTO = {"TARIFAS": ("Producto en Odoo", "ID producto"), "PRODUCTOS": ("Producto en Odoo", "ID producto")}
PROVEEDOR = {"TARIFAS": ("Proveedor", "ID proveedor"), "PRODUCTOS": ("Proveedor por defecto", "ID proveedor"),
             "CONDUCTORES": (None, "ID proveedor"), "RESERVAS": ("Proveedor", "ID proveedor")}
ANALITICA = ("TARIFAS", "PRODUCTOS")


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


def _clave(v) -> str:
    return re.sub(r"\s+", " ", _txt(v)).upper()


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
         "CONDUCTORES": list(tablas["conductores"].values()), "RESERVAS": tablas["reservas"],
         "UBICACIONES": tablas.get("ubicaciones", [])}[hoja]
    return [f for f in t if f.get("_fila")]


def completar(tablas: dict, odoo) -> dict:
    """Completa nombre, referencia e ID. Devuelve las escrituras para la Excel y los avisos para el correo."""
    esc, avisos = [], []
    base = getattr(odoo, "url", "")

    def enlace(modelo, r):
        return {"nombre": r.get("display_name") or r.get("name"), "url": f"{base}/odoo/{modelo}/{r['id']}" if base else None}

    def poner(hoja, fila, columna, valor):
        if columna is None:
            return
        letra = _columna(hoja, columna, esc)
        if _txt(fila.get(columna)) != _txt(valor):
            fila[columna] = valor
            esc.append({"hoja": hoja, "celda": f"{letra}{fila['_fila']}", "valor": valor})

    def avisar(hoja, fila, columna, texto, modelo=None, cands=()):
        avisos.append({"hoja": hoja, "celda": f"{F.COLS[hoja][columna]}{fila['_fila']}", "texto": texto,
                       "odoo": [enlace(modelo, c) for c in list(cands)[:5]]})

    def leer(modelo, dominio, campos):
        return odoo(modelo, "search_read", [dominio], fields=campos) if dominio else []

    # ================= productos
    filas_p = [(h, f) for h in PRODUCTO for f in _filas(tablas, h)]
    for h, _ in filas_p:
        _columna(h, REF_PRODUCTO, esc)

    def pide_p(h, f):  # («ref", valor) o («nombre», valor) o None
        r = _ref_producto(f.get(REF_PRODUCTO))
        if r:
            return "ref", r
        nombre = _txt(f.get(PRODUCTO[h][0]))
        if nombre and not F._int(f.get(PRODUCTO[h][1])):
            return ("ref", _ref_producto(nombre)) if nombre.startswith("[") else ("nombre", nombre)
        return None

    pedidos = [pide_p(h, f) for h, f in filas_p]
    refs = sorted({v for p in pedidos if p and p[0] == "ref" for v in [p[1]]})
    nombres = sorted({v for p in pedidos if p and p[0] == "nombre" for v in [p[1]]})
    campos = ["default_code", "display_name", "name"]
    cand = leer("product.product", [["default_code", "in", refs]] if refs else None, campos)
    for n in nombres:
        cand += leer("product.product", [["name", "=ilike", n]], campos)
    ids = sorted({i for (h, f), p in zip(filas_p, pedidos) if not p and (i := F._int(f.get(PRODUCTO[h][1])))})
    por_id = {p["id"]: p for p in leer("product.product", [["id", "in", ids]] if ids else None, campos)}
    for (h, f), p in zip(filas_p, pedidos):
        nombre, col_id = PRODUCTO[h]
        if p:
            tipo, v = p
            c = [x for x in cand if (_txt(x["default_code"]) == v if tipo == "ref" else _clave(x["name"]) == _clave(v))]
            c = list({x["id"]: x for x in c}.values())
            if len(c) == 1:
                poner(h, f, REF_PRODUCTO, _txt(c[0]["default_code"]) or (v if tipo == "ref" else ""))
                poner(h, f, col_id, c[0]["id"])
                poner(h, f, nombre, c[0]["display_name"])
            else:
                que = f"producto [{v}]" if tipo == "ref" else f"producto «{v}»"
                avisar(h, f, REF_PRODUCTO if tipo == "ref" else nombre,
                       f"{que} " + ("no existe en Odoo" if not c else f"repetido en Odoo ({len(c)} productos): poned la referencia"),
                       "product.product", c)
        elif (i := F._int(f.get(col_id))) and i in por_id:
            if por_id[i]["default_code"]:
                poner(h, f, REF_PRODUCTO, por_id[i]["default_code"])
            if not _txt(f.get(nombre)):
                poner(h, f, nombre, por_id[i]["display_name"])

    # ================= contactos (proveedores y ubicaciones)
    campos = ["ref", "vat", "display_name", "name", "parent_id", "supplier_rank"]

    def buscar_contactos(refs, nombres, con_nif):
        dom = []
        if refs:
            dom = [["ref", "in", sorted(refs)]]
            if con_nif:
                nifs = sorted({x for r in refs for x in (_nif(r), "ES" + _nif(r))})
                dom = ["|", ["ref", "in", sorted(refs)], ["vat", "in", nifs]]
        res = leer("res.partner", dom or None, campos)
        for n in sorted(nombres):
            res += leer("res.partner", [["name", "=ilike", n]], campos)
        return list({x["id"]: x for x in res}.values())

    def elegir(cand, tipo, v, con_nif, proveedor):
        if tipo == "ref":
            c = [p for p in cand if _txt(p["ref"]) == v] or \
                ([p for p in cand if p["vat"] and _nif(p["vat"]) == _nif(v)] if con_nif else [])
        else:
            c = [p for p in cand if _clave(p["name"]) == _clave(v)]
        if len(c) > 1:  # mismo NIF o nombre en varias direcciones: la empresa principal (y, si es una, la proveedora)
            c = [p for p in c if not p["parent_id"]] or c
            if proveedor and len([p for p in c if p["supplier_rank"]]) == 1:
                c = [p for p in c if p["supplier_rank"]]
        return c

    # ---- proveedores
    filas_v = [(h, f) for h in PROVEEDOR for f in _filas(tablas, h)]
    for h, _ in filas_v:
        _columna(h, REF_PROVEEDOR, esc)

    def pide_v(h, f):
        r = _txt(f.get(REF_PROVEEDOR))
        if r:
            return "ref", r
        nombre = _txt(f.get(PROVEEDOR[h][0])) if PROVEEDOR[h][0] else ""
        if nombre and not F._int(f.get(PROVEEDOR[h][1])):
            return "nombre", nombre
        return None

    pedidos = [pide_v(h, f) for h, f in filas_v]
    cand = buscar_contactos({p[1] for p in pedidos if p and p[0] == "ref"}, {p[1] for p in pedidos if p and p[0] == "nombre"}, True)
    ids = sorted({i for (h, f), p in zip(filas_v, pedidos) if not p and (i := F._int(f.get(PROVEEDOR[h][1])))})
    por_id = {p["id"]: p for p in leer("res.partner", [["id", "in", ids]] if ids else None, campos)}
    for (h, f), p in zip(filas_v, pedidos):
        nombre, col_id = PROVEEDOR[h]
        if p:
            tipo, v = p
            c = elegir(cand, tipo, v, True, True)
            if len(c) == 1:
                if tipo == "nombre" and (_txt(c[0]["ref"]) or c[0]["vat"]):
                    poner(h, f, REF_PROVEEDOR, _txt(c[0]["ref"]) or _nif(c[0]["vat"]))
                poner(h, f, col_id, c[0]["id"])
                poner(h, f, nombre, c[0]["display_name"])
            else:
                avisar(h, f, REF_PROVEEDOR if tipo == "ref" else nombre,
                       f"proveedor «{v}» " + ("no existe en Odoo" if not c else f"repetido en Odoo ({len(c)} contactos): poned la referencia o el NIF"),
                       "res.partner", c)
        elif (i := F._int(f.get(col_id))) and i in por_id:
            q = por_id[i]
            if _txt(q["ref"]) or q["vat"]:
                poner(h, f, REF_PROVEEDOR, _txt(q["ref"]) or _nif(q["vat"]))
            if nombre and not _txt(f.get(nombre)):
                poner(h, f, nombre, q["display_name"])

    # ---- ubicaciones (contactos de Odoo de terminales y plantas)
    filas_u = _filas(tablas, "UBICACIONES")
    if filas_u:
        for col in (REF_UBICACION, NOMBRE_UBICACION):
            _columna("UBICACIONES", col, esc)

        def pide_u(f):
            if _txt(f.get(REF_UBICACION)):
                return "ref", _txt(f.get(REF_UBICACION))
            if _txt(f.get(NOMBRE_UBICACION)):
                return "nombre", _txt(f.get(NOMBRE_UBICACION))
            return None

        pedidos = [pide_u(f) for f in filas_u]
        cand = buscar_contactos({p[1] for p in pedidos if p and p[0] == "ref"}, {p[1] for p in pedidos if p and p[0] == "nombre"}, False)
        ids = sorted({i for f, p in zip(filas_u, pedidos) if not p and (i := F._int(f.get("ID en Odoo")))})
        por_id = {p["id"]: p for p in leer("res.partner", [["id", "in", ids]] if ids else None, campos)}
        for f, p in zip(filas_u, pedidos):
            if p:
                tipo, v = p
                c = elegir(cand, tipo, v, False, False)
                if len(c) == 1:
                    poner("UBICACIONES", f, "ID en Odoo", c[0]["id"])
                    poner("UBICACIONES", f, NOMBRE_UBICACION, c[0]["display_name"])
                    if _txt(c[0]["ref"]):
                        poner("UBICACIONES", f, REF_UBICACION, _txt(c[0]["ref"]))
                else:
                    avisar("UBICACIONES", f, REF_UBICACION if tipo == "ref" else NOMBRE_UBICACION,
                           f"ubicación «{v}» " + ("no existe en Odoo" if not c else f"repetida en Odoo ({len(c)} contactos): poned la referencia"),
                           "res.partner", c)
            elif (i := F._int(f.get("ID en Odoo"))) and i in por_id:
                poner("UBICACIONES", f, NOMBRE_UBICACION, por_id[i]["display_name"])
                if _txt(por_id[i]["ref"]):
                    poner("UBICACIONES", f, REF_UBICACION, _txt(por_id[i]["ref"]))
        # el ID resuelto es el que usan las líneas (origen / destino del envío)
        ids_ubic = {}
        for f in filas_u:
            if F._int(f.get("ID en Odoo")) and f.get("Ubicación normalizada"):
                ids_ubic.setdefault(F.L.norm(f["Ubicación normalizada"]), F._int(f["ID en Odoo"]))
        tablas["ids_ubic"] = ids_ubic

    # ---- cuentas analíticas (código o nombre; varias separadas por «,» o «+»)
    filas_a = [(h, f) for h in ANALITICA for f in _filas(tablas, h)]
    for h, _ in filas_a:
        _columna(h, CUENTA, esc)
    trozos = lambda v: [t.strip() for t in re.split(r"[,+;]", _txt(v)) if t.strip()]  # noqa: E731
    textos = sorted({t for h, f in filas_a for t in trozos(f.get(CUENTA)) if not t.isdigit()})
    ids = sorted({int(t) for h, f in filas_a if not _txt(f.get(CUENTA)) for t in trozos(f.get(CUENTA_ID)) if t.isdigit()})
    campos_a = ["code", "name", "display_name"]
    cand = []
    for t in textos:
        cand += leer("account.analytic.account", ["|", ["code", "=ilike", t], ["name", "=ilike", t]], campos_a)
    cand = list({x["id"]: x for x in cand}.values())
    por_id = {a["id"]: a for a in leer("account.analytic.account", [["id", "in", ids]] if ids else None, campos_a)}
    for h, f in filas_a:
        pedido = trozos(f.get(CUENTA))
        if pedido:
            res, mal = [], []
            for t in pedido:
                if t.isdigit():
                    res.append(int(t))
                    continue
                c = [a for a in cand if _clave(a["code"]) == _clave(t)] or [a for a in cand if _clave(a["name"]) == _clave(t)]
                if len(c) == 1:
                    res.append(c[0]["id"])
                else:
                    mal.append((t, c))
            if mal:
                for t, c in mal:
                    avisar(h, f, CUENTA, f"cuenta analítica «{t}» " + ("no existe en Odoo" if not c else f"repetida en Odoo ({len(c)})"),
                           "account.analytic.account", c)
            else:
                poner(h, f, CUENTA_ID, ",".join(str(i) for i in res))
        else:
            idsf = [int(t) for t in trozos(f.get(CUENTA_ID)) if t.isdigit()]
            if idsf and all(i in por_id for i in idsf):
                poner(h, f, CUENTA, ", ".join(_txt(por_id[i]["code"]) or _txt(por_id[i]["name"]) for i in idsf))
    return {"escrituras": esc, "avisos": avisos}
