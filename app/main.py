import hmac
import logging
import mimetypes
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from . import service
from .config import get_settings
from .errors import AppError
from .mock_odoo import MockOdoo
from .odoo_client import OdooClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # sus logs incluyen las URL con access_token
log = logging.getLogger("conductores")

app = FastAPI(title="App de conductores", docs_url=None, redoc_url=None)
_odoo = None


def get_odoo():
    global _odoo
    if _odoo is None:
        s = get_settings()
        _odoo = OdooClient(s) if s.mode == "real" else MockOdoo()
    return _odoo


MIN_CODE_LENGTH = 8
FAIL_DELAY = 1.0  # segundos de espera tras un código incorrecto: frena los intentos al azar


def _igual(a: str, b: str) -> bool:
    return bool(b) and hmac.compare_digest(a.encode(), b.encode())


def es_oficina(codigo: str) -> bool:
    """¿Es el código de la app de oficina y almacén? Mientras no haya uno propio (APP_OFICINA_CODE), vale el general."""
    s = get_settings()
    return _igual(codigo, s.oficina_code) if len(s.oficina_code) >= MIN_CODE_LENGTH else (_igual(codigo, s.access_code) or not s.access_code)


def require_access(x_codigo_acceso: str = Header("")):
    """Código de acceso: el de los conductores (APP_ACCESS_CODE) o el de oficina y almacén (APP_OFICINA_CODE).
    En modo real es obligatorio: sin él configurado, la API se niega a funcionar."""
    s = get_settings()
    if s.mode == "real" and len(s.access_code) < MIN_CODE_LENGTH:
        raise AppError("La app no está configurada. Avisa a la oficina.", 503, "sin_configurar")
    if not s.access_code:
        return  # solo posible en modo simulado
    if not (_igual(x_codigo_acceso, s.access_code) or (len(s.oficina_code) >= MIN_CODE_LENGTH and _igual(x_codigo_acceso, s.oficina_code))):
        log.warning("Código de acceso incorrecto")  # nunca se registra lo que se escribió
        time.sleep(FAIL_DELAY)
        raise AppError("Código de acceso incorrecto.", 401, "codigo_invalido")


def require_oficina(x_codigo_acceso: str = Header("")):
    """Funciones de oficina y almacén: solo con el código de oficina (el de los conductores no sirve)."""
    if not es_oficina(x_codigo_acceso):
        log.warning("Intento de usar una función de oficina sin su código")
        time.sleep(FAIL_DELAY)
        raise AppError("Esta función es de la app de oficina y almacén. Pide su código en la oficina.", 403, "solo_oficina")


access = [Depends(require_access)]
access_oficina = [Depends(require_access), Depends(require_oficina)]


class ResolveBody(BaseModel):
    qr_text: str | None = None
    order_name: str | None = None
    company: str | None = None  # "stinsa" o "transargi" (obligatoria con order_name)
    task_id: int | None = None  # tarea elegida en la lista de órdenes del pedido


