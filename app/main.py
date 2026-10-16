import hmac
import logging
import mimetypes
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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


def require_access(x_codigo_acceso: str = Header("")):
    """Código de acceso compartido. En modo real es obligatorio: sin él configurado, la API se niega a funcionar."""
    s = get_settings()
    if s.mode == "real" and len(s.access_code) < MIN_CODE_LENGTH:
        raise AppError("La app no está configurada. Avisa a la oficina.", 503, "sin_configurar")
    if not s.access_code:
        return  # solo posible en modo simulado
    if not hmac.compare_digest(x_codigo_acceso.encode(), s.access_code.encode()):
        log.warning("Código de acceso incorrecto")  # nunca se registra lo que se escribió
        time.sleep(FAIL_DELAY)
        raise AppError("Código de acceso incorrecto.", 401, "codigo_invalido")


access = [Depends(require_access)]


class ResolveBody(BaseModel):
    qr_text: str | None = None
    order_name: str | None = None
    company: str | None = None  # "stinsa" o "transargi" (obligatoria con order_name)


class Cambio(BaseModel):
    package_id: int
    goods_id: int | None = None
    number_of_packages: int | None = None
    euro_pallet_qty: int | None = None
    shipping_weight: float | None = None


class UpdateBody(BaseModel):
    task_id: int
    sesion: str
    cambios: list[Cambio]


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
def login():
    return {"ok": True}


@app.get("/api/health")
def health():
    s = get_settings()
    info = {"ok": True, "modo": s.mode, "dry_run": s.dry_run, "requiere_codigo": bool(s.access_code) or s.mode == "real"}
    if s.mode != "real":  # ayuda para probar sin QR físico
        info["qr_prueba"] = "https://www.stinsa.com/web/content/900002?access_token=11111111-aaaa-bbbb-cccc-000000000001&download=true"
    return info


@app.post("/api/resolve-qr", dependencies=access)
def resolve_qr(body: ResolveBody):
    result = service.resolve(get_odoo(), get_settings(), qr_text=body.qr_text, order_name=body.order_name, company=body.company)
    log.info("resolve tarea=%s via=%s", result["tarea"]["id"], "qr" if body.qr_text else "pedido")
    return result


@app.post("/api/update", dependencies=access)
def update(body: UpdateBody):
    result = service.update(get_odoo(), get_settings(), body.sesion, body.task_id, [c.model_dump() for c in body.cambios])
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
    return {"resultados": get_odoo().search_goods(q.strip(), 10)}


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


mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")
_public = Path(__file__).resolve().parent.parent / "public"
if _public.is_dir():  # en Vercel los estáticos los sirve la plataforma; en local, FastAPI
    app.mount("/", StaticFiles(directory=_public, html=True), name="public")
