"""Sesión firmada: el servidor recuerda (sin guardar nada) qué tarea se abrió y qué versión de los bultos se cargó."""
import base64
import hashlib
import hmac
import json
import time

from .errors import AppError

TTL_SECONDS = 30 * 60


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_session(secret: str, task_id: int, write_dates: dict[int, str], now: float | None = None, verified: bool = False) -> str:
    payload = {"t": task_id, "w": {str(k): v for k, v in write_dates.items()}, "e": int((now or time.time()) + TTL_SECONDS), "v": verified}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_session(secret: str, token: str, now: float | None = None) -> dict:
    expired = AppError("Ha pasado demasiado tiempo. Vuelve a escanear la orden.", 401, "sesion_caducada")
    try:
        body, sig = token.split(".")
        expected = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            raise ValueError
        payload = json.loads(_unb64(body))
    except (ValueError, TypeError, KeyError):
        raise AppError("La sesión no es válida. Vuelve a escanear la orden.", 401, "sesion_invalida")
    if payload["e"] < (now or time.time()):
        raise expired
    return {"task_id": payload["t"], "write_dates": {int(k): v for k, v in payload["w"].items()}, "verified": bool(payload.get("v"))}
