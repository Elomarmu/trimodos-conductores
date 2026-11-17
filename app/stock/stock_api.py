"""API del control de stock del almacén de Burriana (oficina y almacén; mismo código de acceso que Oficina · PR).

Entradas (mercancía que llega):
  POST /api/stock/entradas/leer            fotos del albarán del cliente → datos leídos + cliente recomendado + conflictos
  POST /api/stock/entradas                 la oficina confirma y guarda la entrada (pendiente de descarga)
  GET  /api/stock/entradas                 entradas (?abiertas=true para las que faltan por cerrar)
  GET  /api/stock/entradas/{id}            detalle: líneas esperadas / verificadas, palets, discrepancias
  POST /api/stock/entradas/{id}/etiqueta   almacén: foto de la etiqueta de un palet → se casa con el albarán
  POST /api/stock/entradas/{id}/palet      almacén: palet a mano (etiqueta ilegible o sin etiqueta)
  POST /api/stock/entradas/{id}/quitar     quita un palet mal fotografiado
  POST /api/stock/entradas/{id}/cerrar     cierra la descarga: si hay discrepancias pide confirmar; vuelca al stock, Excel y correo
  POST /api/stock/entradas/{id}/reenviar   vuelve a mandar el correo y las Excel de una entrada cerrada
Salidas (mercancía que sale):
  POST /api/stock/salidas/leer             foto o captura de la petición del cliente → líneas pedidas (opcional)
  POST /api/stock/salidas                  la oficina carga la petición de salida
  GET  /api/stock/salidas · GET /api/stock/salidas/{id}
  POST /api/stock/salidas/{id}/etiqueta    almacén: foto de la etiqueta de cada palet que carga
  POST /api/stock/salidas/{id}/palet · /quitar · /cerrar · /reenviar        (como en las entradas)
Otros:
  GET  /api/stock/stock                    palets en el almacén (resumen por cliente y referencia + lista)
  GET/POST /api/stock/clientes             clientes del almacén (nombre y alias para reconocerlos en los albaranes)
  POST /api/stock/libro                    rehace CONTROL ALMACEN BURRIANA.xlsx en SharePoint
  GET  /api/stock/facturacion?desde=&hasta=   (preparación) líneas facturables del periodo
El estado se guarda en `stock_store`; las fotos, las Excel y los correos salen por n8n (STOCK_AVISOS_WEBHOOK).
Con DRY_RUN=true (o sin Odoo) se trabaja en memoria y no se envía nada.
"""
from __future__ import annotations

import base64
import datetime as dt
import html
import logging
import os
import re
from zoneinfo import ZoneInfo

import httpx
import hmac

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field

from ..config import get_settings
from ..errors import AppError
from ..pr import pr_api as PA
from . import stock_excel as X
from . import stock_facturacion as F
from . import stock_lector as L
from . import stock_reglas as R
from . import stock_store as S

log = logging.getLogger("stock")
router = APIRouter(prefix="/api/stock", tags=["stock"])
correo = APIRouter(prefix="/api/stock", tags=["stock-correo"])   # n8n: correos del buzón agente.burriana@stinsa.com (clave X-Clave)
MADRID = ZoneInfo("Europe/Madrid")
MAX_FOTOS_ALBARAN = 4


# ---- almacenamiento y envío -----------------------------------------------------------------------------------------

_memoria = S.MemoriaStore()
_store: S.Store | None = None   # los tests lo cambian con set_store


def set_store(store: S.Store | None) -> None:
    global _store
    _store = store


def real() -> bool:
    s = get_settings()
    return s.mode == "real" and not s.dry_run


def get_store() -> S.Store:
    if _store is not None:
        return _store
    return S.OdooAdjuntosStore(PA.odoo_pr()) if real() else _memoria


def ahora() -> str:
    return dt.datetime.now(MADRID).strftime("%Y-%m-%dT%H:%M")


def destinatarios() -> str:
    """A quién va el correo de cada entrada y salida. Primero Carmelo (pruebas); luego traficolevante@stinsa.com
    (variable STOCK_CORREO_PARA; varias direcciones separadas por coma)."""
    return os.environ.get("STOCK_CORREO_PARA", "carmelomartinez@stinsa.com")


def enviar_n8n(datos: dict) -> bool:
    """Flujo de n8n «STOCK BURRIANA» (STOCK_AVISOS_WEBHOOK): sube a SharePoint y manda el correo. Nunca para el trabajo."""
    url = os.environ.get("STOCK_AVISOS_WEBHOOK", "")
    if not real() or not url:
        log.info("Stock sin envío a n8n (%s): %s", "demo" if not real() else "sin webhook", datos.get("evento"))
        return False
    try:
        r = httpx.post(url, json={"para": destinatarios(), **datos}, timeout=60, headers={"X-Clave": os.environ.get("STOCK_AVISOS_CLAVE", "")})
        return r.status_code < 300
    except httpx.HTTPError:
        log.warning("No se ha podido enviar a n8n: %s", datos.get("evento"))
        return False


# ---- modelos de entrada ----------------------------------------------------------------------------------------------

class Archivos(BaseModel):
    archivos: list[PA.Archivo] = Field(min_length=1, max_length=MAX_FOTOS_ALBARAN)


class LineaAlbaran(BaseModel):
    ref: str = Field("", max_length=40)
    descripcion: str = Field("", max_length=120)
    palets: int | None = Field(None, ge=0, le=200)
    bultos: int | None = Field(None, ge=0)
    kg: int | None = Field(None, ge=0)
    lote: str = Field("", max_length=40)


class AlbaranIn(BaseModel):
    numero: str = Field("", max_length=40)
    fecha: str = Field("", max_length=10)
    remitente: str = Field("", max_length=120)
    destinatario: str = Field("", max_length=120)
    cliente_final: str = Field("", max_length=120)    # el cliente de nuestro cliente (solo informativo, no se factura)
    codigo_cliente_final: str = Field("", max_length=40)
    transportista: str = Field("", max_length=120)
    matricula: str = Field("", max_length=20)
    total_palets: int | None = None
    total_kg: int | None = None
    observaciones: str = Field("", max_length=500)
    lineas: list[LineaAlbaran] = Field(max_length=60)
    dudas: list[str] = []


class EntradaIn(BaseModel):
    cliente: str = Field(max_length=40)            # código del cliente
    albaran: AlbaranIn
    fotos: list[PA.Archivo] = Field([], max_length=MAX_FOTOS_ALBARAN)
    esperados: list[dict] = Field([], max_length=600)   # lista de palets del albarán (código, ref, kg, lote), si la trae
    fecha_entrada: str = Field("", max_length=10)       # día de entrada en el almacén (por defecto, el del albarán)
    confirmado: bool = False                       # ya ha visto los errores y sigue


