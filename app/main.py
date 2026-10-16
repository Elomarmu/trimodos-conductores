import logging
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import service
from .config import get_settings
from .errors import AppError
from .mock_odoo import MockOdoo
from .odoo_client import OdooClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("conductores")

app = FastAPI(title="App de conductores", docs_url=None, redoc_url=None)
_odoo = None


def get_odoo():
    global _odoo
    if _odoo is None:
        s = get_settings()
        _odoo = OdooClient(s) if s.mode == "real" else MockOdoo()
    return _odoo


class ResolveBody(BaseModel):
    qr_text: str | None = None
    order_name: str | None = None
    company: str | None = None  # "stinsa" o "transargi" (obligatoria con order_name)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse({"error": exc.message, "codigo": exc.code}, status_code=exc.status)


@app.exception_handler(Exception)
async def unexpected_handler(request: Request, exc: Exception):
    log.exception("Error inesperado")  # sin datos del usuario en el mensaje
    return JSONResponse({"error": "Algo ha fallado. Inténtalo de nuevo.", "codigo": "interno"}, status_code=500)


@app.get("/api/health")
def health():
    s = get_settings()
    return {"ok": True, "modo": s.mode, "dry_run": s.dry_run}


@app.post("/api/resolve-qr")
def resolve_qr(body: ResolveBody):
    result = service.resolve(get_odoo(), get_settings(), qr_text=body.qr_text, order_name=body.order_name, company=body.company)
    log.info("resolve tarea=%s via=%s", result["tarea"]["id"], "qr" if body.qr_text else "pedido")
    return result


@app.get("/api/goods")
def goods(q: str = Query("", max_length=60)):
    return {"resultados": get_odoo().search_goods(q.strip(), 10)}


_public = Path(__file__).resolve().parent.parent / "public"
if _public.is_dir():  # en Vercel los estáticos los sirve la plataforma; en local, FastAPI
    app.mount("/", StaticFiles(directory=_public, html=True), name="public")
