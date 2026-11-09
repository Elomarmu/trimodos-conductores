"""API de los viajes de PR. Se monta en main.py con el mismo código de acceso que el resto de la app.

Conductor:
  POST /api/pr/leer-albaran   {"archivo_b64", "tipo"}   → datos leídos + comprobación de matrículas en Flota
  POST /api/pr/registrar      {"viaje": {...}}          → línea en el pedido del mes + orden de transporte (PDF)
Oficina / almacén:
  GET  /api/pr/buscar?q=      nº de albarán, código Paper o bulto
  POST /api/pr/actualizar     {"bulto_id", "cambios": {...}}   (código Paper, peso, balas, contenedor, datos del tren)
  GET  /api/pr/viajes?fecha=  viajes del pedido del mes
  POST /api/pr/leer-tren      {"archivos": [...]}       → cajas del tren
Con DRY_RUN=true se lee de Odoo pero no se escribe nada.
"""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import html
import logging
import os

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..errors import AppError
from . import pr_excel as X
from . import pr_lector as L
from . import pr_odoo as O
from . import pr_reglas as R

log = logging.getLogger("pr")
router = APIRouter(prefix="/api/pr", tags=["pr"])
MAX_BYTES = 4_000_000  # Vercel admite cuerpos de 4,5 MB: el móvil reduce la foto antes de enviarla


class OdooPR:
    """JSON-RPC con lista blanca propia (pr_odoo.PERMITIDO). Empresa STINSA."""

    def __init__(self, s: Settings):
        if s.mode != "real":
            raise AppError("La app está en modo de prueba sin Odoo.", 503, "sin_odoo")
        self.s, self.uid = s, None
        self.http = httpx.Client(timeout=25)

    def _rpc(self, servicio, metodo, args):
        try:
            r = self.http.post(f"{self.s.odoo_url}/jsonrpc", json={"jsonrpc": "2.0", "method": "call", "id": 1,
                                                                   "params": {"service": servicio, "method": metodo, "args": args}})
            data = r.json()
        except (httpx.HTTPError, ValueError):
            raise AppError("No hay conexión con el sistema. Inténtalo de nuevo.", 503, "sin_conexion")
        if data.get("error"):
            log.warning("Odoo PR: %s", (data["error"].get("data") or {}).get("message") or data["error"].get("message"))
            raise AppError("El sistema no ha podido guardar el viaje. Avisa a la oficina.", 502, "odoo_error")
        return data.get("result")

    def __call__(self, modelo, metodo, args, **kw):
        if (modelo, metodo) not in O.PERMITIDO:
            raise PermissionError(f"{modelo}.{metodo}")
        if self.uid is None:
            self.uid = self._rpc("common", "authenticate", [self.s.odoo_db, self.s.odoo_user, self.s.odoo_password, {}])
            if not self.uid:
                raise AppError("El sistema no ha aceptado el acceso. Avisa a la oficina.", 502, "login_odoo")
        kw["context"] = {"allowed_company_ids": [R.EMPRESA_ID], "lang": "es_ES", "tz": "Europe/Madrid"}
        return self._rpc("object", "execute_kw", [self.s.odoo_db, self.uid, self.s.odoo_password, modelo, metodo, args, kw])


def odoo_pr():
    return OdooPR(get_settings())


def destinatarios() -> str:
    """A quién van los correos de PR. En pruebas, Carmelo; en producción, sevilla@stinsa.com (variable PR_CORREO_PARA)."""
    return os.environ.get("PR_CORREO_PARA", "carmelomartinez@grupotrimodos.com")


ASUNTOS = {"pedido_nuevo": "PR SAICA · pedido nuevo del mes {pedido} ({referencia})",
           "punto_nuevo": "PR SAICA · punto de carga nuevo: {punto}"}
TEXTOS = {"pedido_nuevo": "Se ha creado el pedido <b>{pedido}</b> «{referencia}» de SAICA PAPER con el primer viaje del mes.",
          "punto_nuevo": "Un conductor ha leído un albarán con un punto de carga que no está en la tabla: <b>{punto}</b> "
                         "(albarán {albaran}, {fecha}). El viaje no se ha guardado: hay que darlo de alta con su producto y precio."}


