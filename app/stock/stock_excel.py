"""Excel del almacén de Burriana (las genera la app desde el estado; nadie escribe a mano en ellas), con la imagen de STINSA.

- CONTROL ALMACEN BURRIANA.xlsx (el libro de control, en SharePoint › Almacen Burriana; se rehace tras cada entrada o salida):
    RESUMEN STOCK · STOCK · ENTRADAS · SALIDAS · MOVIMIENTOS · CLIENTES
- ENTRADA E-AAAA-NNNN.xlsx: albarán del cliente frente a los palets verificados (va por correo a la oficina).
- SALIDA S-AAAA-NNNN.xlsx: lo que se ha cargado frente a lo pedido, con la lista de palets (va por correo a la oficina).
"""
from __future__ import annotations

import datetime as dt

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from ..pr import marca as M
from ..pr import pr_excel as X
from . import stock_facturacion as F
from . import stock_reglas as R

LIBRO = "CONTROL ALMACEN BURRIANA.xlsx"


def _fecha(f):
    return X._fecha(f)


def _tabla(ws, fila: int, columnas: list[tuple[str, str, int]], filas: list[dict], relleno=None, formatos: dict | None = None) -> int:
    """columnas = (clave, título, ancho). Devuelve la fila siguiente a la última. `relleno(fila_dict)` → color opcional de la fila."""
    X._cabecera(ws, fila, [t for _, t, _ in columnas], [w for _, _, w in columnas], X.TEAL)
    formatos = formatos or {}
    for i, f in enumerate(filas, start=fila + 1):
        for col, (k, _, _) in enumerate(columnas, start=1):
            v = f.get(k)
            if k.startswith("fecha") and v:
                v = _fecha(v)
            celda = ws.cell(i, col, v if v not in ("", None) else None)
            celda.border = X.BORDE
            celda.alignment = Alignment(vertical="center", wrap_text=k in ("discrepancias", "motivo", "observaciones"))
            if k.startswith("fecha"):
                celda.number_format = "DD/MM/YYYY"
            elif k in formatos:
                celda.number_format = formatos[k]
        color = relleno(f) if relleno else None
        if color:
            for col in range(1, len(columnas) + 1):
                ws.cell(i, col).fill = color
    ws.freeze_panes = ws.cell(fila + 1, 1)
    if filas:
        ws.auto_filter.ref = f"A{fila}:{get_column_letter(len(columnas))}{fila + len(filas)}"
    return fila + len(filas) + 1


def _hoja(wb: Workbook, nombre: str, titulo: str, nota: str, columnas: int, primera: bool = False):
    ws = wb.active if primera else wb.create_sheet()
    ws.title = nombre
    X._membrete(ws, titulo, columnas)
    ws["D2"] = nota
    ws["D2"].font = Font(italic=True, color="595959")
    return ws


def _apaisada(ws):
    ws.page_setup.orientation = "landscape"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0


KG = "#,##0"


# ---- libro de control ----------------------------------------------------------------------------------------------

