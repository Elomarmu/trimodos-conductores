"""Lógica de negocio: resolver el QR o el número de pedido hasta la tarea y sus bultos."""
import hmac
import html
import logging
import math
import re
import unicodedata
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from .config import Settings
from .errors import AppError
from .qr import normalize_order_name, parse_qr
from .tokens import sign_session, verify_session

TASK_MODEL = "project.task"
log = logging.getLogger("conductores")


COMPANIES = {"stinsa": 1, "transargi": 7}


def is_transport_task(task: dict) -> bool:
    """Orden de transporte = tarea marcada como tal, O con una orden ya generada, O con bultos (lo que se edita).
    (Hay tareas reales, p. ej. las del pedido S2621553, con la marca a False: con su PDF o aún sin imprimir.)"""
    return bool(task.get("is_transport_order") or task.get("tms_transport_order_attachment_id") or task.get("tms_package_ids"))
DEFAULT_EDITABLE_STAGES = ("nuevo", "en desarrollo")
NO_PACKAGES_MSG = "Esta orden todavía no tiene creados los lugares de carga y descarga (bultos). Ponte en contacto con tu responsable de tráfico."


def _norm(text: str) -> str:
    """Compara etapas sin importar mayúsculas ni acentos ('En Desarrollo' == 'en desarrollo')."""
    return "".join(c for c in unicodedata.normalize("NFD", text or "") if unicodedata.category(c) != "Mn").casefold().strip()


def is_editable(task: dict, stages=DEFAULT_EDITABLE_STAGES) -> tuple[bool, str | None]:
    """Una orden solo se puede modificar si la etapa de su tarea es «Nuevo» o «En desarrollo» (configurable)."""
    stage = (task.get("stage_id") or [0, ""])[1]
    if _norm(stage) in {_norm(s) for s in stages}:
        return True, None
    permitidas = " o ".join(f"«{s.capitalize()}»" for s in stages)
    return False, f"Esta orden está en la etapa «{stage or 'sin etapa'}». Solo se puede modificar en {permitidas}. Avisa a la oficina."

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
        if not task or not is_transport_task(task):
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
    editable, reason = is_editable(task, settings.editable_stages)
    if not packages:                       # p. ej. órdenes de contenedor: tienen PDF pero no bultos tms.package
        editable, reason = False, NO_PACKAGES_MSG
    session = sign_session(settings.secret_key, task["id"], {p["id"]: p["write_date"] for p in packages}, verified=verified)
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
        if c["goods_id"] == 0:
            out["goods_id"] = False          # 0 = dejar la mercancía en blanco
        elif not odoo.goods_name(c["goods_id"]):
            raise bad("La mercancía elegida no existe. Elígela de la lista.")
        else:
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
    if not task or not is_transport_task(task):
        raise AppError("No encontramos esta orden. Avisa a la oficina.", 404, "no_encontrada")
    packages = {p["id"]: p for p in odoo.read_packages(task.get("tms_package_ids") or [])}
    if not packages:
        raise AppError(NO_PACKAGES_MSG, 409, "sin_bultos")

    editable, reason = is_editable(task, settings.editable_stages)
    if not editable:
        raise AppError(reason, 409, "no_editable")

    changes: dict[int, dict] = {}
    for c in cambios:
        pid = c["package_id"]
        if pid not in packages or pid not in ses["write_dates"]:
            raise AppError("Ese bulto no pertenece a esta orden.", 400, "bulto_ajeno")
        if packages[pid]["write_date"] != ses["write_dates"][pid]:
            raise AppError("Esta orden ha cambiado mientras la editabas. Vuelve a cargarla.", 409, "datos_cambiados")
        cur = packages[pid]
        current = {"goods_id": (cur.get("goods_id") or [None])[0], "number_of_packages": cur.get("number_of_packages"),
                   "euro_pallet_qty": cur.get("euro_pallet_qty"), "shipping_weight": cur.get("shipping_weight")}
        # Solo se validan los campos que CAMBIAN: un bulto vacío o con peso 0 que no se toca no bloquea el guardado.
        changed = {}
        for k in current:
            v = c.get(k)
            if v is None:
                continue
            if (None if (k == "goods_id" and v == 0) else v) != current[k]:   # goods_id 0 = en blanco = None en Odoo
                changed[k] = v
        diff = _clean_change(odoo, changed) if changed else {}
        if diff:
            changes.setdefault(pid, {}).update(diff)
    if not changes:
        raise AppError("No has cambiado ningún dato.", 400, "sin_cambios")

    warnings = []
    for pid, vals in changes.items():
        if vals.get("shipping_weight", 0) > MAX_WEIGHT_WARNING:
            warnings.append(f"{packages[pid]['name']}: el peso supera 44.000 kg.")

    lines = _describe(odoo, packages, changes)
    base = {"ok": True, "bultos_modificados": len(changes), "avisos": warnings}

    if settings.dry_run:
        # DRY_RUN: no se escribe, ni se publica, ni se imprime nada en Odoo. Se devuelve la orden actual.
        log.info("DRY_RUN tarea=%s bultos=%s: no se escribe en Odoo", task_id, sorted(changes))
        current = _current_attachment(odoo, task)
        return {**base, "simulado": True, "modo_aviso": "MODO PRUEBA (DRY_RUN): no se ha guardado nada en Odoo. Este PDF es la orden actual, sin los cambios.", **_pdf_ref(current)}

    done = []
    try:
        for pid, vals in changes.items():
            odoo.write_package(pid, vals)
            done.append(pid)
    except Exception:
        log.exception("Guardado incompleto tarea=%s escritos=%s", task_id, done)
        raise AppError("No se han podido guardar todos los cambios. Vuelve a escanear la orden y revísala.", 502, "guardado_fallo")

    try:
        odoo.post_note(task_id, _note_html(lines, ses["verified"]))
    except Exception:
        log.exception("No se pudo publicar la nota en la tarea %s", task_id)  # los datos ya están guardados: se sigue

    return {**base, "simulado": settings.mode != "real", **_print(odoo, task), "modo_aviso": None if settings.mode == "real" else "MODO SIMULADO: no se ha escrito nada en Odoo. El PDF es una muestra."}