def enviar_n8n(datos: dict) -> bool:
    """n8n (PR_AVISOS_WEBHOOK) manda el correo y, si trae archivos, los sube a SharePoint. Nunca para el trabajo."""
    url = os.environ.get("PR_AVISOS_WEBHOOK", "")
    if not url:
        log.info("PR sin webhook de n8n configurado: %s", datos.get("evento"))
        return False
    try:
        r = httpx.post(url, json={"para": destinatarios(), **datos}, timeout=15,
                       headers={"X-Clave": os.environ.get("PR_AVISOS_CLAVE", "")})
        return r.status_code < 300
    except httpx.HTTPError:
        log.warning("No se ha podido enviar a n8n: %s", datos.get("evento"))
        return False


def avisar(evento: str, datos: dict) -> None:
    e = {k: html.escape(str(v or "")) for k, v in datos.items()}
    enviar_n8n({"evento": evento, "asunto": ASUNTOS[evento].format(**{k: v or "" for k, v in datos.items()}),
                "html": f"<p style='font:11pt Calibri,Arial'>{TEXTOS[evento].format(**e)}</p>", "archivos": []})


# ---- modelos de entrada -----------------------------------------------------------------------------------------

class Archivo(BaseModel):
    archivo_b64: str = Field(max_length=6_000_000)
    tipo: str = Field(max_length=40)


class Archivos(BaseModel):
    archivos: list[Archivo] = Field(min_length=1, max_length=4)


class ViajeIn(BaseModel):
    viaje: dict


class Actualizar(BaseModel):
    bulto_id: int
    cambios: dict


def _bytes(a: Archivo) -> tuple[bytes, str]:
    try:
        datos = base64.b64decode(a.archivo_b64.split(",")[-1], validate=True)
    except (binascii.Error, ValueError):
        raise AppError("El archivo no ha llegado bien. Vuelve a intentarlo.", 400, "archivo_invalido")
    if not datos or len(datos) > MAX_BYTES:
        raise AppError("El archivo es demasiado grande. Haz la foto de nuevo.", 400, "archivo_grande")
    return datos, a.tipo


def _leer(func, *args):
    try:
        return func(*args)
    except L.LecturaError as e:
        raise AppError(str(e), 502, "lectura_fallo")


def _pr(func, *args):
    try:
        return func(*args)
    except O.ErrorPR as e:
        raise AppError(str(e), 409, "pr_dato")


# ---- conductor ----------------------------------------------------------------------------------------------------

@router.post("/leer-albaran")
def leer_albaran(body: Archivo):
    datos, tipo = _bytes(body)
    leido = _leer(L.leer_albaran, datos, tipo)
    viaje = R.limpiar(leido)
    viaje.avisos += [f"Duda al leer: {d}" for d in leido.get("dudas") or []]
    res = {"viaje": viaje.a_dict(), "puntos": [{"clave": p.clave, "nombre": p.nombre} for p in R.PUNTOS_CARGA]}
    if get_settings().mode == "real":
        res["vehiculos"] = O.comprobar_vehiculos(odoo_pr(), viaje.tractora, viaje.remolque)
    if not viaje.punto:
        avisar("punto_nuevo", {"punto": viaje.punto_texto, "albaran": viaje.albaran, "fecha": viaje.fecha})
    log.info("albarán leído tipo=%s punto=%s pendientes=%s", viaje.tipo_albaran,
             viaje.punto.clave if viaje.punto else None, viaje.pendientes)
    return res


@router.post("/comprobar-vehiculos")
def comprobar_vehiculos(tractora: str = Query("", max_length=12), remolque: str = Query("", max_length=12)):
    return O.comprobar_vehiculos(odoo_pr(), R.matricula(tractora), R.matricula(remolque))


@router.post("/registrar")
def registrar(body: ViajeIn):
    s = get_settings()
    v = dict(body.viaje)
    v["tractora"], v["remolque"] = R.matricula(v.get("tractora")), R.matricula(v.get("remolque"))
    v["codigo_paper"] = R.codigo_paper(v.get("codigo_paper"))
    v["peso_kg"] = R.kilos(v.get("peso_kg"))
    if s.dry_run:
        odoo = odoo_pr()
        pedido = O.buscar_pedido_mes(odoo, v.get("fecha") or "")
        return {"simulado": True, "pedido": pedido and pedido["name"], "pedido_nuevo": pedido is None,
                "duplicado": bool(O.ya_registrado(odoo, v.get("albaran"))),
                "vehiculos": O.comprobar_vehiculos(odoo, v["tractora"], v["remolque"])}
    res = _pr(O.registrar, odoo_pr(), v)
    if res.get("pedido_nuevo"):
        avisar("pedido_nuevo", {"pedido": res["pedido"], "referencia": R.referencia_pedido(v["fecha"])})
    from ..main import get_odoo  # imprimir la orden con el cliente general (sesión web)
    cli = get_odoo()
    if not res["duplicado"]:
        try:
            cli.post_note(res["tarea_id"], "<p>Viaje de PR registrado desde la app de conductores (foto del albarán).</p>")
        except Exception:  # la nota es informativa: nunca debe parar el viaje
            log.warning("no se pudo dejar la nota en la tarea %s", res["tarea_id"])
    adj = cli.print_order(res["tarea_id"], R.EMPRESA_ID) if res.get("tarea_id") else None
    if adj:
        res["pdf"] = {"id": adj["id"], "token": adj.get("access_token"), "nombre": adj.get("name")}
    log.info("PR registrado tarea=%s bulto=%s duplicado=%s", res.get("tarea_id"), res.get("bulto"), res["duplicado"])
    return res


