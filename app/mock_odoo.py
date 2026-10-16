"""Odoo simulado con los mismos métodos que OdooClient. Datos basados en el pedido de prueba S2621473."""
import copy

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
        self.tasks = {
            204067: {
                "id": 204067, "name": "S2621473", "company_id": [7, "TRANSARGI S.L.U"], "stage_id": [3, "En curso"],
                "is_transport_order": True, "tms_package_ids": [287102, 287103], "tms_transport_order_attachment_id": [900002, "Orden de transporte - S2621473 - 20260105101500.pdf"],
            }
        }
        self.orders = {"S2621473": [204067]}
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
            900002: {"id": 900002, "res_model": "project.task", "res_id": 204067, "access_token": TOKEN_ACTUAL, "name": "Orden actual"},
            900001: {"id": 900001, "res_model": "project.task", "res_id": 204067, "access_token": TOKEN_ANTIGUO, "name": "Orden antigua"},
        }

    def find_transport_tasks(self, order_name):
        return [copy.deepcopy(self.tasks[i]) for i in sorted(self.orders.get(order_name, []), reverse=True)]

    def read_task(self, task_id):
        return copy.deepcopy(self.tasks.get(task_id))

    def read_packages(self, package_ids):
        return [copy.deepcopy(self.packages[i]) for i in package_ids if i in self.packages]

    def read_attachment(self, attachment_id):
        return copy.deepcopy(self.attachments.get(attachment_id))

    def search_goods(self, query, limit=10):
        q = (query or "").lower()
        return [g for g in GOODS if q in g["name"].lower()][: min(limit, 10)]
