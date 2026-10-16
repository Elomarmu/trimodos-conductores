"""Odoo simulado con los mismos métodos que OdooClient. Datos basados en el pedido de prueba S2621473."""
import copy
import uuid
from datetime import datetime

from .pdf_sample import make_sample_pdf

TOKEN_ACTUAL = "11111111-aaaa-bbbb-cccc-000000000001"
TOKEN_ANTIGUO = "22222222-aaaa-bbbb-cccc-000000000002"

GOODS = [
    {"id": 1, "name": "Cereal a granel"},
    {"id": 2, "name": "Cartón y papel"},
    {"id": 3, "name": "Bebidas"},
    {"id": 4, "name": "Material de construcción"},
    {"id": 5, "name": "Productos químicos"},
]


class MockOdoo:
    def __init__(self):
        self.pdfs = {}
        self.tasks = {
            204067: {
                "id": 204067, "name": "S2621473", "company_id": [7, "TRANSARGI S.L.U"], "stage_id": [3, "En curso"],
                "is_transport_order": True, "tms_package_ids": [287102, 287103], "tms_transport_order_attachment_id": [900002, "Orden de transporte - S2621473 - 20260105101500.pdf"],
                "is_closed": False, "invoice_count": 0,
            },
            300001: {
                "id": 300001, "name": "CAMION COMPLETO FRANCIA", "company_id": [1, "STINSA"], "stage_id": [1, "Nuevo"],
                "is_transport_order": True, "tms_package_ids": [287103], "tms_transport_order_attachment_id": [900003, "Orden de transporte - S2621473 - 20260106090000.pdf"],
                "is_closed": False, "invoice_count": 0,
            },
            300002: {
                "id": 300002, "name": "ORDEN FACTURADA", "company_id": [7, "TRANSARGI, S.L.U"], "stage_id": [5, "Hecho"],
                "is_transport_order": True, "tms_package_ids": [287102], "tms_transport_order_attachment_id": [900004, "Orden de transporte - S2600001 - 20260101090000.pdf"],
                "is_closed": True, "invoice_count": 1,
            },
        }
        # (pedido, id de compañía) -> tareas: el mismo número puede existir en STINSA y en TRANSARGI
        self.orders = {("S2621473", 7): [204067], ("S2621473", 1): [300001], ("S2600001", 7): [300002]}
        self.packages = {
            287102: {
                "id": 287102, "name": "PK2627486", "carrier_tracking_ref": "REF-0042", "goods_id": [2, "Cartón y papel"],
                "number_of_packages": 12, "euro_pallet_qty": 10, "shipping_weight": 8450.0,
                "shipping_origin_id": [11, "Almacén Zaragoza"], "shipping_destination_id": [12, "Fábrica Valencia"],
                "pickup_date": "2026-01-06 07:00:00", "forecast_unload_date": "2026-01-06 16:00:00", "state": "draft",
                "write_date": "2026-01-05 10:15:00",
            },
            287103: {
                "id": 287103, "name": "PK2627487", "carrier_tracking_ref": "REF-0043", "goods_id": [3, "Bebidas"],
                "number_of_packages": 4, "euro_pallet_qty": 4, "shipping_weight": 3200.0,
                "shipping_origin_id": [11, "Almacén Zaragoza"], "shipping_destination_id": [13, "Centro logístico Madrid"],
                "pickup_date": "2026-01-06 07:00:00", "forecast_unload_date": "2026-01-06 19:00:00", "state": "draft",
                "write_date": "2026-01-05 10:15:00",
            },
        }
        self.attachments = {
            900002: {"id": 900002, "res_model": "project.task", "res_id": 204067, "access_token": TOKEN_ACTUAL, "name": "Orden de transporte - S2621473 - 20260105101500.pdf"},
            900003: {"id": 900003, "res_model": "project.task", "res_id": 300001, "access_token": "33333333-aaaa-bbbb-cccc-000000000003", "name": "Orden de transporte - S2621473 - 20260106090000.pdf"},
            900004: {"id": 900004, "res_model": "project.task", "res_id": 300002, "access_token": "44444444-aaaa-bbbb-cccc-000000000004", "name": "Orden de transporte - S2600001 - 20260101090000.pdf"},
            900001: {"id": 900001, "res_model": "project.task", "res_id": 204067, "access_token": TOKEN_ANTIGUO, "name": "Orden antigua"},
        }

    def find_transport_tasks(self, order_name, company_id):
        return [copy.deepcopy(self.tasks[i]) for i in sorted(self.orders.get((order_name, company_id), []), reverse=True)]

    def read_task(self, task_id):
        return copy.deepcopy(self.tasks.get(task_id))

    def read_packages(self, package_ids):
        return [copy.deepcopy(self.packages[i]) for i in package_ids if i in self.packages]

    def read_attachment(self, attachment_id):
        return copy.deepcopy(self.attachments.get(attachment_id))

    def search_goods(self, query, limit=10):
        q = (query or "").lower()
        return [g for g in GOODS if q in g["name"].lower()][: min(limit, 10)]

    def goods_name(self, goods_id):
        return next((g["name"] for g in GOODS if g["id"] == goods_id), None)

    def update_packages(self, task_id, changes):
        """changes: {package_id: {campo: valor}}. Aplica en memoria y genera un PDF de muestra nuevo."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for pid, vals in changes.items():
            pkg = self.packages[pid]
            for k, v in vals.items():
                pkg[k] = [v, self.goods_name(v)] if k == "goods_id" else v
            pkg["write_date"] = now
        task = self.tasks[task_id]
        att_id = max(self.attachments) + 1
        token = str(uuid.uuid4())
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        name = f"Orden de transporte - SIMULACION - {stamp}.pdf"
        self.attachments[att_id] = {"id": att_id, "res_model": "project.task", "res_id": task_id, "access_token": token, "name": name}
        task["tms_transport_order_attachment_id"] = [att_id, name]
        lines = ["*** DOCUMENTO DE MUESTRA - SIMULACION, NO ES UNA ORDEN REAL ***", f"Generado: {now}", ""]
        for p in (self.packages[i] for i in task["tms_package_ids"]):
            lines += [
                f"{p['name']}: {p['shipping_origin_id'][1]} -> {p['shipping_destination_id'][1]}",
                f"   Mercancia: {p['goods_id'][1]}   Elementos: {p['number_of_packages']}   Euro: {p['euro_pallet_qty']}   Peso: {p['shipping_weight']:,.0f} kg",
                "",
            ]
        self.pdfs[att_id] = make_sample_pdf(f"Orden de transporte {task['name']}", lines)
        return att_id, token

    def get_pdf(self, attachment_id, token):
        att = self.attachments.get(attachment_id)
        if not att or att["access_token"] != token:
            return None
        return self.pdfs.get(attachment_id)

