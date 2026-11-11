"""Cliente Odoo con lista blanca. NO existe ninguna llamada genérica: cada operación permitida es un método fijo.

Lectura de tareas/bultos/mercancías/vehículos/adjuntos; escritura de tres campos en tms.package y dos en project.task;
creación de mercancías solo con name; message_post SOLO en project.task e impresión solo del informe de transporte.
"""
import json
import logging
from urllib.parse import urlsplit

import httpx

from .config import Settings
from .errors import AppError

log = logging.getLogger("conductores")

ORDER_MODEL = "sale.order"
TASK_MODEL = "project.task"
PACKAGE_MODEL = "tms.package"
GOODS_MODEL = "tms.goods"
FLEET_MODEL = "fleet.vehicle"
ATTACHMENT_MODEL = "ir.attachment"

TASK_FIELDS = [
    "name", "company_id", "stage_id", "is_transport_order", "tms_package_ids",
    "tms_transport_order_attachment_id", "write_date", "tractor_id", "trailer_id",
]
PACKAGE_FIELDS = [
    "name", "carrier_tracking_ref", "goods_id", "number_of_packages", "euro_pallet_qty", "shipping_weight",
    "shipping_origin_id", "shipping_destination_id", "pickup_date", "forecast_unload_date", "state", "write_date",
]
ATTACHMENT_FIELDS = ["res_model", "res_id", "access_token", "name"]
PACKAGE_WRITABLE_FIELDS = frozenset({"goods_id", "number_of_packages", "shipping_weight"})
EDITABLE_FIELDS = PACKAGE_WRITABLE_FIELDS
TASK_WRITABLE_FIELDS = frozenset({"tractor_id", "trailer_id"})
REPORT_NAME = "tms_reporting.tms_transportation_order"
MAINTENANCE_MESSAGE = "El sistema está en mantenimiento. Inténtalo de nuevo en unos minutos."
NO_RESPONSE_MESSAGE = "El sistema no responde ahora mismo. Inténtalo de nuevo en unos minutos."
ACCESS_MESSAGE = "Hay un problema de acceso al sistema. Avisa a la oficina."


def _is_html_response(response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "").lower()
    prefix = response.content[:2048].lstrip().lower()
    return "text/html" in content_type or prefix.startswith((b"<!doctype html", b"<html")) or b"<html" in prefix


def _has_maintenance_marker(response: httpx.Response) -> bool:
    body = response.content[:8192].lower()
    return any(marker in body for marker in (b"maintenance", b"mantenimiento", b"under maintenance", b"en mantenimiento"))


def _check_upstream_response(
    response: httpx.Response,
    operation: str,
    *,
    authentication: bool = False,
    html_is_unexpected: bool = True,
) -> None:
    status = response.status_code
    content_type = response.headers.get("content-type", "unknown").split(";", 1)[0][:80]
    is_html = _is_html_response(response)
    if status in (502, 503, 504) or _has_maintenance_marker(response) or (is_html and html_is_unexpected):
        log.warning("Odoo maintenance operation=%s status=%s content_type=%s", operation, status, content_type)
        raise AppError(MAINTENANCE_MESSAGE, 503, "odoo_mantenimiento")
    if authentication and status in (401, 403):
        log.warning("Odoo technical login rejected operation=%s status=%s", operation, status)
        raise AppError(ACCESS_MESSAGE, 502, "odoo_acceso")
    if status >= 400:
        log.warning("Odoo HTTP error operation=%s status=%s content_type=%s", operation, status, content_type)
        raise AppError("El sistema no ha podido responder. Inténtalo de nuevo.", 502, "odoo_error")


def _network_error(operation: str, error: httpx.HTTPError) -> AppError:
    log.warning("Odoo request failed operation=%s exception=%s", operation, type(error).__name__)
    return AppError(NO_RESPONSE_MESSAGE, 503, "odoo_no_responde")

