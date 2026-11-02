"""Ordena y da el mismo formato a las columnas de las tablas de la Excel ARGON.

- Orden lógico de columnas en PRODUCTOS, TARIFAS, CONDUCTORES, RESERVAS y UBICACIONES (el automatismo lee por el
  nombre de la cabecera, no por la posición, así que cambiar el orden no afecta).
- Cabecera **amarilla** = lo rellena la persona; cabecera **azul** = lo completa el automatismo (ID de Odoo, contadores…).
  También en CHASIS y VENCIMIENTOS (sin cambiarles el orden).
- Las columnas añadidas después («Ref. producto», «Ref./NIF proveedor», «Nombre en Odoo»…) toman, fila a fila, el
  formato de su columna «hermana» (la del ID) y su ancho.
- Mueve también las listas desplegables y el filtro; si se mueve la lista de UBICACIONES, se actualiza la lista
  desplegable de la hoja diaria. No toca la hoja diaria ni SITUACION HOY; VENCIMIENTOS no se reordena (fórmulas).
"""
from __future__ import annotations

import copy
import re

from openpyxl.styles import Font, PatternFill
from openpyxl.utils import column_index_from_string, get_column_letter

FILA_CAB = 3

ORDEN = {
    "PRODUCTOS": ["Código", "Qué es", "Palabra en la celda", "¿Se factura?", "Ref. producto", "Producto en Odoo",
                  "ID producto", "Unidad", "Precio por defecto €", "Coste por defecto €", "Ref./NIF proveedor",
                  "Proveedor por defecto", "ID proveedor", "Cuenta analítica", "Cuenta analítica (ID)", "Notas"],
    "TARIFAS": ["Servicio", "Origen", "Destino", "Cargado/Vacío", "¿Se factura?", "Ref. producto", "Producto en Odoo",
                "ID producto", "Unidad", "Km", "Precio venta €", "Ref./NIF proveedor", "Proveedor", "ID proveedor",
                "Coste €", "Cuenta analítica", "Cuenta analítica (ID)", "Vigente desde", "Veces en la Excel",
                "Última vez", "Origen del dato"],
    "CONDUCTORES": ["Código", "Tipo", "Nombre", "Empresa", "ID conductor en Odoo", "Tractora habitual", "ID tractora",
                    "Ref./NIF proveedor", "ID proveedor", "Coste por km €", "Veces en la Excel", "Notas"],
    "RESERVAS": ["Nº expedición", "Fecha salida", "Origen", "Destino", "Contenedor", "Ref./NIF proveedor", "Proveedor",
                 "ID proveedor", "Coste €", "Estado", "Origen del dato", "Notas"],
    "UBICACIONES": ["Texto escrito en la Excel", "Ubicación normalizada", "Tipo", "País", "Ref. Odoo", "Nombre en Odoo",
                    "ID en Odoo", "Notas"],
}
APARTE = {"UBICACIONES": ["Lista desplegable"]}  # columnas fuera de la tabla (tras una columna vacía, sin filtro)
# lo que completa el automatismo (cabecera azul); el resto lo rellena la persona (cabecera amarilla)
AUTOMATICAS = {"ID producto", "ID proveedor", "Cuenta analítica (ID)", "ID en Odoo", "ID conductor en Odoo",
               "ID tractora", "ID remolque en Odoo", "Veces en la Excel", "Última vez"}
AUTOMATICAS_HOJA = {"VENCIMIENTOS": {"Vence", "Días que faltan", "Estado", "Origen del dato"},  # con fórmula o del proceso
                    "TARIFAS": {"Origen del dato"}}
SOLO_COLOR = ["CHASIS", "VENCIMIENTOS"]
# columna que da el formato (fila a fila) a las columnas nuevas o sin formato
MODELO = {"Ref. producto": "ID producto", "Ref./NIF proveedor": "ID proveedor", "Ref. Odoo": "ID en Odoo",
          "Nombre en Odoo": "ID en Odoo", "Cuenta analítica": "Cuenta analítica (ID)"}
