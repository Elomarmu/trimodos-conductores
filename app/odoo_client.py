"""Cliente Odoo con lista blanca. NO existe ninguna llamada genérica: cada operación permitida es un método fijo.

Fase 1: solo lectura. La escritura (tms.package) y message_post se añaden en la fase 3 con las mismas reglas.
"""
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

# (modelo, método) permitidos. Todo lo demás se rechaza antes de salir hacia Odoo.
ALLOWED_CALLS = {
    (ORDER_MODEL, "search_read"),
    (TASK_MODEL, "read"),
    (TASK_MODEL, "search_read"),
    (TASK_MODEL, "fields_get"),
    (PACKAGE_MODEL, "read"),
    (PACKAGE_MODEL, "fields_get"),
    (GOODS_MODEL, "name_search"),
    (ATTACHMENT_MODEL, "read"),
    ("res.company", "search_read"),
}


class OdooClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self._uid: int | None = None
        self._http = httpx.Client(timeout=25)

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

    def list_companies(self) -> list[dict]:
        return self._call("res.company", "search_read", [[]], {"fields": ["name"]})

    def describe_fields(self, model: str, filters: list[str]) -> dict:
        """Solo diagnóstico: lista campos (nombre, tipo, etiqueta) cuyo nombre contenga alguno de los filtros."""
        if model not in (TASK_MODEL, PACKAGE_MODEL):
            raise PermissionError(model)
        info = self._call(model, "fields_get", [], {"attributes": ["type", "string", "selection"]})
        return {k: v for k, v in info.items() if any(f in k for f in filters)}