# ---- oficina / almacén ----------------------------------------------------------------------------------------------

@router.get("/buscar")
def buscar(q: str = Query("", max_length=40)):
    p = O.buscar_viaje(odoo_pr(), q)
    if not p:
        raise AppError("No encuentro ese viaje.", 404, "no_encontrado")
    return {"bulto": p, "datos": R.leer_observaciones(p.get("unload_note"))}


@router.post("/actualizar")
def actualizar(body: Actualizar):
    s = get_settings()
    odoo = odoo_pr()
    ps = odoo("tms.package", "search_read", [[["id", "=", body.bulto_id], ["partner_id", "=", R.CLIENTE_ID]]],
              fields=["name", "task_ids", "sale_line_ids", "unload_note", "shipping_origin_id"])
    if not ps:
        raise AppError("No encuentro ese viaje.", 404, "no_encontrado")
    if s.dry_run:
        return {"simulado": True, "bulto": ps[0]["name"]}
    hechos = _pr(O.actualizar, odoo, ps[0], body.cambios)
    res = {"bulto": ps[0]["name"], "cambiado": hechos}
    if ps[0].get("task_ids") and set(hechos) & {"código Paper", "peso", "balas", "contenedor"}:
        from ..main import get_odoo
        adj = get_odoo().print_order(ps[0]["task_ids"][0], R.EMPRESA_ID)
        res["pdf"] = {"id": adj["id"], "token": adj.get("access_token"), "nombre": adj.get("name")}
    return res


@router.get("/viajes")
def viajes(desde: str = Query("", max_length=10)):
    lista = O.viajes(odoo_pr(), desde or None)
    return {"viajes": lista, "siguiente_tren": O.siguiente_tren(lista)}


class CajaTren(BaseModel):
    orden: int | None = None
    vagon: str = ""
    segunda: bool = False
    caja_movil: str
    tara_kg: int | None = None
    bulto_id: int | None = None
    comprobaciones: dict = {}


class TrenIn(BaseModel):
    tren: int
    convoy: str = Field("", max_length=20)
    fecha: str = Field(max_length=10)
    cajas: list[CajaTren] = Field(max_length=60)


def tabla_html(cab: list[str], filas: list[list]) -> str:
    th = "".join(f"<th style='padding:4px 8px;background:#1F4E79;color:#fff;text-align:left'>{html.escape(c)}</th>" for c in cab)
    tr = "".join("<tr>" + "".join(f"<td style='padding:3px 8px;border-bottom:1px solid #ddd'>{html.escape(str(v if v is not None else ''))}</td>"
                                  for v in f) + "</tr>" for f in filas)
    return f"<table style='border-collapse:collapse;font:10pt Calibri,Arial'><tr>{th}</tr>{tr}</table>" if filas else "<p>Ninguno.</p>"