class Cambio(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_id: int
    goods_id: int | None = None
    new_goods_name: str | None = None
    number_of_packages: int | None = None
    shipping_weight: float | None = None


class VehiculoSeleccionado(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vehicle_id: int | None = None
    matricula: str = ""
    confirmar_tipo_incorrecto: bool = False


class UpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: int
    sesion: str
    cambios: list[Cambio]
    tractora: VehiculoSeleccionado | None = None
    remolque: VehiculoSeleccionado | None = None


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse({"error": "Revisa los datos introducidos.", "codigo": "dato_invalido"}, status_code=400)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse({"error": exc.message, "codigo": exc.code}, status_code=exc.status)


@app.exception_handler(Exception)
async def unexpected_handler(request: Request, exc: Exception):
    log.exception("Error inesperado")  # sin datos del usuario en el mensaje
    return JSONResponse({"error": "Algo ha fallado. Inténtalo de nuevo.", "codigo": "interno"}, status_code=500)


@app.post("/api/login", dependencies=access)
def login(x_codigo_acceso: str = Header("")):
    return {"ok": True, "rol": "oficina" if es_oficina(x_codigo_acceso) else "conductor"}


@app.get("/api/health")
def health():
    s = get_settings()
    odoo_host = urlsplit(s.odoo_url).hostname if s.odoo_url else None
    info = {
        "ok": True,
        "modo": s.mode,
        "dry_run": s.dry_run,
        "requiere_codigo": bool(s.access_code) or s.mode == "real",
        "odoo": odoo_host,
    }
    if s.mode != "real":  # ayuda para probar sin QR físico
        info["qr_prueba"] = "https://www.stinsa.com/web/content/900002?access_token=11111111-aaaa-bbbb-cccc-000000000001&download=true"
    return info


@app.post("/api/resolve-qr", dependencies=access)
def resolve_qr(body: ResolveBody):
    result = service.resolve(
        get_odoo(), get_settings(), qr_text=body.qr_text, order_name=body.order_name,
        company=body.company, selected_task_id=body.task_id,
    )
    if result.get("seleccion_requerida"):
        log.info("resolve pedido=%s tareas=%s: se requiere selección", result["pedido"], len(result["ordenes"]))
    else:
        log.info("resolve tarea=%s via=%s", result["tarea"]["id"], "qr" if body.qr_text else "pedido")
    return result


@app.post("/api/update", dependencies=access)
def update(body: UpdateBody):
    vehiculos = None
    if {"tractora", "remolque"} & body.model_fields_set:
        vehiculos = {}
        for role in ("tractora", "remolque"):
            if role in body.model_fields_set:
                selection = getattr(body, role)
                vehiculos[role] = selection.model_dump() if selection else {"vehicle_id": None, "matricula": ""}
    result = service.update(
        get_odoo(), get_settings(), body.sesion, body.task_id,
        [c.model_dump() for c in body.cambios], vehiculos=vehiculos,
    )
    log.info("update tarea=%s bultos=%s", body.task_id, result["bultos_modificados"])
    return result


class ReprintBody(BaseModel):
    task_id: int
    sesion: str


@app.post("/api/reprint", dependencies=access)
def reprint(body: ReprintBody):
    result = service.reprint(get_odoo(), get_settings(), body.sesion, body.task_id)
    log.info("reprint tarea=%s", body.task_id)
    return result


@app.get("/api/pdf/{attachment_id}", dependencies=access)
def pdf(attachment_id: int, token: str = Query("", max_length=80)):
    data = get_odoo().get_pdf(attachment_id, token)
    if not data:
        raise AppError("No encontramos el documento. Vuelve a generarlo.", 404, "pdf_no_encontrado")
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="orden-{attachment_id}.pdf"'})


@app.get("/api/goods", dependencies=access)
def goods(q: str = Query("", max_length=60)):
    query = q.strip()
    resultados = get_odoo().search_goods(query, 10) if query else []
    nombre_nuevo = service.normalize_goods_name(query)
    existe = any(service.normalize_goods_name(g["name"]) == nombre_nuevo for g in resultados)
    return {"resultados": resultados, "crear_nombre": nombre_nuevo if len(nombre_nuevo) >= 2 and not existe else None}


@app.get("/api/vehicles", dependencies=access)
def vehicles(
    task_id: int,
    sesion: str = Query(..., max_length=2048),
    tipo: str = Query(..., pattern="^(tractor|trailer)$"),
    matricula: str = Query("", max_length=40),
):
    return service.lookup_vehicles(get_odoo(), get_settings(), sesion, task_id, tipo, matricula)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"  # nunca guardar datos de órdenes en cachés
    elif request.url.path == "/sw.js":
        response.headers["Cache-Control"] = "no-cache"
    return response


# Proceso ARGON (Excel de Carburos → pedidos en Odoo 18). Protegido por su propia clave (ARGON_CLAVE).
from .argon import argon_api  # noqa: E402

app.include_router(argon_api.router)

# Viajes de PR de SAICA PAPER (albarán → pedido mensual; tren → cajas móviles). Mismo código de acceso que la app.
from .pr import pr_api  # noqa: E402

app.include_router(pr_api.router, dependencies=access)             # conductor (y oficina): registrar un viaje
app.include_router(pr_api.oficina, dependencies=access_oficina)    # oficina y almacén: solo con su código
app.include_router(pr_api.n8n)                                     # resumen de la mañana: clave del flujo de n8n (X-Clave)

mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")
_public = Path(__file__).resolve().parent.parent / "public"
if _public.is_dir():  # en Vercel los estáticos los sirve la plataforma; en local, FastAPI
    app.mount("/", StaticFiles(directory=_public, html=True), name="public")
