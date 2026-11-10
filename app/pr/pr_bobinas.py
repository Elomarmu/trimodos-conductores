"""Bobinas de un convoy de SAICA (pantalla «SAICA - Detalle convoy»): lectura con Claude, totales por clase y Excel.

Cada caja móvil trae una cabecera (expedición, matrícula de la caja, nº de bobinas en rojo, fecha, peso) y una línea
por tipo de bobina: «TRA <nº bobinas> <TIPO>-<gramaje> <diámetro> <altura>». TRA no aporta nada.
Una clase de bobina = mismo tipo, gramaje, diámetro y altura.
"""
from __future__ import annotations

import datetime as dt
import io
import re

import httpx
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import pr_lector as L

PROMPT = """Son una o varias capturas (o fotos de la pantalla) de «SAICA - Detalle convoy»: la composición de un tren
de bobinas de papel. Arriba está la cabecera: CONVOY, CENTRO, DESTINO, FECHA LLEGADA, VAGONES, VACIOS y ESTADO.
Debajo hay una cajita por caja móvil, ordenadas por columnas (de arriba abajo y de izquierda a derecha). Cada cajita trae:
- arriba «EXP» y el número de expedición (7 cifras, p. ej. 7366034);
- la matrícula de la caja móvil (p. ej. CRPU0910069, ICR102028, MXMU1450334); puede salir cortada: cópiala como se vea;
- en rojo el número total de bobinas de la caja, luego la fecha, «T», a veces un código de muelle (M1, M7, S0…),
  el peso en toneladas con coma decimal (p. ej. 23,81) y «OPT.»;
- una línea por tipo de bobina: «TRA 8 DS-150 140 203» = 8 bobinas de tipo DS, gramaje 150, diámetro 140, altura 203.

Por cada cajita devuelve en «lineas» cada línea SIN la palabra TRA, con el formato exacto «8 DS-150 140 203».
Lee las cajas en orden (columna a columna). Si llegan varias capturas del mismo convoy, no repitas las cajas que salgan
en dos capturas (misma expedición). No inventes líneas: si algo no se lee seguro, explícalo en «dudas»."""

