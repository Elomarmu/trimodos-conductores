"""Histórico ARGON: rotaciones por equipo y otros datos del año, para el cuadro de mandos.

Se calcula con la misma lógica que los pedidos de Odoo (argon_facturacion) y se escribe cada noche en la hoja
«DASHBOARD» de la Excel, en bloques «#TABLA <nombre>» + cabecera + filas, que el cuadro de mandos lee con el
conector de Microsoft 365. Solo cuenta lo que ya ha pasado (celdas de antes de hoy).

Tablas:
  ROTACIONES  una fila por rotación (de la descarga anterior a la siguiente descarga) que acaba o sigue en el periodo
  EQUIPOS     una fila por contenedor: rotaciones, días medios, trenes, días por situación
  RUTAS       tramos de tren por origen → destino (cargado / vacío): número y días medios en tren
  LUGARES     cargas y descargas por sitio
  MESES       por mes: descargas (rotaciones cerradas), cargas, trenes, días en taller
  DIAS        por contenedor y situación: días del periodo (tren cargado, tren vacío, carga, descarga, terminal…)
"""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter, defaultdict

import argon_facturacion as F
import argon_lector as L

TITULO = "HISTÓRICO ARGON"
HOJA = "DASHBOARD"
HOJA_VACIA = "Hoja1"   # hoja vacía de la Excel que se aprovecha (y se renombra) si aún no existe DASHBOARD
FILAS = 1500            # se escribe siempre este bloque para borrar lo que quedara de la noche anterior
COLS = 14

SITUACIONES = ["Tren cargado", "Tren vacío", "Carga", "Descarga", "En terminal o planta", "Taller / bloqueo",
               "Sin anotar", "Fuera de servicio"]


def _d(s: str | None) -> dt.date | None:
    return dt.date.fromisoformat(s) if s else None


def _r1(x):
    return None if x is None else round(float(x), 1)


def _media(v: list):
    return _r1(sum(v) / len(v)) if v else None


