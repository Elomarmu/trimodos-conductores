"""Viaje de bobinas: Lista de Carga → línea del pedido «BOBINAS {MES}» (datos reales comprobados el 27/11/2026)."""
import pytest

from app.pr import bobinas_reglas as BR
from app.pr import pr_odoo as O


@pytest.mark.parametrize("lugar,clave", [
    ("SAICA PACK SEVILLA", "SEVILLA"),
    ("ENVASES RAMBALEÑOS, SL - LA RAMBLA (CÓRDOBA)", "CORDOBA"),
    ("ENVASES RAMBLEÑOS SL", "CORDOBA"),
    ("SKG HUELVA", "HUELVA"),
    ("ONDUPACK ALMENDRALEJO (BADAJOZ)", "ALMENDRALEJO"),
    ("SAICA PACK - PATERNA (VALENCIA)", "VALENCIA"),
])
def test_destino(lugar, clave):
    assert BR.destino(lugar).clave == clave


def test_destino_desconocido():
    assert BR.destino("CARTONAJES DESCONOCIDOS") is None
    assert BR.destino(None) is None


def test_referencia_pedido():
    assert BR.referencia_pedido("2026-11-24") == "BOBINAS NOVIEMBRE"


class OdooFalso:
    def __init__(self, pedido=True, previo=None, coste=110.0):
        self.llamadas, self.pedido, self.previo, self.coste = [], pedido, previo, coste

    def __call__(self, modelo, metodo, args, **kw):
        self.llamadas.append((modelo, metodo, args, kw))
        if modelo == "tms.package" and metodo == "search_read":
            return [self.previo] if self.previo else []
        if modelo == "fleet.vehicle":
            placa = args[0][0][2]
            return [{"id": 90 if placa.startswith("7") else 8279, "license_plate": placa, "driver_id": [9225, "JOSÉ GONZÁLEZ"]}]
        if modelo == "sale.order" and metodo == "search_read":
            return [{"id": 577417, "name": "S26100643", "state": "draft", "locked": False}] if self.pedido else []
        if modelo == "sale.order" and metodo == "create":
            return 999
        if modelo == "sale.order.line" and metodo == "search_read":
            if ["vendor_id", "=", 9225] in args[0]:
                return [{"purchase_price": self.coste}]
            return [{"id": 1, "task_id": False, "order_id": [577417, "S26100643"], "price_unit": 5.92, "purchase_price": self.coste}]
        if modelo == "ir.sequence":
            return "PK2699999"
        if metodo == "search_count":
            return 0
        if modelo == "tms.package" and metodo == "create":
            return 555
        return True


VIAJE = {"documento": "7375296", "lugar_entrega": "SAICA PACK SEVILLA", "fecha": "2026-11-25", "bobinas": 11,
         "peso_kg": 28101, "tractora": "7033JNX", "remolque": "R3860BCJ"}


def _linea(odoo):
    return next(a[1]["order_line"][0][2] for m, f, a, _ in odoo.llamadas if m == "sale.order" and f == "write")


def test_registra_linea_como_la_oficina():
    odoo = OdooFalso()
    res = O.registrar_bobinas(odoo, dict(VIAJE))
    l = _linea(odoo)
    assert l["product_id"] == 41 and l["name"] == "BOBINAS PAPEL SEVILLA"
    assert l["product_uom_qty"] == 28.101
    assert l["shipping_origin_id"] == 12888 and l["shipping_destination_id"] == 7206
    assert l["tractor_id"] == 90 and l["trailer_id"] == 8279 and l["vendor_id"] == l["driver_id"] == 9225
    assert l["purchase_price"] == 110.0
    assert "price_unit" not in l          # lo pone el producto
    bulto = next(a[0] for m, f, a, _ in odoo.llamadas if m == "tms.package" and f == "create")
    assert bulto["goods_id"] == 93 and bulto["carrier_tracking_ref"] == "7375296" and bulto["number_of_packages"] == 11
    assert res["pedido"] == "S26100643" and not res["pedido_nuevo"] and res["toneladas"] == 28.101
    assert not any(f == "action_confirm" for _, f, _, _ in odoo.llamadas)   # el pedido se queda como lo tiene la oficina


def test_crea_pedido_del_mes_en_borrador():
    odoo = OdooFalso(pedido=False)
    res = O.registrar_bobinas(odoo, dict(VIAJE))
    vals = next(a[0] for m, f, a, _ in odoo.llamadas if m == "sale.order" and f == "create")
    assert vals["client_order_ref"] == "BOBINAS NOVIEMBRE" and vals["partner_id"] == 9228 and vals["type_id"] == 36
    assert res["pedido_nuevo"]


def test_no_repite():
    odoo = OdooFalso(previo={"id": 1, "name": "PK1", "sale_line_ids": [7], "task_ids": []})
    res = O.registrar_bobinas(odoo, dict(VIAJE))
    assert res["duplicado"] and not any(f == "write" and m == "sale.order" for m, f, _, _ in odoo.llamadas)


def test_destino_desconocido_no_guarda():
    with pytest.raises(O.ErrorPR, match="lugar de entrega"):
        O.registrar_bobinas(OdooFalso(), dict(VIAJE, lugar_entrega="OTRO SITIO"))
