"""Servicios facturables a partir de la Excel ARGON.

Convierte los movimientos en servicios (tramo de tren, carretera, trayecto con chasis propio, horas de espera,
carga, descarga…) y les pone producto, precio, proveedor, coste, conductor, tractora y remolque con las tablas
de la propia Excel: PRODUCTOS, TARIFAS, CONDUCTORES y CHASIS.

Uso:  python argon_facturacion.py ARGON.xlsx carpeta_salida [--desde 2026-10-01] [--hasta 2026-10-31]
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).parent))
import argon_lector as L  # noqa: E402

LLEGADAS = ("SITUACION", "DISPONIBLE", "CARGA", "DESCARGA", "TRAYECTO")


@dataclass
class Servicio:
    id_servicio: str
    fecha: str
    fecha_fin: str | None
    contenedor: str
    codigo_corto: str
    servicio: str              # TREN | CARRETERA | TRAYECTO | ESPERA | CARGA | DESCARGA | <código pactado>
    origen: str | None
    destino: str | None
    via: str | None            # «CR A PORT BOU» anotado mientras el tren va en ruta
    expedicion: str | None
    cargado: str
    cantidad: float
    unidad: str
    conductor_codigo: str | None
    chasis: str | None
    texto: str
    # lo que pone la Excel (tablas)
    producto: str | None = None
    producto_id: int | None = None
    se_factura: str | None = None
    precio: float | None = None
    proveedor: str | None = None
    proveedor_id: int | None = None
    coste: float | None = None
    conductor: str | None = None
    conductor_id: int | None = None
    tractora: str | None = None
    tractora_id: int | None = None
    remolque: str | None = None
    remolque_id: int | None = None
    origen_id: int | None = None
    destino_id: int | None = None
    cancelada: bool = False
    forzar: str | None = None         # FACT / NOFACT escrito en la celda
    rotacion: str | None = None       # contenedor + fecha de la carga que abre la rotación
    analitica: dict | None = None     # analytic_distribution de la línea
    reserva: str | None = None        # nº de expedición encontrado en RESERVAS
    estado: str = ""
    falta: list = field(default_factory=list)
    notas: list = field(default_factory=list)


# ------------------------------------------------------------------ tablas de la Excel
def _num(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _int(v):
    n = _num(v)
    return int(n) if n is not None else None


def _filas(wb, hoja, fila_cab=3):
    if hoja not in wb.sheetnames:
        return []
    ws = wb[hoja]
    cab = [str(c.value or "").strip() for c in ws[fila_cab]]
    res = []
    for fila in ws.iter_rows(min_row=fila_cab + 1, values_only=True):
        if not any(v not in (None, "") for v in fila):
            continue
        res.append(dict(zip(cab, fila)))
    return res


def leer_tablas(ruta) -> dict:
    wb = openpyxl.load_workbook(ruta, data_only=True)
    alias = L.leer_alias(wb)
    ids_ubic, tipos = {}, {}
    for f in _filas(wb, "UBICACIONES"):
        if f.get("Ubicación normalizada") and f.get("Tipo"):
            tipos[L.norm(f["Ubicación normalizada"])] = L.norm(f["Tipo"])
        if f.get("Ubicación normalizada") and _int(f.get("ID en Odoo")):
            ids_ubic[L.norm(f["Ubicación normalizada"])] = _int(f["ID en Odoo"])
    productos = {}
    for f in _filas(wb, "PRODUCTOS"):
        if f.get("Código"):
            productos[L.norm(f["Código"])] = f
    tarifas = []
    for f in _filas(wb, "TARIFAS"):
        if f.get("Servicio"):
            tarifas.append({**f, "_s": L.norm(f["Servicio"]), "_o": L.norm(f.get("Origen") or "*"),
                            "_d": L.norm(f.get("Destino") or "*"), "_c": L.norm(f.get("Cargado/Vacío") or "*")})
    conductores = {L.norm(f["Código"]): f for f in _filas(wb, "CONDUCTORES") if f.get("Código")}
    chasis = {str(f["Nº en la Excel"]).strip().lstrip("R"): f for f in _filas(wb, "CHASIS") if f.get("Nº en la Excel")}
    reservas = []
    for f in _filas(wb, "RESERVAS"):
        if f.get("Fecha salida") and f.get("Origen"):
            fs = f["Fecha salida"]
            fs = fs.date() if isinstance(fs, dt.datetime) else fs
            reservas.append({**f, "_fecha": fs if isinstance(fs, dt.date) else None,
                             "_o": alias.get(L.norm(f["Origen"]), L.norm(f["Origen"])),
                             "_d": alias.get(L.norm(f.get("Destino") or ""), L.norm(f.get("Destino") or "")),
                             "_c": L.norm(f.get("Contenedor") or "").replace(" ", "")})
    return {"alias": alias, "ids_ubic": ids_ubic, "tipos": tipos, "productos": productos, "tarifas": tarifas,
            "conductores": conductores, "chasis": chasis, "reservas": reservas}


def rotaciones(servicios: list[Servicio]) -> None:
    """Un pedido por contenedor desde la descarga anterior hasta la siguiente descarga (incluida): entra todo lo
    que pasa entre medias (vuelta en vacío, carga, tren cargado y acarreo a la planta de descarga)."""
    por_cont = defaultdict(list)
    for sv in servicios:
        por_cont[sv.contenedor].append(sv)
    for cont, lst in por_cont.items():
        lst.sort(key=lambda x: (x.fecha, 1 if x.servicio == "DESCARGA" else 0))
        actual, descargado = f"{cont}|{lst[0].fecha}", None
        for sv in lst:
            if descargado and sv.fecha > descargado:
                actual, descargado = f"{cont}|{sv.fecha}", None
            sv.rotacion = actual
            if sv.servicio == "DESCARGA":
                descargado = sv.fecha


def cruzar_reservas(servicios: list[Servicio], reservas: list) -> dict:
    """Une cada salida en tren con su reserva (mismo contenedor, misma terminal, ±2 días)."""
    usadas = set()
    for sv in servicios:
        if sv.servicio != "TREN":
            continue
        f = dt.date.fromisoformat(sv.fecha)
        cont = sv.contenedor.replace(" ", "")
        cand = [(abs((r["_fecha"] - f).days), i) for i, r in enumerate(reservas)
                if i not in usadas and r["_fecha"] and abs((r["_fecha"] - f).days) <= 2 and r["_o"] == L.norm(sv.origen or "")
                and (not r["_c"] or r["_c"] in (cont, sv.codigo_corto))]
        if sv.expedicion:
            cand = [c for c in cand if str(reservas[c[1]].get("Nº expedición") or "").strip() == sv.expedicion] or cand
        if not cand:
            continue
        _, i = min(cand)
        usadas.add(i)
        r = reservas[i]
        sv.reserva = str(r.get("Nº expedición") or "").strip() or None
        if not sv.expedicion and sv.reserva:
            sv.expedicion = sv.reserva
            sv.notas.append("nº de expedición tomado de RESERVAS")
        elif sv.expedicion and sv.reserva and sv.expedicion != sv.reserva:
            sv.notas.append(f"la Excel dice {sv.expedicion} y la reserva {sv.reserva}")
        sv._reserva = r
    sin_sl = [r for i, r in enumerate(reservas) if i not in usadas]
    return {"reservas_sin_salida": sin_sl}


# ------------------------------------------------------------------ movimientos → servicios
def construir(movs: list[dict], tipos: dict | None = None) -> list[Servicio]:
    """tipos: UBICACIONES → tipo. El tren sigue por terminales ferroviarias (LLAGOSTA → BOELLA, MADRID → CONSTANTI)
    hasta el primer sitio que no lo es, una carga, una descarga u otra salida: ese es el destino que se factura."""
    tipos = tipos or {}
    es_terminal = lambda u: tipos.get(L.norm(u or "")) in ("TERMINAL FERROVIARIA", "TERMINAL DE PASO")  # noqa: E731
    de_paso = lambda u: tipos.get(L.norm(u or "")) == "TERMINAL DE PASO"  # noqa: E731
    en_ruta = set()  # CR anotadas con el tren en camino: no son servicio aparte
    incluidas = set()  # salidas en tren desde una terminal de paso que ya van dentro del tramo anterior
    por_cont = defaultdict(list)
    for m in movs:
        por_cont[m["contenedor"]].append(m)
    res: list[Servicio] = []

    def nuevo(m, servicio, origen, destino, cantidad=1.0, unidad="servicio", fin=None, via=None):
        sid = f"{m['contenedor']}|{m['fecha']}|{servicio}|{origen or ''}|{destino or ''}|{m.get('referencia') or ''}"
        res.append(Servicio(
            id_servicio=sid, fecha=m["fecha"], fecha_fin=fin, contenedor=m["contenedor"], codigo_corto=m["codigo_corto"],
            servicio=servicio, origen=origen, destino=destino, via=via, expedicion=m.get("referencia"),
            cargado={"CARGADO": "Cargado", "VACIO": "Vacío"}.get(m["carga"], ""), cantidad=cantidad, unidad=unidad,
            conductor_codigo=m.get("iniciales"), chasis=m.get("chasis"), texto=m["texto"],
            cancelada=bool(m.get("cancelada")), forzar=m.get("forzar")))

    for cont, lst in por_cont.items():
        lst.sort(key=lambda x: x["fecha"])
        ultimo_sitio = None
        fin_tren = ""  # hasta dónde llega el último tramo de tren ya creado
        for i, m in enumerate(lst):
            t = m["tipo"]
            if (m["bloqueado"] and not m.get("cancelada") and m.get("forzar") != "FACT") or t == "FUERA_SERVICIO":
                continue
            # Llegada con hora a otra terminal sin «SL» anotada (p. ej. MORELL → «LEIXOES 20:00»): tren deducido
            if (t == "DISPONIBLE" and ultimo_sitio and m["ubicacion"] and m["ubicacion"] != ultimo_sitio
                    and es_terminal(m["ubicacion"]) and m["fecha"] > fin_tren):
                ant = next((x for x in reversed(lst[:i]) if x["ubicacion"] == ultimo_sitio), m)
                nuevo(ant, "TREN", ultimo_sitio, m["ubicacion"], fin=m["fecha"])
                res[-1].cargado = {"CARGADO": "Cargado", "VACIO": "Vacío"}.get(m["carga"], res[-1].cargado)
                res[-1].notas.append("DEDUCIDO: no hay «SL» en la Excel; llega a otra terminal con hora")
                fin_tren = m["fecha"]
            if t == "SALIDA_TREN" and id(m) in incluidas:
                continue
            if t == "SALIDA_TREN":
                origen = m["ubicacion"]
                destino, fin, via = None, None, []
                f0 = dt.date.fromisoformat(m["fecha"])
                for n in lst[i + 1:]:
                    if destino and (dt.date.fromisoformat(n["fecha"]) - f0).days > 12:
                        break  # más de 12 días: ya no es el mismo viaje
                    if n["tipo"] == "CARRETERA":
                        via.append(n["texto"])
                        en_ruta.add(id(n))  # «CR A PORT BOU» mientras el tren va de camino: es el mismo viaje
                        continue
                    if n["bloqueado"]:
                        break
                    if n["tipo"] == "SALIDA_TREN" and de_paso(n["ubicacion"]) and (destino is None or destino == n["ubicacion"]
                                                                               or de_paso(destino)):
                        incluidas.add(id(n))  # «SL PORT BOU» en mitad del viaje: sigue siendo el mismo tramo
                        if destino:
                            via.append(destino)
                        destino = None
                        continue
                    if n["tipo"] in ("SITUACION", "DISPONIBLE"):
                        if n["ubicacion"] and n["ubicacion"] != origen or destino:
                            if destino and n["ubicacion"] != destino:
                                via.append(destino)
                            destino, fin = n["ubicacion"] or destino, n["fecha"]
                            if not es_terminal(destino) and destino != origen:
                                break
                        continue
                    if destino:
                        break
                    if n["tipo"] in LLEGADAS or (n["tipo"] == "SALIDA_TREN" and n["ubicacion"] != origen):
                        destino, fin = n["ubicacion"] if n["tipo"] != "TRAYECTO" else n["origen"], n["fecha"]
                        break
                nuevo(m, "TREN", origen, destino, fin=fin, via=" · ".join(via) or None)
                fin_tren = fin or m["fecha"]
            elif t == "CARRETERA":
                if id(m) not in en_ruta:
                    nuevo(m, "CARRETERA", ultimo_sitio, m["ubicacion"])
            elif t == "TRAYECTO":
                nuevo(m, "TRAYECTO", m["origen"], m["ubicacion"])
            elif t == "CARGA":
                nuevo(m, "CARGA", None, m["ubicacion"])
                if m.get("horas_espera"):
                    nuevo(m, "ESPERA", None, m["ubicacion"], cantidad=m["horas_espera"], unidad="hora")
            elif t == "DESCARGA":
                nuevo(m, "DESCARGA", None, m["ubicacion"])
            # códigos pactados escritos en la celda (TRASVASE, PESAR…) se añaden en tarificar()
            if t not in ("SALIDA_TREN", "CARRETERA") and m["ubicacion"]:
                ultimo_sitio = m["ubicacion"]
            elif t == "CARRETERA" and m["ubicacion"]:
                ultimo_sitio = m["ubicacion"]
    return res


def codigos_pactados(movs: list[dict], tablas: dict) -> list[Servicio]:
    """Servicios por palabra clave escrita en la celda (filas de PRODUCTOS con «Palabra en la celda»)."""
    extra = []
    claves = [(L.norm(p["Palabra en la celda"]), cod) for cod, p in tablas["productos"].items()
              if p.get("Palabra en la celda")]
    for m in movs:
        t = L.norm(m["texto"])
        for palabra, cod in claves:
            for pal in [x.strip() for x in palabra.split(",") if x.strip()]:
                if re.search(rf"\b{re.escape(pal)}\b", t):
                    extra.append(Servicio(
                        id_servicio=f"{m['contenedor']}|{m['fecha']}|{cod}|{m['ubicacion'] or ''}", fecha=m["fecha"],
                        fecha_fin=None, contenedor=m["contenedor"], codigo_corto=m["codigo_corto"], servicio=cod,
                        origen=None, destino=m["ubicacion"], via=None, expedicion=m.get("referencia"),
                        cargado="", cantidad=1, unidad="servicio", conductor_codigo=m.get("iniciales"),
                        chasis=m.get("chasis"), texto=m["texto"]))
                    break
    return extra


def _tarifa(s: Servicio, tarifas: list, fecha: str):
    o, d = L.norm(s.origen or ""), L.norm(s.destino or "")
    mejor, puntos = None, -1
    for t in tarifas:
        if t["_s"] != s.servicio:
            continue
        if all(_num(t.get(k)) is None for k in ("Precio venta €", "Coste €")) and not t.get("Proveedor") \
                and L.norm(t.get("¿Se factura?") or "") != "NO":
            continue  # fila de tramo aún sin rellenar
        desde = t.get("Vigente desde")
        if isinstance(desde, (dt.date, dt.datetime)) and desde.strftime("%Y-%m-%d") > fecha:
            continue
        c = {"Cargado": "CARGADO", "Vacío": "VACIO"}.get(s.cargado, "")
        p = (2 if t["_o"] == o else 0 if t["_o"] == "*" else -9) + (2 if t["_d"] == d else 0 if t["_d"] == "*" else -9) \
            + (1 if t["_c"] == c else 0 if t["_c"] in ("*", "") else -9)
        if p >= 0 and p > puntos:
            mejor, puntos = t, p
    return mejor


def tarificar(servicios: list[Servicio], tablas: dict) -> list[Servicio]:
    for s in servicios:
        p = tablas["productos"].get(s.servicio, {})
        s.producto = p.get("Producto en Odoo")
        s.producto_id = _int(p.get("ID producto"))
        s.se_factura = (p.get("¿Se factura?") or "PENDIENTE").upper()
        s.precio, s.coste = _num(p.get("Precio por defecto €")), _num(p.get("Coste por defecto €"))
        s.proveedor, s.proveedor_id = p.get("Proveedor por defecto"), _int(p.get("ID proveedor"))
        t = _tarifa(s, tablas["tarifas"], s.fecha)
        if not t and s.servicio == "TREN" and tablas["tipos"].get(L.norm(s.origen or "")) == "TERMINAL DE PASO":
            # sale desde una terminal de paso (SL ZGZA → LEIXOES): vale la tarifa del mismo destino si solo hay una
            c = {"Cargado": "CARGADO", "Vacío": "VACIO"}.get(s.cargado, "")
            cand = [x for x in tablas["tarifas"] if x["_s"] == "TREN" and x["_d"] == L.norm(s.destino or "")
                    and x["_c"] in (c, "*", "") and x.get("ID producto")]
            if cand and len({x.get("ID producto") for x in cand}) == 1:
                t = cand[0]
                s.notas.append(f"tarifa de {cand[0].get('Origen')} → {s.destino}: sale desde una terminal de paso")
        if t and L.norm(t.get("¿Se factura?") or "") == "NO":
            s.se_factura = "NO"
        if t:
            s.precio = _num(t.get("Precio venta €")) if _num(t.get("Precio venta €")) is not None else s.precio
            s.coste = _num(t.get("Coste €")) if _num(t.get("Coste €")) is not None else s.coste
            s.proveedor = t.get("Proveedor") or s.proveedor
            s.proveedor_id = _int(t.get("ID proveedor")) or s.proveedor_id
            if t.get("Producto en Odoo"):
                s.producto, s.producto_id = t["Producto en Odoo"], _int(t.get("ID producto")) or s.producto_id
            if _num(t.get("Km")):
                s.cantidad, s.unidad = _num(t["Km"]), "km"
            if t.get("Cuenta analítica (ID)"):
                p = {**p, "Cuenta analítica (ID)": t["Cuenta analítica (ID)"]}
        r = getattr(s, "_reserva", None)
        if r:  # lo reservado manda sobre la tarifa: proveedor y coste reales
            s.proveedor = r.get("Proveedor") or s.proveedor
            s.proveedor_id = _int(r.get("ID proveedor")) or s.proveedor_id
            s.coste = _num(r.get("Coste €")) if _num(r.get("Coste €")) is not None else s.coste
        cuenta = p.get("Cuenta analítica (ID)")
        cuenta = str(int(cuenta)) if isinstance(cuenta, float) else str(cuenta or "").replace(" ", "").replace("+", ",")
        s.analitica = {cuenta: 100} if cuenta and cuenta.replace(",", "").isdigit() else None
        externo = False
        if s.conductor_codigo:
            c = tablas["conductores"].get(L.norm(s.conductor_codigo))
            if c and L.norm(c.get("Tipo") or "") == "EMPRESA EXTERNA":
                externo = True  # p. ej. CVA: se lo lleva otra empresa; sin conductor, va como proveedor
                s.conductor = c.get("Nombre") or s.conductor_codigo
                s.proveedor = c.get("Nombre") or s.proveedor
                s.proveedor_id = _int(c.get("ID proveedor")) or s.proveedor_id
                if s.unidad == "km" and _num(c.get("Coste por km €")) is not None:
                    s.coste = _num(c.get("Coste por km €"))
            elif c:
                s.conductor, s.conductor_id = c.get("Nombre"), _int(c.get("ID conductor en Odoo"))
                s.tractora, s.tractora_id = c.get("Tractora habitual"), _int(c.get("ID tractora"))
        if s.chasis:
            ch = tablas["chasis"].get(s.chasis.lstrip("R"))
            if ch:
                s.remolque, s.remolque_id = ch.get("Matrícula"), _int(ch.get("ID remolque en Odoo"))
        s.origen_id = tablas["ids_ubic"].get(L.norm(s.origen or ""))
        s.destino_id = tablas["ids_ubic"].get(L.norm(s.destino or ""))

        falta = []
        cond = tablas["conductores"].get(L.norm(s.conductor_codigo or "")) or {}
        motivo = None
        if s.forzar == "NOFACT":
            motivo = "marcado NOFACT en la celda"
        elif s.forzar == "FACT":
            s.se_factura = "SÍ"
            if s.cancelada:
                s.notas.append("cancelada pero se factura (FACT)")
        elif s.cancelada:
            motivo = "cancelada"
        elif s.servicio == "CARRETERA":
            motivo = "la hace CVA y la cobra a Carburos"
        elif L.norm(cond.get("Tipo") or "").startswith("INFORMATIVO"):
            motivo = f"lo lleva {cond.get('Nombre') or s.conductor_codigo} y no lo facturamos nosotros"
        elif s.se_factura == "NO":
            motivo = "no se factura"
        if motivo:
            info = tablas["productos"].get("INFORMATIVO", {})
            s.producto, s.producto_id = info.get("Producto en Odoo"), _int(info.get("ID producto"))
            s.precio, s.coste, s.proveedor, s.proveedor_id = 0.0, 0.0, None, None
            s.conductor = s.conductor_id = s.tractora = s.tractora_id = None
            s.cantidad, s.unidad = 1.0, "servicio"
            s.notas.append(f"Informativo: {motivo}")
            s.falta = [] if s.producto_id else ["crear en Odoo el producto informativo"]
            s.estado = "INFORMATIVO"
            continue
        if s.se_factura != "SÍ" and s.se_factura != "SI":
            falta.append("pactar si se factura")
        if not s.producto_id:
            falta.append("producto")
        if not s.analitica:
            falta.append("cuenta analítica")
        if s.precio is None:
            falta.append("precio")
        if s.servicio == "TREN":
            if not s.destino:
                falta.append("destino del tren (aún no ha llegado)")
            if not s.expedicion:
                s.notas.append("sin nº de expedición (no impide facturar; sí cuadrar la factura del proveedor)")
            if not s.proveedor_id:
                falta.append("proveedor")
            if s.coste is None:
                falta.append("coste")
        if s.servicio in ("CARRETERA",) and (not s.proveedor_id and not s.conductor_id):
            falta.append("proveedor o conductor")
        if s.servicio == "TRAYECTO" and externo:
            if not s.proveedor_id:
                falta.append(f"ficha de proveedor de {s.conductor_codigo} en CONDUCTORES")
        elif s.servicio == "TRAYECTO":
            if s.conductor_codigo and not s.conductor_id:
                falta.append(f"conductor {s.conductor_codigo} en CONDUCTORES")
            if not s.conductor_codigo:
                falta.append("código de conductor")
            if s.chasis and not s.remolque_id:
                falta.append(f"chasis {s.chasis} en CHASIS")
        if any(n.startswith("DEDUCIDO") for n in s.notas):
            falta.append("confirmar la salida en tren (no está anotada)")
        s.falta = falta
        s.estado = "LISTO PARA ODOO" if not falta else "FALTAN DATOS"
    return servicios


def calcular(ruta, desde=None, hasta=None):
    conts, estados = L.leer(ruta)
    movs = L.movimientos(estados)
    tablas = leer_tablas(ruta)
    servs = construir(movs, tablas["tipos"]) + codigos_pactados(movs, tablas)
    rotaciones(servs)                       # antes de filtrar fechas: la rotación puede empezar antes
    cruce = cruzar_reservas(servs, tablas["reservas"])
    if desde:
        servs = [s for s in servs if s.fecha >= desde.isoformat()]
    if hasta:
        servs = [s for s in servs if s.fecha <= hasta.isoformat()]
    servs.sort(key=lambda s: (s.fecha, s.contenedor, s.servicio))
    tarificar(servs, tablas)
    calcular.cruce = cruce
    return servs


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    ruta, salida = Path(argv[1]), Path(argv[2])
    desde = dt.date.fromisoformat(argv[argv.index("--desde") + 1]) if "--desde" in argv else None
    hasta = dt.date.fromisoformat(argv[argv.index("--hasta") + 1]) if "--hasta" in argv else None
    salida.mkdir(parents=True, exist_ok=True)
    servs = calcular(ruta, desde, hasta)
    filas = [asdict(s) for s in servs]
    for f in filas:
        f.pop("_reserva", None)
    (salida / "servicios.json").write_text(json.dumps(filas, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    L._csv(salida / "servicios.csv", filas)
    resumen = defaultdict(lambda: defaultdict(int))
    for s in servs:
        resumen[s.servicio][s.estado] += 1
    print(json.dumps({k: dict(v) for k, v in resumen.items()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
