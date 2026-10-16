"""Lectura del texto del QR y del número de pedido escrito a mano."""
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from .errors import AppError

ORDER_RE = re.compile(r"^S\d{5,9}$")
CONTENT_RE = re.compile(r"^/web/content/(\d+)/?$")


@dataclass(frozen=True)
class QrData:
    attachment_id: int
    access_token: str


def normalize_order_name(text: str) -> str:
    name = (text or "").strip().upper().replace(" ", "")
    if not ORDER_RE.match(name):
        raise AppError("El número de pedido no es válido. Ejemplo: S2621473", 400, "pedido_invalido")
    return name


def parse_qr(text: str, allowed_hosts: set[str]) -> QrData:
    """Extrae (id de adjunto, access_token) de la URL del QR de la orden de transporte."""
    bad = AppError("No se reconoce este código. Prueba de nuevo o escribe el número de pedido.", 400, "qr_invalido")
    try:
        url = urlparse((text or "").strip())
    except ValueError:
        raise bad
    if url.scheme not in ("http", "https") or not url.hostname:
        raise bad
    if url.hostname.lower() not in allowed_hosts:
        raise bad
    m = CONTENT_RE.match(url.path)
    token = (parse_qs(url.query).get("access_token") or [""])[0]
    if not m or not token:
        raise bad
    return QrData(attachment_id=int(m.group(1)), access_token=token)
