"""Cliente Odoo con lista blanca. NO existe ninguna llamada genérica: cada operación permitida es un método fijo.

Lectura de tareas/bultos/mercancías/adjuntos; escritura SOLO de 4 campos en tms.package; message_post SOLO en project.task;
impresión SOLO del informe tms_reporting.tms_transportation_order.
"""
import json

import httpx

from .config import Settings
from .errors import AppError

ORDER_MODEL = "sale.order"
TASK_MODEL = "project.task"
PACKAGE_MODEL = "tms.package"
GOODS_MODEL = "tms.goods"
ATTACHMENT_MODEL = "ir.attachment"

TASK_FIELDS = ["name", "company_id", "stage_id", "is_transport_order", "tms_package_ids", "tms_transport_order_attachment_id", "is_closed", "invoice_count"]
PACKAGE_FIELDS = [
    "name", "carrier_tracking_ref", "goods_id", "number_of_packages", "euro_pallet_qty", "shipping_weight",
    "shipping_origin_id", "shipping_destination_id", "pickup_date", "forecast_unload_date", "state", "write_date",
]
ATTACHMENT_FIELDS = ["res_model", "res_id", "access_token", "name"]
EDITABLE_FIELDS = frozenset({"goods_id", "number_of_packages", "euro_pallet_qty", "shipping_weight"})
REPORT_NAME = "tms_reporting.tms_transportation_order"

# (modelo, método) permitidos. Todo lo demás se rechaza antes de salir hacia Odoo.
ALLOWED_CALLS = {
    (ORDER_MODEL, "search_read"),
    (TASK_MODEL, "read"),
    (TASK_MODEL, "search_read"),
    (TASK_MODEL, "fields_get"),
    (PACKAGE_MODEL, "read"),
    (PACKAGE_MODEL, "fields_get"),
    (GOODS_MODEL, "name_search"),
    (GOODS_MODEL, "read"),
    (ATTACHMENT_MODEL, "read"),
    ("res.company", "search_read"),
    (PACKAGE_MODEL, "write"),        # solo vía write_package (campos EDITABLE_FIELDS)
    (TASK_MODEL, "message_post"),    # solo vía post_note
}