ESQUEMA = {
    "type": "object",
    "properties": {
        "convoy": {"type": ["string", "null"]},
        "centro": {"type": ["string", "null"]},
        "destino": {"type": ["string", "null"]},
        "fecha_llegada": {"type": ["string", "null"], "description": "AAAA-MM-DD"},
        "vagones": {"type": ["integer", "null"]},
        "cajas": {"type": "array", "items": {"type": "object", "properties": {
            "expedicion": {"type": ["string", "null"]},
            "caja_movil": {"type": ["string", "null"]},
            "bobinas": {"type": ["integer", "null"], "description": "Número en rojo de la cabecera de la caja"},
            "peso_t": {"type": ["number", "null"]},
            "lineas": {"type": "array", "items": {"type": "string"}, "description": "«8 DS-150 140 203»"},
        }, "required": ["expedicion", "bobinas", "lineas"]}},
        "dudas": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["convoy", "cajas", "dudas"],
}

LINEA = re.compile(r"^\s*(?:TRA\s+)?(\d+)\s+([A-Z]+)\s*-\s*(\d+)\s+(\d+)\s+(\d+)\s*$", re.I)


def leer(archivos: list[tuple[bytes, str]], cliente: httpx.Client | None = None) -> dict:
    """Mucha salida (20-30 cajas): se dan más tokens y más tiempo que a un albarán."""
    return L._preguntar(archivos, PROMPT, "detalle_convoy", ESQUEMA, cliente, max_tokens=8000, timeout=55)


def _entero(v) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def limpiar(leido: dict) -> dict:
    """Separa cada línea en número, tipo, gramaje, diámetro y altura; quita cajas repetidas y avisa si algo no cuadra."""
    avisos, cajas, vistas = list(leido.get("dudas") or []), [], set()
    for i, c in enumerate(leido.get("cajas") or [], start=1):
        exp = re.sub(r"\D", "", str(c.get("expedicion") or "")) or None
        if exp and exp in vistas:
            continue
        if exp:
            vistas.add(exp)
        nombre = exp or f"caja {i}"
        lineas = []
        for texto in c.get("lineas") or []:
            m = LINEA.match(str(texto).replace("–", "-"))
            if not m:
                avisos.append(f"Exp. {nombre}: no se entiende la línea «{texto}».")
                continue
            n, tipo, g, d, a = m.groups()
            lineas.append({"bobinas": int(n), "tipo": tipo.upper(), "gramaje": int(g), "diametro": int(d), "altura": int(a)})
        suma, total = sum(x["bobinas"] for x in lineas), _entero(c.get("bobinas"))
        if total is not None and suma != total:
            avisos.append(f"Exp. {nombre} ({c.get('caja_movil') or '¿caja?'}): las líneas suman {suma} bobinas y la cabecera dice {total}.")
        cajas.append({"expedicion": exp, "caja_movil": re.sub(r"\s", "", str(c.get("caja_movil") or "")) or None,
                      "bobinas": total if total is not None else suma, "peso_t": c.get("peso_t"), "lineas": lineas})
    vagones = _entero(leido.get("vagones"))
    if vagones and vagones != len(cajas):
        avisos.append(f"La cabecera dice {vagones} vagones y se han leído {len(cajas)} cajas: puede faltar alguna captura.")
    return {"convoy": str(leido.get("convoy") or "").strip() or None, "centro": leido.get("centro"),
            "destino": leido.get("destino"), "fecha_llegada": leido.get("fecha_llegada"), "vagones": vagones,
            "cajas": cajas, "avisos": avisos}


def agrupar(cajas: list[dict]) -> list[dict]:
    """Total por clase (tipo, gramaje, diámetro, altura), ordenado por tipo con más bobinas y luego por medidas."""
    tot: dict[tuple, int] = {}
    for c in cajas:
        for x in c["lineas"]:
            k = (x["tipo"], x["gramaje"], x["diametro"], x["altura"])
            tot[k] = tot.get(k, 0) + x["bobinas"]
    por_tipo: dict[str, int] = {}
    for (t, *_), n in tot.items():
        por_tipo[t] = por_tipo.get(t, 0) + n
    orden = sorted(tot, key=lambda k: (-por_tipo[k[0]], k[0], k[1], k[2], k[3]))
    return [{"tipo": k[0], "gramaje": k[1], "diametro": k[2], "altura": k[3], "bobinas": tot[k]} for k in orden]


def por_tipo(clases: list[dict]) -> list[tuple[str, int]]:
    t: dict[str, int] = {}
    for c in clases:
        t[c["tipo"]] = t.get(c["tipo"], 0) + c["bobinas"]
    return sorted(t.items(), key=lambda x: (-x[1], x[0]))


# ---- Excel ---------------------------------------------------------------------------------------------------------

AZUL = PatternFill("solid", fgColor="1F4E79")
GRIS = PatternFill("solid", fgColor="D9E1F2")
FINA = Side(style="thin", color="A6A6A6")
BORDE = Border(left=FINA, right=FINA, top=FINA, bottom=FINA)


def _cab(ws, fila, titulos, anchos):
    for c, (t, w) in enumerate(zip(titulos, anchos), start=1):
        x = ws.cell(fila, c, t)
        x.fill, x.font, x.border = AZUL, Font(bold=True, color="FFFFFF"), BORDE
        x.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[get_column_letter(c)].width = w


def _filas(ws, desde, filas, negrita=False):
    for i, f in enumerate(filas, start=desde):
        for j, v in enumerate(f, start=1):
            x = ws.cell(i, j, v)
            x.border, x.alignment = BORDE, Alignment(horizontal="center")
            if negrita:
                x.font, x.fill = Font(bold=True), GRIS
    return desde + len(filas)


def fecha_es(f: str | None) -> str:
    try:
        return dt.date.fromisoformat((f or "")[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return f or ""


def titulo(d: dict) -> str:
    return (f"Convoy {d.get('convoy') or '¿?'} · {d.get('centro') or ''} → {d.get('destino') or ''} · "
            f"llegada {fecha_es(d.get('fecha_llegada'))}").replace(" ·  → ", " · ")


def excel(d: dict) -> bytes:
    clases = agrupar(d["cajas"])
    total = sum(c["bobinas"] for c in clases)
    wb = Workbook()

    ws = wb.active
    ws.title = "RESUMEN"
    ws["A1"] = "BOBINAS POR CLASE"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"{titulo(d)} · {len(d['cajas'])} cajas · {total} bobinas · {len(clases)} clases"
    _cab(ws, 4, ["Tipo", "Gramaje", "Diámetro", "Altura", "Bobinas"], [10, 11, 11, 10, 11])
    fin = _filas(ws, 5, [[c["tipo"], c["gramaje"], c["diametro"], c["altura"], c["bobinas"]] for c in clases])
    _filas(ws, fin, [["TOTAL", None, None, None, total]], negrita=True)
    ws.freeze_panes = "A5"
    ws.auto_filter.ref = f"A4:E{fin - 1}"

    for j, (t, w) in enumerate([("Tipo", 10), ("Bobinas", 11)], start=7):
        x = ws.cell(4, j, t)
        x.fill, x.font, x.border = AZUL, Font(bold=True, color="FFFFFF"), BORDE
        ws.column_dimensions[get_column_letter(j)].width = w
    for i, (t, n) in enumerate(por_tipo(clases), start=5):
        for j, v in ((7, t), (8, n)):
            x = ws.cell(i, j, v)
            x.border, x.alignment = BORDE, Alignment(horizontal="center")

    wd = wb.create_sheet("DETALLE CAJAS")
    wd["A1"] = titulo(d)
    wd["A1"].font = Font(bold=True, size=12)
    _cab(wd, 3, ["Expedición", "Caja móvil", "Bobinas", "Tipo", "Gramaje", "Diámetro", "Altura"], [12, 15, 9, 8, 10, 10, 9])
    filas = [[c["expedicion"], c["caja_movil"], x["bobinas"], x["tipo"], x["gramaje"], x["diametro"], x["altura"]]
             for c in d["cajas"] for x in c["lineas"]]
    fin = _filas(wd, 4, filas)
    wd.freeze_panes = "A4"
    wd.auto_filter.ref = f"A3:G{max(fin - 1, 3)}"

    if d.get("avisos"):
        wa = wb.create_sheet("AVISOS")
        wa.column_dimensions["A"].width = 110
        for i, a in enumerate(d["avisos"], start=1):
            wa.cell(i, 1, a)

    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def nombre_excel(d: dict) -> str:
    return f"BOBINAS CONVOY {re.sub(r'[^0-9A-Za-z-]', '', str(d.get('convoy') or 'SIN NUMERO'))}.xlsx"
