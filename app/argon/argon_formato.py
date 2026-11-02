"""Ordena y da el mismo formato a las columnas de las tablas de la Excel ARGON.

- Orden lógico de columnas en PRODUCTOS, TARIFAS, CONDUCTORES y RESERVAS (el automatismo lee por el nombre de la
  cabecera, no por la posición, así que cambiar el orden no afecta).
- Las columnas añadidas después («Ref. producto», «Ref./NIF proveedor»…) toman el formato de la cabecera y de su
  columna «hermana» (la del ID) fila a fila, y su ancho.
- Mueve también las listas desplegables y el filtro. No toca la hoja diaria, VENCIMIENTOS (tiene fórmulas por
  posición), UBICACIONES (su lista desplegable la usa la hoja diaria) ni SITUACION HOY.
"""
from __future__ import annotations

import copy
import re

from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

FILA_CAB = 3

ORDEN = {
    "PRODUCTOS": ["Código", "Qué es", "Palabra en la celda", "¿Se factura?", "Ref. producto", "Producto en Odoo",
                  "ID producto", "Unidad", "Precio por defecto €", "Coste por defecto €", "Ref./NIF proveedor",
                  "Proveedor por defecto", "ID proveedor", "Cuenta analítica (ID)", "Notas"],
    "TARIFAS": ["Servicio", "Origen", "Destino", "Cargado/Vacío", "¿Se factura?", "Ref. producto", "Producto en Odoo",
                "ID producto", "Unidad", "Km", "Precio venta €", "Ref./NIF proveedor", "Proveedor", "ID proveedor",
                "Coste €", "Cuenta analítica (ID)", "Vigente desde", "Veces en la Excel", "Última vez", "Origen del dato"],
    "CONDUCTORES": ["Código", "Tipo", "Nombre", "Empresa", "ID conductor en Odoo", "Tractora habitual", "ID tractora",
                    "Ref./NIF proveedor", "ID proveedor", "Coste por km €", "Veces en la Excel", "Notas"],
    "RESERVAS": ["Nº expedición", "Fecha salida", "Origen", "Destino", "Contenedor", "Ref./NIF proveedor", "Proveedor",
                 "ID proveedor", "Coste €", "Estado", "Origen del dato", "Notas"],
}
# columna que da el formato (fila a fila) a las columnas nuevas o sin formato
MODELO = {"Ref. producto": "ID producto", "Ref./NIF proveedor": "ID proveedor"}
LISTAS = {("TARIFAS", "Servicio"): '"TREN,TRAYECTO,ESPERA,CARGA,DESCARGA,TRASVASE,PESAJE"'}  # CR ya no es servicio


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


def ordenar_hoja(ws, orden: list[str]) -> dict:
    cab = _cab(ws)
    nombres = [n for n in orden if n in cab or n in MODELO] + [n for n in sorted(cab, key=cab.get) if n not in orden]
    viejo = {n: cab.get(n) for n in nombres}
    mapa = {viejo[n]: i for i, n in enumerate(nombres, 1) if viejo[n]}   # columna vieja → nueva
    ult_fila = ws.max_row
    ncol_vieja = max(cab.values()) if cab else 0
    # foto de las celdas (desde la cabecera) y anchos
    foto = {}
    for col in range(1, ncol_vieja + 1):
        for fila in range(FILA_CAB, ult_fila + 1):
            c = ws.cell(fila, col)
            foto[(fila, col)] = (c.value, copy.copy(c._style), c.comment, c.hyperlink)
    anchos = {col: ws.column_dimensions[get_column_letter(col)].width for col in range(1, ncol_vieja + 1)}
    for (fila, col) in foto:
        c = ws.cell(fila, col)
        c.value, c.comment, c.hyperlink = None, None, None
    estilo_cab = foto[(FILA_CAB, 1)][1]
    for i, n in enumerate(nombres, 1):
        vieja = viejo[n]
        modelo = viejo.get(MODELO.get(n)) if n in MODELO else None
        for fila in range(FILA_CAB, ult_fila + 1):
            c = ws.cell(fila, i)
            if vieja:
                v, st, com, hl = foto[(fila, vieja)]
            else:
                v, st, com, hl = (n if fila == FILA_CAB else None), None, None, None
            c.value = v
            if fila == FILA_CAB:
                c._style = copy.copy(estilo_cab)            # todas las cabeceras iguales
            elif modelo:
                c._style = copy.copy(foto[(fila, modelo)][1])  # mismo formato que su columna hermana
            elif st is not None:
                c._style = st
            if com:
                c.comment = com
            if hl:
                c.hyperlink = hl
        ancho = anchos.get(modelo) if modelo else anchos.get(vieja)
        if n in MODELO:
            ancho = max(ancho or 0, 16)
        if ancho:
            ws.column_dimensions[get_column_letter(i)].width = ancho
    # columnas que sobran a la derecha (si había alguna vacía)
    for col in range(len(nombres) + 1, ncol_vieja + 1):
        for fila in range(FILA_CAB, ult_fila + 1):
            ws.cell(fila, col).value = None
    # listas desplegables
    nuevas = []
    for dv in ws.data_validations.dataValidation:
        sq = _mover_rango(str(dv.sqref), mapa)
        if not sq:
            continue
        dv2 = copy.copy(dv)
        dv2.sqref = sq
        nuevas.append(dv2)
    ws.data_validations.dataValidation = []
    for dv in nuevas:
        letra = re.match(r"[A-Z]+", str(dv.sqref)).group(0)
        nombre = next((n for n, i in zip(nombres, range(1, len(nombres) + 1)) if get_column_letter(i) == letra), None)
        if (ws.title, nombre) in LISTAS:
            dv.formula1 = LISTAS[(ws.title, nombre)]
        ws.data_validations.append(dv)
    # filtro hasta la última columna
    if ws.auto_filter.ref:
        m = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", ws.auto_filter.ref)
        if m:
            ws.auto_filter.ref = f"A{m.group(2)}:{get_column_letter(len(nombres))}{max(int(m.group(4)), ult_fila)}"
    return {"hoja": ws.title, "columnas": nombres, "nuevas": [n for n in nombres if not viejo[n]]}


def ordenar(wb) -> list[dict]:
    return [ordenar_hoja(wb[h], o) for h, o in ORDEN.items() if h in wb.sheetnames]