class LineaPedido(BaseModel):
    ref: str = Field("", max_length=40)
    descripcion: str = Field("", max_length=120)
    palets: int | None = Field(None, ge=0, le=200)


class SalidaIn(BaseModel):
    cliente: str = Field(max_length=40)
    referencia: str = Field("", max_length=60)
    destino: str = Field("", max_length=120)
    transportista: str = Field("", max_length=120)
    fecha_pedida: str = Field("", max_length=10)
    esperados: list[dict] = Field([], max_length=600)   # palets pedidos uno a uno (código, ref), si la petición los lista
    lineas: list[LineaPedido] = Field(min_length=1, max_length=60)
    foto: PA.Archivo | None = None                 # la petición del cliente (se guarda y va en el correo)
    confirmado: bool = False


class Etiqueta(BaseModel):
    foto: PA.Archivo


class PaletManual(BaseModel):
    ref: str = Field("", max_length=40)
    descripcion: str = Field("", max_length=120)
    lote: str = Field("", max_length=40)
    sscc: str = Field("", max_length=30)
    kg: int | None = Field(None, ge=0)
    bultos: int | None = Field(None, ge=0)
    origen: str = Field("", max_length=10)   # «barras» si el código se ha leído con la cámara; vacío: escrito o elegido de la lista


class Quitar(BaseModel):
    palet: str = Field(max_length=40)


class Cerrar(BaseModel):
    confirmado: bool = False
    nota: str = Field("", max_length=300)
    observaciones: str | None = Field(None, max_length=1000)   # incidencias de la descarga o la carga (si vienen, sustituyen a las guardadas)


class Observaciones(BaseModel):
    texto: str = Field("", max_length=1000)


class FechaEntrada(BaseModel):
    fecha: str = Field(max_length=10)


class ClienteIn(BaseModel):
    codigo: str = Field("", max_length=40)
    nombre: str = Field(min_length=2, max_length=120)
    alias: list[str] = Field([], max_length=20)
    activo: bool = True
    referencias: list[dict] | None = Field(None, max_length=600)   # catálogo: [{ref, descripcion}]; None = no tocar


# ---- utilidades -------------------------------------------------------------------------------------------------------

def _leer(func, *args):
    try:
        return func(*args)
    except L.LecturaError as e:
        raise AppError(str(e), 502, "lectura_fallo")


REFERENCIAS_RAMINATRANS = [
    ("6030100800103", "TAZA WC TRIPOLI BLANCO DUAL"), ("6030100800222", "CISTERNA ENTRADA SUP WC TRIPOLI / CORINTO"),
    ("6030100800223", "CISTERNA ENTRADA INF WC TRIPOLI / CORINTO"), ("6030100100223", "CISTERNA ENTRADA INF WC OLIMPIA / CRETA / ATENAS / ESPARTA"),
    ("6030100111103", "PACK WC OLIMPIA SUSPENDIDO BLANCO"), ("6030100200103", "WC ATENAS"), ("6030100211103", "PACK WC ATENAS SUSPENDIDO BLANCO")]
# El primer cliente del almacén: así está dado de alta desde el primer día (se puede cambiar en «Clientes del almacén»).
CLIENTE_INICIAL = {"codigo": "RAMINATRANS", "nombre": "Raminatrans Castellón, S.L.", "activo": True, "odoo_partner_id": None,
                   "alias": ["RAMINATRANS", "GRUPO RAMINATRANS", "RAMINATRANS CASTELLON"],
                   "referencias": [{"ref": r, "descripcion": d} for r, d in REFERENCIAS_RAMINATRANS]}


def clientes(store) -> list[dict]:
    guardados = store.leer("config", "clientes")
    return guardados["clientes"] if guardados and guardados.get("clientes") else [dict(CLIENTE_INICIAL)]


def _documento(store, tipo: str, ident: str) -> dict:
    d = store.leer(tipo, ident)
    if not d:
        raise AppError("No encuentro esa " + ("entrada." if tipo == "entrada" else "salida."), 404, "no_encontrado")
    return d


def _limpio(nombre: str) -> str:
    return re.sub(r'[\\/:*?"<>|#%]+', "-", nombre).strip(" .")


