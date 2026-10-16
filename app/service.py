"""Lógica de negocio: resolver el QR o el número de pedido hasta la tarea y sus bultos."""
import hmac
import math
import re
from urllib.parse import urlparse

from .config import Settings
from .errors import AppError
from .qr import normalize_order_name, parse_qr
from .tokens import sign_session, verify_session

TASK_MODEL = "project.task"


COMPANIES = {"stinsa": 1, "transargi": 7}
LOCKED_PACKAGE_STATES = ("done", "cancel")


def is_editable(task: dict, packages: list[dict]) -> tuple[bool, str | None]:
    """No editable si la tarea está cerrada, ya tiene facturas o algún bulto está terminado/cancelado."""
    if task.get("is_closed"):
        return False, "Esta orden ya está cerrada. Avisa a la oficina."
    if (task.get("invoice_count") or 0) > 0:
        return False, "Esta orden ya está facturada. Avisa a la oficina."
    if any(p.get("state") in LOCKED_PACKAGE_STATES for p in packages):
        return False, "Esta orden ya está terminada o cancelada. Avisa a la oficina."
    return True, None


def _allowed_hosts(settings: Settings) -> set[str]:
    hosts = set(settings.qr_allowed_hosts)
    if settings.mode != "real":
        hosts.add("www.stinsa.com")  # el QR de prueba del modo simulado
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


def resolve(odoo, settings: Settings, qr_text: str | None = None, order_name: str | None = None, company: str | None = None) -> dict:
    not_found = AppError("No encontramos esta orden. Revisa el número o avisa a la oficina.", 404, "no_encontrada")
    verified = False
    outdated = False
    order_label = None

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
        found = re.search(r"\bS\d{5,9}\b", att.get("name") or "")  # el adjunto se llama "Orden de transporte - S2621473 - ..."
        order_label = found.group(0) if found else None
    elif order_name:
        name = normalize_order_name(order_name)
        company_id = COMPANIES.get((company or "").strip().lower())
        if not company_id:
            raise AppError("Elige la empresa: TRANSARGI o STINSA.", 400, "falta_empresa")
        tasks = odoo.find_transport_tasks(name, company_id)
        if not tasks:
            raise not_found
        task = tasks[0]  # la más reciente
        order_label = name
    else:
        raise AppError("Escanea el código o escribe el número de pedido.", 400, "faltan_datos")

    packages = odoo.read_packages(task.get("tms_package_ids") or [])
    editable, reason = is_editable(task, packages)
    session = sign_session(settings.secret_key, task["id"], {p["id"]: p["write_date"] for p in packages})
    return {
        "tarea": {"id": task["id"], "nombre": task["name"], "pedido": order_label or None, "compania": _m2o(task.get("company_id")), "etapa": _m2o(task.get("stage_id"))},
        "bultos": [_package_view(p) for p in packages],
        "version_antigua": outdated,
        "aviso": "Esta orden tiene una versión más nueva." if outdated else None,
        "verificado_por_qr": verified,
        "editable": editable,
        "motivo_no_editable": reason,
        "sesion": session,
    }


# ---------------------------------------------------------------------------
# Guardado
# ---------------------------------------------------------------------------
MAX_WEIGHT_WARNING = 44000
FIELD_LABELS = {"goods_id": "Mercancía", "number_of_packages": "Elementos", "euro_pallet_qty": "Euro palets", "shipping_weight": "Peso"}


def _clean_change(odoo, c: dict) -> dict:
    """Valida los valores de un bulto y devuelve solo los campos informados."""
    bad = lambda msg: AppError(msg, 400, "dato_invalido")  # noqa: E731
    out: dict = {}
    if c.get("goods_id") is not None:
        if not odoo.goods_name(c["goods_id"]):
            raise bad("La mercancía elegida no existe. Elígela de la lista.")
        out["goods_id"] = c["goods_id"]
    for field, label in (("number_of_packages", "Elementos"), ("euro_pallet_qty", "Euro palets")):
        if c.get(field) is not None:
            if c[field] < 0:
                raise bad(f"{label} no puede ser negativo.")
            out[field] = c[field]
    if c.get("shipping_weight") is not None:
        w = c["shipping_weight"]
        if not math.isfinite(w) or w <= 0:
            raise bad("El peso tiene que ser mayor que 0.")
        out["shipping_weight"] = float(w)
    return out


def update(odoo, settings: Settings, sesion: str, task_id: int, cambios: list[dict]) -> dict:
    """Valida todo en el servidor (no se fía del cliente) y guarda los cambios."""
    ses = verify_session(settings.secret_key, sesion)
    if ses["task_id"] != task_id:
        raise AppError("La sesión no corresponde a esta orden. Vuelve a escanearla.", 401, "sesion_invalida")
    if not cambios:
        raise AppError("No has cambiado ningún dato.", 400, "sin_cambios")

    task = odoo.read_task(task_id)
    if not task or not task.get("is_transport_order"):
        raise AppError("No encontramos esta orden. Avisa a la oficina.", 404, "no_encontrada")
    packages = {p["id"]: p for p in odoo.read_packages(task.get("tms_package_ids") or [])}

    editable, reason = is_editable(task, list(packages.values()))
    if not editable:
        raise AppError(reason, 409, "no_editable")

    changes: dict[int, dict] = {}
    for c in cambios:
        pid = c["package_id"]
        if pid not in packages or pid not in ses["write_dates"]:
            raise AppError("Ese bulto no pertenece a esta orden.", 400, "bulto_ajeno")
        if packages[pid]["write_date"] != ses["write_dates"][pid]:
            raise AppError("Esta orden ha cambiado mientras la editabas. Vuelve a cargarla.", 409, "datos_cambiados")
        clean = _clean_change(odoo, c)
        cur = packages[pid]
        current = {"goods_id": (cur.get("goods_id") or [None])[0], "number_of_packages": cur.get("number_of_packages"),
                   "euro_pallet_qty": cur.get("euro_pallet_qty"), "shipping_weight": cur.get("shipping_weight")}
        diff = {k: v for k, v in clean.items() if v != current[k]}
        if diff:
            changes.setdefault(pid, {}).update(diff)
    if not changes:
        raise AppError("No has cambiado ningún dato.", 400, "sin_cambios")

    warnings = []
    for pid, vals in changes.items():
        if vals.get("shipping_weight", 0) > MAX_WEIGHT_WARNING:
            warnings.append(f"{packages[pid]['name']}: el peso supera 44.000 kg.")

    if settings.mode == "real":
        raise AppError("El guardado real todavía no está activado.", 501, "no_implementado")  # fase 3

    attachment_id, token = odoo.update_packages(task_id, changes)
    return {
        "ok": True, "simulado": True, "bultos_modificados": len(changes), "avisos": warnings,
        "adjunto_id": attachment_id, "pdf_url": f"/api/pdf/{attachment_id}?token={token}",
    }