def libro_control(entradas: list[dict], salidas: list[dict], clientes: list[dict], hoy: dt.date | None = None,
                  generado: dt.datetime | None = None, controles: list[dict] | None = None) -> bytes:
    hoy = hoy or dt.date.today()
    existencias = R.stock(entradas, salidas, controles)
    cuando = f"{generado or dt.datetime.now():%d/%m/%Y %H:%M}"
    nota = f"Generado por la app el {cuando}. No escribir aquí: se rehace tras cada entrada o salida."
    wb = Workbook()

    # RESUMEN: por cliente y referencia
    ws = _hoja(wb, "RESUMEN STOCK", "CONTROL DE STOCK · ALMACÉN DE BURRIANA", nota, 7, primera=True)
    grupos: dict[tuple[str, str, str], dict] = {}
    for p in existencias:
        g = grupos.setdefault((p["cliente_nombre"], p["ref"], p["descripcion"]), {"cliente": p["cliente_nombre"], "ref": p["ref"],
                              "descripcion": p["descripcion"], "palets": 0, "kg": 0, "fecha_mas_antiguo": p["fecha_entrada"]})
        g["palets"] += 1
        g["kg"] += p["kg"] or 0
        g["fecha_mas_antiguo"] = min(g["fecha_mas_antiguo"], p["fecha_entrada"])
    filas = sorted(grupos.values(), key=lambda g: (g["cliente"], g["ref"]))
    for g in filas:
        g["dias"] = R.dias_entre(g["fecha_mas_antiguo"], hoy.isoformat())
    fin = _tabla(ws, 4, [("cliente", "Cliente", 30), ("ref", "Referencia", 18), ("descripcion", "Descripción", 36), ("palets", "Palets", 9),
                         ("kg", "Kg", 11), ("fecha_mas_antiguo", "Entrada más antigua", 14), ("dias", "Días", 7)], filas, formatos={"kg": KG})
    ws.cell(fin + 1, 1, f"TOTAL: {len(existencias)} palets · {sum(p['kg'] or 0 for p in existencias):,.0f} kg".replace(",", ".")).font = Font(bold=True, color=M.TEAL_OSCURO)
    _apaisada(ws)

    # STOCK: un palet por fila
    ws = _hoja(wb, "STOCK", "PALETS EN EL ALMACÉN", nota, 13)
    por_cli = lambda p: p["cliente_nombre"]
    filas = [dict(p, dias=R.dias_entre(p["fecha_entrada"], hoy.isoformat())) for p in sorted(existencias, key=lambda p: (por_cli(p), p["ref"], p["fecha_entrada"]))]
    for f in filas:
        f["aviso"] = "Más de un mes" if f["dias"] > R.DIAS_ESTOCAJE_AVISO else ""
    _tabla(ws, 4, [("id", "Palet", 17), ("cliente_nombre", "Cliente", 28), ("ref", "Referencia", 16), ("descripcion", "Descripción", 32), ("lote", "Lote", 15), ("ubicacion", "Ubicación", 14),
                   ("sscc", "Código palet", 21), ("kg", "Kg", 9), ("fecha_prod", "Fecha producción", 13), ("cliente_final", "Cliente final", 24), ("entrada", "Entrada", 14), ("albaran", "Albarán", 14),
                   ("fecha_entrada", "Fecha entrada", 13), ("dias", "Días", 7), ("aviso", "Aviso", 14)], filas, relleno=lambda f: X.AMARILLO if f["aviso"] else None, formatos={"kg": KG})
    _apaisada(ws)

    # ENTRADAS: una fila por línea del albarán
    ws = _hoja(wb, "ENTRADAS", "ENTRADAS", nota, 14)
    filas = []
    for e in sorted(entradas, key=lambda e: e["id"], reverse=True):
        if e["estado"] == R.ENTRADA_ANULADA:
            continue
        disc = " | ".join(e.get("discrepancias_cierre") or [])
        for l in R.estado_lineas(e) or [{}]:
            filas.append({"id": e["id"], "fecha": R.fecha_entrada(e), "cliente": e["cliente"]["nombre"], "cliente_final": e["albaran"].get("cliente_final") or "",
                          "observaciones": e.get("observaciones") or "", "albaran": e["albaran"].get("numero"), "matricula": e["albaran"].get("matricula"), "ref": l.get("ref"),
                          "descripcion": l.get("descripcion"), "palets_albaran": l.get("esperados"), "palets_verificados": l.get("verificados"),
                          "kg_albaran": l.get("kg_albaran"), "kg_etiquetas": l.get("kg_etiquetas"), "estado": e["estado"].upper(), "discrepancias": disc})
    _tabla(ws, 4, [("id", "Entrada", 14), ("fecha", "Fecha entrada", 12), ("cliente", "Cliente", 26), ("cliente_final", "Cliente final", 24), ("albaran", "Albarán", 14), ("matricula", "Matrícula", 11),
                   ("ref", "Referencia", 15), ("descripcion", "Descripción", 28), ("palets_albaran", "Palets albarán", 9), ("palets_verificados", "Palets verificados", 11),
                   ("kg_albaran", "Kg albarán", 10), ("kg_etiquetas", "Kg etiquetas", 10), ("estado", "Estado", 12), ("discrepancias", "Discrepancias", 44), ("observaciones", "Observaciones almacén", 40)],
          filas, relleno=lambda f: X.AMARILLO if f["estado"] != "CERRADA" else (X.ROJO if f["discrepancias"] else None), formatos={"kg_albaran": KG, "kg_etiquetas": KG})
    _apaisada(ws)

    # SALIDAS: una fila por línea pedida
    ws = _hoja(wb, "SALIDAS", "SALIDAS", nota, 11)
    filas = []
    for s in sorted(salidas, key=lambda s: s["id"], reverse=True):
        if s["estado"] == R.SALIDA_ANULADA:
            continue
        disc = " | ".join(s.get("discrepancias_cierre") or [])
        for l in R.estado_lineas_salida(s):
            filas.append({"id": s["id"], "fecha": (s.get("cargada") or s["creada"])[:10], "cliente": s["cliente"]["nombre"], "pedido": s.get("referencia"),
                          "destino": s.get("destino"), "ref": l["ref"], "descripcion": l["descripcion"], "palets_pedidos": l["pedidos"],
                          "palets_cargados": l["cargados"], "kg": l["kg"], "estado": s["estado"].upper(), "discrepancias": disc,
                          "observaciones": s.get("observaciones") or ""})
    _tabla(ws, 4, [("id", "Salida", 14), ("fecha", "Fecha", 11), ("cliente", "Cliente", 26), ("pedido", "Pedido cliente", 16), ("destino", "Destino", 24),
                   ("ref", "Referencia", 15), ("descripcion", "Descripción", 28), ("palets_pedidos", "Palets pedidos", 9), ("palets_cargados", "Palets cargados", 9),
                   ("kg", "Kg", 10), ("estado", "Estado", 12), ("discrepancias", "Discrepancias", 44), ("observaciones", "Observaciones almacén", 40)], filas,
          relleno=lambda f: X.AMARILLO if f["estado"] != "CARGADA" else (X.ROJO if f["discrepancias"] else None), formatos={"kg": KG})
    _apaisada(ws)

    # MOVIMIENTOS: base de la facturación (entradas, salidas y estocaje)
    ws = _hoja(wb, "MOVIMIENTOS", "MOVIMIENTOS · BASE DE LA FACTURACIÓN", "Un movimiento por palet. De aquí saldrán la manipulación de entrada, la de salida y el estocaje.", 9)
    nombres = {c["codigo"]: c["nombre"] for c in clientes}
    filas = [dict(m, cliente=nombres.get(m["cliente"], m["cliente"])) for m in F.movimientos(entradas, salidas, controles)]
    _tabla(ws, 4, [("fecha", "Fecha", 11), ("tipo", "Tipo", 10), ("cliente", "Cliente", 28), ("ref", "Referencia", 16), ("lote", "Lote", 14), ("sscc", "Código palet", 21),
                   ("palet_id", "Palet", 17), ("kg", "Kg", 9), ("documento", "Documento", 14)], filas,
          relleno=lambda f: X.VERDE if f["tipo"] == "ENTRADA" else X.ROJO, formatos={"kg": KG})
    _apaisada(ws)

    # CLIENTES
    ws = _hoja(wb, "CLIENTES", "CLIENTES DEL ALMACÉN", "Los nombres y alias sirven para reconocer al cliente en los albaranes.", 4)
    _tabla(ws, 4, [("codigo", "Código", 16), ("nombre", "Nombre", 36), ("alias", "Alias", 40), ("activo", "Activo", 8)],
           [{"codigo": c["codigo"], "nombre": c["nombre"], "alias": ", ".join(c.get("alias") or []), "activo": "No" if c.get("activo") is False else "Sí"} for c in clientes])
    wb.active = 0
    return X._guardar(wb)