LISTAS = {("TARIFAS", "Servicio"): '"TREN,TRAYECTO,ESPERA,CARGA,DESCARGA,TRASVASE,PESAJE"'}  # CR ya no es servicio
AMARILLO = PatternFill("solid", fgColor="FFFFD966")
NOTA = " Cabecera amarilla = lo rellenáis vosotros (referencia o nombre tal como se ve en Odoo); cabecera azul = lo completa el automatismo cada noche."


def _cab(ws) -> dict:
    return {str(c.value).strip(): c.column for c in ws[FILA_CAB] if c.value not in (None, "")}


def _mover_rango(sqref: str, mapa: dict) -> str | None:
    """«D4:D60» con la columna D movida a la F → «F4:F60». Solo rangos de una columna."""
    res = []
    for parte in str(sqref).split():
        m = re.fullmatch(r"([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?", parte)
        if not m:
            return None
        c1, f1, c2, f2 = m.groups()
        if c2 and c2 != c1:
            return None
        n = mapa.get(column_index_from_string(c1))
        if n is None:
            continue
        letra = get_column_letter(n)
        res.append(f"{letra}{f1}" + (f":{letra}{f2}" if c2 else ""))
    return " ".join(res) or None


def _colorear(ws):
    """Cabeceras: azul (automático) o amarilla (lo rellena la persona) y la nota de la fila 2."""
    for c in ws[FILA_CAB]:
        if c.value in (None, ""):
            continue
        if str(c.value).strip() not in AUTOMATICAS | AUTOMATICAS_HOJA.get(ws.title, set()):
            c.fill = AMARILLO
            c.font = Font(name=c.font.name, size=c.font.size, bold=True, color="FF1F1F1F")
    nota = ws.cell(2, 1)
    if isinstance(nota.value, str) and "Cabecera amarilla" not in nota.value:
        nota.value = nota.value + NOTA
    elif nota.value in (None, ""):
        nota.value = NOTA.strip()


def ordenar_hoja(ws, orden: list[str], aparte: list[str] = ()) -> tuple[dict, dict]:
    cab = _cab(ws)
    nombres = [n for n in orden if n in cab or n in MODELO]
    nombres += [n for n in sorted(cab, key=cab.get) if n not in orden and n not in aparte]
    n_tabla = len(nombres)
    destino = {n: i for i, n in enumerate(nombres, 1)}
    for k, n in enumerate([n for n in aparte if n in cab], start=n_tabla + 2):  # una columna vacía en medio
        destino[n] = k
    viejo = {n: cab.get(n) for n in destino}
    mapa = {viejo[n]: i for n, i in destino.items() if viejo[n]}   # columna vieja → nueva
    ult_fila = ws.max_row
    ncol_vieja = max(cab.values()) if cab else 0
    foto = {}
    for col in range(1, ncol_vieja + 1):
        for fila in range(FILA_CAB, ult_fila + 1):
            c = ws.cell(fila, col)
            foto[(fila, col)] = (c.value, copy.copy(c._style), c.comment, c.hyperlink)
    anchos = {col: ws.column_dimensions[get_column_letter(col)].width for col in range(1, ncol_vieja + 1)}
    vacio = copy.copy(ws.cell(ult_fila + 5, ncol_vieja + 5)._style)
    for (fila, col) in foto:
        c = ws.cell(fila, col)
        c.value, c.comment, c.hyperlink = None, None, None
        c._style = copy.copy(vacio)
    estilo_cab = foto[(FILA_CAB, 1)][1]
    for n, i in destino.items():
        vieja = viejo[n]
        modelo = viejo.get(MODELO.get(n)) if n in MODELO else None
        for fila in range(FILA_CAB, ult_fila + 1):
            c = ws.cell(fila, i)
            v, st, com, hl = foto[(fila, vieja)] if vieja else ((n if fila == FILA_CAB else None), None, None, None)
            c.value = v
            if fila == FILA_CAB:
                c._style = copy.copy(estilo_cab)
            elif modelo:
                c._style = copy.copy(foto[(fila, modelo)][1])
            elif st is not None:
                c._style = st
            if com:
                c.comment = com
            if hl:
                c.hyperlink = hl
        ancho = anchos.get(modelo) if modelo else anchos.get(vieja)
        if n in MODELO:
            ancho = max(ancho or 0, 18 if n == "Nombre en Odoo" else 14)
        if ancho:
            ws.column_dimensions[get_column_letter(i)].width = ancho
    # listas desplegables de la propia hoja
    nuevas = []
    for dv in ws.data_validations.dataValidation:
        sq = _mover_rango(str(dv.sqref), mapa)
        if sq:
            dv2 = copy.copy(dv)
            dv2.sqref = sq
            nuevas.append(dv2)
    ws.data_validations.dataValidation = []
    por_letra = {get_column_letter(i): n for n, i in destino.items()}
    for dv in nuevas:
        nombre = por_letra.get(re.match(r"[A-Z]+", str(dv.sqref)).group(0))
        if (ws.title, nombre) in LISTAS:
            dv.formula1 = LISTAS[(ws.title, nombre)]
        ws.data_validations.append(dv)
    if ws.auto_filter.ref:
        m = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", ws.auto_filter.ref)
        if m:
            ws.auto_filter.ref = f"A{m.group(2)}:{get_column_letter(n_tabla)}{max(int(m.group(4)), ult_fila)}"
    _colorear(ws)
    return mapa, {"hoja": ws.title, "columnas": nombres + [n for n in aparte if n in cab],
                  "nuevas": [n for n in destino if not viejo[n]]}


