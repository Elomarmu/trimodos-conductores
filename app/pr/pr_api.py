"""API de los viajes de PR. Se monta en main.py con el mismo código de acceso que el resto de la app.

POST /api/pr/leer-albaran  {"archivo_b64": "...", "tipo": "image/jpeg"}  → datos del viaje para que el conductor los revise
POST /api/pr/leer-tren     {"archivos": [{"archivo_b64": "...", "tipo": "application/pdf"}]} → cajas del tren
Fase 2: solo lectura (no escribe en Odoo).
"""
from __future__ import annotations

import base64
import binascii
import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..errors import AppError
from . import pr_lector as L
from . import pr_reglas as R

log = logging.getLogger("pr")
router = APIRouter(prefix="/api/pr", tags=["pr"])
MAX_BYTES = 4_000_000  # Vercel admite cuerpos de 4,5 MB: el móvil reduce la foto antes de enviarla


class Archivo(BaseModel):
    archivo_b64: str = Field(max_length=6_000_000)
    tipo: str = Field(max_length=40)


class Archivos(BaseModel):
    archivos: list[Archivo] = Field(min_length=1, max_length=4)


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


@router.post("/leer-albaran")
def leer_albaran(body: Archivo):
    datos, tipo = _bytes(body)
    leido = _leer(L.leer_albaran, datos, tipo)
    viaje = R.limpiar(leido)
    viaje.avisos += [f"Duda al leer: {d}" for d in leido.get("dudas") or []]
    log.info("albarán leído tipo=%s punto=%s pendientes=%s", viaje.tipo_albaran,
             viaje.punto.clave if viaje.punto else None, viaje.pendientes)
    return {"viaje": viaje.a_dict()}


@router.post("/leer-tren")
def leer_tren(body: Archivos):
    leido = _leer(L.leer_tren, [_bytes(a) for a in body.archivos])
    tren = R.limpiar_tren(leido)
    log.info("tren leído n=%s cajas=%s", tren["numero_tren"], len(tren["cajas"]))
    return tren
