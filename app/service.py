"""Lógica de negocio: resolver el QR o el número de pedido hasta la tarea y sus bultos."""
import hmac
from urllib.parse import urlparse

from .config import Settings
from .errors import AppError
from .qr import normalize_order_name, parse_qr
from .tokens import sign_session

TASK_MODEL = "project.task"


def is_editable(task: dict) -> tuple[bool, str | None]:
    """Regla de 'tarea cerrada o facturada'. PENDIENTE de confirmar con los datos reales (ver scripts/diagnostico.py)."""
    return True, None


def _allowed_hosts(settings: Settings) -> set[str]:
    hosts = set(settings.qr_allowed_hosts)
    if settings.odoo_url:
        hosts.add((urlparse(settings.odoo_url).hostname or "").lower())
    return hosts


def _m2o(value) -> dict | None:
    return {"id": value[0], "name": value[1]} if value else None


def _package_view(p: dict) -> dict:
    return {
        "id": p["id"], "name": p["name"], "referencia": p.get("carrier_tracking_ref") or "",
        "origen": _m2o(p.get("shipping_origin_id")), "destino": _m2o(p.get("shipping_destination_id")),
        "fecha_carga": p.get("pickup_date") or None, "fecha_descarga": p.get("forecast_unload_date") or None,
        "estado": p.get("state"),
        "mercancia": _m2o(p.get("goods_id")),
        "elementos": p.get("number_of_packages") or 0,
        "euro_palets": p.get("euro_pallet_qty") or 0,
        "peso": p.get("shipping_weight") or 0.0,
    }


def resolve(odoo, settings: Settings, qr_text: str | None = None, order_name: str | None = None) -> dict:
    not_found = AppError("No encontramos esta orden. Revisa el número o avisa a la oficina.", 404, "no_encontrada")
    verified = False
    outdated = False

    if qr_text:
        qr = parse_qr(qr_text, _allowed_hosts(settings))
        att = odoo.read_attachment(qr.attachment_id)
        # Mismo mensaje si no existe o si el token no coincide: no damos pistas.
        if not att or att.get("res_model") != TASK_MODEL or not att.get("access_token") \
                or not hmac.compare_digest(str(att["access_token"]), qr.access_token):
            raise not_found
        task = odoo.read_task(att["res_id"])
        if not task or not task.get("is_transport_order"):
            raise not_found
        current = task.get("tms_transport_order_attachment_id")
        outdated = not current or current[0] != att["id"]
        verified = True
    elif order_name:
        name = normalize_order_name(order_name)
        tasks = odoo.find_transport_tasks(name)
        if not tasks:
            raise not_found
        task = tasks[0]  # la más reciente
    else:
        raise AppError("Escanea el código o escribe el número de pedido.", 400, "faltan_datos")

    packages = odoo.read_packages(task.get("tms_package_ids") or [])
    editable, reason = is_editable(task)
    session = sign_session(settings.secret_key, task["id"], {p["id"]: p["write_date"] for p in packages})
    return {
        "tarea": {"id": task["id"], "nombre": task["name"], "compania": _m2o(task.get("company_id")), "etapa": _m2o(task.get("stage_id"))},
        "bultos": [_package_view(p) for p in packages],
        "version_antigua": outdated,
        "aviso": "Esta orden tiene una versión más nueva." if outdated else None,
        "verificado_por_qr": verified,
        "editable": editable,
        "motivo_no_editable": reason,
        "sesion": session,
    }