# ---- Excel de una entrada ------------------------------------------------------------------------------------------

def _incidencia_txt(p: dict) -> str:
    return " | ".join(i["texto"] + (f" ({len(i['fotos'])} foto{'s' if len(i['fotos']) != 1 else ''})" if i.get("fotos") else "") for i in p.get("incidencias") or [] if i.get("texto") or i.get("fotos"))


def nombre_entrada(e: dict) -> str:
    return f"{'INVENTARIO' if e.get('tipo') == 'inventario' else 'ENTRADA'} {e['id']}.xlsx"


def nombre_salida(s: dict) -> str:
    return f"SALIDA {s['id']}.xlsx"


def carpeta(op: dict) -> str:
    """Carpeta de SharePoint con las fotos y la Excel de la operación: «ENTRADA E-2026-0001» (dentro de Almacen Burriana)."""
    return f"{ {'E': 'ENTRADA', 'I': 'INVENTARIO', 'C': 'CONTROL'}.get(op['id'][:1], 'SALIDA') } {op['id']}"


def excel_entrada(e: dict) -> bytes:
    wb = Workbook()
    ws = _hoja(wb, "ENTRADA", f"{'ENTRADA MANUAL DE INVENTARIO' if e.get('tipo') == 'inventario' else 'ENTRADA'} {e['id']} · {e['cliente']['nombre']}", R.ALMACEN, 9, primera=True)
    a = e["albaran"]
    datos = [("Albarán", a.get("numero")), ("Fecha albarán", _fecha(a.get("fecha"))), ("Fecha entrada en almacén", _fecha(R.fecha_entrada(e))),
             ("Remitente", a.get("remitente")), ("Cliente final (informativo)", " · ".join(x for x in (a.get("cliente_final"), a.get("codigo_cliente_final")) if x)),
             ("Transportista", a.get("transportista")), ("Matrícula", a.get("matricula")), ("Cerrada", (e.get("cerrada") or "")[:16].replace("T", " ")),
             ("Observaciones del albarán", a.get("observaciones")), ("Incidencias en el almacén", e.get("observaciones"))]
    for i, (k, v) in enumerate(datos, start=4):
        ws.cell(i, 1, k).font = Font(bold=True, color=M.TEAL_OSCURO)
        ws.cell(i, 2, v)
        if k in ("Fecha albarán", "Fecha entrada en almacén"):
            ws.cell(i, 2).number_format = "DD/MM/YYYY"
        if k == "Incidencias en el almacén" and v:
            ws.cell(i, 2).font = Font(bold=True, color="C00000")
        ws.cell(i, 2).alignment = Alignment(horizontal="left")
    fila = _tabla(ws, 4 + len(datos) + 1, [("ref", "Referencia", 18), ("descripcion", "Descripción", 34), ("lote", "Lote", 14), ("esperados", "Palets albarán", 10),
                  ("verificados", "Palets verificados", 11), ("kg_albaran", "Kg albarán", 11), ("kg_etiquetas", "Kg etiquetas", 11)],
                  R.estado_lineas(e), relleno=lambda l: X.VERDE if l["verificados"] == l["esperados"] else X.ROJO, formatos={"kg_albaran": KG, "kg_etiquetas": KG})
    ws.freeze_panes = None
    ws.cell(fila + 1, 1, "Palets verificados con su etiqueta").font = Font(bold=True, color=M.TEAL_OSCURO)
    palets = [dict(p, incidencia_txt=_incidencia_txt(p), estado_txt="OK" if p["estado"] == "ok" else ("ACEPTADO" if p.get("aceptado") else "DISCREPANCIA"), linea_txt=(R.estado_lineas(e)[p["linea"]]["ref"] if p.get("linea") is not None else "")) for p in R.activos(e)]
    fila = _tabla(ws, fila + 2, [("id", "Palet", 18), ("sscc", "Código palet", 22), ("ref", "Referencia", 18), ("lote", "Lote", 15), ("ubicacion", "Ubicación", 14), ("fecha_prod", "Fecha producción", 13), ("kg", "Kg", 11),
                  ("estado_txt", "Estado", 14), ("motivo", "Motivo", 50), ("incidencia_txt", "Incidencia", 50)], palets,
                  relleno=lambda p: None if p["estado_txt"] == "OK" else X.AMARILLO, formatos={"kg": KG})
    ws.freeze_panes = None
    ws.auto_filter.ref = None
    disc = e.get("discrepancias_cierre") or []
    ws.cell(fila + 1, 1, "Sin discrepancias." if not disc else "Discrepancias aceptadas al cerrar: " + " | ".join(disc)
            + (f" · Nota: {e['nota_cierre']}" if e.get("nota_cierre") else "")).font = Font(bold=True, color="C00000" if disc else M.TEAL_OSCURO)
    _apaisada(ws)
    return X._guardar(wb)


