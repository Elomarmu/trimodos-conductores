"""API de los viajes de PR. Se monta en main.py con el mismo código de acceso que el resto de la app.

Conductor y oficina:
  POST /api/pr/leer-albaran         foto del albarán → datos leídos + comprobación de matrículas en Flota
  POST /api/pr/comprobar-vehiculos  ?tractora=&remolque=
  POST /api/pr/registrar            {"viaje", "foto", "di"} → línea en el pedido del mes + orden de transporte (PDF)
Oficina:
  GET  /api/pr/buscar?q=            nº de albarán, código Paper o bulto (con los documentos que tiene)
  GET  /api/pr/pendientes           cargas a las que les falta algún dato o documento
  POST /api/pr/actualizar           {"bulto_id", "cambios", "foto", "di"} (código Paper, peso, balas, contenedor, documentos)
  POST /api/pr/leer-tren            listado del tren → cajas
  POST /api/pr/tren/composicion     guarda la composición (avisa si es repetida; «confirmado» para seguir)
  POST /api/pr/tren/enviar          las dos Excel + correo con fotos (n8n)
  POST /api/pr/bobinas/leer         capturas del «Detalle convoy» de SAICA → cajas y total por clase de bobina
  POST /api/pr/bobinas/enviar       Excel de bobinas por clase → descarga y correo a Sevilla (n8n)
Oficina y almacén:
  GET  /api/pr/tren/actual · POST /api/pr/tren/asignar · POST /api/pr/tren/quitar
  POST /api/pr/tren/mover {"vagon", "hacia": subir|bajar} · POST /api/pr/tren/editar {"caja_movil", "nueva_caja", "nuevo_vagon"}
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
import hmac
import os

import httpx
from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..errors import AppError
from . import pr_bobinas as B
from . import pr_excel as X
from . import pr_lector as L
from . import pr_odoo as O
from . import pr_reglas as R

log = logging.getLogger("pr")
router = APIRouter(prefix="/api/pr", tags=["pr"])             # conductor: leer el albarán y registrar el viaje
oficina = APIRouter(prefix="/api/pr", tags=["pr-oficina"])    # oficina y almacén (main.py exige su código)
n8n = APIRouter(prefix="/api/pr", tags=["pr-n8n"])           # n8n: resumen de la mañana (con la clave del flujo)
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


def subir_registro(odoo, lista: list[dict] | None = None) -> None:
    """Rehace REGISTRO VIAJES PR.xlsx desde Odoo y lo sube a SharePoint › Automatizacion (n8n, sin correo).
    Se llama cada vez que se da de alta o cambia una carga. Nunca para el trabajo."""
    try:
        lista = lista if lista is not None else O.viajes(odoo)
        enviar_n8n({"evento": "registro", "correo": False, "asunto": "", "html": "", "archivos": [
            {"nombre": "REGISTRO VIAJES PR.xlsx", "b64": base64.b64encode(X.registro(lista)).decode()}]})
    except Exception:
        log.exception("no se pudo poner al día REGISTRO VIAJES PR en SharePoint")


def avisar(evento: str, datos: dict, enlaces: list[tuple[str, str]] = ()) -> None:
    """Correo corto de aviso; «enlaces» = botones (texto, url) debajo del texto, p. ej. al pedido en Odoo."""
    e = {k: html.escape(str(v or "")) for k, v in datos.items()}
    botones_html = "".join(boton(t, u) for t, u in enlaces)
    enviar_n8n({"evento": evento, "asunto": ASUNTOS[evento].format(**{k: v or "" for k, v in datos.items()}),
                "html": f"<p style='font:11pt Calibri,Arial'>{TEXTOS[evento].format(**e)}</p>"
                        + (f"<p>{botones_html}</p>" if botones_html else ""), "archivos": []})


# ---- modelos de entrada -----------------------------------------------------------------------------------------

class Archivo(BaseModel):
    archivo_b64: str = Field(max_length=6_000_000)
    tipo: str = Field(max_length=40)


class Archivos(BaseModel):
    archivos: list[Archivo] = Field(min_length=1, max_length=4)


class ViajeIn(BaseModel):
    viaje: dict
    foto: Archivo | None = None      # la foto del albarán (se guarda en Odoo y luego en SharePoint)
    di: Archivo | None = None        # foto o PDF del DI (documento de identificación de residuos)


class Actualizar(BaseModel):
    bulto_id: int
    cambios: dict
    foto: Archivo | None = None      # foto nueva del albarán (la oficina al modificar la carga)
    di: Archivo | None = None        # DI de residuos nuevo


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
    res["documentos"] = {}
    for doc, archivo in (("albaran", body.foto), ("di", body.di)):
        if archivo and not res["duplicado"]:
            try:
                datos, tipo = _bytes(archivo)
                O.guardar_foto(odoo, res["tarea_id"], v.get("albaran"), base64.b64encode(datos).decode(), tipo, doc)
                res["documentos"][doc] = True
            except Exception:  # un documento nunca debe impedir el viaje
                log.warning("no se pudo guardar %s en la tarea %s", R.DOCUMENTOS[doc][3], res["tarea_id"])
    res["faltan_docs"] = [] if res["duplicado"] else [R.DOCUMENTOS[d][3] for d in R.DOCUMENTOS if not res["documentos"].get(d)]
    if res.get("pedido_nuevo"):
        avisar("pedido_nuevo", {"pedido": res["pedido"], "referencia": R.referencia_pedido(v["fecha"])},
               [(f"Pedido {res['pedido']}", enlace_odoo("sale.order", res.get("pedido_id"))),
                ("Primer viaje (orden de transporte)", enlace_odoo("project.task", res.get("tarea_id")))])
    from ..main import get_odoo  # nota en la tarea con el cliente general
    cli = get_odoo()
    if not res["duplicado"]:
        try:
            cli.post_note(res["tarea_id"], "<p>Viaje de PR registrado desde la app de conductores (foto del albarán).</p>")
        except Exception:  # la nota es informativa: nunca debe parar el viaje
            log.warning("no se pudo dejar la nota en la tarea %s", res["tarea_id"])
    imprimir(res.get("tarea_id"), res)
    if not res["duplicado"]:
        subir_registro(odoo)
    log.info("PR registrado tarea=%s bulto=%s duplicado=%s", res.get("tarea_id"), res.get("bulto"), res["duplicado"])
    return res


# ---- oficina / almacén ----------------------------------------------------------------------------------------------

@oficina.get("/buscar")
def buscar(q: str = Query("", max_length=40)):
    p = O.buscar_viaje(odoo_pr(), q)
    if not p:
        raise AppError("No encuentro ese viaje.", 404, "no_encontrado")
    odoo = odoo_pr()
    tarea = (p.get("task_ids") or [None])[0]
    return {"bulto": p, "datos": R.leer_observaciones(p.get("unload_note")),
            "documentos": O.documentos(odoo, [tarea]).get(tarea, {d: False for d in R.DOCUMENTOS})}


@oficina.post("/actualizar")
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
    nuevos = set()
    for doc, archivo in (("albaran", body.foto), ("di", body.di)):
        if archivo and tarea:
            datos, tipo = _bytes(archivo)
            O.guardar_foto(odoo, tarea, R.leer_observaciones(ps[0].get("unload_note")).get("albaran"), base64.b64encode(datos).decode(),
                           tipo, doc)
            hechos.append(R.DOCUMENTOS[doc][3])
            nuevos.add(doc)
    if not hechos:
        raise AppError("No has cambiado nada.", 400, "sin_cambios")
    viaje = next((v for v in O.viajes(odoo) if v["bulto_id"] == body.bulto_id), None)
    en_tren = None
    if viaje and viaje.get("caja_movil") and viaje.get("tren"):   # ya va en un tren: sus documentos en SharePoint al día
        sincronizar_foto(odoo, viaje, nuevos=nuevos)
        t = O.tren_actual(odoo)
        if t and str(t["tren"]) == str(viaje["tren"]):
            en_tren = t
    res = {"bulto": ps[0]["name"], "cambiado": hechos,
           "faltan_docs": [R.DOCUMENTOS[d][3] for d in R.DOCUMENTOS if viaje and viaje.get("doc_" + d) is False]}
    if tarea and set(hechos) & {"código Paper", "peso", "balas", "contenedor"}:
        imprimir(tarea, res)
    if en_tren:   # la Excel del tren lleva el código Paper, las balas y el peso de esta carga
        poner_al_dia(odoo, en_tren, O.viajes(odoo))
    else:
        subir_registro(odoo)
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

def sincronizar_foto(odoo, viaje: dict, nuevos: set[str] | frozenset = frozenset(), lista: list[dict] | None = None) -> None:
    """Deja los documentos del viaje (foto del albarán y DI de residuos) en la carpeta de su tren: «código Paper - caja móvil»
    y «código Paper - caja móvil - DI». Los sube si no estaban, los renombra si ha cambiado el código o la caja, o los
    sustituye si la oficina ha puesto uno nuevo («nuevos»). Nunca para el trabajo."""
    for doc in R.DOCUMENTOS:
        try:
            lista = lista if lista is not None else O.viajes(odoo)
            _sincronizar_doc(odoo, viaje, doc, doc in nuevos, lista)
        except Exception:
            log.exception("no se pudo poner al día %s del bulto %s en SharePoint", R.DOCUMENTOS[doc][3], viaje.get("bulto"))


def _sincronizar_doc(odoo, viaje: dict, doc: str, nuevo: bool, lista: list[dict]) -> None:
    _, clave, sufijo, _ = R.DOCUMENTOS[doc]
    carpeta = R.carpeta_tren(viaje["tren"])
    actual = viaje.get(clave) or ""                      # «carpeta/nombre» si ya está en SharePoint
    claves = [c for _, c, _, _ in R.DOCUMENTOS.values()]
    ocupados = {x[k].split("/", 1)[-1] for x in lista for k in claves
                if (x.get(k) or "").startswith(carpeta + "/") and not (x["bulto_id"] == viaje["bulto_id"] and k == clave)}
    foto = leer_foto_o_nada(odoo, viaje, doc)
    ext = R.extension(foto["tipo"]) if foto else (("." + actual.rsplit(".", 1)[-1]) if "." in actual else ".jpg")
    nombre = R.nombre_foto(viaje.get("codigo_paper"), viaje.get("albaran"), viaje["caja_movil"], ext, ocupados, sufijo)
    destino = f"{carpeta}/{nombre}"
    if actual and actual.startswith(carpeta + "/") and not nuevo:
        if actual != destino:
            enviar_n8n({"evento": "foto_renombrar", "correo": False, "carpeta": carpeta,
                        "viejo": actual.split("/", 1)[1], "nuevo": nombre})
            O.apuntar_foto(odoo, viaje["bulto_id"], destino, doc)
        return
    if not foto:
        return                                              # todavía no tiene ese documento
    if actual and actual != destino:
        borrar_foto(actual)
    enviar_n8n({"evento": "foto_subir", "correo": False, "carpeta": carpeta, "nombre": nombre, "tipo": foto["tipo"],
                "b64": foto["b64"]})
    O.apuntar_foto(odoo, viaje["bulto_id"], destino, doc)


def leer_foto_o_nada(odoo, viaje: dict, doc: str = "albaran") -> dict | None:
    try:
        return O.leer_foto(odoo, viaje.get("tarea_id"), doc)
    except Exception:
        return None


def borrar_foto(ruta: str) -> None:
    if ruta and "/" in ruta:
        carpeta, nombre = ruta.split("/", 1)
        enviar_n8n({"evento": "foto_borrar", "correo": False, "carpeta": carpeta, "nombre": nombre})


@oficina.get("/pendientes")
def pendientes():
    """Oficina: cargas a las que les falta algún dato o documento (foto del albarán, DI de residuos)."""
    lista = O.viajes(odoo_pr())
    return {"pendientes": [{k: v.get(k) for k in ("bulto_id", "bulto", "albaran", "codigo_paper", "fecha", "lugar", "estado", "tarea_id")}
                           | {"faltan": R.faltan(v)} for v in lista if R.faltan(v)]}


@oficina.get("/viajes")
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
    convoy: str = Field("", max_length=20)            # nº de convoy de entrada (el del listado de Captrain)
    convoy_salida: str = Field("", max_length=20)     # nº de convoy de salida (si ya se sabe; si no, después)
    fecha: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    cajas: list[CajaTren] = Field(min_length=1, max_length=60)


class Asignar(BaseModel):
    bulto_id: int = 0                # 0 con «vacia»: la caja va vacía
    caja_movil: str = Field("", max_length=20)
    vacia: bool = False


class Mover(BaseModel):
    vagon: str = Field(min_length=1, max_length=20)
    hacia: str = Field(pattern=r"^(subir|bajar)$")


class Editar(BaseModel):
    caja_movil: str = Field(min_length=1, max_length=20)
    nueva_caja: str = Field("", max_length=20)
    nuevo_vagon: str = Field("", max_length=20)


URL_EXCEL_TREN = "__URL_EXCEL_TREN__"   # n8n lo cambia por el enlace de la Excel del tren en SharePoint
LETRA = "font:11pt Calibri,Arial"


class Enlace(tuple):
    """Celda con enlace: Enlace((texto, url))."""


def enlace_odoo(modelo: str, ident: int | None) -> str:
    url = get_settings().odoo_url
    return f"{url}/web#id={int(ident)}&model={modelo}&view_type=form" if url and ident else ""


def _celda(v) -> str:
    if isinstance(v, Enlace):
        texto, url = v
        texto = html.escape(str(texto if texto not in (None, "") else "ver"))
        return f"<a href='{html.escape(url)}' style='color:#1F4E79'>{texto}</a>" if url else texto
    return html.escape(str(v if v is not None else ""))


def tabla_html(cab: list[str], filas: list[list]) -> str:
    th = "".join(f"<th style='padding:4px 8px;background:#1F4E79;color:#fff;text-align:left'>{html.escape(c)}</th>" for c in cab)
    tr = "".join("<tr>" + "".join(f"<td style='padding:3px 8px;border-bottom:1px solid #ddd'>{_celda(v)}</td>"
                                  for v in f) + "</tr>" for f in filas)
    return f"<table style='border-collapse:collapse;font:10pt Calibri,Arial'><tr>{th}</tr>{tr}</table>" if filas else f"<p style='{LETRA}'>Ninguno.</p>"


def boton(texto: str, url: str) -> str:
    if not url:
        return ""
    return (f"<a href='{html.escape(url)}' style='display:inline-block;margin:0 8px 8px 0;padding:8px 16px;background:#1F4E79;"
            f"color:#fff;text-decoration:none;border-radius:4px;font:bold 10pt Calibri,Arial'>{html.escape(texto)}</a>")


def botones(lista: list[dict], tren: dict | None) -> str:
    """Al principio del correo: un botón por pedido de PR que salga en el correo y otro a la Excel del tren."""
    pedidos = {}
    for v in lista:
        if v.get("pedido_id"):
            pedidos[v["pedido_id"]] = v["pedido"]
    res = "".join(boton(f"Pedido {nombre}", enlace_odoo("sale.order", ident)) for ident, nombre in pedidos.items())
    if tren:
        res += boton(f"Excel del tren {int(tren['tren'])}", URL_EXCEL_TREN)
    return f"<p>{res}</p>" if res else ""


def tarea(v: dict):
    """Celda «albarán» con enlace a la tarea del viaje en Odoo."""
    return Enlace((v.get("albaran") or v.get("bulto"), enlace_odoo("project.task", v.get("tarea_id"))))


def faltan(v: dict) -> str:
    return ", ".join(R.faltan(v))


def excel_tren(tren: dict | None) -> str:
    return f"CARTON PAJA TREN {int(tren['tren'])}.xlsx" if tren else ""


def ruta_excel_tren(tren: dict | None) -> str:
    """La Excel del tren va dentro de su carpeta, junto a las fotos: «CARTON PAJA TREN N/CARTON PAJA TREN N.xlsx»."""
    return f"{R.carpeta_tren(tren['tren'])}/{excel_tren(tren)}" if tren else ""


@oficina.post("/tren/composicion")
def tren_composicion(body: TrenIn):
    """Oficina: guarda la composición del tren (vagones, cajas, taras) para que el almacén asigne las cargas."""
    odoo = odoo_pr()
    cajas = [c.model_dump() for c in body.cajas]
    actual = O.tren_actual(odoo)
    if actual and int(actual["tren"]) != int(body.tren) and not actual.get("terminado"):
        raise AppError(f"El tren {actual['tren']} todavía no está terminado. Termínalo antes de cargar otra composición "
                       "(Oficina · PR › Asignar cargas a cajas › «Terminar el tren y enviar las Excel»).", 409, "tren_abierto")
    if not body.confirmado:
        repetida = O.composiciones_repetidas(odoo, body.tren, body.convoy, cajas)
        if repetida:   # la app lo enseña y pide volver a confirmar
            raise AppError(" ".join(repetida) + " ¿Quieres guardarla igualmente?", 409, "tren_repetido")
    if get_settings().dry_run:
        return {"simulado": True, "tren": body.tren}
    datos = _pr(O.guardar_composicion, odoo, body.tren, body.convoy, body.fecha, cajas, body.convoy_salida)
    # Si se vuelve a cargar la composición del MISMO tren, las cargas ya asignadas se mantienen; las que estaban en una caja
    # que ya no está en el tren vuelven a stock (y sus documentos salen de la carpeta del tren).
    lista = O.viajes(odoo)
    cajas_nuevas = {c["caja_movil"] for c in datos["cajas"]}
    a_stock = [v for v in lista if v.get("tren") == str(datos["tren"]) and v.get("caja_movil") and v["caja_movil"] not in cajas_nuevas]
    for v in a_stock:
        O.quitar(odoo, v["bulto_id"])
        for _, clave, _, _ in R.DOCUMENTOS.values():
            if v.get(clave):
                borrar_foto(v[clave])
    if a_stock:
        lista = O.viajes(odoo)
    # El libro CARTON PAJA TREN <n> (con las cargas que ya tenga + hoja COMPOSICION) a la carpeta del tren, sin correo.
    por_caja = {v["caja_movil"]: v for v in lista if v.get("tren") == str(datos["tren"]) and v.get("caja_movil")}
    enviado = subir_excel_tren(datos, por_caja)
    if a_stock:
        subir_registro(odoo, lista)
    log.info("composición del tren %s guardada: %s cajas, %s cargas se mantienen, %s vuelven a stock (SharePoint: %s)",
             body.tren, len(datos["cajas"]), len(por_caja), len(a_stock), enviado)
    return {"tren": datos["tren"], "cajas": len(datos["cajas"]), "excel": excel_tren(datos), "enviado": enviado,
            "mantenidas": len(por_caja), "a_stock": [v.get("codigo_paper") or v.get("albaran") for v in a_stock]}


def subir_excel_tren(t: dict, por_caja: dict) -> bool:
    """Sube (o sustituye) el libro CARTON PAJA TREN <n> en su carpeta de SharePoint, sin correo."""
    libro = X.carton_paja_tren(t["tren"], O.convoy_tren(t), t["fecha"], O.cajas_en_orden(t), por_caja, t.get("convoy") or "")
    return enviar_n8n({"evento": "composicion", "correo": False, "asunto": "", "html": "", "archivos": [
        {"nombre": excel_tren(t), "carpeta": R.carpeta_tren(t["tren"]), "b64": base64.b64encode(libro).decode()}]})


class Salida(BaseModel):
    convoy_salida: str = Field("", max_length=20)


@oficina.post("/tren/salida")
def tren_salida(body: Salida):
    """Oficina: nº de convoy de salida del tren (no es el mismo que el de entrada del listado)."""
    odoo = odoo_pr()
    t, lista = _tren(odoo, abierto=True), O.viajes(odoo)
    if get_settings().dry_run:
        return {"simulado": True}
    afectados = _pr(O.cambiar_salida, odoo, t, lista, body.convoy_salida)
    lista = O.viajes(odoo) if afectados else lista
    poner_al_dia(odoo, t, lista, registro=bool(afectados))
    log.info("tren %s: convoy de salida %s (%s cargas)", t["tren"], body.convoy_salida, len(afectados))
    return O.estado_tren(odoo, t, lista)


def _tren(odoo, abierto: bool = False):
    t = O.tren_actual(odoo)
    if not t:
        raise AppError("La oficina todavía no ha cargado ningún tren.", 404, "sin_tren")
    if abierto:
        _pr(O.abierto, t)
    return t


def poner_al_dia(odoo, t: dict, lista: list[dict], registro: bool = True) -> None:
    """Tras cada cambio en el tren: la Excel del tren (y la de registro) se sustituyen en SharePoint. Nunca para el trabajo."""
    try:
        por_caja = {v["caja_movil"]: v for v in lista if v.get("tren") == str(t["tren"]) and v.get("caja_movil")}
        subir_excel_tren(t, por_caja)
    except Exception:
        log.exception("no se pudo poner al día la Excel del tren %s", t.get("tren"))
    if registro:
        subir_registro(odoo, lista)


@oficina.get("/tren/actual")
def tren_actual():
    """Oficina y almacén: tren cargado, cajas con su carga y cargas en stock."""
    odoo = odoo_pr()
    return O.estado_tren(odoo, _tren(odoo), O.viajes(odoo))


@oficina.post("/tren/asignar")
def tren_asignar(body: Asignar):
    odoo = odoo_pr()
    t, lista = _tren(odoo, abierto=True), O.viajes(odoo)
    if get_settings().dry_run:
        return {"simulado": True}
    if body.vacia:
        _pr(O.marcar_vacia, odoo, t, lista, body.caja_movil)
        log.info("tren %s: caja %s vacía", t["tren"], body.caja_movil)
        poner_al_dia(odoo, t, lista, registro=False)
        return O.estado_tren(odoo, t, lista)
    if not body.bulto_id:
        raise AppError("Elige la carga.", 400, "sin_carga")
    bulto = _pr(O.asignar, odoo, t, lista, body.bulto_id, body.caja_movil)
    log.info("tren %s: %s → caja %s", t["tren"], bulto, body.caja_movil)
    lista = O.viajes(odoo)
    viaje = next((v for v in lista if v["bulto_id"] == body.bulto_id), None)
    if viaje:
        sincronizar_foto(odoo, viaje, lista=lista)
        lista = O.viajes(odoo)
    poner_al_dia(odoo, t, lista)
    return O.estado_tren(odoo, t, lista)


@oficina.post("/tren/quitar")
def tren_quitar(body: Asignar):
    odoo = odoo_pr()
    t = _tren(odoo, abierto=True)
    if get_settings().dry_run:
        return {"simulado": True}
    lista = O.viajes(odoo)
    if body.vacia:
        _pr(O.quitar_vacia, odoo, t, lista, body.caja_movil)
        poner_al_dia(odoo, t, lista, registro=False)
        return O.estado_tren(odoo, t, lista)
    previo = next((v for v in lista if v["bulto_id"] == body.bulto_id), None)
    _pr(O.quitar, odoo, body.bulto_id, t, lista)
    for _, clave, _, _ in R.DOCUMENTOS.values():
        if previo and previo.get(clave):
            borrar_foto(previo[clave])
    lista = O.viajes(odoo)
    poner_al_dia(odoo, t, lista)
    return O.estado_tren(odoo, t, lista)


@oficina.post("/tren/mover")
def tren_mover(body: Mover):
    """Almacén: sube o baja un vagón ya cargado (arriba = junto a la locomotora)."""
    odoo = odoo_pr()
    t = _tren(odoo, abierto=True)
    if get_settings().dry_run:
        return {"simulado": True}
    _pr(O.mover_vagon, odoo, t, body.vagon, body.hacia)
    lista = O.viajes(odoo)
    poner_al_dia(odoo, t, lista, registro=False)
    return O.estado_tren(odoo, t, lista)


@oficina.post("/tren/editar")
def tren_editar(body: Editar):
    """Almacén y oficina: corrige el nombre de una caja móvil o el número de su vagón."""
    if not (body.nueva_caja.strip() or body.nuevo_vagon.strip()):
        raise AppError("No has cambiado nada.", 400, "sin_cambios")
    odoo = odoo_pr()
    t, lista = _tren(odoo, abierto=True), O.viajes(odoo)
    if get_settings().dry_run:
        return {"simulado": True}
    afectados = _pr(O.editar, odoo, t, lista, body.caja_movil, body.nueva_caja, body.nuevo_vagon)
    log.info("tren %s: caja %s corregida (caja=%s vagón=%s)", t["tren"], body.caja_movil, body.nueva_caja, body.nuevo_vagon)
    lista = O.viajes(odoo)
    if body.nueva_caja.strip():   # la foto se llama «código Paper - caja móvil»: se renombra
        for v in lista:
            if v["bulto_id"] in afectados:
                sincronizar_foto(odoo, v, lista=lista)
        lista = O.viajes(odoo)
    poner_al_dia(odoo, t, lista, registro=bool(afectados))
    return O.estado_tren(odoo, t, lista)


@oficina.post("/tren/rehacer")
def tren_rehacer():
    """Oficina: vuelve a dejar la carpeta del tren como dice la app: la Excel del tren rehecha desde Odoo y los documentos
    (albarán y DI) de cada carga asignada subidos o renombrados. Sirve después de cualquier fallo de SharePoint o n8n."""
    odoo = odoo_pr()
    t, lista = _tren(odoo), O.viajes(odoo)
    if get_settings().dry_run:
        return {"simulado": True}
    del_tren = [v for v in lista if v.get("tren") == str(t["tren"]) and v.get("caja_movil")]
    for v in del_tren:
        sincronizar_foto(odoo, v, lista=lista)
    lista = O.viajes(odoo)
    poner_al_dia(odoo, t, lista)
    log.info("tren %s rehecho: Excel y documentos de %s cargas", t["tren"], len(del_tren))
    return O.estado_tren(odoo, t, lista)


@oficina.post("/tren/reabrir")
def tren_reabrir():
    """Oficina: «Modificar el tren» terminado (hasta que se cargue la composición del siguiente)."""
    odoo = odoo_pr()
    t = _tren(odoo)
    if get_settings().dry_run:
        return {"simulado": True}
    O.reabrir(odoo, t)
    log.info("tren %s reabierto para modificarlo", t["tren"])
    return O.estado_tren(odoo, t, O.viajes(odoo))


@oficina.post("/tren/enviar")
def tren_enviar():
    """Tren terminado: las dos Excel (descarga, SharePoint y correo por n8n)."""
    s = get_settings()
    odoo = odoo_pr()
    t, lista = _tren(odoo), O.viajes(odoo)
    por_caja = {v["caja_movil"]: v for v in lista if v.get("tren") == str(t["tren"]) and v.get("caja_movil")}
    cajas = O.cajas_en_orden(t)
    archivos = [{"nombre": excel_tren(t), "carpeta": R.carpeta_tren(t["tren"]),
                 "b64": base64.b64encode(X.carton_paja_tren(t["tren"], O.convoy_tren(t), t["fecha"], cajas, por_caja,
                                                            t.get("convoy") or "")).decode()},
                {"nombre": "REGISTRO VIAJES PR.xlsx", "b64": base64.b64encode(X.registro(lista)).decode()}]
    stock = [v for v in lista if v["estado"] == "En stock"]
    fecha_es = html.escape("-".join(reversed(str(t["fecha"]).split("-"))))
    del_tren = [por_caja[c["caja_movil"]] for c in cajas if c["caja_movil"] in por_caja]
    cuerpo = (botones(del_tren + stock, t)
              + f"<p style='{LETRA}'><b>Tren {int(t['tren'])}</b> (convoy de salida {html.escape(O.convoy_tren(t))}, "
              f"{fecha_es}): {len(por_caja)} cargas en {len(t['cajas'])} cajas.</p>"
              + tabla_html(["Ord", "Vagón", "Caja", "Código Paper", "Albarán", "Lugar", "Balas", "Peso kg"],
                           [["" if c["segunda"] else c["ord"], "" if c["segunda"] else c["vagon"], c["caja_movil"], v.get("codigo_paper"),
                             tarea(v), v.get("lugar"), v.get("bultos"), v.get("peso_kg")] if (v := por_caja.get(c["caja_movil"]))
                            else ["" if c["segunda"] else c["ord"], "" if c["segunda"] else c["vagon"], c["caja_movil"],
                                  "CAJA VACÍA" if c["vacia"] else "sin asignar", "", "", "", ""] for c in cajas])
              + f"<p style='{LETRA}'><b>Quedan en stock para el siguiente tren: {len(stock)}</b></p>"
              + tabla_html(["Fecha", "Código Paper", "Albarán", "Lugar"], [[v["fecha"], v["codigo_paper"], tarea(v), v["lugar"]] for v in stock]))
    fotos, sin_docs = [], []
    for c in cajas:
        v = por_caja.get(c["caja_movil"])
        if not v:
            continue
        for doc, (_, clave, sufijo, texto) in R.DOCUMENTOS.items():
            f = leer_foto_o_nada(odoo, v, doc)
            if f:
                nombre = (v.get(clave) or "").split("/", 1)[-1] or R.nombre_foto(v.get("codigo_paper"), v.get("albaran"),
                                                                                c["caja_movil"], R.extension(f["tipo"]), set(), sufijo)
                fotos.append({"nombre": nombre, "b64": f["b64"], "tipo": f["tipo"]})
            else:
                sin_docs.append([c["caja_movil"], tarea(v), v.get("codigo_paper"), texto])
    cuerpo += (f"<p style='{LETRA}'>Van adjuntos {len(fotos)} documentos (fotos de los albaranes y DI de residuos), también en "
               f"SharePoint › Automatizacion › {R.carpeta_tren(t['tren'])}.</p>")
    if sin_docs:
        cuerpo += (f"<p style='{LETRA};color:#C00000'><b>Faltan {len(sin_docs)} documentos:</b></p>"
                   + tabla_html(["Caja", "Albarán (tarea)", "Código Paper", "Falta"], sin_docs))
    enviado = False if s.dry_run else enviar_n8n({"evento": "tren", "asunto": f"PR SAICA · Tren {t['tren']} del {fecha_es} ({len(por_caja)} cargas)",
                                                  "html": cuerpo, "archivos": archivos, "fotos": fotos,
                                                  "carpeta": R.carpeta_tren(t["tren"]), "excel_tren": ruta_excel_tren(t)})
    if not s.dry_run:
        O.terminar(odoo, t, dt.datetime.now(R.MADRID).isoformat(timespec="minutes"))
    log.info("tren %s enviado y terminado: %s cargas, %s en stock", t["tren"], len(por_caja), len(stock))
    return {"simulado": s.dry_run, "tren": t["tren"], "cargas": len(por_caja), "en_stock": len(stock), "enviado": enviado, "archivos": archivos}


def dias_resumen(hoy: dt.date) -> tuple[dt.date, dt.date]:
    """Días que cubre el correo de la mañana: ayer; el lunes, sábado y domingo."""
    ayer = hoy - dt.timedelta(days=1)
    return (hoy - dt.timedelta(days=2) if hoy.weekday() == 0 else ayer), ayer


def _dias_es(desde: dt.date, hasta: dt.date) -> str:
    return f"{hasta:%d/%m}" if desde == hasta else f"del {desde:%d/%m} al {hasta:%d/%m}"


def require_n8n(enviar: bool = False, x_clave: str = Header(""), x_codigo_acceso: str = Header("")):
    """El resumen de la mañana lo pide n8n con la clave secreta del flujo (X-Clave = PR_AVISOS_CLAVE), o la oficina con su código.
    PUENTE mientras n8n no mande X-Clave: con el código general se puede leer, pero nunca enviar el correo."""
    s = get_settings()
    clave = os.environ.get("PR_AVISOS_CLAVE", "")
    if len(clave) >= 8 and hmac.compare_digest(x_clave.encode(), clave.encode()):
        return
    if len(s.oficina_code) >= 8 and hmac.compare_digest(x_codigo_acceso.encode(), s.oficina_code.encode()):
        return
    if not enviar and s.access_code and hmac.compare_digest(x_codigo_acceso.encode(), s.access_code.encode()):
        log.warning("resumen pedido con el código general: n8n tiene que mandar la cabecera X-Clave")
        return
    if s.mode != "real" and not s.access_code:
        return   # simulado sin código
    raise AppError("No autorizado.", 401, "codigo_invalido")


@n8n.get("/resumen", dependencies=[Depends(require_n8n)])
def resumen(enviar: bool = False, hoy: str = Query("", pattern=r"^(\d{4}-\d{2}-\d{2})?$")):
    """Para n8n por la mañana (lunes a sábado): llegadas a la nave, cargas en el tren, datos pendientes y stock."""
    odoo = odoo_pr()
    lista = O.viajes(odoo)
    try:
        t = O.tren_actual(odoo)
    except AppError:
        t = None
    dia = dt.date.fromisoformat(hoy) if hoy else dt.datetime.now(R.MADRID).date()
    desde, hasta = dias_resumen(dia)
    en_rango = lambda f: bool(f) and desde.isoformat() <= f[:10] <= hasta.isoformat()
    llegados = [v for v in lista if en_rango(v.get("registrado"))]
    # Todas las cargas del tren actual, en el orden del tren (da igual cuándo llegaron a la nave o se cargaron)
    al_tren = []
    if t:
        por_caja = {v["caja_movil"]: v for v in lista if v.get("tren") == str(t["tren"]) and v.get("caja_movil")}
        al_tren = [por_caja[c["caja_movil"]] for c in O.cajas_en_orden(t) if c["caja_movil"] in por_caja]
    titulo_tren = (f"Viajes cargados en el tren {int(t['tren'])}" + (f" (convoy {O.convoy_tren(t)})" if O.convoy_tren(t) else "")
                   + (" · terminado" if t.get("terminado") else " · cargando") if t else "Viajes cargados en el tren")
    pendientes = [v for v in lista if faltan(v)]
    stock = [v for v in lista if v["estado"] == "En stock"]
    dias = _dias_es(desde, hasta)
    h3 = lambda texto: f"<h3 style='font:bold 12pt Calibri,Arial;color:#1F4E79;margin:18px 0 6px'>{html.escape(texto)}</h3>"
    cuerpo = (f"<p style='{LETRA}'><b>Viajes de PR de SAICA · resumen del {dia:%d/%m/%Y}</b></p>"
              + botones(llegados + al_tren + pendientes + stock, t)
              + h3(f"Viajes que llegaron a stock de la nave ({dias}): {len(llegados)}")
              + tabla_html(["Fecha carga", "Albarán (tarea)", "Código Paper", "Lugar", "Tractora", "Transportista", "Balas", "Peso kg"],
                           [[v["fecha"], tarea(v), v.get("codigo_paper"), v["lugar"], v["tractora"], v["colaborador"], v.get("bultos"),
                             v["peso_kg"]] for v in llegados])
              + h3(f"{titulo_tren}: {len(al_tren)}")
              + tabla_html(["Tren", "Vagón", "Caja", "Albarán (tarea)", "Código Paper", "Lugar", "Peso kg"],
                           [[v["tren"], v.get("vagon"), v["caja_movil"], tarea(v), v.get("codigo_paper"), v["lugar"], v["peso_kg"]]
                            for v in al_tren])
              + h3(f"Viajes con datos o documentos pendientes: {len(pendientes)}")
              + tabla_html(["Fecha carga", "Albarán (tarea)", "Lugar", "Estado", "Falta"],
                           [[v["fecha"], tarea(v), v["lugar"], v["estado"], faltan(v)] for v in pendientes])
              + h3(f"Viajes que quedan en stock: {len(stock)}")
              + tabla_html(["Fecha carga", "Albarán (tarea)", "Código Paper", "Lugar", "Balas", "Peso kg"],
                           [[v["fecha"], tarea(v), v.get("codigo_paper"), v["lugar"], v.get("bultos"), v["peso_kg"]] for v in stock]))
    archivos = [{"nombre": "REGISTRO VIAJES PR.xlsx", "b64": base64.b64encode(X.registro(lista)).decode()}]
    datos = {"evento": "resumen", "asunto": f"PR SAICA · resumen {dia:%d/%m/%Y}", "html": cuerpo, "archivos": archivos,
             "excel_tren": ruta_excel_tren(t)}
    if enviar:
        datos["enviado"] = enviar_n8n(datos)
    return {"para": destinatarios(), **datos}


@oficina.post("/leer-tren")
def leer_tren(body: Archivos):
    leido = _leer(L.leer_tren, [_bytes(a) for a in body.archivos])
    tren = R.limpiar_tren(leido)
    log.info("tren leído n=%s cajas=%s", tren["numero_tren"], len(tren["cajas"]))
    return tren


# ---- oficina: bobinas de un convoy de SAICA --------------------------------------------------------------------------

class LineaBobina(BaseModel):
    bobinas: int = Field(ge=0, le=999)
    tipo: str = Field(max_length=10)
    gramaje: int = Field(ge=0, le=9999)
    diametro: int = Field(ge=0, le=999)
    altura: int = Field(ge=0, le=999)


class CajaBobinas(BaseModel):
    expedicion: str | None = Field(None, max_length=20)
    caja_movil: str | None = Field(None, max_length=20)
    bobinas: int | None = None
    peso_t: float | None = None
    lineas: list[LineaBobina] = Field(max_length=30)


class ConvoyBobinas(BaseModel):
    convoy: str | None = Field(None, max_length=20)
    centro: str | None = Field(None, max_length=40)
    destino: str | None = Field(None, max_length=40)
    fecha_llegada: str | None = Field(None, max_length=10)
    vagones: int | None = None
    cajas: list[CajaBobinas] = Field(min_length=1, max_length=80)
    avisos: list[str] = Field(default_factory=list, max_length=100)


@oficina.post("/bobinas/leer")
def bobinas_leer(body: Archivos):
    """Oficina: capturas o fotos de «SAICA - Detalle convoy» → cajas con sus líneas y el total por clase."""
    d = B.limpiar(_leer(B.leer, [_bytes(a) for a in body.archivos]))
    if not d["cajas"]:
        raise AppError("No se han leído cajas en la captura. Prueba con otra captura de la pantalla completa.", 422, "sin_cajas")
    clases = B.agrupar(d["cajas"])
    log.info("bobinas convoy %s: %s cajas, %s clases", d["convoy"], len(d["cajas"]), len(clases))
    return {**d, "clases": clases, "por_tipo": B.por_tipo(clases), "total": sum(c["bobinas"] for c in clases)}


@oficina.post("/bobinas/enviar")
def bobinas_enviar(body: ConvoyBobinas):
    """Oficina: la Excel de bobinas por clase se descarga en la app y se manda por correo a Sevilla (n8n)."""
    d = body.model_dump()
    clases = B.agrupar(d["cajas"])
    total = sum(c["bobinas"] for c in clases)
    nombre = B.nombre_excel(d)
    libro = base64.b64encode(B.excel(d)).decode()
    tipos = " · ".join(f"{t} {n}" for t, n in B.por_tipo(clases))
    cuerpo = (f"<p style='font:11pt Calibri,Arial'><b>{html.escape(B.titulo(d))}</b>: {len(d['cajas'])} cajas, "
              f"<b>{total} bobinas</b> en {len(clases)} clases ({html.escape(tipos)}).</p>"
              + tabla_html(["Tipo", "Gramaje", "Diámetro", "Altura", "Bobinas"],
                           [[c["tipo"], c["gramaje"], c["diametro"], c["altura"], c["bobinas"]] for c in clases])
              + ("<p style='font:11pt Calibri,Arial'><b>Revisar:</b><br>" + "<br>".join(html.escape(a) for a in d["avisos"]) + "</p>"
                 if d["avisos"] else "")
              + f"<p style='font:11pt Calibri,Arial'>Va adjunta la Excel {html.escape(nombre)} (resumen por clase y detalle por caja).</p>")
    archivos = [{"nombre": nombre, "b64": libro}]
    s = get_settings()
    enviado = False if s.dry_run else enviar_n8n({
        "evento": "bobinas", "asunto": f"Bobinas convoy {d.get('convoy') or ''} · llegada {B.fecha_es(d.get('fecha_llegada'))} ({total} bobinas)",
        "html": cuerpo, "archivos": archivos})
    log.info("bobinas convoy %s enviadas: %s bobinas (%s)", d.get("convoy"), total, enviado)
    return {"simulado": s.dry_run, "enviado": enviado, "para": destinatarios(), "total": total, "clases": len(clases), "archivos": archivos}