def _guardar_foto(store, doc: str, nombre: str, archivo: PA.Archivo) -> str:
    datos, tipo = PA._bytes(archivo)
    nombre = _limpio(nombre) + {"application/pdf": ".pdf", "image/png": ".png", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx"}.get(tipo, ".jpg")
    store.guardar_foto(doc, nombre, datos, tipo)
    return nombre


def _fotos_de(store, doc: str) -> list[dict]:
    return [{"nombre": f["nombre"], "b64": f["b64"], "tipo": f["tipo"]} for f in store.leer_fotos(doc)]


def _esperar_abierta(op: dict, cerrado: str, que: str) -> None:
    if op["estado"] in (cerrado, R.ENTRADA_ANULADA):
        raise AppError(f"Esta {que} ya está {'cerrada' if que == 'entrada' else 'cargada'}.", 409, "cerrada")


def _por_revisar(op: dict) -> bool:
    return op.get("origen") == "correo" and not op.get("revisada")


def _exigir_revisada(op: dict) -> None:
    if _por_revisar(op):
        raise AppError("Esta operación llegó por correo y la oficina todavía no la ha revisado. Pulsa «Está bien» tras comprobar los datos.", 409, "sin_revisar")


def _resumen_entrada(e: dict) -> dict:
    lineas = R.estado_lineas(e)
    return {"id": e["id"], "estado": e["estado"], "cliente": e["cliente"], "albaran": e["albaran"].get("numero"), "fecha": e["albaran"].get("fecha"),
            "fecha_entrada": R.fecha_entrada(e), "cliente_final": e["albaran"].get("cliente_final") or "", "por_revisar": _por_revisar(e),
            "creada": e["creada"], "cerrada": e.get("cerrada"), "esperados": sum(l["esperados"] for l in lineas),
            "verificados": sum(l["verificados"] for l in lineas), "discrepancias": len(R.discrepancias_entrada(e)) if e["estado"] != R.ENTRADA_CERRADA
            else len(e.get("discrepancias_cierre") or [])}


def _detalle_entrada(e: dict) -> dict:
    return {**_resumen_entrada(e), "albaran_datos": e["albaran"], "lineas": R.estado_lineas(e), "palets": R.activos(e),
            "avisos": e.get("avisos") or [], "pendientes": R.pendientes_entrada(e) if e["estado"] != R.ENTRADA_CERRADA else [],
            "discrepancias_lista": R.discrepancias_entrada(e) if e["estado"] != R.ENTRADA_CERRADA else e.get("discrepancias_cierre") or [],
            "nota_cierre": e.get("nota_cierre") or "", "observaciones": e.get("observaciones") or ""}


def _resumen_salida(s: dict) -> dict:
    lineas = R.estado_lineas_salida(s)
    return {"id": s["id"], "estado": s["estado"], "cliente": s["cliente"], "referencia": s.get("referencia"), "destino": s.get("destino"), "por_revisar": _por_revisar(s),
            "creada": s["creada"], "cargada": s.get("cargada"), "pedidos": sum(l["pedidos"] for l in lineas), "cargados": sum(l["cargados"] for l in lineas),
            "discrepancias": len(R.discrepancias_salida(s)) if s["estado"] != R.SALIDA_CARGADA else len(s.get("discrepancias_cierre") or [])}


def _detalle_salida(s: dict) -> dict:
    return {**_resumen_salida(s), "datos": {k: s.get(k) for k in ("referencia", "destino", "transportista", "fecha_pedida")}, "lineas": R.estado_lineas_salida(s),
            "palets": [p for p in s["palets"] if p.get("estado_alta") != "quitado"], "avisos": s.get("avisos") or [],
            "discrepancias_lista": R.discrepancias_salida(s) if s["estado"] != R.SALIDA_CARGADA else s.get("discrepancias_cierre") or [],
            "nota_cierre": s.get("nota_cierre") or "", "observaciones": s.get("observaciones") or ""}


def _todo(store) -> tuple[list[dict], list[dict], list[dict]]:
    return store.listar("entrada"), store.listar("salida"), clientes(store)


def _sscc_en_stock(entradas, salidas) -> set[str]:
    return {p["sscc"] for p in R.stock(entradas, salidas) if p.get("sscc")}


# ---- clientes ---------------------------------------------------------------------------------------------------------

@router.get("/clientes")
def get_clientes():
    return {"clientes": clientes(get_store())}


@router.post("/clientes")
def post_cliente(body: ClienteIn):
    store = get_store()
    lista = clientes(store)
    codigo = (body.codigo or R.norm(body.nombre).split(" ")[0]).upper()
    c = {"codigo": codigo, "nombre": body.nombre.strip(), "alias": [a.strip() for a in body.alias if a.strip()], "activo": body.activo, "odoo_partner_id": None,
         "referencias": [{"ref": str(r.get("ref") or "").strip(), "descripcion": str(r.get("descripcion") or "").strip()}
                         for r in body.referencias or [] if str(r.get("ref") or "").strip()]}
    previo = R.cliente_por_codigo(lista, codigo)
    if previo:
        c["odoo_partner_id"] = previo.get("odoo_partner_id")
        if body.referencias is None:
            c["referencias"] = previo.get("referencias") or []
        lista = [c if x["codigo"] == codigo else x for x in lista]
    else:
        lista.append(c)
    store.escribir("config", "clientes", {"clientes": lista})
    return {"cliente": c, "clientes": lista}


# ---- entradas ---------------------------------------------------------------------------------------------------------

@router.post("/entradas/leer")
def entradas_leer(body: Archivos):
    return _preparar_entrada([PA._bytes(a) for a in body.archivos])


def _preparar_entrada(todos: list[tuple[bytes, str]]) -> dict:
    """Albarán (fotos, PDF y/o Excel del cliente) → datos leídos, cliente recomendado y conflictos. Lo usan la pantalla y el correo."""
    store = get_store()
    excels = [a for a in todos if L.es_excel(*a)]
    archivos = [a for a in todos if a not in excels]
    leido = _leer(L.leer_albaran, archivos) if archivos else {"lineas": [], "dudas": []}
    if excels:   # el Excel del cliente (una fila por palet) manda sobre lo que lea la IA del PDF
        ex = _leer(L.leer_excel_albaran, excels[0][0])
        leido_pdf = leido
        lotes = {R.sscc(x.get("codigo")): x.get("entrada") for x in leido_pdf.get("palets_detalle") or [] if x.get("entrada")}
        detalle = [{**x, "entrada": x.get("entrada") or lotes.get(R.sscc(x["codigo"]), "")} for x in ex["palets_detalle"]]   # el lote («Entrada») sale del PDF
        leido = {**leido, "lineas": ex["lineas"], "palets_detalle": detalle, "total_palets": ex["total_palets"],
                 "total_kg": ex["total_kg"], "dudas": list(leido_pdf.get("dudas") or [])}
        en_pdf = {R.sscc(x.get("codigo")) for x in leido_pdf.get("palets_detalle") or []}
        if en_pdf and en_pdf != {R.sscc(x["codigo"]) for x in ex["palets_detalle"]}:
            leido["dudas"].append("El PDF y el Excel no listan exactamente los mismos palets: manda el Excel (es el que se usa).")
        if leido_pdf.get("total_palets") and leido_pdf["total_palets"] != ex["total_palets"]:
            leido["dudas"].append(f"El PDF dice {leido_pdf['total_palets']} palets y el Excel trae {ex['total_palets']}.")
        if not archivos:
            leido["dudas"].append("Solo has subido el Excel: faltan el número y la fecha del albarán (están en el PDF); escríbelos.")
    albaran = R.limpiar_albaran(leido)
    esperados = R.limpiar_esperados(leido.get("palets_detalle"))
    if esperados and not any(l["palets"] for l in albaran["lineas"]):   # albarán que solo lista palets: se cuentan
        for l in albaran["lineas"]:
            l["palets"] = sum(1 for x in esperados if R.referencia(x["ref"]) == R.referencia(l["ref"]))
    cli = clientes(store)
    sugeridos = R.sugerir_clientes(cli, *R.textos_cliente(albaran))
    elegido = sugeridos[0][0] if sugeridos and (len(sugeridos) == 1 or sugeridos[0][1] > sugeridos[1][1]) else None
    entradas = store.listar("entrada")
    return {"albaran": albaran, "esperados": esperados, "fecha_entrada": albaran["fecha"] or dt.datetime.now(MADRID).date().isoformat(),
            "cliente_sugerido": elegido["codigo"] if elegido else None,
            "candidatos": [{"codigo": c["codigo"], "nombre": c["nombre"], "puntos": p} for c, p in sugeridos[:3]],
            "avisos": R.conflictos_entrada(albaran, elegido, cli, entradas), "clientes": cli}


@router.post("/entradas")
def entradas_crear(body: EntradaIn):
    store = get_store()
    cli = clientes(store)
    cliente = R.cliente_por_codigo(cli, body.cliente)
    if not cliente:
        raise AppError("Elige el cliente de la lista (si es nuevo, dalo de alta primero).", 400, "sin_cliente")
    albaran = R.limpiar_albaran({"numero_albaran": body.albaran.numero, "fecha": body.albaran.fecha, "remitente": body.albaran.remitente,
                                 "destinatario": body.albaran.destinatario, "cliente_final": body.albaran.cliente_final, "codigo_cliente_final": body.albaran.codigo_cliente_final, "transportista": body.albaran.transportista, "matricula": body.albaran.matricula,
                                 "total_palets": body.albaran.total_palets, "total_kg": body.albaran.total_kg, "observaciones": body.albaran.observaciones,
                                 "lineas": [{"referencia": l.ref, "descripcion": l.descripcion, "palets": l.palets, "bultos": l.bultos, "kg": l.kg, "lote": l.lote}
                                            for l in body.albaran.lineas], "dudas": body.albaran.dudas})
    entradas = store.listar("entrada")
    avisos = R.conflictos_entrada(albaran, cliente, cli, entradas)
    errores = [a["texto"] for a in avisos if a["nivel"] == "error"]
    if errores and not body.confirmado:
        raise AppError(" ".join(errores) + " ¿Quieres guardarla igualmente?", 409, "entrada_conflicto")
    ident = R.siguiente_id("E", [e["id"] for e in entradas], dt.datetime.now(MADRID).year)
    e = {"id": ident, "estado": R.ENTRADA_PENDIENTE, "creada": ahora(), "cliente": {"codigo": cliente["codigo"], "nombre": cliente["nombre"]},
         "albaran": {k: albaran[k] for k in ("numero", "fecha", "remitente", "destinatario", "cliente_final", "codigo_cliente_final", "transportista", "matricula", "total_palets", "total_kg", "observaciones")},
         "fecha_entrada": R.fecha(body.fecha_entrada) or albaran["fecha"] or dt.datetime.now(MADRID).date().isoformat(),
         "lineas": albaran["lineas"], "esperados": R.limpiar_esperados(body.esperados), "avisos": avisos, "palets": []}
    store.escribir("entrada", ident, e)
    for i, f in enumerate(body.fotos, start=1):
        try:
            _guardar_foto(store, ident, f"albaran {i}", f)
        except AppError:   # una foto mala no impide la entrada
            log.warning("foto del albarán %s de %s no guardada", i, ident)
    log.info("entrada %s creada (%s, albarán %s)", ident, cliente["codigo"], albaran["numero"])
    return _detalle_entrada(e)


@router.get("/entradas")
def entradas_lista(abiertas: bool = Query(False)):
    res = [_resumen_entrada(e) for e in get_store().listar("entrada") if e["estado"] != R.ENTRADA_ANULADA
           and (not abiertas or e["estado"] != R.ENTRADA_CERRADA)]
    return {"entradas": sorted(res, key=lambda e: e["id"], reverse=True)}


@router.get("/entradas/{ident}")
def entrada_detalle(ident: str):
    return _detalle_entrada(_documento(get_store(), "entrada", ident))


def _anadir_palet_entrada(store, e: dict, etiqueta: dict, foto: PA.Archivo | None, manual: bool, origen: str = "") -> dict:
    entradas, salidas = store.listar("entrada"), store.listar("salida")
    palet = R.verificar_palet(e, etiqueta, _sscc_en_stock(entradas, salidas))
    n = max([int(p["id"].rsplit("-", 1)[1]) for p in e["palets"]] or [0]) + 1
    palet.update(id=f"{e['id']}-{n:02d}", manual=manual, hora=ahora(), foto="", origen=origen)
    if foto:
        palet["foto"] = _guardar_foto(store, e["id"], f"{n:02d} {etiqueta.get('ref') or 'palet'}", foto)
    e["palets"].append(palet)
    e["estado"] = R.ENTRADA_DESCARGANDO
    store.escribir("entrada", e["id"], e)
    return palet


@router.post("/entradas/{ident}/etiqueta")
def entrada_etiqueta(ident: str, body: Etiqueta):
    store = get_store()
    e = _documento(store, "entrada", ident)
    _esperar_abierta(e, R.ENTRADA_CERRADA, "entrada")
    datos, tipo = PA._bytes(body.foto)
    etiqueta = R.limpiar_etiqueta(_leer(L.leer_etiqueta, datos, tipo))
    if not (etiqueta["sscc"] or etiqueta["ref"] or etiqueta["descripcion"]):
        raise AppError("No se lee la etiqueta. Haz otra foto con más luz o apunta el palet a mano.", 422, "etiqueta_ilegible")
    palet = _anadir_palet_entrada(store, e, etiqueta, body.foto, False)
    return {"palet": palet, "entrada": _detalle_entrada(e)}


@router.post("/entradas/{ident}/palet")
def entrada_palet_manual(ident: str, body: PaletManual):
    store = get_store()
    e = _documento(store, "entrada", ident)
    _esperar_abierta(e, R.ENTRADA_CERRADA, "entrada")
    codigo = R.sscc(body.sscc)
    if not (body.ref or body.descripcion or codigo):
        raise AppError("Escribe el código del palet o su referencia.", 400, "sin_referencia")
    datos = {"sscc": body.sscc, "referencia": body.ref, "descripcion": body.descripcion, "lote": body.lote, "kg": body.kg, "bultos": body.bultos}
    exp = next((x for x in e.get("esperados") or [] if codigo and x["codigo"] == codigo), None)
    if exp and not body.ref:   # solo llega el código (leído con la cámara o elegido de la lista): lo demás sale del albarán
        datos.update(referencia=exp.get("ref") or "", lote=body.lote or exp.get("lote") or "", kg=body.kg if body.kg is not None else exp.get("kg"))
    palet = _anadir_palet_entrada(store, e, R.limpiar_etiqueta(datos), None, body.origen != "barras", body.origen)
    return {"palet": palet, "entrada": _detalle_entrada(e)}


@router.post("/entradas/{ident}/quitar")
def entrada_quitar(ident: str, body: Quitar):
    store = get_store()
    e = _documento(store, "entrada", ident)
    _esperar_abierta(e, R.ENTRADA_CERRADA, "entrada")
    p = next((p for p in e["palets"] if p["id"] == body.palet and p.get("estado_alta") != "quitado"), None)
    if not p:
        raise AppError("No encuentro ese palet.", 404, "no_encontrado")
    p["estado_alta"] = "quitado"
    store.escribir("entrada", e["id"], e)
    return _detalle_entrada(e)


def _cuerpo_correo(titulo: str, cabecera: str, filas_cab: list[str], tabla: str, disc: list[str], nota: str, observaciones: str = "") -> str:
    estilo = "font:11pt Calibri,Arial"
    inc = (f"<p style='{estilo};color:#C00000'><b>Incidencias en el almacén:</b><br>" + html.escape(observaciones).replace("\n", "<br>") + "</p>") if observaciones else ""
    d = (f"<p style='{estilo};color:#C00000'><b>Discrepancias aceptadas:</b><br>" + "<br>".join(html.escape(x) for x in disc)
         + (f"<br><i>{html.escape(nota)}</i>" if nota else "") + "</p>") if disc else f"<p style='{estilo}'>Sin discrepancias.</p>"
    return (f"<p style='{estilo}'><b>{html.escape(titulo)}</b></p><p style='{estilo}'>{cabecera}</p>"
            + "".join(f"<p style='{estilo}'>{x}</p>" for x in filas_cab) + inc + tabla + d
            + f"<p style='{estilo};color:#595959'>Se adjuntan la Excel y las fotos para reenviar al cliente. {html.escape(R.ALMACEN)}.</p>")


def _enviar_entrada(store, e: dict) -> tuple[bool, list[dict]]:
    """Correo (Excel de la entrada + fotos) y libro de control en SharePoint, en UNA llamada a n8n."""
    entradas, salidas, cli = _todo(store)
    carpeta = X.carpeta(e)
    archivos = [{"nombre": X.nombre_entrada(e), "carpeta": carpeta, "correo": True, "b64": base64.b64encode(X.excel_entrada(e)).decode()},
                {"nombre": X.LIBRO, "carpeta": "", "correo": False, "b64": base64.b64encode(X.libro_control(entradas, salidas, cli)).decode()}]
    fotos = [dict(f, carpeta=carpeta) for f in _fotos_de(store, e["id"])]
    lineas = R.estado_lineas(e)
    tabla = PA.tabla_html(["Referencia", "Descripción", "Palets albarán", "Palets verificados", "Kg albarán", "Kg etiquetas"],
                          [[l["ref"], l["descripcion"], l["esperados"], l["verificados"], l["kg_albaran"], l["kg_etiquetas"]] for l in lineas])
    a = e["albaran"]
    html_ = _cuerpo_correo(f"Entrada {e['id']} · {e['cliente']['nombre']}",
                           f"Albarán <b>{html.escape(a.get('numero') or '—')}</b> del {html.escape('-'.join(reversed((a.get('fecha') or '').split('-'))))} · "
                           f"{html.escape(a.get('matricula') or '')} · {html.escape(a.get('transportista') or '')}",
                           [f"{sum(l['verificados'] for l in lineas)} de {sum(l['esperados'] for l in lineas)} palets verificados con su etiqueta."],
                           tabla, e.get("discrepancias_cierre") or [], e.get("nota_cierre") or "", e.get("observaciones") or "")
    ok = enviar_n8n({"evento": "entrada_cerrada", "carpeta": carpeta,
                     "asunto": ("⚠ " if e.get("observaciones") else "") + f"Almacén Burriana · Entrada {e['id']} · {e['cliente']['nombre']} · albarán {a.get('numero') or ''}".strip(),
                     "html": html_, "archivos": archivos, "fotos": fotos})
    return ok, archivos[:1]


@router.post("/entradas/{ident}/cerrar")
def entrada_cerrar(ident: str, body: Cerrar):
    store = get_store()
    e = _documento(store, "entrada", ident)
    _esperar_abierta(e, R.ENTRADA_CERRADA, "entrada")
    _exigir_revisada(e)
    if not R.activos(e):
        raise AppError("Todavía no hay ningún palet verificado.", 409, "sin_palets")
    disc = R.discrepancias_entrada(e)
    if disc and not body.confirmado:
        raise AppError("Hay discrepancias entre el albarán y las etiquetas:\n" + "\n".join("· " + d for d in disc) + "\n¿Cierras la entrada igualmente?", 409, "discrepancias")
    for p in R.activos(e):
        if p["estado"] == "discrepancia":
            p["aceptado"] = True
    if body.observaciones is not None:
        e["observaciones"] = body.observaciones.strip()
    e.update(estado=R.ENTRADA_CERRADA, cerrada=ahora(), discrepancias_cierre=disc, nota_cierre=body.nota.strip())
    store.escribir("entrada", e["id"], e)   # primero el estado: el envío nunca lo deshace
    enviado, archivos = _enviar_entrada(store, e)
    log.info("entrada %s cerrada: %s palets, %s discrepancias, enviado=%s", e["id"], len(R.activos(e)), len(disc), enviado)
    return {"entrada": _detalle_entrada(e), "enviado": enviado, "para": destinatarios(), "archivos": archivos}


@router.post("/entradas/{ident}/fecha")
def entrada_fecha(ident: str, body: FechaEntrada):
    """Cambia el día de entrada en el almacén (p. ej. el camión se cargó un día y se descarga al siguiente). Solo antes de cerrarla."""
    store = get_store()
    e = _documento(store, "entrada", ident)
    _esperar_abierta(e, R.ENTRADA_CERRADA, "entrada")
    f = R.fecha(body.fecha)
    if not f:
        raise AppError("La fecha no es válida.", 400, "fecha_invalida")
    e["fecha_entrada"] = f
    store.escribir("entrada", e["id"], e)
    return _detalle_entrada(e)


@router.post("/entradas/{ident}/revisar")
def entrada_revisar(ident: str):
    """La oficina da por buena una entrada preparada desde un correo."""
    store = get_store()
    e = _documento(store, "entrada", ident)
    e["revisada"] = True
    store.escribir("entrada", e["id"], e)
    return _detalle_entrada(e)


@router.post("/entradas/{ident}/observaciones")
def entrada_observaciones(ident: str, body: Observaciones):
    store = get_store()
    e = _documento(store, "entrada", ident)
    _esperar_abierta(e, R.ENTRADA_CERRADA, "entrada")
    e["observaciones"] = body.texto.strip()
    store.escribir("entrada", e["id"], e)
    return _detalle_entrada(e)


@router.post("/entradas/{ident}/reenviar")
def entrada_reenviar(ident: str):
    store = get_store()
    e = _documento(store, "entrada", ident)
    if e["estado"] != R.ENTRADA_CERRADA:
        raise AppError("La entrada todavía no está cerrada.", 409, "abierta")
    enviado, archivos = _enviar_entrada(store, e)
    return {"enviado": enviado, "para": destinatarios(), "archivos": archivos}


# ---- salidas ----------------------------------------------------------------------------------------------------------

@router.post("/salidas/leer")
def salidas_leer(body: Archivos):
    return _preparar_salida([PA._bytes(a) for a in body.archivos])


def _preparar_salida(todos: list[tuple[bytes, str]]) -> dict:
    store = get_store()
    excels = [a for a in todos if L.es_excel(*a)]
    resto = [a for a in todos if a not in excels]
    leido = _leer(L.leer_peticion, resto) if resto else {"lineas": [], "dudas": []}
    if excels:   # Excel con una fila por palet: da las líneas y la lista exacta de códigos pedidos
        ex = _leer(L.leer_excel_albaran, excels[0][0])
        leido = {**leido, "lineas": ex["lineas"], "palets_detalle": ex["palets_detalle"]}
    pet = R.limpiar_peticion(leido)
    esperados = R.limpiar_esperados(leido.get("palets_detalle"))
    cli = clientes(store)
    sugeridos = R.sugerir_clientes(cli, pet["cliente_texto"])
    return {"peticion": pet, "esperados": esperados, "cliente_sugerido": sugeridos[0][0]["codigo"] if sugeridos else None,
            "candidatos": [{"codigo": c["codigo"], "nombre": c["nombre"], "puntos": p} for c, p in sugeridos[:3]], "clientes": cli}


@router.post("/salidas")
def salidas_crear(body: SalidaIn):
    store = get_store()
    cliente = R.cliente_por_codigo(clientes(store), body.cliente)
    if not cliente:
        raise AppError("Elige el cliente de la lista.", 400, "sin_cliente")
    entradas, salidas = store.listar("entrada"), store.listar("salida")
    ident = R.siguiente_id("S", [s["id"] for s in salidas], dt.datetime.now(MADRID).year)
    s = {"id": ident, "estado": R.SALIDA_PEDIDA, "creada": ahora(), "cliente": {"codigo": cliente["codigo"], "nombre": cliente["nombre"]},
         "referencia": body.referencia.strip(), "destino": body.destino.strip(), "transportista": body.transportista.strip(),
         "fecha_pedida": R.fecha(body.fecha_pedida), "lineas": [{"ref": l.ref.strip(), "descripcion": l.descripcion.strip(), "palets": l.palets} for l in body.lineas],
         "esperados": R.limpiar_esperados(body.esperados), "palets": [], "avisos": []}
    existencias = R.stock(entradas, salidas)
    s["avisos"] = R.conflictos_salida(s, existencias)
    en_stock = {p["sscc"] for p in existencias if p.get("sscc")}
    ausentes = [x["codigo"] for x in s["esperados"] if x["codigo"] not in en_stock]
    if ausentes:
        s["avisos"].append(R.aviso("error", f"{len(ausentes)} palets de la petición no están en el stock del almacén ({', '.join(ausentes[:5])}{'…' if len(ausentes) > 5 else ''})."))
    errores = [a["texto"] for a in s["avisos"] if a["nivel"] == "error"]
    if errores and not body.confirmado:
        raise AppError(" ".join(errores) + " ¿Quieres cargar la petición igualmente?", 409, "salida_conflicto")
    store.escribir("salida", ident, s)
    if body.foto:
        try:
            _guardar_foto(store, ident, "peticion", body.foto)
        except AppError:
            log.warning("foto de la petición de %s no guardada", ident)
    log.info("salida %s creada (%s)", ident, cliente["codigo"])
    return _detalle_salida(s)


@router.get("/salidas")
def salidas_lista(abiertas: bool = Query(False)):
    res = [_resumen_salida(s) for s in get_store().listar("salida") if s["estado"] != R.SALIDA_ANULADA and (not abiertas or s["estado"] != R.SALIDA_CARGADA)]
    return {"salidas": sorted(res, key=lambda s: s["id"], reverse=True)}


@router.get("/salidas/{ident}")
def salida_detalle(ident: str):
    return _detalle_salida(_documento(get_store(), "salida", ident))


def _anadir_palet_salida(store, s: dict, etiqueta: dict, foto: PA.Archivo | None, manual: bool, origen: str = "") -> dict:
    entradas, salidas = store.listar("entrada"), store.listar("salida")
    palet = R.cargar_palet(s, etiqueta, R.stock(entradas, salidas), salidas)
    n = len(s["palets"]) + 1
    palet.update(n=n, manual=manual, hora=ahora(), foto="", origen=origen)
    if foto:
        palet["foto"] = _guardar_foto(store, s["id"], f"{n:02d} {palet['palet_id'] or palet['ref'] or 'palet'}", foto)
    s["palets"].append(palet)
    s["estado"] = R.SALIDA_PREPARANDO
    store.escribir("salida", s["id"], s)
    return palet


@router.post("/salidas/{ident}/etiqueta")
def salida_etiqueta(ident: str, body: Etiqueta):
    store = get_store()
    s = _documento(store, "salida", ident)
    _esperar_abierta(s, R.SALIDA_CARGADA, "salida")
    datos, tipo = PA._bytes(body.foto)
    etiqueta = R.limpiar_etiqueta(_leer(L.leer_etiqueta, datos, tipo))
    if not (etiqueta["sscc"] or etiqueta["ref"]):
        raise AppError("No se lee la etiqueta. Haz otra foto con más luz o apunta el palet a mano.", 422, "etiqueta_ilegible")
    palet = _anadir_palet_salida(store, s, etiqueta, body.foto, False)
    return {"palet": palet, "salida": _detalle_salida(s)}


@router.post("/salidas/{ident}/palet")
def salida_palet_manual(ident: str, body: PaletManual):
    store = get_store()
    s = _documento(store, "salida", ident)
    _esperar_abierta(s, R.SALIDA_CARGADA, "salida")
    if not (body.sscc or body.ref):
        raise AppError("Escribe el código del palet o su referencia.", 400, "sin_referencia")
    etiqueta = R.limpiar_etiqueta({"sscc": body.sscc, "referencia": body.ref, "descripcion": body.descripcion, "lote": body.lote, "kg": body.kg, "bultos": body.bultos})
    palet = _anadir_palet_salida(store, s, etiqueta, None, body.origen != "barras", body.origen)
    return {"palet": palet, "salida": _detalle_salida(s)}


@router.get("/salidas/{ident}/disponibles")
def salida_disponibles(ident: str):
    """Palets del almacén que se pueden cargar en esta salida (para la lista desplegable)."""
    store = get_store()
    s = _documento(store, "salida", ident)
    if s["estado"] == R.SALIDA_CARGADA:
        return {"palets": []}
    entradas, salidas = store.listar("entrada"), store.listar("salida")
    return {"palets": R.disponibles_salida(s, R.stock(entradas, salidas), salidas)}


@router.post("/salidas/{ident}/quitar")
def salida_quitar(ident: str, body: Quitar):
    store = get_store()
    s = _documento(store, "salida", ident)
    _esperar_abierta(s, R.SALIDA_CARGADA, "salida")
    p = next((p for p in s["palets"] if str(p.get("n")) == body.palet and p.get("estado_alta") != "quitado"), None)
    if not p:
        raise AppError("No encuentro ese palet.", 404, "no_encontrado")
    p["estado_alta"] = "quitado"
    store.escribir("salida", s["id"], s)
    return _detalle_salida(s)


def _enviar_salida(store, s: dict) -> tuple[bool, list[dict]]:
    entradas, salidas, cli = _todo(store)
    carpeta = X.carpeta(s)
    archivos = [{"nombre": X.nombre_salida(s), "carpeta": carpeta, "correo": True, "b64": base64.b64encode(X.excel_salida(s)).decode()},
                {"nombre": X.LIBRO, "carpeta": "", "correo": False, "b64": base64.b64encode(X.libro_control(entradas, salidas, cli)).decode()}]
    fotos = [dict(f, carpeta=carpeta) for f in _fotos_de(store, s["id"])]
    lineas = R.estado_lineas_salida(s)
    tabla = PA.tabla_html(["Referencia", "Descripción", "Palets pedidos", "Palets cargados", "Kg"],
                          [[l["ref"], l["descripcion"], l["pedidos"], l["cargados"], l["kg"]] for l in lineas])
    html_ = _cuerpo_correo(f"Salida {s['id']} · {s['cliente']['nombre']}",
                           f"Pedido <b>{html.escape(s.get('referencia') or '—')}</b> · destino {html.escape(s.get('destino') or '—')} · {html.escape(s.get('transportista') or '')}",
                           [f"{sum(l['cargados'] for l in lineas)} de {sum(l['pedidos'] for l in lineas)} palets cargados."],
                           tabla, s.get("discrepancias_cierre") or [], s.get("nota_cierre") or "", s.get("observaciones") or "")
    ok = enviar_n8n({"evento": "salida_cargada", "carpeta": carpeta, "asunto": ("⚠ " if s.get("observaciones") else "") + f"Almacén Burriana · Salida {s['id']} · {s['cliente']['nombre']}"
                     + (f" · pedido {s['referencia']}" if s.get("referencia") else ""), "html": html_, "archivos": archivos, "fotos": fotos})
    return ok, archivos[:1]


@router.post("/salidas/{ident}/cerrar")
def salida_cerrar(ident: str, body: Cerrar):
    store = get_store()
    s = _documento(store, "salida", ident)
    _esperar_abierta(s, R.SALIDA_CARGADA, "salida")
    _exigir_revisada(s)
    if not [p for p in s["palets"] if p.get("estado_alta") != "quitado"]:
        raise AppError("Todavía no hay ningún palet cargado.", 409, "sin_palets")
    disc = R.discrepancias_salida(s)
    if disc and not body.confirmado:
        raise AppError("Hay discrepancias entre lo pedido y lo cargado:\n" + "\n".join("· " + d for d in disc) + "\n¿Das la salida por cargada igualmente?", 409, "discrepancias")
    for p in s["palets"]:
        if p.get("estado_alta") != "quitado" and p["estado"] == "discrepancia":
            p["aceptado"] = True
    if body.observaciones is not None:
        s["observaciones"] = body.observaciones.strip()
    s.update(estado=R.SALIDA_CARGADA, cargada=ahora(), discrepancias_cierre=disc, nota_cierre=body.nota.strip())
    store.escribir("salida", s["id"], s)
    enviado, archivos = _enviar_salida(store, s)
    log.info("salida %s cargada: %s discrepancias, enviado=%s", s["id"], len(disc), enviado)
    return {"salida": _detalle_salida(s), "enviado": enviado, "para": destinatarios(), "archivos": archivos}


@router.post("/salidas/{ident}/revisar")
def salida_revisar(ident: str):
    store = get_store()
    s = _documento(store, "salida", ident)
    s["revisada"] = True
    store.escribir("salida", s["id"], s)
    return _detalle_salida(s)


@router.post("/salidas/{ident}/observaciones")
def salida_observaciones(ident: str, body: Observaciones):
    store = get_store()
    s = _documento(store, "salida", ident)
    _esperar_abierta(s, R.SALIDA_CARGADA, "salida")
    s["observaciones"] = body.texto.strip()
    store.escribir("salida", s["id"], s)
    return _detalle_salida(s)


@router.post("/salidas/{ident}/reenviar")
def salida_reenviar(ident: str):
    store = get_store()
    s = _documento(store, "salida", ident)
    if s["estado"] != R.SALIDA_CARGADA:
        raise AppError("La salida todavía no está cargada.", 409, "abierta")
    enviado, archivos = _enviar_salida(store, s)
    return {"enviado": enviado, "para": destinatarios(), "archivos": archivos}


# ---- stock, libro y facturación ------------------------------------------------------------------------------------------

@router.get("/stock")
def stock_actual(cliente: str = Query("", max_length=40)):
    store = get_store()
    entradas, salidas, cli = _todo(store)
    palets = [p for p in R.stock(entradas, salidas) if not cliente or p["cliente"] == cliente]
    hoy = dt.datetime.now(MADRID).date().isoformat()
    grupos: dict[tuple, dict] = {}
    for p in palets:
        g = grupos.setdefault((p["cliente"], p["ref"]), {"cliente": p["cliente"], "cliente_nombre": p["cliente_nombre"], "ref": p["ref"],
                              "descripcion": p["descripcion"], "palets": 0, "kg": 0, "desde": p["fecha_entrada"]})
        g["palets"] += 1
        g["kg"] += p["kg"] or 0
        g["desde"] = min(g["desde"], p["fecha_entrada"])
    for g in grupos.values():
        g["dias"] = R.dias_entre(g["desde"], hoy)
    return {"total_palets": len(palets), "total_kg": sum(p["kg"] or 0 for p in palets), "resumen": sorted(grupos.values(), key=lambda g: (g["cliente_nombre"], g["ref"])),
            "palets": palets}


@router.post("/libro")
def libro():
    """Rehace CONTROL ALMACEN BURRIANA.xlsx en SharePoint (por si se ha tocado a mano o falló un envío)."""
    entradas, salidas, cli = _todo(get_store())
    contenido = X.libro_control(entradas, salidas, cli)
    enviado = enviar_n8n({"evento": "libro", "correo": False, "asunto": "", "html": "", "archivos": [
        {"nombre": X.LIBRO, "carpeta": "", "correo": False, "b64": base64.b64encode(contenido).decode()}], "fotos": []})
    return {"enviado": enviado, "archivo": {"nombre": X.LIBRO, "b64": base64.b64encode(contenido).decode()}}


@router.get("/facturacion")
def facturacion(desde: str = Query(pattern=r"^\d{4}-\d{2}-\d{2}$"), hasta: str = Query(pattern=r"^\d{4}-\d{2}-\d{2}$")):
    """Preparación de la facturación en Odoo: movimientos del periodo convertidos en líneas (sin tarifas, sin importe)."""
    entradas, salidas, _ = _todo(get_store())
    return {"desde": desde, "hasta": hasta, "lineas": F.lineas_facturables(entradas, salidas, desde, hasta)}


# ---- correos del buzón agente.burriana@stinsa.com (los manda n8n) ------------------------------------------------------

PALABRAS_SALIDA = ("SALIDA", "RECOGER", "RECOGIDA", "RETIRAR", "RETIRADA", "PEDIDO DE CARGA")


def require_correo(x_clave: str = Header("")):
    """n8n manda la clave secreta del flujo en X-Clave (STOCK_CORREO_CLAVE, mínimo 8 caracteres). Sin clave configurada, no hay acceso."""
    clave = os.environ.get("STOCK_CORREO_CLAVE", "")
    if len(clave) >= 8 and hmac.compare_digest(x_clave.encode(), clave.encode()):
        return
    raise AppError("No autorizado.", 401, "codigo_invalido")


class Adjunto(BaseModel):
    nombre: str = Field("", max_length=200)
    tipo: str = Field("", max_length=100)
    archivo_b64: str = Field(max_length=6_000_000)


class CorreoIn(BaseModel):
    mensaje_id: str = Field("", max_length=400)      # id del correo: si n8n repite el envío, no se duplica la operación
    de: str = Field("", max_length=200)
    asunto: str = Field("", max_length=300)
    cuerpo: str = Field("", max_length=8000)
    adjuntos: list[Adjunto] = Field([], max_length=8)


def _tipo_adjunto(a: Adjunto) -> str:
    n = a.nombre.lower()
    if n.endswith(".xlsx"):
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if n.endswith(".pdf"):
        return "application/pdf"
    if n.endswith(".png"):
        return "image/png"
    if n.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    return a.tipo


def _resultado(estado: str, **resto) -> dict:
    return {"resultado": estado, "para": destinatarios(), **resto}


@correo.post("/correo", dependencies=[Depends(require_correo)])
def correo_recibido(body: CorreoIn):
    """Un correo del cliente con su albarán (PDF, Excel o fotos): se prepara la ENTRADA (o la SALIDA, si el correo habla de
    recoger o retirar mercancía) para que la oficina la revise en la app. Nunca se cierra nada solo."""
    store = get_store()
    if body.mensaje_id:   # el mismo correo otra vez (n8n reintenta): devuelve lo ya creado
        for tipo in ("entrada", "salida"):
            previo = next((d for d in store.listar(tipo) if d.get("correo_id") == body.mensaje_id), None)
            if previo:
                return _resultado("creada", tipo=tipo, id=previo["id"], cliente=previo["cliente"]["nombre"], avisos=[], repetido=True)
    adjuntos = []
    for a in body.adjuntos:
        tipo = _tipo_adjunto(a)
        if tipo == "application/pdf" or tipo.startswith("image/") or L.es_excel(b"", tipo):
            adjuntos.append(PA.Archivo(archivo_b64=a.archivo_b64, tipo=tipo))
    if not adjuntos:
        return _resultado("no_procesada", motivo="El correo no trae ningún PDF, Excel ni foto con el albarán o la petición.")
    texto = " " + R.norm(body.asunto + " " + body.cuerpo) + " "
    es_salida = any(f" {w} " in texto for w in PALABRAS_SALIDA)
    try:
        todos = [PA._bytes(a) for a in adjuntos]
        if es_salida:
            prep = _preparar_salida(todos)
            if not prep["cliente_sugerido"]:
                return _resultado("no_procesada", tipo="salida", motivo="No reconozco al cliente de la petición de salida: cárgala a mano en la app.")
            p = prep["peticion"]
            if not p["lineas"]:
                return _resultado("no_procesada", tipo="salida", motivo="No he encontrado qué palets piden: cárgala a mano en la app.")
            op = salidas_crear(SalidaIn(cliente=prep["cliente_sugerido"], referencia=p["referencia"], destino=p["destino"], fecha_pedida=p["fecha"],
                                        lineas=[LineaPedido(ref=l["ref"], descripcion=l["descripcion"], palets=l["palets"]) for l in p["lineas"]],
                                        esperados=prep["esperados"], foto=adjuntos[0]))
            tipo, tabla = "salida", "salida"
            avisos = [a["texto"] for a in op["avisos"]]
        else:
            prep = _preparar_entrada(todos)
            if not prep["cliente_sugerido"]:
                return _resultado("no_procesada", tipo="entrada", motivo="No reconozco al cliente del albarán (o encaja con varios): cárgalo a mano en la app.",
                                  avisos=[a["texto"] for a in prep["avisos"]])
            op = entradas_crear(EntradaIn(cliente=prep["cliente_sugerido"], albaran=AlbaranIn(**prep["albaran"]), esperados=prep["esperados"],
                                          fecha_entrada=prep["fecha_entrada"], fotos=adjuntos[:MAX_FOTOS_ALBARAN]))
            tipo, tabla = "entrada", "entrada"
            avisos = [a["texto"] for a in prep["avisos"]]
    except AppError as e:   # albarán repetido, petición con palets que no hay, lectura fallida…: lo ve la oficina por correo
        return _resultado("no_procesada", tipo="salida" if es_salida else "entrada", motivo=e.message)
    doc = store.leer(tabla, op["id"])
    doc.update(origen="correo", revisada=False, correo_id=body.mensaje_id, correo_de=body.de, correo_asunto=body.asunto)
    store.escribir(tabla, op["id"], doc)
    log.info("%s %s preparada desde un correo de %s", tipo, op["id"], body.de)
    extra = {"albaran": prep["albaran"]["numero"], "palets": sum(l.get("palets") or 0 for l in prep["albaran"]["lineas"])} if tipo == "entrada" else {
        "pedido": prep["peticion"]["referencia"], "palets": sum(l.get("palets") or 0 for l in prep["peticion"]["lineas"])}
    return _resultado("creada", tipo=tipo, id=op["id"], cliente=doc["cliente"]["nombre"], avisos=avisos, **extra)
