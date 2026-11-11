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


def normalize_plate(value: str) -> str:
    return "".join(char for char in (value or "").upper() if char.isalnum())


def _plate_pattern(value: str) -> str:
    return "%" + "%".join(normalize_plate(value)) + "%"


def normalize_goods_name(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip().upper()


def _vehicle_company_id(vehicle: dict) -> int | None:
    company = vehicle.get("company_id")
    return company[0] if company else None


def _vehicle_view(vehicle: dict) -> dict:
    model = _m2o(vehicle.get("model_id"))
    return {
        "id": vehicle["id"],
        "matricula": vehicle.get("license_plate") or "",
        "modelo": model["name"] if model else "",
        "tipo": vehicle.get("vehicle_type") or "",
        "company_id": _vehicle_company_id(vehicle),
    }


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


def _task_choices(odoo, settings: Settings, tasks: list[dict], order_name: str, company_id: int) -> dict:
    package_ids = list(dict.fromkeys(
        package_id for task in tasks for package_id in (task.get("tms_package_ids") or [])
    ))
    packages = {p["id"]: p for p in odoo.read_packages(package_ids)}
    vehicle_ids = list(dict.fromkeys(
        ref[0] for task in tasks for ref in (task.get("tractor_id"), task.get("trailer_id")) if ref
    ))
    vehicles = {v["id"]: _vehicle_view(v) for v in odoo.read_vehicles(vehicle_ids)}
    choices = []
    for task in tasks:
        task_packages = [packages[pid] for pid in (task.get("tms_package_ids") or []) if pid in packages]
        task_packages.sort(key=lambda p: p.get("pickup_date") or "")
        date = next((p["pickup_date"] for p in task_packages if p.get("pickup_date")), None)
        origin = next((p["shipping_origin_id"][1] for p in task_packages if p.get("shipping_origin_id")), "")
        destination = next((p["shipping_destination_id"][1] for p in reversed(task_packages) if p.get("shipping_destination_id")), "")
        editable, reason = is_editable(task, settings.editable_stages)
        if not task_packages:
            editable, reason = False, NO_PACKAGES_MSG

        def plate(field: str) -> str:
            ref = task.get(field)
            if not ref:
                return ""
            vehicle = vehicles.get(ref[0])
            return (vehicle and vehicle["matricula"]) or ref[1]

        stage = task.get("stage_id") or [0, ""]
        choices.append({
            "id": task["id"],
            "nombre": task.get("name") or f"Tarea {task['id']}",
            "fecha_carga": date,
            "origen": origin,
            "destino": destination,
            "tractora": plate("tractor_id"),
            "remolque": plate("trailer_id"),
            "etapa": stage[1],
            "editable": editable,
            "motivo_no_editable": reason,
        })
    choices.sort(key=lambda item: (item["fecha_carga"] is None, item["fecha_carga"] or "", item["nombre"].casefold()))
    return {"seleccion_requerida": True, "pedido": order_name, "ordenes": choices}


def resolve(
    odoo,
    settings: Settings,
    qr_text: str | None = None,
    order_name: str | None = None,
    company: str | None = None,
    selected_task_id: int | None = None,
) -> dict:
    not_found = AppError("No encontramos esta orden. Revisa el número o avisa a la oficina.", 404, "no_encontrada")
    verified = False
    outdated = False
    order_label = None

    if qr_text:
        if selected_task_id is not None:
            raise AppError("No se puede elegir una tarea al abrir una orden por QR.", 400, "dato_invalido")
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
        tasks = [
            item for item in odoo.find_transport_tasks(name, company_id)
            if is_transport_task(item) and (item.get("company_id") or [None])[0] == company_id
        ]
        if not tasks:
            raise not_found
        if selected_task_id is not None:
            task = next((item for item in tasks if item["id"] == selected_task_id), None)
            if task is None:
                raise AppError("La tarea elegida no pertenece a este pedido y empresa. Vuelve a buscar el pedido.", 400, "tarea_ajena")
        elif len(tasks) > 1:
            return _task_choices(odoo, settings, tasks, name, company_id)
        else:
            task = tasks[0]
        order_label = name
    else:
        if selected_task_id is not None:
            raise AppError("No se puede elegir una tarea sin pedido.", 400, "dato_invalido")
        raise AppError("Escanea el código o escribe el número de pedido.", 400, "faltan_datos")

    packages = odoo.read_packages(task.get("tms_package_ids") or [])
    editable, reason = is_editable(task, settings.editable_stages)
    if not packages:                       # p. ej. órdenes de contenedor: tienen PDF pero no bultos tms.package
        editable, reason = False, NO_PACKAGES_MSG
    vehicle_refs = [task.get(field) for field in ("tractor_id", "trailer_id") if task.get(field)]
    vehicle_records = {
        vehicle["id"]: _vehicle_view(vehicle)
        for vehicle in odoo.read_vehicles([ref[0] for ref in vehicle_refs])
    }
    session = sign_session(
        settings.secret_key, task["id"], {p["id"]: p["write_date"] for p in packages},
        verified=verified, task_write_date=task.get("write_date"),
    )
    return {
        "tarea": {
            "id": task["id"], "nombre": task["name"], "pedido": order_label or None,
            "compania": _m2o(task.get("company_id")), "etapa": _m2o(task.get("stage_id")),
        },
        "vehiculos": {
            "tractora": vehicle_records.get((task.get("tractor_id") or [None])[0]),
            "remolque": vehicle_records.get((task.get("trailer_id") or [None])[0]),
        },
        "bultos": [_package_view(p) for p in packages],
        "version_antigua": outdated,
        "aviso": "Esta orden tiene una versión más nueva." if outdated else None,
        "verificado_por_qr": verified,
        "editable": editable,
        "motivo_no_editable": reason,
        "umbral_peso_kg": settings.weight_warning_kg,   # la pantalla pide confirmar a partir de este peso
        "sesion": session,
    }


def lookup_vehicles(odoo, settings: Settings, sesion: str, task_id: int, role: str, plate: str) -> dict:
    if role not in ("tractor", "trailer"):
        raise AppError("Tipo de vehículo no válido.", 400, "dato_invalido")
    ses = verify_session(settings.secret_key, sesion)
    if ses["task_id"] != task_id:
        raise AppError("La sesión no corresponde a esta orden. Vuelve a escanearla.", 401, "sesion_invalida")
    task = odoo.read_task(task_id)
    if not task or not is_transport_task(task):
        raise AppError("No encontramos esta orden. Avisa a la oficina.", 404, "no_encontrada")
    normalized = normalize_plate(plate)
    if not normalized:
        return {"encontrada": False, "resultados": [], "matricula_normalizada": ""}
    company_id = (task.get("company_id") or [None])[0]
    candidates = odoo.search_vehicles(_plate_pattern(normalized), company_id)
    exact = [
        _vehicle_view(vehicle) for vehicle in candidates
        if normalize_plate(vehicle.get("license_plate") or "") == normalized
        and _vehicle_company_id(vehicle) in (None, company_id)
    ]
    return {
        "encontrada": bool(exact),
        "resultados": exact[:10],
        "matricula_normalizada": normalized,
    }


# ---------------------------------------------------------------------------
# Guardado
# ---------------------------------------------------------------------------
FIELD_LABELS = {"goods_id": "Mercancía", "number_of_packages": "Bultos", "shipping_weight": "Kilos"}


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
    if c.get("new_goods_name") is not None:
        if c.get("goods_id") is not None:
            raise bad("Elige una mercancía existente o crea una nueva, pero no ambas.")
        name = normalize_goods_name(c["new_goods_name"])
        if not name or len(name) > 60:
            raise bad("El nombre de la mercancía tiene que tener entre 1 y 60 caracteres.")
        out["new_goods_name"] = name
    for field, label in (("number_of_packages", "Bultos"),):
        if c.get(field) is not None:
            if not isinstance(c[field], int) or isinstance(c[field], bool) or c[field] < 0:
                raise bad(f"{label} tiene que ser un número entero, 0 o más.")
            out[field] = c[field]
    if c.get("shipping_weight") is not None:
        w = c["shipping_weight"]
        if not math.isfinite(w) or w <= 0:
            raise bad("El peso tiene que ser mayor que 0.")
        out["shipping_weight"] = float(w)
    return out


def _vehicle_label(vehicle: dict | None) -> str:
    if not vehicle:
        return "(en blanco)"
    plate = vehicle.get("license_plate") or "(sin matrícula)"
    model = _m2o(vehicle.get("model_id"))
    return f"{plate} ({model['name']})" if model else plate


def _select_vehicle(odoo, role: str, selection: dict, company_id: int, current_id: int | None) -> dict | None:
    label = "tractora" if role == "tractora" else "remolque"
    expected_type = "tractor" if role == "tractora" else "trailer"
    normalized = normalize_plate(selection.get("matricula") or "")
    vehicle_id = selection.get("vehicle_id")
    if not normalized:
        if role == "tractora":
            raise AppError("La tractora es obligatoria.", 400, "tractora_obligatoria")
        if vehicle_id:
            raise AppError("El remolque no es válido. Vuelve a comprobar la matrícula.", 400, "vehiculo_no_encontrado")
        return None

    candidates = odoo.search_vehicles(_plate_pattern(normalized), company_id)
    exact = [
        vehicle for vehicle in candidates
        if normalize_plate(vehicle.get("license_plate") or "") == normalized
        and _vehicle_company_id(vehicle) in (None, company_id)
    ]
    if not exact:
        raise AppError(
            f"No encontramos la matrícula {normalized}. Revísala o avisa a tu responsable de tráfico.",
            400, "vehiculo_no_encontrado",
        )
    if vehicle_id is None:
        if len(exact) != 1:
            raise AppError(
                f"Hay varias coincidencias para {normalized}. Elige una de la lista.",
                409, "vehiculo_duplicado",
            )
        selected = exact[0]
    else:
        selected = next((vehicle for vehicle in exact if vehicle["id"] == vehicle_id), None)
        if selected is None:
            raise AppError("La matrícula elegida ha cambiado. Vuelve a comprobarla.", 409, "vehiculo_cambiado")

    if selected.get("vehicle_type") and selected["vehicle_type"] != expected_type \
            and selected["id"] != current_id and not selection.get("confirmar_tipo_incorrecto"):
        actual = "remolque" if selected["vehicle_type"] == "trailer" else "tractora"
        raise AppError(
            f"La matrícula {selected.get('license_plate')} corresponde a un {actual}, no a {label}. "
            "Confirma que quieres continuar.",
            409, "tipo_vehiculo_incorrecto",
        )
    return selected


def update(
    odoo,
    settings: Settings,
    sesion: str,
    task_id: int,
    cambios: list[dict],
    vehiculos: dict | None = None,
) -> dict:
    """Valida todo en el servidor (no se fía del cliente) y guarda los cambios."""
    ses = verify_session(settings.secret_key, sesion)
    if ses["task_id"] != task_id:
        raise AppError("La sesión no corresponde a esta orden. Vuelve a escanearla.", 401, "sesion_invalida")
    if not cambios and not vehiculos:
        raise AppError("No has cambiado ningún dato.", 400, "sin_cambios")

    task = odoo.read_task(task_id)
    if not task or not is_transport_task(task):
        raise AppError("No encontramos esta orden. Avisa a la oficina.", 404, "no_encontrada")
    if not ses.get("task_write_date") or task.get("write_date") != ses["task_write_date"]:
        raise AppError("Esta orden ha cambiado mientras la editabas. Vuelve a cargarla.", 409, "datos_cambiados")
    packages = {p["id"]: p for p in odoo.read_packages(task.get("tms_package_ids") or [])}
    if not packages:
        raise AppError(NO_PACKAGES_MSG, 409, "sin_bultos")

    editable, reason = is_editable(task, settings.editable_stages)
    if not editable:
        raise AppError(reason, 409, "no_editable")

    changes: dict[int, dict] = {}
    seen_packages: set[int] = set()
    for c in cambios:
        pid = c["package_id"]
        if pid not in packages or pid not in ses["write_dates"]:
            raise AppError("Ese bulto no pertenece a esta orden.", 400, "bulto_ajeno")
        if pid in seen_packages:
            raise AppError("Hay un bulto repetido en los cambios. Vuelve a revisar la orden.", 400, "dato_invalido")
        seen_packages.add(pid)
        if packages[pid]["write_date"] != ses["write_dates"][pid]:
            raise AppError("Esta orden ha cambiado mientras la editabas. Vuelve a cargarla.", 409, "datos_cambiados")
        cur = packages[pid]
        current = {"goods_id": (cur.get("goods_id") or [None])[0], "number_of_packages": cur.get("number_of_packages"),
                   "shipping_weight": cur.get("shipping_weight")}
        # Solo se validan los campos que CAMBIAN: un bulto vacío o con peso 0 que no se toca no bloquea el guardado.
        changed = {}
        for k in current:
            v = c.get(k)
            if v is None:
                continue
            if (None if (k == "goods_id" and v == 0) else v) != current[k]:   # goods_id 0 = en blanco = None en Odoo
                changed[k] = v
        new_goods_name = c.get("new_goods_name")
        if new_goods_name is not None and normalize_goods_name(new_goods_name) != normalize_goods_name((cur.get("goods_id") or [None, ""])[1]):
            changed["new_goods_name"] = new_goods_name
        diff = _clean_change(odoo, changed) if changed else {}
        if diff:
            changes.setdefault(pid, {}).update(diff)

    company_id = (task.get("company_id") or [None])[0]
    if not company_id:
        raise AppError("La orden no tiene una empresa válida. Avisa a la oficina.", 409, "compania_invalida")
    current_vehicle_ids = {
        "tractora": (task.get("tractor_id") or [None])[0],
        "remolque": (task.get("trailer_id") or [None])[0],
    }
    current_vehicles = {
        vehicle["id"]: vehicle
        for vehicle in odoo.read_vehicles([vehicle_id for vehicle_id in current_vehicle_ids.values() if vehicle_id])
    }
    vehicle_changes: dict[str, int | bool] = {}
    vehicle_lines: list[str] = []
    effective_tractor_id = current_vehicle_ids["tractora"]
    for role, field in (("tractora", "tractor_id"), ("remolque", "trailer_id")):
        current_id = current_vehicle_ids[role]
        current_vehicle = current_vehicles.get(current_id)
        selection = (vehiculos or {}).get(role)
        if selection is None:
            if role in (vehiculos or {}):
                selection = {"vehicle_id": None, "matricula": ""}
            else:
                selection = {
                    "vehicle_id": current_id,
                    "matricula": current_vehicle.get("license_plate", "") if current_vehicle else "",
                    "confirmar_tipo_incorrecto": True,
                }
        selected = _select_vehicle(odoo, role, selection, company_id, current_id)
        selected_id = selected["id"] if selected else None
        if role == "tractora":
            effective_tractor_id = selected_id
        if selected_id != current_id:
            vehicle_changes[field] = selected_id or False
            vehicle_lines.append(
                f"{'Tractora' if role == 'tractora' else 'Remolque'}: "
                f"{_vehicle_label(current_vehicle)} → {_vehicle_label(selected)}"
            )

    if not effective_tractor_id:
        raise AppError("La tractora es obligatoria.", 400, "tractora_obligatoria")
    if not changes and not vehicle_changes:
        raise AppError("No has cambiado ningún dato.", 400, "sin_cambios")

    pending_goods: dict[str, list[int]] = {}
    for pid, vals in list(changes.items()):
        name = vals.pop("new_goods_name", None)
        pending_creation = False
        if name:
            same_name = next(
                (g for g in odoo.search_goods(name, 10) if normalize_goods_name(g["name"]) == name),
                None,
            )
            if same_name:
                vals["goods_id"] = same_name["id"]
                if same_name["id"] == (packages[pid].get("goods_id") or [None])[0]:
                    vals.pop("goods_id")
            else:
                pending_goods.setdefault(name, []).append(pid)
                pending_creation = True
        if not vals and not pending_creation:
            del changes[pid]
    if not changes and not vehicle_changes:
        raise AppError("No has cambiado ningún dato.", 400, "sin_cambios")

    warnings = []
    for pid, vals in changes.items():
        if vals.get("shipping_weight", 0) > settings.weight_warning_kg:
            warnings.append(f"{packages[pid]['name']}: peso superior a {fmt_num(settings.weight_warning_kg)} kg; es posible que el conjunto supere la MMA autorizada.")

    base = {
        "ok": True, "bultos_modificados": len(changes),
        "vehiculos_modificados": len(vehicle_changes), "avisos": warnings,
    }

    if settings.dry_run:
        # DRY_RUN: no se escribe, ni se publica, ni se imprime nada en Odoo. Se devuelve la orden actual.
        log.info("DRY_RUN tarea=%s bultos=%s vehiculos=%s: no se escribe en Odoo", task_id, sorted(changes), sorted(vehicle_changes))
        current = _current_attachment(odoo, task)
        return {**base, "simulado": True, "modo_aviso": "MODO PRUEBA (DRY_RUN): no se ha guardado nada en Odoo. Este PDF es la orden actual, sin los cambios.", **_pdf_ref(current)}

    created_goods: dict[str, int] = {}
    created_names: set[str] = set()
    try:
        for name in pending_goods:
            exact = next(
                (g for g in odoo.search_goods(name, 10) if normalize_goods_name(g["name"]) == name),
                None,
            )
            if exact:
                created_goods[name] = exact["id"]
            else:
                created_goods[name] = odoo.create_goods(name)
                created_names.add(name)
        for name, package_ids in pending_goods.items():
            for pid in package_ids:
                changes[pid]["goods_id"] = created_goods[name]
    except Exception as e:
        log.exception("No se pudo crear mercancía nueva tarea=%s", task_id)
        raise AppError("No se ha podido crear la mercancía nueva. No se han guardado los cambios.", 502, "mercancia_crear_fallo") from e

    created_package_ids = {
        pid for name, package_ids in pending_goods.items() if name in created_names for pid in package_ids
    }
    lines = _describe(odoo, packages, changes, created_package_ids)
    if vehicle_lines:
        lines.insert(0, ("Vehículos de la orden", vehicle_lines))
    lines.insert(0, ("Tarea", [task.get("name") or f"Tarea {task_id}"]))

    done = []
    try:
        if vehicle_changes:
            odoo.write_task_vehicles(task_id, vehicle_changes)
            done.append("vehiculos")
        for pid, vals in changes.items():
            odoo.write_package(pid, vals)
            done.append(f"bulto:{pid}")
    except Exception:
        log.exception("Guardado incompleto tarea=%s escritos=%s", task_id, done)
        raise AppError("No se han podido guardar todos los cambios. Vuelve a escanear la orden y revísala.", 502, "guardado_fallo")

    try:
        odoo.post_note(task_id, _note_html(lines, ses["verified"], warnings))
    except Exception:
        log.exception("No se pudo publicar la nota en la tarea %s", task_id)  # los datos ya están guardados: se sigue

    return {
        **base, "mercancias_nuevas_creadas": len(created_names),
        "simulado": settings.mode != "real", **_print(odoo, task),
        "modo_aviso": None if settings.mode == "real" else "MODO SIMULADO: no se ha escrito nada en Odoo. El PDF es una muestra.",
    }


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
    except AppError as e:
        message = "Los cambios se han guardado, pero no se ha podido generar la nueva orden. Pulsa «Reintentar PDF»."
        if e.code == "odoo_mantenimiento":
            message += " (el sistema está en mantenimiento)"
        raise AppError(message, 502, "pdf_fallo") from e
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


def _describe(
    odoo,
    packages: dict,
    changes: dict,
    created_goods_packages: set[int] | None = None,
) -> list[tuple[str, list[str]]]:
    """[(bulto, ['Mercancía: A → B', ...])] con los valores de antes y de ahora."""
    created_goods_packages = created_goods_packages or set()
    out = []
    for pid, vals in changes.items():
        p = packages[pid]
        rows = []
        for field, after in vals.items():
            if field == "goods_id":
                before_s = (p.get("goods_id") or [0, "(en blanco)"])[1]
                after_s = odoo.goods_name(after) if after else "(en blanco)"
                if pid in created_goods_packages:
                    after_s += " (mercancía nueva creada desde la app)"
            else:
                before_s, after_s = fmt_num(p.get(field) or 0), fmt_num(after)
                if field == "shipping_weight":
                    before_s, after_s = before_s + " kg", after_s + " kg"
            rows.append(f"{FIELD_LABELS[field]}: {before_s} → {after_s}")
        out.append((p["name"], rows))
    return out


def _note_html(lines: list[tuple[str, list[str]]], verified: bool, avisos: list[str] | None = None) -> str:
    when = datetime.now(ZoneInfo("Europe/Madrid")).strftime("%d/%m/%Y %H:%M")
    items = "".join(f"<li><b>{html.escape(name)}</b>: {html.escape('; '.join(rows))}</li>" for name, rows in lines)
    who = "orden abierta con el QR verificado" if verified else "orden abierta por número de pedido (sin QR)"
    aviso = "".join(f"<p><b>Aviso:</b> {html.escape(a)} La app pidió confirmar el peso.</p>" for a in (avisos or []))
    return (f"<p><b>Modificado desde la app de conductores</b> — {when}</p><ul>{items}</ul>{aviso}"
            f"<p>Conductor: sin identificar ({who}).</p>")