def reprint(odoo, settings: Settings, sesion: str, task_id: int) -> dict:
    """Reintento si los cambios se guardaron pero falló el PDF."""
    ses = verify_session(settings.secret_key, sesion)
    if ses["task_id"] != task_id:
        raise AppError("La sesión no corresponde a esta orden. Vuelve a escanearla.", 401, "sesion_invalida")
    task = odoo.read_task(task_id)
    if not task or not is_transport_task(task):
        raise AppError("No encontramos esta orden. Avisa a la oficina.", 404, "no_encontrada")
    if settings.dry_run:
        return {"ok": True, "simulado": True, "modo_aviso": "MODO PRUEBA (DRY_RUN): no se ha impreso nada.", **_pdf_ref(_current_attachment(odoo, task))}
    return {"ok": True, "simulado": settings.mode != "real", "modo_aviso": None, **_print(odoo, task)}


def _print(odoo, task: dict) -> dict:
    try:
        att = odoo.print_order(task["id"], task["company_id"][0])
    except AppError:
        raise AppError("Los cambios se han guardado, pero no se ha podido generar la nueva orden. Pulsa «Reintentar PDF».", 502, "pdf_fallo")
    except Exception:
        log.exception("Fallo al imprimir la tarea %s", task["id"])
        raise AppError("Los cambios se han guardado, pero no se ha podido generar la nueva orden. Pulsa «Reintentar PDF».", 502, "pdf_fallo")
    return _pdf_ref(att)


def _current_attachment(odoo, task: dict) -> dict:
    ref = task.get("tms_transport_order_attachment_id")
    att = ref and odoo.read_attachment(ref[0])
    if not att:
        raise AppError("Esta orden todavía no tiene un PDF generado.", 404, "sin_pdf")
    return att


def _pdf_ref(att: dict) -> dict:
    return {"adjunto_id": att["id"], "pdf_url": f"/api/pdf/{att['id']}?token={att['access_token']}"}


def fmt_num(n) -> str:
    n = float(n)
    text = f"{int(n):,}" if n == int(n) else f"{n:,.2f}".rstrip("0").rstrip(".")
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".")


def _describe(odoo, packages: dict, changes: dict) -> list[tuple[str, list[str]]]:
    """[(bulto, ['Mercancía: A → B', ...])] con los valores de antes y de ahora."""
    out = []
    for pid, vals in changes.items():
        p = packages[pid]
        rows = []
        for field, after in vals.items():
            if field == "goods_id":
                before_s = (p.get("goods_id") or [0, "(en blanco)"])[1]
                after_s = odoo.goods_name(after) if after else "(en blanco)"
            else:
                before_s, after_s = fmt_num(p.get(field) or 0), fmt_num(after)
                if field == "shipping_weight":
                    before_s, after_s = before_s + " kg", after_s + " kg"
            rows.append(f"{FIELD_LABELS[field]}: {before_s} → {after_s}")
        out.append((p["name"], rows))
    return out


def _note_html(lines: list[tuple[str, list[str]]], verified: bool) -> str:
    when = datetime.now(ZoneInfo("Europe/Madrid")).strftime("%d/%m/%Y %H:%M")
    items = "".join(f"<li><b>{html.escape(name)}</b>: {html.escape('; '.join(rows))}</li>" for name, rows in lines)
    who = "orden abierta con el QR verificado" if verified else "orden abierta por número de pedido (sin QR)"
    return (f"<p><b>Modificado desde la app de conductores</b> — {when}</p><ul>{items}</ul>"
            f"<p>Conductor: sin identificar ({who}).</p>")

