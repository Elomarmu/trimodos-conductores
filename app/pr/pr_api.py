"""API de los viajes de PR. Se monta en main.py con el mismo código de acceso que el resto de la app.

Conductor y oficina:
  POST /api/pr/leer-albaran         foto del albarán → datos leídos + comprobación de matrículas en Flota
  POST /api/pr/comprobar-vehiculos  ?tractora=&remolque=
  POST /api/pr/registrar            {"viaje", "foto"} → línea en el pedido del mes + orden de transporte (PDF)
Oficina:
  GET  /api/pr/buscar?q=            nº de albarán, código Paper o bulto
  POST /api/pr/actualizar           {"bulto_id", "cambios", "foto"} (código Paper, peso, balas, contenedor)
  POST /api/pr/leer-tren            listado del tren → cajas
  POST /api/pr/tren/composicion     guarda la composición (avisa si es repetida; «confirmado» para seguir)
  POST /api/pr/tren/enviar          las dos Excel + correo con fotos (n8n)
Oficina y almacén:
  GET  /api/pr/tren/actual · POST /api/pr/tren/asignar · POST /api/pr/tren/quitar
n8n:
  GET  /api/pr/resumen              resumen de la mañana con la Excel de registro
Las fotos y las Excel van a SharePoint y los correos salen por n8n (PR_AVISOS_WEBHOOK).
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
        r = httpx.post(url, json={"para": destinatarios(), **datos}, timeout=40,
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
    foto: Archivo | None = None      # la foto del albarán (se guarda en Odoo y luego en SharePoint)


class Actualizar(BaseModel):
    bulto_id: int
    cambios: dict
    foto: Archivo | None = None      # foto nueva del albarán (la oficina al modificar la carga)


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
        try:
            res["vehiculos"] = O.comprobar_vehiculos(odoo_pr(), viaje.tractora, viaje.remolque)
        except AppError:   # sin conexión con Odoo: los datos leídos se enseñan igual; se comprueba al guardar
            res["vehiculos"] = None
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
    odoo = odoo_pr()
    res = _pr(O.registrar, odoo, v)
    if body.foto and not res["duplicado"]:
        try:
            datos, tipo = _bytes(body.foto)
            O.guardar_foto(odoo, res["tarea_id"], v.get("albaran"), base64.b64encode(datos).decode(), tipo)
        except Exception:  # la foto nunca debe impedir el viaje
            log.warning("no se pudo guardar la foto del albarán en la tarea %s", res["tarea_id"])
    if res.get("pedido_nuevo"):
        avisar("pedido_nuevo", {"pedido": res["pedido"], "referencia": R.referencia_pedido(v["fecha"])})
    from ..main import get_odoo  # nota en la tarea con el cliente general
    cli = get_odoo()
    if not res["duplicado"]:
        try:
            cli.post_note(res["tarea_id"], "<p>Viaje de PR registrado desde la app de conductores (foto del albarán).</p>")
        except Exception:  # la nota es informativa: nunca debe parar el viaje
            log.warning("no se pudo dejar la nota en la tarea %s", res["tarea_id"])
    imprimir(res.get("tarea_id"), res)
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
    hechos = _pr(O.actualizar, odoo, ps[0], body.cambios) if body.cambios else []
    tarea = (ps[0].get("task_ids") or [None])[0]
    if body.foto and tarea:
        datos, tipo = _bytes(body.foto)
        O.guardar_foto(odoo, tarea, R.leer_observaciones(ps[0].get("unload_note")).get("albaran"), base64.b64encode(datos).decode(), tipo)
        hechos.append("foto del albarán")
    if not hechos:
        raise AppError("No has cambiado nada.", 400, "sin_cambios")
    viaje = next((v for v in O.viajes(odoo) if v["bulto_id"] == body.bulto_id), None)
    if viaje and viaje.get("caja_movil") and viaje.get("tren"):   # ya va en un tren: su foto en SharePoint se pone al día
        sincronizar_foto(odoo, viaje, foto_nueva=bool(body.foto))
    res = {"bulto": ps[0]["name"], "cambiado": hechos}
    if tarea and set(hechos) & {"código Paper", "peso", "balas", "contenedor"}:
        imprimir(tarea, res)
    return res


def imprimir(tarea_id: int | None, res: dict) -> dict | None:
    """Genera la orden de transporte en PDF. Si falla, lo guardado ya está guardado: se avisa y se sigue."""
    if not tarea_id:
        return None
    from ..main import get_odoo  # imprimir con el cliente general (sesión web)
    try:
        adj = get_odoo().print_order(tarea_id, R.EMPRESA_ID)
    except AppError:
        res["aviso_pdf"] = "Los datos están guardados, pero no se ha podido generar la orden en PDF. Inténtalo más tarde."
        return None
    res["pdf"] = {"id": adj["id"], "token": adj.get("access_token"), "nombre": adj.get("name")}
    return adj


# ---- foto del albarán en SharePoint › Automatizacion › CARTON PAJA TREN <n> (lo hace n8n) --------------------------

def sincronizar_foto(odoo, viaje: dict, foto_nueva: bool = False, lista: list[dict] | None = None) -> None:
    """Deja la foto del viaje en la carpeta de su tren con el nombre «código Paper - caja móvil»: la sube si no estaba,
    la renombra si ha cambiado el código, o la sustituye si la oficina ha hecho una foto nueva. Nunca para el trabajo."""
    try:
        carpeta = R.carpeta_tren(viaje["tren"])
        lista = lista if lista is not None else O.viajes(odoo)
        actual = viaje.get("foto") or ""                      # «carpeta/nombre» si ya está en SharePoint
        ocupados = {v["foto"].split("/", 1)[-1] for v in lista
                    if v.get("foto", "").startswith(carpeta + "/") and v["bulto_id"] != viaje["bulto_id"]}
        foto = leer_foto_o_nada(odoo, viaje)
        ext = R.extension(foto["tipo"]) if foto else (("." + actual.rsplit(".", 1)[-1]) if "." in actual else ".jpg")
        nombre = R.nombre_foto(viaje.get("codigo_paper"), viaje.get("albaran"), viaje["caja_movil"], ext, ocupados)
        destino = f"{carpeta}/{nombre}"
        if actual and actual.startswith(carpeta + "/") and not foto_nueva:
            if actual != destino:
                enviar_n8n({"evento": "foto_renombrar", "correo": False, "carpeta": carpeta,
                            "viejo": actual.split("/", 1)[1], "nuevo": nombre})
                O.apuntar_foto(odoo, viaje["bulto_id"], destino)
            return
        if not foto:
            return                                              # viaje sin foto guardada (anterior a este cambio)
        if actual and actual != destino:
            borrar_foto(actual)
        enviar_n8n({"evento": "foto_subir", "correo": False, "carpeta": carpeta, "nombre": nombre, "tipo": foto["tipo"],
                    "b64": foto["b64"]})
        O.apuntar_foto(odoo, viaje["bulto_id"], destino)
    except Exception:
        log.exception("no se pudo poner al día la foto del bulto %s en SharePoint", viaje.get("bulto"))


def leer_foto_o_nada(odoo, viaje: dict) -> dict | None:
    try:
        return O.leer_foto(odoo, viaje.get("tarea_id"))
    except Exception:
        return None


def borrar_foto(ruta: str) -> None:
    if ruta and "/" in ruta:
        carpeta, nombre = ruta.split("/", 1)
        enviar_n8n({"evento": "foto_borrar", "correo": False, "carpeta": carpeta, "nombre": nombre})


@router.get("/viajes")
def viajes(desde: str = Query("", max_length=10)):
    lista = O.viajes(odoo_pr(), desde or None)
    return {"viajes": lista, "siguiente_tren": O.siguiente_tren(lista)}


class CajaTren(BaseModel):
    orden: int | None = None
    vagon: str = ""
    segunda: bool = False
    caja_movil: str = Field(min_length=1, max_length=20)
    tara_kg: int | None = None


class TrenIn(BaseModel):
    confirmado: bool = False         # ya ha visto el aviso de composición repetida y sigue
    tren: int
    convoy: str = Field("", max_length=20)
    fecha: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    cajas: list[CajaTren] = Field(min_length=1, max_length=60)


class Asignar(BaseModel):
    bulto_id: int
    caja_movil: str = Field("", max_length=20)


def tabla_html(cab: list[str], filas: list[list]) -> str:
    th = "".join(f"<th style='padding:4px 8px;background:#1F4E79;color:#fff;text-align:left'>{html.escape(c)}</th>" for c in cab)
    tr = "".join("<tr>" + "".join(f"<td style='padding:3px 8px;border-bottom:1px solid #ddd'>{html.escape(str(v if v is not None else ''))}</td>"
                                  for v in f) + "</tr>" for f in filas)
    return f"<table style='border-collapse:collapse;font:10pt Calibri,Arial'><tr>{th}</tr>{tr}</table>" if filas else "<p>Ninguno.</p>"


@router.post("/tren/composicion")
def tren_composicion(body: TrenIn):
    """Oficina: guarda la composición del tren (vagones, cajas, taras) para que el almacén asigne las cargas."""
    odoo = odoo_pr()
    cajas = [c.model_dump() for c in body.cajas]
    if not body.confirmado:
        repetida = O.composiciones_repetidas(odoo, body.tren, body.convoy, cajas)
        if repetida:   # la app lo enseña y pide volver a confirmar
            raise AppError(" ".join(repetida) + " ¿Quieres guardarla igualmente?", 409, "tren_repetido")
    if get_settings().dry_run:
        return {"simulado": True, "tren": body.tren}
    datos = _pr(O.guardar_composicion, odoo, body.tren, body.convoy, body.fecha, cajas)
    # El libro CARTON PAJA TREN <n> (cajas aún vacías + hoja COMPOSICION) a SharePoint › Automatizacion (n8n), sin correo.
    # Al terminar el tren se vuelve a subir con las cargas.
    nombre = f"CARTON PAJA TREN {datos['tren']}.xlsx"
    libro = X.carton_paja_tren(datos["tren"], body.convoy, body.fecha, datos["cajas"], {})
    enviado = enviar_n8n({"evento": "composicion", "correo": False, "asunto": "", "html": "", "archivos": [
        {"nombre": nombre, "b64": base64.b64encode(libro).decode()}]})
    log.info("composición del tren %s guardada: %s cajas (SharePoint: %s)", body.tren, len(datos["cajas"]), enviado)
    return {"tren": datos["tren"], "cajas": len(datos["cajas"]), "excel": nombre, "enviado": enviado}


def _tren(odoo):
    t = O.tren_actual(odoo)
    if not t:
        raise AppError("La oficina todavía no ha cargado ningún tren.", 404, "sin_tren")
    return t


@router.get("/tren/actual")
def tren_actual():
    """Oficina y almacén: tren cargado, cajas con su carga y cargas en stock."""
    odoo = odoo_pr()
    return O.estado_tren(odoo, _tren(odoo), O.viajes(odoo))


@router.post("/tren/asignar")
def tren_asignar(body: Asignar):
    odoo = odoo_pr()
    t, lista = _tren(odoo), O.viajes(odoo)
    if get_settings().dry_run:
        return {"simulado": True}
    bulto = _pr(O.asignar, odoo, t, lista, body.bulto_id, body.caja_movil)
    log.info("tren %s: %s → caja %s", t["tren"], bulto, body.caja_movil)
    lista = O.viajes(odoo)
    viaje = next((v for v in lista if v["bulto_id"] == body.bulto_id), None)
    if viaje:
        sincronizar_foto(odoo, viaje, lista=lista)
        lista = O.viajes(odoo)
    return O.estado_tren(odoo, t, lista)


@router.post("/tren/quitar")
def tren_quitar(body: Asignar):
    odoo = odoo_pr()
    t = _tren(odoo)
    if get_settings().dry_run:
        return {"simulado": True}
    previo = next((v for v in O.viajes(odoo) if v["bulto_id"] == body.bulto_id), None)
    _pr(O.quitar, odoo, body.bulto_id)
    if previo and previo.get("foto"):
        borrar_foto(previo["foto"])
    return O.estado_tren(odoo, t, O.viajes(odoo))


@router.post("/tren/enviar")
def tren_enviar():
    """Tren terminado: las dos Excel (descarga, SharePoint y correo por n8n)."""
    s = get_settings()
    odoo = odoo_pr()
    t, lista = _tren(odoo), O.viajes(odoo)
    por_caja = {v["caja_movil"]: v for v in lista if v.get("tren") == str(t["tren"]) and v.get("caja_movil")}
    archivos = [{"nombre": f"CARTON PAJA TREN {t['tren']}.xlsx",
                 "b64": base64.b64encode(X.carton_paja_tren(t["tren"], t.get("convoy", ""), t["fecha"], t["cajas"], por_caja)).decode()},
                {"nombre": "REGISTRO VIAJES PR.xlsx", "b64": base64.b64encode(X.registro(lista)).decode()}]
    stock = [v for v in lista if v["estado"] == "En stock"]
    fecha_es = html.escape("-".join(reversed(str(t["fecha"]).split("-"))))
    cuerpo = (f"<p style='font:11pt Calibri,Arial'><b>Tren {int(t['tren'])}</b> (convoy {html.escape(str(t.get('convoy') or ''))}, "
              f"{fecha_es}): {len(por_caja)} cargas en {len(t['cajas'])} cajas.</p>"
              + tabla_html(["Vagón", "Caja", "Código Paper", "Albarán", "Lugar", "Balas", "Peso kg"],
                           [[c["vagon"], c["caja_movil"], v.get("codigo_paper"), v.get("albaran"), v.get("lugar"), v.get("bultos"),
                             v.get("peso_kg")] for c in t["cajas"] if (v := por_caja.get(c["caja_movil"]))])
              + f"<p style='font:11pt Calibri,Arial'><b>Quedan en stock para el siguiente tren: {len(stock)}</b></p>"
              + tabla_html(["Fecha", "Código Paper", "Albarán", "Lugar"], [[v["fecha"], v["codigo_paper"], v["albaran"], v["lugar"]] for v in stock]))
    fotos = []
    for c in t["cajas"]:
        v = por_caja.get(c["caja_movil"])
        f = leer_foto_o_nada(odoo, v) if v else None
        if f:
            nombre = (v.get("foto") or "").split("/", 1)[-1] or R.nombre_foto(v.get("codigo_paper"), v.get("albaran"),
                                                                              c["caja_movil"], R.extension(f["tipo"]), set())
            fotos.append({"nombre": nombre, "b64": f["b64"], "tipo": f["tipo"]})
    cuerpo += (f"<p style='font:11pt Calibri,Arial'>Van adjuntas las {len(fotos)} fotos de los albaranes "
               f"(también en SharePoint › Automatizacion › {R.carpeta_tren(t['tren'])}).</p>")
    enviado = False if s.dry_run else enviar_n8n({"evento": "tren", "asunto": f"PR SAICA · Tren {t['tren']} del {fecha_es} ({len(por_caja)} cargas)",
                                                  "html": cuerpo, "archivos": archivos, "fotos": fotos,
                                                  "carpeta": R.carpeta_tren(t["tren"])})
    log.info("tren %s enviado: %s cargas, %s en stock", t["tren"], len(por_caja), len(stock))
    return {"simulado": s.dry_run, "tren": t["tren"], "cargas": len(por_caja), "en_stock": len(stock), "enviado": enviado, "archivos": archivos}


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
