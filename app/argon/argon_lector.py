"""Lector de la Excel ARGON (seguimiento de contenedores de argón de Carburos).

Convierte la rejilla día × contenedor (texto + colores) en datos estructurados:
  - estado diario: una fila por día y contenedor con algo escrito
  - movimientos:   solo los cambios (lo que se enviaría a Odoo)

Uso:  python argon_lector.py ARGON.xlsx carpeta_salida [--desde 2026-10-01] [--hasta 2026-12-31]
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

import openpyxl

HOJA = "ARGON-DIA A DIA"
FILA_PREFIJO, FILA_NUMERO, FILA_TAMANO, FILA_CAPAC, FILA_BOMBA, FILA_OBS = 2, 3, 4, 5, 6, 7
PRIMERA_FILA_DIA = 8
COL_OBS_TXT = "OBS"  # columna de observaciones diarias (AH)

# Colores que usa el equipo (valores reales encontrados en la Excel)
VERDE_CARGADO = {"FF006600", "FF00B050", "FF008000"}
ROJO_BLOQUEO = {"FFFF0000", "FFEE0000", "FFC00000"}
AMARILLO_CARGA = {"FFFFFF00"}
NARANJA_DESCARGA = {"FFFF9900", "FFFFC000", "FFED7D31"}
FUERA_SERVICIO = ("OFF HIRED", "OUT OF TRAFFIC", "OUT")

RE_HORA = re.compile(r"\b(\d{1,2})[:.](\d{2})\b")
RE_REF = re.compile(r"\b(\d{6})\b")            # nº de expedición / reserva de tren (p. ej. 914616)
RE_CHASIS = re.compile(r"^(?:R)?(\d{4})(?:[A-Z]{3})?\b")  # 4154 PK ALG, R4154BBZ ...
RE_DURACION = re.compile(r"\b(\d+)\s*H\b")      # GENT W 2h -> tiempo de carga


def sin_acentos(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


def norm(t) -> str:
    return re.sub(r"\s+", " ", sin_acentos(str(t)).upper()).strip()


@dataclass
class Contenedor:
    columna: str
    codigo_corto: str
    prefijo: str
    numero: str
    matricula: str
    tamano: str | None
    capacidad_tm: str | None
    bomba: str | None
    observaciones: str | None


@dataclass
class Estado:
    fecha: str
    contenedor: str
    codigo_corto: str
    texto: str
    tipo: str                 # SITUACION | TRAYECTO | DISPONIBLE | SALIDA_TREN | CARRETERA | CARGA | DESCARGA | FUERA_SERVICIO
    ubicacion: str | None
    hora: str | None
    referencia: str | None
    chasis: str | None
    origen: str | None        # trayectos con chasis propio: «NEG-PK ALG»
    iniciales: str | None     # código del conductor que lleva el contenedor («JE», «MG»…)
    horas_espera: int | None  # «GENT W 2h»: horas de espera adicionales a facturar
    carga: str                # CARGADO | VACIO | SIN_COLOR
    bloqueado: bool
    comentario: str | None
    celda: str
    avisos: list = field(default_factory=list)
    cancelada: bool = False   # «ROZ CL CANCELADA»
    forzar: str | None = None  # «FACT» = facturar aunque esté cancelada o en rojo · «NOFACT» = no facturar


def color_rgb(color, tema: dict) -> str | None:
    """Devuelve el color como ARGB aproximado (resuelve colores de tema con su tinte)."""
    if color is None:
        return None
    if color.type == "rgb" and color.rgb and color.rgb != "00000000":
        return str(color.rgb).upper()
    if color.type == "theme" and color.theme is not None:
        base = tema.get(color.theme)
        if not base:
            return None
        r, g, b = (int(base[i:i + 2], 16) for i in (0, 2, 4))
        t = color.tint or 0
        f = (lambda x: x * (1 + t)) if t < 0 else (lambda x: x + (255 - x) * t)
        return "FF%02X%02X%02X" % tuple(round(f(x)) for x in (r, g, b))
    return None


def es_marron(rgb: str | None) -> bool:
    # marrón = tema «accent2» oscurecido (ED7D31 al -50 % → 773F19) u otros marrones parecidos
    if not rgb:
        return False
    r, g, b = (int(rgb[i:i + 2], 16) for i in (2, 4, 6))
    return r > g > b and r < 0xC0 and r - b > 0x30


def _rojizo(rgb: str | None) -> bool:
    if not rgb:
        return False
    r, g, b = (int(rgb[i:i + 2], 16) for i in (2, 4, 6))
    return r >= 0xB0 and g < 0x40 and b < 0x40


def leer_tema(wb) -> dict:
    orden = ["lt1", "dk1", "lt2", "dk2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6"]
    xml = wb.loaded_theme.decode("utf-8") if wb.loaded_theme else ""
    res = {}
    for i, n in enumerate(orden):
        m = re.search(rf"<a:{n}>.*?(?:srgbClr val=\"|lastClr=\")([0-9A-Fa-f]{{6}})", xml, re.S)
        if m:
            res[i] = m.group(1).upper()
    return res


def leer_alias(wb) -> dict:
    """Tabla UBICACIONES de la propia Excel (texto que se escribe → ubicación normalizada)."""
    alias = {}
    if "UBICACIONES" in wb.sheetnames:
        ws = wb["UBICACIONES"]
        for fila in ws.iter_rows(min_row=4, values_only=True):
            if fila[0] and fila[1]:
                alias[norm(fila[0])] = norm(fila[1])
    return alias


RE_CHASIS_PREF = re.compile(r"^R?(\d{4,5})(?:[A-Z]{3})?\s+")
RE_INICIALES = re.compile(r"\s+([A-Z]{1,3})$")


RE_NOFACT = re.compile(r"\bNO ?FACT\b")
RE_FACT = re.compile(r"\bFACT\b")
RE_CANCEL = re.compile(r"\b(CANCELAD[OA]|ANULAD[OA])\b")


def marcas(texto: str) -> tuple[bool, str | None]:
    """Códigos de facturación escritos en la celda: CANCELADA, FACT (facturar) y NOFACT (no facturar)."""
    t = norm(texto)
    forzar = "NOFACT" if RE_NOFACT.search(t) else "FACT" if RE_FACT.search(t) else None
    return bool(RE_CANCEL.search(t)), forzar


def _limpia(t: str) -> str:
    t = RE_NOFACT.sub("", t)
    t = RE_FACT.sub("", t)
    t = RE_CANCEL.sub("", t)
    t = RE_HORA.sub("", t)
    t = RE_REF.sub("", t)
    t = RE_DURACION.sub("", t)
    t = re.sub(r"^(SL|CR)\s+(A\s+)?", "", t)
    t = re.sub(r"\(.*?\)|\*", "", t)
    t = re.sub(r"^DESCARGA\s*", "", t)
    return re.sub(r"\s+", " ", t).strip(" -/")


def _sitio(t: str, alias: dict) -> tuple[str, str | None]:
    """Sitio normalizado y, si sobra al final, iniciales de 1-3 letras (p. ej. conductor JE)."""
    t = t.strip(" -/")
    if t in alias:
        return alias[t], None
    m = RE_INICIALES.search(t)
    if m and t[:m.start()].strip() in alias:
        return alias[t[:m.start()].strip()], m.group(1)
    return t, None


def ubicacion_de(texto: str, alias: dict) -> tuple[str | None, str | None, str | None]:
    """Devuelve (ubicación, origen, iniciales).
    «4154 PK ALG» → PK ALGECIRAS (chasis aparte); «4154 NEG-PK ALG JE» → trayecto LA NEGRILLA → PK ALGECIRAS."""
    t = _limpia(texto)
    t = RE_CHASIS_PREF.sub("", t)
    if not t:
        return None, None, None
    if t in alias:
        return alias[t], None, None
    if "-" in t:
        a, b = [x.strip() for x in t.split("-", 1)]
        oa, _ = _sitio(a, alias)
        db, ini = _sitio(b, alias)
        if oa in alias.values() and db in alias.values():
            return db, oa, ini
    u, ini = _sitio(t, alias)
    return u, None, ini


def contenedores(ws) -> list[Contenedor]:
    res = []
    for c in range(2, ws.max_column + 1):
        pref, num = ws.cell(FILA_PREFIJO, c).value, ws.cell(FILA_NUMERO, c).value
        if not pref or not num or norm(num) in ("D", "OBS"):
            continue
        p = norm(pref)
        m = re.match(r"([A-Z]{4})\s*\(?\s*(\d+)?\s*\)?", p)
        prefijo = m.group(1) if m else p[:4]
        corto = m.group(2) if m and m.group(2) else ""
        numero = norm(num)
        res.append(Contenedor(
            columna=openpyxl.utils.get_column_letter(c), codigo_corto=corto, prefijo=prefijo,
            numero=numero, matricula=f"{prefijo} {numero}",
            tamano=str(ws.cell(FILA_TAMANO, c).value or "") or None,
            capacidad_tm=str(ws.cell(FILA_CAPAC, c).value or "") or None,
            bomba=str(ws.cell(FILA_BOMBA, c).value or "") or None,
            observaciones=str(ws.cell(FILA_OBS, c).value or "") or None))
    return res


def fechas(ws) -> dict[int, dt.date]:
    """Fecha de cada fila. Usa la fecha real si la hay; si no (Excel antigua con días sueltos)
    cuenta días seguidos desde la primera fecha completa."""
    res, base = {}, None
    for r in range(PRIMERA_FILA_DIA, ws.max_row + 1):
        v = ws.cell(r, 1).value
        if v is None:
            continue
        if base is None:
            if not isinstance(v, (dt.datetime, dt.date)):
                continue
            base = (v.date() if isinstance(v, dt.datetime) else v, r)
        res[r] = base[0] + dt.timedelta(days=r - base[1])
        if isinstance(v, (dt.datetime, dt.date)):
            real = v.date() if isinstance(v, dt.datetime) else v
            if real != res[r] and real.replace(year=res[r].year) == res[r]:
                pass  # año mal escrito en la Excel (p. ej. 1-ene-2024 en vez de 2025): se corrige
    return res


def interpretar(texto: str, celda, tema, alias) -> tuple[str, str | None, str | None, str | None, str | None, str, bool, list]:
    t = norm(texto)
    avisos = []
    relleno = celda.fill.fgColor if celda.fill and celda.fill.fill_type == "solid" else None
    fondo = color_rgb(relleno, tema) if relleno is not None else None
    letra = color_rgb(celda.font.color, tema) if celda.font else None

    if t.startswith(FUERA_SERVICIO):
        tipo = "FUERA_SERVICIO"
    elif t.startswith("SL ") or t == "SL":
        tipo = "SALIDA_TREN"
    elif t.startswith("CR ") or t.startswith("CR-"):
        tipo = "CARRETERA"
    elif fondo in AMARILLO_CARGA:
        tipo = "CARGA"
    elif fondo in NARANJA_DESCARGA or t.startswith("DESCARGA"):
        tipo = "DESCARGA"
    elif RE_HORA.search(t):
        tipo = "DISPONIBLE"
    else:
        tipo = "SITUACION"
    ubic, origen, ini = (None, None, None) if tipo == "FUERA_SERVICIO" else ubicacion_de(t, alias)
    if origen and tipo == "SITUACION":
        tipo = "TRAYECTO"

    hora = None
    if (m := RE_HORA.search(t)):
        hora = f"{int(m.group(1)):02d}:{m.group(2)}"
    ref = (m.group(1) if (m := RE_REF.search(t)) else None)
    chasis = (m.group(1) if (m := RE_CHASIS_PREF.match(t)) else None)

    if letra in VERDE_CARGADO:
        carga = "CARGADO"
    elif es_marron(letra):
        carga = "VACIO"
    else:
        carga = "SIN_COLOR"
    bloqueado = letra in ROJO_BLOQUEO or _rojizo(letra)   # letra roja (negrita o no) = bloqueado
    if bloqueado:
        carga = "SIN_COLOR"
    if tipo == "CARGA" and carga == "VACIO":
        avisos.append("Carga marcada con letra de vacío")
    if fondo and fondo not in AMARILLO_CARGA | NARANJA_DESCARGA and not fondo.startswith("FFBF") and fondo != "FFFFFFFF":
        avisos.append(f"Fondo no reconocido {fondo}")
    return tipo, ubic, hora, ref, chasis, origen, ini, carga, bloqueado, avisos


def leer(ruta: str | Path, desde: dt.date | None = None, hasta: dt.date | None = None):
    wb = openpyxl.load_workbook(ruta)
    ws = wb[HOJA]
    tema, alias = leer_tema(wb), leer_alias(wb)
    conts = contenedores(ws)
    conocidas = set(alias.values())
    dias = fechas(ws)
    estados: list[Estado] = []
    for r, f in dias.items():
        if (desde and f < desde) or (hasta and f > hasta):
            continue
        for k in conts:
            celda = ws[f"{k.columna}{r}"]
            if celda.value is None or not str(celda.value).strip():
                continue
            tipo, ubic, hora, ref, chasis, origen, ini, carga, bloq, avisos = interpretar(str(celda.value), celda, tema, alias)
            if ubic and alias and ubic not in conocidas and not bloq:  # en rojo el texto es el motivo, no un sitio
                avisos.append("Sitio no está en UBICACIONES")
            estados.append(Estado(
                fecha=f.isoformat(), contenedor=k.matricula, codigo_corto=k.codigo_corto,
                texto=str(celda.value).strip(), tipo=tipo, ubicacion=ubic, hora=hora, referencia=ref,
                chasis=chasis, origen=origen, iniciales=ini,
                horas_espera=(int(m.group(1)) if tipo == "CARGA" and (m := RE_DURACION.search(norm(celda.value))) else None),
                carga=carga, bloqueado=bloq, cancelada=marcas(celda.value)[0], forzar=marcas(celda.value)[1],
                comentario=(celda.comment.text.split(":", 1)[-1].strip() if celda.comment else None),
                celda=celda.coordinate, avisos=avisos))
    return conts, estados


def movimientos(estados: list[Estado]) -> list[dict]:
    """Un movimiento cada vez que cambia algo relevante del contenedor respecto al día anterior anotado."""
    ultimo: dict[str, tuple] = {}
    res = []
    ubic_previa: dict[str, str | None] = {}
    for e in sorted(estados, key=lambda x: (x.contenedor, x.fecha)):
        if not e.ubicacion and e.tipo == "DESCARGA":
            e.ubicacion = ubic_previa.get(e.contenedor)  # «DESCARGA» a secas = donde estaba
        if e.tipo not in ("SALIDA_TREN", "CARRETERA", "TRAYECTO") and e.ubicacion:
            ubic_previa[e.contenedor] = e.ubicacion
        elif e.tipo in ("CARRETERA", "TRAYECTO") and e.ubicacion:
            ubic_previa[e.contenedor] = e.ubicacion
        clave = (e.tipo, e.ubicacion, e.origen, e.hora, e.referencia, e.carga, e.bloqueado, e.horas_espera, e.iniciales,
                 e.cancelada, e.forzar)
        anterior = ultimo.get(e.contenedor)
        if anterior is None or anterior[0] != clave:
            d = asdict(e)
            d["situacion_anterior"] = anterior[1] if anterior else None
            d["id_evento"] = f"{e.contenedor}|{e.fecha}|{e.tipo}|{e.ubicacion}|{e.referencia or ''}"
            res.append(d)
        ultimo[e.contenedor] = (clave, e.ubicacion)
    return res


def _csv(ruta: Path, filas: list[dict]):
    if not filas:
        return
    with ruta.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(filas[0].keys()), delimiter=";")
        w.writeheader()
        for f in filas:
            w.writerow({k: ("; ".join(v) if isinstance(v, list) else v) for k, v in f.items()})


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    ruta, salida = Path(argv[1]), Path(argv[2])
    desde = hasta = None
    if "--desde" in argv:
        desde = dt.date.fromisoformat(argv[argv.index("--desde") + 1])
    if "--hasta" in argv:
        hasta = dt.date.fromisoformat(argv[argv.index("--hasta") + 1])
    salida.mkdir(parents=True, exist_ok=True)
    conts, estados = leer(ruta, desde, hasta)
    movs = movimientos(estados)
    _csv(salida / "contenedores.csv", [asdict(c) for c in conts])
    _csv(salida / "estado_diario.csv", [asdict(e) for e in estados])
    _csv(salida / "movimientos.csv", movs)
    (salida / "movimientos.json").write_text(json.dumps(movs, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"contenedores": len(conts), "estados": len(estados), "movimientos": len(movs)}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