def _leyenda(wb):
    """La fila de la LEYENDA sobre referencias explica también nombres, ubicaciones, cuentas y colores."""
    if "LEYENDA" not in wb.sheetnames:
        return
    ws = wb["LEYENDA"]
    for fila in ws.iter_rows(min_row=4):
        if isinstance(fila[0].value, str) and fila[0].value.startswith("Producto o proveedor en PRODUCTOS"):
            vals = ["Producto, proveedor, ubicación o cuenta analítica (tablas)",
                    "Lo que veis en Odoo: la referencia (código entre corchetes, referencia o NIF del contacto, código "
                    "de la cuenta) o el nombre. Columnas con cabecera amarilla",
                    "2250 · A86868114 · TERMINAL SEVILLA LA NEGRILLA · TECO, LAR", "(normal)",
                    "Cada noche se completan el resto de datos y el ID de Odoo (cabecera azul); si no se encuentra o hay "
                    "varios iguales, avisa en el correo con enlace a la celda y a Odoo"]
            for c, v in zip(fila[:5], vals):
                c.value = v
            break


def ordenar(wb) -> list[dict]:
    res, mover_lista = [], None
    for h, o in ORDEN.items():
        if h not in wb.sheetnames:
            continue
        mapa, r = ordenar_hoja(wb[h], o, APARTE.get(h, ()))
        res.append(r)
        if h == "UBICACIONES":
            mover_lista = mapa
    for h in SOLO_COLOR:
        if h in wb.sheetnames:
            _colorear(wb[h])
    # la lista desplegable de sitios de la hoja diaria apunta a UBICACIONES: si la columna se ha movido, se corrige
    if mover_lista:
        for ws in wb.worksheets:
            for dv in ws.data_validations.dataValidation:
                f = dv.formula1 or ""
                m = re.search(r"UBICACIONES!\$([A-Z]+)\$(\d+):\$([A-Z]+)\$(\d+)", f)
                if m and m.group(1) == m.group(3):
                    n = mover_lista.get(column_index_from_string(m.group(1)))
                    if n:
                        L = get_column_letter(n)
                        dv.formula1 = f.replace(m.group(0), f"UBICACIONES!${L}${m.group(2)}:${L}${m.group(4)}")
    _leyenda(wb)
    return res