# Permisos que se comprueban sin ejecutar nada: (modelo, operación, ¿debería tenerlo el usuario técnico?)
EXPECTED_PERMISSIONS = [
    (TASK_MODEL, "read", True), (TASK_MODEL, "write", True),       # write: necesario para publicar la nota (message_post)
    (TASK_MODEL, "unlink", False),
    (PACKAGE_MODEL, "read", True), (PACKAGE_MODEL, "write", True),
    (PACKAGE_MODEL, "create", False), (PACKAGE_MODEL, "unlink", False),
    (GOODS_MODEL, "read", True), (GOODS_MODEL, "write", False),
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

    # ---- bajo nivel (privado) -------------------------------------------------
    def _rpc(self, service: str, method: str, args: list):
        payload = {"jsonrpc": "2.0", "method": "call", "params": {"service": service, "method": method, "args": args}, "id": 1}
        try:
            r = self._http.post(f"{self.s.odoo_url}/jsonrpc", json=payload)
            r.raise_for_status()
            data = r.json()
        except httpx.HTTPError:
            raise AppError("No hay conexión con el sistema. Inténtalo de nuevo.", 503, "sin_conexion")
        if "error" in data:
            raise AppError("El sistema no ha podido responder. Inténtalo de nuevo.", 502, "odoo_error")
        return data["result"]

    def _login(self) -> int:
        if self._uid is None:
            uid = self._rpc("common", "authenticate", [self.s.odoo_db, self.s.odoo_user, self.s.odoo_password, {}])
            if not uid:
                raise AppError("El sistema no ha aceptado el acceso. Avisa a la oficina.", 502, "login_odoo")
            self._uid = uid
        return self._uid

    def _call(self, model: str, method: str, args: list, kwargs: dict | None = None):
        if (model, method) not in ALLOWED_CALLS:
            raise PermissionError(f"Operación no permitida: {model}.{method}")
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
        return self._call(
            TASK_MODEL, "search_read", [[["id", "in", orders[0]["tasks_ids"]], ["is_transport_order", "=", True]]],
            {"fields": TASK_FIELDS, "order": "id desc"},
        )

    def read_task(self, task_id: int) -> dict | None:
        rows = self._call(TASK_MODEL, "read", [[task_id]], {"fields": TASK_FIELDS})
        return rows[0] if rows else None

    def read_packages(self, package_ids: list[int]) -> list[dict]:
        if not package_ids:
            return []
        return self._call(PACKAGE_MODEL, "read", [package_ids], {"fields": PACKAGE_FIELDS})

    def read_attachment(self, attachment_id: int) -> dict | None:
        rows = self._call(ATTACHMENT_MODEL, "read", [[attachment_id]], {"fields": ATTACHMENT_FIELDS})
        return rows[0] if rows else None

    def search_goods(self, query: str, limit: int = 10) -> list[dict]:
        limit = min(limit, 10)
        return [{"id": i, "name": n} for i, n in self._call(GOODS_MODEL, "name_search", [query], {"limit": limit})]

    # ---- escritura (solo lo imprescindible) ------------------------------------
    def write_package(self, package_id: int, values: dict) -> None:
        if not values or not set(values) <= EDITABLE_FIELDS:
            raise PermissionError("Campo no permitido")
        self._call(PACKAGE_MODEL, "write", [[int(package_id)], values])

    def post_note(self, task_id: int, html_body: str) -> None:
        """Nota interna (sin avisar a seguidores) en la tarea."""
        self._call(TASK_MODEL, "message_post", [[int(task_id)]],
                   {"body": html_body, "message_type": "comment", "subtype_xmlid": "mail.mt_note"})

    def print_order(self, task_id: int, company_id: int) -> dict:
        """Imprime la orden de transporte (Odoo crea un adjunto nuevo en la tarea) y devuelve ese adjunto."""
        fail = AppError("No se ha podido generar la orden de transporte. Inténtalo de nuevo.", 502, "pdf_fallo")
        try:
            r = self._http.post(f"{self.s.odoo_url}/web/session/authenticate", json={
                "jsonrpc": "2.0", "method": "call",
                "params": {"db": self.s.odoo_db, "login": self.s.odoo_user, "password": self.s.odoo_password}})
            res = r.json().get("result") or {}
            if not res.get("uid"):
                raise fail
            r = self._http.get(f"{self.s.odoo_url}/report/pdf/{REPORT_NAME}/{int(task_id)}",
                               params={"context": json.dumps({"allowed_company_ids": [int(company_id)]})}, timeout=90)
        except httpx.HTTPError:
            raise fail
        if r.status_code != 200 or not r.content.startswith(b"%PDF"):
            raise fail
        task = self.read_task(task_id)
        att_ref = task and task.get("tms_transport_order_attachment_id")
        att = att_ref and self.read_attachment(att_ref[0])
        if not att:
            raise fail
        return att

    def get_pdf(self, attachment_id: int, token: str) -> bytes | None:
        """Descarga el PDF con el access_token del adjunto (sin sesión: si el token no vale, Odoo lo rechaza)."""
        if not token:
            return None
        try:
            r = self._anon.get(f"{self.s.odoo_url}/web/content/{int(attachment_id)}",
                               params={"access_token": token, "download": "true"})
        except httpx.HTTPError:
            raise AppError("No hay conexión. Inténtalo de nuevo.", 503, "sin_conexion")
        return r.content if r.status_code == 200 and r.content.startswith(b"%PDF") else None

    def goods_name(self, goods_id: int) -> str | None:
        rows = self._call(GOODS_MODEL, "read", [[goods_id]], {"fields": ["name"]})
        return rows[0]["name"] if rows else None

    def check_permissions(self) -> list[tuple[str, str, bool, bool]]:
        """(modelo, operación, esperado, real). Solo consulta los derechos del usuario; no modifica nada."""
        return [(m, op, expected, bool(self._call(m, "check_access_rights", [op], {"raise_exception": False})))
                for m, op, expected in EXPECTED_PERMISSIONS]

    def list_companies(self) -> list[dict]:
        return self._call("res.company", "search_read", [[]], {"fields": ["name"]})

    def describe_fields(self, model: str, filters: list[str]) -> dict:
        """Solo diagnóstico: lista campos (nombre, tipo, etiqueta) cuyo nombre contenga alguno de los filtros."""
        if model not in (TASK_MODEL, PACKAGE_MODEL):
            raise PermissionError(model)
        info = self._call(model, "fields_get", [], {"attributes": ["type", "string", "selection"]})
        return {k: v for k, v in info.items() if any(f in k for f in filters)}
