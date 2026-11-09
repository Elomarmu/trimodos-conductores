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
import logging
import os

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..errors import AppError
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


def avisar(evento: str, datos: dict) -> None:
    """Aviso a la oficina (sevilla@stinsa.com) a través de n8n: pedido nuevo, punto de carga nuevo… Nunca para el viaje."""
    url = os.environ.get("PR_AVISOS_WEBHOOK", "")
    if not url:
        log.info("aviso PR sin webhook configurado: %s", evento)
        return
    try:
        httpx.post(url, json={"evento": evento, **datos}, timeout=8)
    except httpx.HTTPError:
        log.warning("No se ha podido enviar el aviso PR %s", evento)


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
def viajes(fecha: str = Query("", max_length=10)):
    return {"viajes": O.viajes_del_mes(odoo_pr(), O.mes_de(fecha))}


@router.post("/leer-tren")
def leer_tren(body: Archivos):
    leido = _leer(L.leer_tren, [_bytes(a) for a in body.archivos])
    tren = R.limpiar_tren(leido)
    log.info("tren leído n=%s cajas=%s", tren["numero_tren"], len(tren["cajas"]))
    return tren
