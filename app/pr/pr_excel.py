"""Las dos Excel de los viajes de PR (se generan desde Odoo; nadie escribe a mano en ellas).

- CARTON PAJA TREN (una por tren): una fila por caja móvil, como la Excel de siempre, sin las columnas de stock.
- REGISTRO VIAJES PR (acumulativa): una fila por viaje, con su estado («En stock» o «Tren N»).
"""
from __future__ import annotations

import datetime as dt
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import pr_reglas as R

AZUL = PatternFill("solid", fgColor="1F4E79")
GRIS = PatternFill("solid", fgColor="D9E1F2")
AMARILLO = PatternFill("solid", fgColor="FFF2CC")
VERDE = PatternFill("solid", fgColor="E2EFDA")
ROJO = PatternFill("solid", fgColor="F8CBAD")
FINA = Side(style="thin", color="A6A6A6")
BORDE = Border(left=FINA, right=FINA, top=FINA, bottom=FINA)
BLANCO_NEGRITA = Font(bold=True, color="FFFFFF")
CENTRO = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _fecha(texto: str | None):
    try:
        return dt.date.fromisoformat((texto or "")[:10])
    except ValueError:
        return None


def _guardar(wb: Workbook) -> bytes:
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def _cabecera(ws, fila: int, titulos: list[str], anchos: list[int]):
    for c, (t, w) in enumerate(zip(titulos, anchos), start=1):
        celda = ws.cell(fila, c, t)
        celda.fill, celda.font, celda.alignment, celda.border = AZUL, BLANCO_NEGRITA, CENTRO, BORDE
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.row_dimensions[fila].height = 32


# ---- CARTON PAJA TREN ---------------------------------------------------------------------------------------------

COLS_TREN = ["ord", "Fecha", "Matrícula", "Balas", "Peso", "Lugar de carga", "ORDEN", "Nº Viaje", "Vagón", "Caja móvil",
             "Tara C.Movil", "Tipo", "Balas", "Neto C.Movil", "Dist. balas", "Lonas", "Puertas", "Precinto"]
ANCHOS_TREN = [5, 11, 11, 7, 9, 22, 16, 15, 15, 14, 10, 7, 7, 11, 9, 8, 9, 9]


def carton_paja_tren(tren: int, convoy: str, fecha: str, cajas: list[dict], viajes_por_bulto: dict[int, dict]) -> bytes:
    """cajas: filas del tren (orden, vagon, segunda, caja_movil, tara_kg, bulto_id?, comprobaciones?)."""
    wb = Workbook()
    ws = wb.active
    ws.title = f"TREN {tren}"
    ws["A1"] = f"CARTON PAJA · TREN {tren}"
    ws["A1"].font = Font(bold=True, size=16)
    ws["H1"], ws["J1"] = "PUESTO A DISPOSICION", "HORA INICIO"
    ws["H2"], ws["J2"] = "finalizacion estimada", "HORA SALIDA"
    ws["O1"], ws["Q1"] = "TREN", tren
    ws["O2"], ws["Q2"] = "Nº Convoy", convoy
    ws["O3"], ws["Q3"] = "Fecha", _fecha(fecha)
    ws["Q3"].number_format = "DD/MM/YYYY"
    for c in ("H1", "J1", "H2", "J2", "O1", "O2", "O3"):
        ws[c].font = Font(bold=True)
    ws["Q1"].font = Font(bold=True, size=14)
    ws["B3"] = "Mercancía recogida"
    ws["B3"].font = Font(bold=True, italic=True)
    ws.merge_cells("O4:R4")
    ws["O4"] = "COMPROBACIONES"
    ws["O4"].fill, ws["O4"].font, ws["O4"].alignment = GRIS, Font(bold=True), CENTRO
    _cabecera(ws, 5, COLS_TREN, ANCHOS_TREN)
    fila, cargadas, peso = 6, 0, 0
    for c in cajas:
        v = viajes_por_bulto.get(c.get("bulto_id")) or {}
        comp = c.get("comprobaciones") or {}
        valores = ["“" if c.get("segunda") else c.get("orden"), _fecha(v.get("fecha")), v.get("tractora"), v.get("bultos"),
                   v.get("peso_kg"), v.get("lugar"), v.get("codigo_paper"), v.get("albaran"),
                   "“" if c.get("segunda") else c.get("vagon"), c.get("caja_movil"), c.get("tara_kg"), v.get("tipo"),
                   v.get("bultos"), v.get("peso_kg")]
        valores += [("OK" if comp.get(k) else "NO") if v else "" for k, _ in R.COMPROBACIONES]
        for col, val in enumerate(valores, start=1):
            celda = ws.cell(fila, col, val if val not in ("", None) else None)
            celda.border = BORDE
            celda.alignment = Alignment(horizontal="center" if col not in (6,) else "left", vertical="center")
            if col == 2:
                celda.number_format = "DD/MM/YYYY"
            if col in (5, 11, 14):
                celda.number_format = "#,##0"
            if col >= 15 and val:
                celda.fill = VERDE if val == "OK" else ROJO
        if not v:  # caja vacía: se ve de un vistazo
            for col in range(1, 15):
                ws.cell(fila, col).fill = AMARILLO
        else:
            cargadas += 1
            peso += v.get("peso_kg") or 0
        fila += 1
    fila += 1
    ws.cell(fila, 1, f"Cajas: {len(cajas)} · cargadas: {cargadas} · vacías: {len(cajas) - cargadas} · peso neto: {peso:,.0f} kg"
            .replace(",", ".")).font = Font(bold=True)
    ws.freeze_panes = "A6"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    return _guardar(wb)