def excel_salida(s: dict) -> bytes:
    wb = Workbook()
    ws = _hoja(wb, "SALIDA", f"SALIDA {s['id']} · {s['cliente']['nombre']}", R.ALMACEN, 8, primera=True)
    datos = [("Pedido del cliente", s.get("referencia")), ("Destino", s.get("destino")), ("Transportista / matrícula", s.get("transportista")),
             ("Fecha pedida", _fecha(s.get("fecha_pedida"))), ("Cargada", (s.get("cargada") or "")[:16].replace("T", " ")),
             ("Incidencias en el almacén", s.get("observaciones"))]
    for i, (k, v) in enumerate(datos, start=4):
        ws.cell(i, 1, k).font = Font(bold=True, color=M.TEAL_OSCURO)
        ws.cell(i, 2, v)
        if k == "Fecha pedida":
            ws.cell(i, 2).number_format = "DD/MM/YYYY"
        if k == "Incidencias en el almacén" and v:
            ws.cell(i, 2).font = Font(bold=True, color="C00000")
        ws.cell(i, 2).alignment = Alignment(horizontal="left")
    lineas = R.estado_lineas_salida(s)
    fila = _tabla(ws, 4 + len(datos) + 1, [("ref", "Referencia", 18), ("descripcion", "Descripción", 34), ("pedidos", "Palets pedidos", 11), ("cargados", "Palets cargados", 11),
                  ("kg", "Kg cargados", 12)], lineas, relleno=lambda l: X.VERDE if l["cargados"] == l["pedidos"] else X.ROJO, formatos={"kg": KG})
    ws.freeze_panes = None
    ws.cell(fila, 1, f"TOTAL: {sum(l['cargados'] for l in lineas)} palets · {sum(l['kg'] or 0 for l in lineas):,.0f} kg".replace(",", ".")).font = Font(bold=True, color=M.TEAL_OSCURO)
    ws.cell(fila + 2, 1, "Palets cargados").font = Font(bold=True, color=M.TEAL_OSCURO)
    palets = [dict(p, incidencia_txt=_incidencia_txt(p), estado_txt="OK" if p["estado"] == "ok" else ("ACEPTADO" if p.get("aceptado") else "DISCREPANCIA")) for p in s["palets"] if p.get("estado_alta") != "quitado"]
    fila = _tabla(ws, fila + 3, [("palet_id", "Palet", 18), ("sscc", "Código palet", 22), ("ref", "Referencia", 18), ("lote", "Lote", 15), ("ubicacion", "Ubicación", 14), ("fecha_prod", "Fecha producción", 13), ("kg", "Kg", 11),
                  ("estado_txt", "Estado", 14), ("motivo", "Motivo", 50), ("incidencia_txt", "Incidencia", 50)], palets, relleno=lambda p: None if p["estado_txt"] == "OK" else X.AMARILLO, formatos={"kg": KG})
    ws.freeze_panes = None
    ws.auto_filter.ref = None
    disc = s.get("discrepancias_cierre") or []
    ws.cell(fila + 1, 1, "Sin discrepancias." if not disc else "Discrepancias aceptadas al cerrar: " + " | ".join(disc)
            + (f" · Nota: {s['nota_cierre']}" if s.get("nota_cierre") else "")).font = Font(bold=True, color="C00000" if disc else M.TEAL_OSCURO)
    _apaisada(ws)
    return X._guardar(wb)