def datos(ruta, hoy: dt.date, desde: dt.date = dt.date(2026, 1, 1)) -> dict:
    """Las tablas del histórico como listas de filas (dict)."""
    conts, estados = L.leer(ruta)
    estados = [e for e in estados if e.fecha < hoy.isoformat()]
    movs = L.movimientos(estados)
    tablas = F.leer_tablas(ruta)
    servs = F.construir(movs, tablas["tipos"]) + F.codigos_pactados(movs, tablas)
    bloq = F.bloqueos(estados, hoy)
    servs += F.servicios_bloqueo(bloq)
    F.rotaciones(servs, bloq)
    codigo = {k.matricula: k.codigo_corto for k in conts}
    d0, d1 = desde.isoformat(), (hoy - dt.timedelta(days=1)).isoformat()

    # ---------------------------------------------------------------- rotaciones
    por_rot = defaultdict(list)
    for s in servs:
        por_rot[s.rotacion].append(s)
    rot_filas, descargas_por_cont = [], defaultdict(list)
    for s in servs:
        if s.servicio == "DESCARGA":
            descargas_por_cont[s.contenedor].append(s.fecha)
    for lst in descargas_por_cont.values():
        lst.sort()
    for rid, lst in por_rot.items():
        lst.sort(key=lambda x: (x.fecha, 1 if x.servicio == "DESCARGA" else 0))
        cont = lst[0].contenedor
        desc = [x for x in lst if x.servicio == "DESCARGA"]
        fin = desc[-1].fecha if desc else None
        ultima = max(x.fecha_fin or x.fecha for x in lst)
        if (fin or ultima) < d0:
            continue  # acabó antes del periodo
        cargas = [x for x in lst if x.servicio == "CARGA"]
        trenes = [x for x in lst if x.servicio == "TREN"]
        previas = [f for f in descargas_por_cont[cont] if fin and f < fin]
        ciclo = (_d(fin) - _d(previas[-1])).days if fin and previas else None
        carga_a_desc = (_d(fin) - _d(cargas[0].fecha)).days if fin and cargas else None
        dias_tren = sum(((_d(t.fecha_fin) or _d(t.fecha)) - _d(t.fecha)).days for t in trenes)
        rot_filas.append({
            "equipo": cont, "codigo": codigo.get(cont, ""), "inicio": lst[0].fecha,
            "carga_en": (cargas[0].destino or cargas[0].origen or "") if cargas else "",
            "fecha_carga": cargas[0].fecha if cargas else "",
            "descarga_en": (desc[-1].destino or desc[-1].origen or "") if desc else "",
            "fecha_descarga": fin or "", "estado": "Cerrada" if fin else "En curso",
            "dias_ciclo": ciclo, "dias_carga_descarga": carga_a_desc,
            "trenes": len(trenes), "trenes_cargado": sum(1 for t in trenes if t.cargado == "Cargado"),
            "dias_tren": dias_tren, "bloqueos": sum(1 for x in lst if x.servicio == "BLOQUEO"),
        })
    rot_filas.sort(key=lambda r: (r["equipo"], r["inicio"]))

    # ---------------------------------------------------------------- días por situación (línea de tiempo)
    tramos_tren = defaultdict(list)  # contenedor → (desde, hasta, cargado)
    for s in servs:
        if s.servicio == "TREN" and s.fecha_fin:
            tramos_tren[s.contenedor].append((s.fecha, s.fecha_fin, s.cargado == "Cargado"))
    est_dia = {(e.contenedor, e.fecha): e for e in estados}
    dias = defaultdict(Counter)
    f = desde
    fechas = []
    while f <= hoy - dt.timedelta(days=1):
        fechas.append(f.isoformat())
        f += dt.timedelta(days=1)
    for k in conts:
        c = k.matricula
        primero = min((e.fecha for e in estados if e.contenedor == c), default=None)
        for fe in fechas:
            if not primero or fe < primero:
                continue
            e = est_dia.get((c, fe))
            tren = next((t for t in tramos_tren[c] if t[0] <= fe < t[1]), None)
            if e and e.tipo == "FUERA_SERVICIO":
                sit = "Fuera de servicio"
            elif e and e.bloqueado:
                sit = "Taller / bloqueo"
            elif tren:
                sit = "Tren cargado" if tren[2] else "Tren vacío"
            elif e and e.tipo == "CARGA":
                sit = "Carga"
            elif e and e.tipo == "DESCARGA":
                sit = "Descarga"
            elif e:
                sit = "En terminal o planta"
            else:
                sit = "Sin anotar"
            dias[c][sit] += 1
    # contenedores que llevan todo el periodo fuera de servicio no aportan nada
    activos = [k.matricula for k in conts if sum(dias[k.matricula].values()) > dias[k.matricula]["Fuera de servicio"]]
    dias_filas = [{"equipo": c, "codigo": codigo.get(c, ""), "situacion": s, "dias": dias[c][s]}
                  for c in activos for s in SITUACIONES if dias[c][s]]

    # ---------------------------------------------------------------- equipos
    eq_filas = []
    for c in activos:
        rs = [r for r in rot_filas if r["equipo"] == c]
        cerr = [r for r in rs if r["estado"] == "Cerrada" and r["fecha_descarga"] >= d0]
        tot = sum(dias[c].values()) or 1
        # días cargado: de la carga a la descarga de cada rotación (o hasta ayer si sigue en curso), dentro del periodo
        cargado = set()
        for r in rs:
            if r["fecha_carga"]:
                a = max(_d(r["fecha_carga"]), desde)
                b = _d(r["fecha_descarga"]) if r["fecha_descarga"] else hoy - dt.timedelta(days=1)
                while a <= b:
                    cargado.add(a)
                    a += dt.timedelta(days=1)
        eq_filas.append({
            "equipo": c, "codigo": codigo.get(c, ""), "rotaciones": len(cerr),
            "dias_ciclo_medio": _media([r["dias_ciclo"] for r in cerr if r["dias_ciclo"] is not None]),
            "dias_carga_descarga_medio": _media([r["dias_carga_descarga"] for r in cerr if r["dias_carga_descarga"] is not None]),
            "trenes": sum(r["trenes"] for r in rs if r["inicio"] >= d0 or r["estado"] == "Cerrada"),
            "dias_tren": dias[c]["Tren cargado"] + dias[c]["Tren vacío"],
            "dias_taller": dias[c]["Taller / bloqueo"], "dias_sin_anotar": dias[c]["Sin anotar"],
            "dias_cargado": len(cargado), "pct_cargado": _r1(min(100.0, 100 * len(cargado) / tot)),
            "ultima_descarga": max((r["fecha_descarga"] for r in cerr), default=""),
            "en_curso": "Sí" if any(r["estado"] == "En curso" for r in rs) else "No",
        })
    eq_filas.sort(key=lambda r: (-r["rotaciones"], r["equipo"]))

    # ---------------------------------------------------------------- rutas de tren
    ru = defaultdict(list)
    for s in servs:
        if s.servicio == "TREN" and d0 <= s.fecha <= d1:
            ru[(s.origen or "?", s.destino or "?", "Cargado" if s.cargado == "Cargado" else "Vacío")].append(
                ((_d(s.fecha_fin) - _d(s.fecha)).days) if s.fecha_fin else None)
    rutas = [{"origen": o, "destino": d, "carga": cg, "tramos": len(v), "dias_medios": _media([x for x in v if x is not None])}
             for (o, d, cg), v in ru.items()]
    rutas.sort(key=lambda r: (-r["tramos"], r["origen"], r["destino"]))

    # ---------------------------------------------------------------- lugares
    lu = defaultdict(Counter)
    for s in servs:
        if s.servicio in ("CARGA", "DESCARGA") and d0 <= s.fecha <= d1:
            lu[s.destino or s.origen or "?"][s.servicio] += 1
    lugares = [{"lugar": k, "cargas": v["CARGA"], "descargas": v["DESCARGA"]} for k, v in lu.items()]
    lugares.sort(key=lambda r: (-(r["cargas"] + r["descargas"]), r["lugar"]))

    # ---------------------------------------------------------------- meses
    me = defaultdict(Counter)
    for s in servs:
        if d0 <= s.fecha <= d1:
            me[s.fecha[:7]][s.servicio] += 1
    taller_mes = Counter()
    for (c, fe), e in est_dia.items():
        if d0 <= fe <= d1 and e.bloqueado and c in activos:
            taller_mes[fe[:7]] += 1
    meses = [{"mes": m, "descargas": v["DESCARGA"], "cargas": v["CARGA"], "trenes": v["TREN"],
              "trenes_cargado": sum(1 for s in servs if s.servicio == "TREN" and s.fecha[:7] == m and s.cargado == "Cargado"),
              "dias_taller": taller_mes[m]} for m, v in sorted(me.items())]

    return {"ROTACIONES": rot_filas, "EQUIPOS": eq_filas, "RUTAS": rutas, "LUGARES": lugares, "MESES": meses,
            "DIAS": dias_filas, "_desde": d0, "_hasta": d1}