# ---- REGISTRO VIAJES PR -------------------------------------------------------------------------------------------

COLS_REG = [("fecha", "Fecha carga", 11), ("estado", "Estado", 11), ("albaran", "Nº albarán", 15),
            ("codigo_paper", "Código carga Paper", 16), ("lugar", "Lugar de carga", 22), ("tractora", "Tractora", 10),
            ("remolque", "Remolque", 10), ("colaborador", "Transportista", 28), ("bultos", "Balas", 7),
            ("peso_kg", "Peso neto kg", 11), ("toneladas", "Toneladas facturadas", 11), ("tipo", "Tipo", 6),
            ("ler", "LER", 8), ("contenedor", "Contenedor", 13), ("tren", "Tren", 6), ("convoy", "Nº convoy", 9),
            ("fecha_tren", "Fecha tren", 11), ("vagon", "Vagón", 14), ("caja_movil", "Caja móvil", 13),
            ("comprobaciones", "Comprobaciones", 38), ("pedido", "Pedido Odoo", 11), ("bulto", "Bulto", 11)]


def registro(viajes: list[dict], generado: dt.datetime | None = None) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "VIAJES PR"
    ws["A1"] = "REGISTRO DE VIAJES DE PR · SAICA PAPER"
    ws["A1"].font = Font(bold=True, size=14)
    stock = [v for v in viajes if v.get("estado") == "En stock"]
    ws["A2"] = (f"Generado desde Odoo el {(generado or dt.datetime.now()):%d/%m/%Y %H:%M}. "
                f"{len(viajes)} viajes · {len(stock)} en stock esperando tren. No escribir aquí: se rehace cada vez.")
    ws["A2"].font = Font(italic=True, color="595959")
    _cabecera(ws, 4, [t for _, t, _ in COLS_REG], [w for _, _, w in COLS_REG])
    for i, v in enumerate(viajes, start=5):
        for col, (k, _, _) in enumerate(COLS_REG, start=1):
            val = v.get(k)
            if k in ("fecha", "fecha_tren"):
                val = _fecha(val)
            celda = ws.cell(i, col, val if val not in ("", None) else None)
            celda.border = BORDE
            if k in ("fecha", "fecha_tren"):
                celda.number_format = "DD/MM/YYYY"
            elif k == "peso_kg":
                celda.number_format = "#,##0"
            elif k == "toneladas":
                celda.number_format = "0.00"
        ws.cell(i, 2).fill = AMARILLO if v.get("estado") == "En stock" else VERDE
        faltan = [t for k, t in (("codigo_paper", "código Paper"), ("peso_kg", "peso"), ("bultos", "balas")) if not v.get(k)]
        if faltan:
            ws.cell(i, len(COLS_REG) + 1, "Falta: " + ", ".join(faltan)).font = Font(color="C00000", bold=True)
    ws.cell(4, len(COLS_REG) + 1, "Avisos").font = Font(bold=True)
    ws.column_dimensions[get_column_letter(len(COLS_REG) + 1)].width = 30
    ws.freeze_panes = "C5"
    ws.auto_filter.ref = f"A4:{get_column_letter(len(COLS_REG))}{max(4, len(viajes) + 4)}"
    return _guardar(wb)