# (modelo, método) permitidos. Todo lo demás se rechaza antes de salir hacia Odoo.
ALLOWED_CALLS = {
    (ORDER_MODEL, "search_read"),
    (TASK_MODEL, "read"),
    (TASK_MODEL, "search_read"),
    (TASK_MODEL, "fields_get"),
    (FLEET_MODEL, "search_read"),
    (FLEET_MODEL, "read"),
    (GOODS_MODEL, "create"),
    (PACKAGE_MODEL, "read"),
    (PACKAGE_MODEL, "fields_get"),
    (GOODS_MODEL, "name_search"),
    (GOODS_MODEL, "read"),
    (ATTACHMENT_MODEL, "read"),
    ("res.company", "search_read"),
    ("ir.actions.report", "search_read"),   # solo vía find_report (diagnóstico)
    (PACKAGE_MODEL, "write"),        # solo campos PACKAGE_WRITABLE_FIELDS
    (TASK_MODEL, "write"),           # solo tractor_id y trailer_id
    (TASK_MODEL, "message_post"),    # solo vía post_note
}

# Permisos que se comprueban sin ejecutar nada: (modelo, operación, ¿debería tenerlo el usuario técnico?)
EXPECTED_PERMISSIONS = [
    (TASK_MODEL, "read", True), (TASK_MODEL, "write", True),       # write: necesario para publicar la nota (message_post)
    (TASK_MODEL, "unlink", False),
    (PACKAGE_MODEL, "read", True), (PACKAGE_MODEL, "write", True),
    (PACKAGE_MODEL, "create", False), (PACKAGE_MODEL, "unlink", False),
    (GOODS_MODEL, "read", True), (GOODS_MODEL, "create", True), (GOODS_MODEL, "write", False),
    (FLEET_MODEL, "read", True),
    (ATTACHMENT_MODEL, "read", True),
    (ORDER_MODEL, "read", True), (ORDER_MODEL, "write", False), (ORDER_MODEL, "unlink", False),
]
ALLOWED_CALLS |= {(m, "check_access_rights") for m, _, _ in EXPECTED_PERMISSIONS}


class OdooClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self._uid: int | None = None
        self._http = httpx.Client(timeout=25)   # JSON-RPC y sesión web (informe)
        self._anon = httpx.Client(timeout=40)   # descarga por token, SIN cookies de sesión
        self._major_cache: int | None = None

    # ---- bajo nivel (privado) -------------------------------------------------
    def _rpc(self, service: str, method: str, args: list, timeout: float | None = None):
        payload = {"jsonrpc": "2.0", "method": "call", "params": {"service": service, "method": method, "args": args}, "id": 1}
        try:
            options = {"timeout": timeout} if timeout is not None else {}
            r = self._http.post(f"{self.s.odoo_url}/jsonrpc", json=payload, **options)
        except httpx.HTTPError as e:
            raise _network_error("jsonrpc", e) from e
        _check_upstream_response(r, "jsonrpc", authentication=service == "common" and method == "authenticate")
        try:
            data = r.json()
        except ValueError as e:
            log.warning("Odoo invalid response operation=jsonrpc status=%s content_type=%s",
                        r.status_code, r.headers.get("content-type", "unknown").split(";", 1)[0][:80])
            raise AppError("El sistema no ha podido responder. Inténtalo de nuevo.", 502, "odoo_error") from e
        if "error" in data:
            error = data["error"]
            error_type = error.get("data", {}).get("name", "jsonrpc_error") if isinstance(error, dict) else "jsonrpc_error"
            if service == "common" and method == "authenticate":
                host = urlsplit(self.s.odoo_url).hostname or "sin-configurar"
                log.warning("Odoo technical login rejected host=%s reason=credentials_or_2fa", host)
                raise AppError(ACCESS_MESSAGE, 502, "odoo_acceso")
            log.warning("Odoo JSON-RPC error service=%s method=%s type=%s", service, method, str(error_type)[:120])
            raise AppError("El sistema no ha podido responder. Inténtalo de nuevo.", 502, "odoo_error")
        return data["result"]

    def _login(self) -> int:
        if self._uid is None:
            uid = self._rpc("common", "authenticate", [self.s.odoo_db, self.s.odoo_user, self.s.odoo_password, {}])
            if not uid:
                host = urlsplit(self.s.odoo_url).hostname or "sin-configurar"
                log.warning("Odoo technical login rejected host=%s reason=credentials_or_2fa", host)
                raise AppError(ACCESS_MESSAGE, 502, "odoo_acceso")
            self._uid = uid
        return self._uid

    def probe(self, timeout: float = 2.0) -> str:
        """Comprobación de solo lectura y acotada: conecta y valida el acceso técnico."""
        host = urlsplit(self.s.odoo_url).hostname or "sin-configurar"
        if not self.s.odoo_url:
            log.warning("Odoo health check failed host=%s reason=url_not_configured", host)
            return "no_responde"
        try:
            uid = self._rpc(
                "common", "authenticate",
                [self.s.odoo_db, self.s.odoo_user, self.s.odoo_password, {}],
                timeout=timeout,
            )
        except AppError as e:
            return {
                "odoo_mantenimiento": "mantenimiento",
                "odoo_no_responde": "no_responde",
                "odoo_acceso": "acceso",
            }.get(e.code, "no_responde")
        if not uid:
            log.warning("Odoo health check login rejected host=%s reason=credentials_or_2fa", host)
            return "acceso"
        return "ok"

    def _call(self, model: str, method: str, args: list, kwargs: dict | None = None):
        if (model, method) not in ALLOWED_CALLS:
            raise PermissionError(f"Operación no permitida: {model}.{method}")
        if model == PACKAGE_MODEL and method == "write":
            if len(args) != 2 or not isinstance(args[1], dict) or not args[1] or not set(args[1]) <= PACKAGE_WRITABLE_FIELDS:
                raise PermissionError("Campos no permitidos en tms.package.write")
        if model == TASK_MODEL and method == "write":
            if len(args) != 2 or not isinstance(args[1], dict) or not args[1] or not set(args[1]) <= TASK_WRITABLE_FIELDS:
                raise PermissionError("Campos no permitidos en project.task.write")
            if any(value is not False and (not isinstance(value, int) or value <= 0) for value in args[1].values()):
                raise PermissionError("Vehículo no válido")
        if model == GOODS_MODEL and method == "create":
            if len(args) != 1 or not isinstance(args[0], dict) or set(args[0]) != {"name"} \
                    or not isinstance(args[0]["name"], str) or not args[0]["name"].strip():
                raise PermissionError("Solo se permite crear tms.goods con el campo name")
        kwargs = dict(kwargs or {})
        kwargs["context"] = {"allowed_company_ids": self.s.company_ids, "lang": "es_ES"}
        uid = self._login()
        return self._rpc("object", "execute_kw", [self.s.odoo_db, uid, self.s.odoo_password, model, method, args, kwargs])

    # ---- operaciones permitidas ----------------------------------------------
    def find_transport_tasks(self, order_name: str, company_id: int) -> list[dict]:
        orders = self._call(
            ORDER_MODEL, "search_read", [[["name", "=", order_name], ["company_id", "=", company_id]]],
            {"fields": ["tasks_ids"], "limit": 1},
        )
        if not orders or not orders[0]["tasks_ids"]:
            return []
        tasks = self._call(
            TASK_MODEL, "search_read",
            [[["id", "in", orders[0]["tasks_ids"]], "|", "|", ["is_transport_order", "=", True],
              ["tms_transport_order_attachment_id", "!=", False], ["tms_package_ids", "!=", False]]],
            {"fields": TASK_FIELDS, "order": "is_transport_order desc, id desc"},  # primero las marcadas, luego la más reciente
        )
        return tasks

    def read_task(self, task_id: int) -> dict | None:
        rows = self._call(TASK_MODEL, "read", [[task_id]], {"fields": TASK_FIELDS})
        return rows[0] if rows else None

    def read_vehicles(self, vehicle_ids: list[int]) -> list[dict]:
        if not vehicle_ids:
            return []
        return self._call(
            FLEET_MODEL, "read", [vehicle_ids],
            {"fields": ["license_plate", "display_name", "model_id", "vehicle_type", "company_id"]},
        )

    def search_vehicles(self, pattern: str, company_id: int, limit: int = 50) -> list[dict]:
        domain = [
            "&", ["license_plate", "ilike", pattern], "|",
            ["company_id", "=", False], ["company_id", "=", company_id],
        ]
        return self._call(
            FLEET_MODEL, "search_read", [domain],
            {
                "fields": ["license_plate", "display_name", "model_id", "vehicle_type", "company_id"],
                "limit": min(max(limit, 1), 50),
                "order": "license_plate, id",
            },
        )

    def read_packages(self, package_ids: list[int]) -> list[dict]:
        if not package_ids:
            return []
        return self._call(PACKAGE_MODEL, "read", [package_ids], {"fields": PACKAGE_FIELDS})

    def read_attachment(self, attachment_id: int) -> dict | None:
        rows = self._call(ATTACHMENT_MODEL, "read", [[attachment_id]], {"fields": ATTACHMENT_FIELDS})
        return rows[0] if rows else None

    def search_goods(self, query: str, limit: int = 10) -> list[dict]:
        limit = min(limit, 10)
        found = self._call(GOODS_MODEL, "name_search", [query], {"limit": limit})
        if len(found) < limit and len(query.strip()) >= 4:
            found += self._call(GOODS_MODEL, "name_search", [query.strip()[:4]], {"limit": limit})
        unique = {goods_id: name for goods_id, name in found}
        return [{"id": goods_id, "name": name} for goods_id, name in list(unique.items())[:limit]]

    # ---- escritura (solo lo imprescindible) ------------------------------------
    def write_package(self, package_id: int, values: dict) -> None:
        if not values or not set(values) <= PACKAGE_WRITABLE_FIELDS:
            raise PermissionError("Campo no permitido")
        self._call(PACKAGE_MODEL, "write", [[int(package_id)], values])

    def write_task_vehicles(self, task_id: int, values: dict) -> None:
        if not values or not set(values) <= TASK_WRITABLE_FIELDS:
            raise PermissionError("Campo no permitido")
        self._call(TASK_MODEL, "write", [[int(task_id)], values])

    def create_goods(self, name: str) -> int:
        if not name or not name.strip():
            raise ValueError("El nombre de la mercancía no puede estar vacío.")
        return self._call(GOODS_MODEL, "create", [{"name": name}])

    def _major(self) -> int:
        if self._major_cache is None:
            self._major_cache = int(self._rpc("common", "version", [])["server_version_info"][0])
        return self._major_cache

    def post_note(self, task_id: int, html_body: str) -> None:
        """Nota interna (sin avisar a seguidores) en la tarea."""
        kwargs = {"body": html_body, "message_type": "comment", "subtype_xmlid": "mail.mt_note"}
        if self._major() >= 18:
            # Odoo 18 escapa el cuerpo que llega por RPC (se vería el HTML escrito); hay que declararlo como HTML.
            kwargs["body_is_html"] = True
        self._call(TASK_MODEL, "message_post", [[int(task_id)]], kwargs)

    def print_order(self, task_id: int, company_id: int) -> dict:
        """Imprime la orden de transporte (Odoo crea un adjunto nuevo en la tarea) y devuelve ese adjunto."""
        try:
            r = self._http.post(f"{self.s.odoo_url}/web/session/authenticate", json={
                "jsonrpc": "2.0", "method": "call",
                "params": {"db": self.s.odoo_db, "login": self.s.odoo_user, "password": self.s.odoo_web_password}})
        except httpx.HTTPError as e:
            raise _network_error("web_authenticate", e) from e
        _check_upstream_response(r, "web_authenticate", authentication=True)
        try:
            res = r.json().get("result") or {}
        except ValueError as e:
            log.warning("Odoo invalid response operation=web_authenticate status=%s content_type=%s",
                        r.status_code, r.headers.get("content-type", "unknown").split(";", 1)[0][:80])
            raise AppError("El sistema no ha podido responder. Inténtalo de nuevo.", 502, "odoo_error") from e
        if not res.get("uid"):
            host = urlsplit(self.s.odoo_url).hostname or "sin-configurar"
            log.warning("Odoo sesión web no se abrió host=%s task=%s reason=credentials_or_2fa", host, task_id)
            raise AppError(ACCESS_MESSAGE, 502, "odoo_acceso")
        try:
            r = self._http.get(f"{self.s.odoo_url}/report/pdf/{REPORT_NAME}/{int(task_id)}",
                               params={"context": json.dumps({"allowed_company_ids": [int(company_id)]})}, timeout=90)
        except httpx.HTTPError as e:
            raise _network_error("report_pdf", e) from e
        if r.status_code in (502, 503, 504) or _has_maintenance_marker(r):
            _check_upstream_response(r, "report_pdf", html_is_unexpected=False)
        if r.status_code != 200 or not r.content.startswith(b"%PDF"):
            log.warning("print_order tarea=%s: el informe devolvió HTTP %s (%s); 403 = el usuario no tiene permiso para imprimirlo",
                        task_id, r.status_code, r.headers.get("content-type"))
            raise AppError("El sistema no ha podido responder. Inténtalo de nuevo.", 502, "pdf_fallo")
        task = self.read_task(task_id)
        att_ref = task and task.get("tms_transport_order_attachment_id")
        att = att_ref and self.read_attachment(att_ref[0])
        if not att:
            log.warning("print_order tarea=%s: se imprimió pero la tarea no tiene orden de transporte nueva", task_id)
            raise AppError("El sistema no ha podido responder. Inténtalo de nuevo.", 502, "odoo_error")
        return att

    def check_web_login(self) -> bool:
        """Solo comprueba si las credenciales sirven para abrir una sesión web (lo que hace falta para imprimir). No imprime nada."""
        try:
            r = self._http.post(f"{self.s.odoo_url}/web/session/authenticate", json={
                "jsonrpc": "2.0", "method": "call",
                "params": {"db": self.s.odoo_db, "login": self.s.odoo_user, "password": self.s.odoo_web_password}})
            return bool((r.json().get("result") or {}).get("uid"))
        except (httpx.HTTPError, ValueError):
            return False

    def get_pdf(self, attachment_id: int, token: str) -> bytes | None:
        """Descarga el PDF con el access_token del adjunto (sin sesión: si el token no vale, Odoo lo rechaza)."""
        if not token:
            return None
        try:
            r = self._anon.get(f"{self.s.odoo_url}/web/content/{int(attachment_id)}",
                               params={"access_token": token, "download": "true"})
        except httpx.HTTPError as e:
            raise _network_error("pdf_download", e) from e
        if r.status_code in (502, 503, 504) or _has_maintenance_marker(r):
            _check_upstream_response(r, "pdf_download", html_is_unexpected=False)
        return r.content if r.status_code == 200 and r.content.startswith(b"%PDF") else None

    def goods_name(self, goods_id: int) -> str | None:
        rows = self._call(GOODS_MODEL, "read", [[goods_id]], {"fields": ["name"]})
        return rows[0]["name"] if rows else None

    def check_permissions(self) -> list[tuple[str, str, bool, bool]]:
        """(modelo, operación, esperado, real). Solo consulta los derechos del usuario; no modifica nada."""
        return [(m, op, expected, bool(self._call(m, "check_access_rights", [op], {"raise_exception": False})))
                for m, op, expected in EXPECTED_PERMISSIONS]

    def find_report(self) -> list[dict]:
        """Diagnóstico: comprueba que existe el informe de la orden de transporte."""
        return self._call("ir.actions.report", "search_read", [[["report_name", "=", REPORT_NAME]]], {"fields": ["report_name", "model", "report_type"]})

    def server_version(self) -> str:
        return self._rpc("common", "version", [])["server_version"]

    def list_companies(self) -> list[dict]:
        return self._call("res.company", "search_read", [[]], {"fields": ["name"]})

    def describe_fields(self, model: str, filters: list[str]) -> dict:
        """Solo diagnóstico: lista campos (nombre, tipo, etiqueta) cuyo nombre contenga alguno de los filtros."""
        if model not in (TASK_MODEL, PACKAGE_MODEL):
            raise PermissionError(model)
        info = self._call(model, "fields_get", [], {"attributes": ["type", "string", "selection"]})
        return {k: v for k, v in info.items() if any(f in k for f in filters)}