@router.post("/tren/guardar")
def guardar_tren(body: TrenIn):
    s = get_settings()
    odoo = odoo_pr()
    cajas = [c.model_dump() for c in body.cajas]
    if s.dry_run:
        hechos = [str(c["bulto_id"]) for c in cajas if c.get("bulto_id")]
    else:
        hechos = _pr(O.guardar_tren, odoo, body.tren, body.convoy, body.fecha, cajas)
    lista = O.viajes(odoo)
    por_bulto = {v["bulto_id"]: v for v in lista}
    nombre_tren = f"CARTON PAJA TREN {body.tren}.xlsx"
    archivos = [{"nombre": nombre_tren, "b64": base64.b64encode(X.carton_paja_tren(body.tren, body.convoy, body.fecha, cajas, por_bulto)).decode()},
                {"nombre": "REGISTRO VIAJES PR.xlsx", "b64": base64.b64encode(X.registro(lista)).decode()}]
    stock = [v for v in lista if v["estado"] == "En stock"]
    cargados = [por_bulto.get(c["bulto_id"]) for c in cajas if c.get("bulto_id")]
    cuerpo = (f"<p style='font:11pt Calibri,Arial'><b>Tren {body.tren}</b> (convoy {html.escape(body.convoy)}, "
              f"{html.escape(body.fecha)}): {len(hechos)} viajes cargados en {len(cajas)} cajas.</p>"
              + tabla_html(["Vagón", "Caja", "Albarán", "Lugar", "Balas", "Peso kg"],
                           [[c["vagon"], c["caja_movil"], (por_bulto.get(c["bulto_id"]) or {}).get("albaran"),
                             (por_bulto.get(c["bulto_id"]) or {}).get("lugar"), (por_bulto.get(c["bulto_id"]) or {}).get("bultos"),
                             (por_bulto.get(c["bulto_id"]) or {}).get("peso_kg")] for c in cajas if c.get("bulto_id")])
              + f"<p style='font:11pt Calibri,Arial'><b>Quedan en stock para el siguiente tren: {len(stock)}</b></p>"
              + tabla_html(["Fecha", "Albarán", "Lugar", "Tractora"], [[v["fecha"], v["albaran"], v["lugar"], v["tractora"]] for v in stock]))
    enviado = False if s.dry_run else enviar_n8n({"evento": "tren", "asunto": f"PR SAICA · Tren {body.tren} ({len(cargados)} viajes)",
                                                  "html": cuerpo, "archivos": archivos})
    log.info("tren %s guardado: %s viajes, %s en stock", body.tren, len(hechos), len(stock))
    return {"simulado": s.dry_run, "bultos": hechos, "en_stock": len(stock), "enviado": enviado, "archivos": archivos}


@router.get("/resumen")
def resumen(enviar: bool = False):
    """Para n8n por la mañana: novedades de las últimas 24 h, viajes con datos pendientes, stock y la Excel de registro."""
    lista = O.viajes(odoo_pr())
    ayer = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    nuevos = [v for v in lista if v["fecha"] >= ayer]
    faltan = [v for v in lista if not (v["codigo_paper"] and v["peso_kg"] and v["bultos"])]
    stock = [v for v in lista if v["estado"] == "En stock"]
    cuerpo = ("<p style='font:11pt Calibri,Arial'><b>Viajes de PR de SAICA · resumen</b></p>"
              + "<h3 style='font:bold 11pt Calibri,Arial'>Viajes cargados desde ayer</h3>"
              + tabla_html(["Fecha", "Pedido", "Albarán", "Lugar", "Tractora", "Transportista", "Peso kg"],
                           [[v["fecha"], v["pedido"], v["albaran"], v["lugar"], v["tractora"], v["colaborador"], v["peso_kg"]] for v in nuevos])
              + "<h3 style='font:bold 11pt Calibri,Arial'>Viajes con datos pendientes</h3>"
              + tabla_html(["Fecha", "Albarán", "Lugar", "Falta"],
                           [[v["fecha"], v["albaran"], v["lugar"], ", ".join(t for k, t in (("codigo_paper", "código Paper"), ("peso_kg", "peso"),
                                                                                         ("bultos", "balas")) if not v[k])] for v in faltan])
              + f"<h3 style='font:bold 11pt Calibri,Arial'>En stock esperando tren: {len(stock)}</h3>"
              + tabla_html(["Fecha", "Albarán", "Lugar"], [[v["fecha"], v["albaran"], v["lugar"]] for v in stock]))
    archivos = [{"nombre": "REGISTRO VIAJES PR.xlsx", "b64": base64.b64encode(X.registro(lista)).decode()}]
    datos = {"evento": "resumen", "asunto": f"PR SAICA · resumen {dt.date.today():%d/%m/%Y}", "html": cuerpo, "archivos": archivos}
    if enviar:
        datos["enviado"] = enviar_n8n(datos)
    return {"para": destinatarios(), **datos}


@router.post("/leer-tren")
def leer_tren(body: Archivos):
    leido = _leer(L.leer_tren, [_bytes(a) for a in body.archivos])
    tren = R.limpiar_tren(leido)
    log.info("tren leído n=%s cajas=%s", tren["numero_tren"], len(tren["cajas"]))
    return tren