def _celda(v):
    """Las fechas («2026-07-13», «2026-07») van como texto (apóstrofo delante) para que Excel no las cambie de
    formato y el cuadro de mandos las lea siempre igual; los códigos de contenedor («136») también."""
    if v is None:
        return ""
    if isinstance(v, str) and (re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", v) or re.fullmatch(r"\d+", v)):
        return "'" + v
    return v


def matriz(dat: dict, hoy: dt.date) -> list[list]:
    """La hoja DASHBOARD como matriz de valores (siempre FILAS × COLS, con vacíos para borrar lo anterior)."""
    f = lambda s: dt.date.fromisoformat(s).strftime("%d/%m/%Y") if s else ""  # noqa: E731
    filas = [[f"{TITULO} — del {f(dat['_desde'])} al {f(dat['_hasta'])}, calculado el {hoy.strftime('%d/%m/%Y')}. "
              "No escribáis en esta hoja: la rellena el proceso de las 5:00."], []]
    for nombre in ("EQUIPOS", "ROTACIONES", "MESES", "RUTAS", "LUGARES", "DIAS"):
        lst = dat[nombre]
        filas.append(["#TABLA", nombre])
        if lst:
            cab = list(lst[0].keys())
            filas.append(cab)
            for r in lst:
                filas.append([_celda(r[k]) for k in cab])
        filas.append([])
    n = max(FILAS, len(filas))
    return [(fila + [""] * COLS)[:COLS] for fila in filas] + [[""] * COLS for _ in range(n - len(filas))]


def _col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def escrituras(ruta, hoy: dt.date) -> list[dict]:
    """Lo que hay que escribir en la Excel para el histórico (n8n: nodo «Escribir en la Excel»).
    Si aún no hay hoja DASHBOARD se usa la hoja vacía «Hoja1»; la noche siguiente se renombra (en otra vuelta,
    para que el cambio de nombre y la escritura no se pisen)."""
    import openpyxl
    wb = openpyxl.load_workbook(ruta, read_only=True)
    nombres = wb.sheetnames
    if HOJA in nombres:
        hoja = HOJA
    elif HOJA_VACIA in nombres:
        ws = wb[HOJA_VACIA]
        a1 = next(ws.iter_rows(min_row=1, max_row=1, max_col=1, values_only=True), (None,))[0]
        if a1 and str(a1).startswith(TITULO):
            return [{"hoja": HOJA_VACIA, "ruta": f"worksheets('{HOJA_VACIA}')", "cuerpo": {"name": HOJA}}]
        if any(v not in (None, "") for fila in ws.iter_rows(max_row=200, max_col=30, values_only=True) for v in fila):
            return []  # «Hoja1» tiene otros datos: no se toca
        hoja = HOJA_VACIA
    else:
        return []
    m = matriz(datos(ruta, hoy), hoy)
    return [{"hoja": hoja, "celda": f"A1:{_col(COLS)}{len(m)}", "valores": m}]
