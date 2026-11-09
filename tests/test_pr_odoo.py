"""Registro de viajes de PR con un Odoo simulado que imita lo visto en la v18 de pruebas (09/11/2026)."""
import pytest

from app.pr import pr_odoo as O
from app.pr import pr_reglas as R

JEREZ = {"albaran": "592611000006", "fecha": "2026-11-09", "hora": "13:35", "tractora": "8734KDW", "remolque": "R8896BDF",
         "punto": {"clave": "JEREZ"}, "peso_kg": 24800, "bultos": 21, "codigo_paper": "S261104001499",
         "codigo_natur": "5926000419", "ler": "191201", "calidad": "110104 CARTON PAJA 100% 1.05.00",
         "bruto_kg": 39880, "tara_kg": 15080, "contenedor": ""}
LEOPROEX = dict(JEREZ, albaran="26371", punto={"clave": "LEOPROEX"}, tractora="1222HTP", remolque="R3403BBP",
                codigo_paper="", contenedor="NPWU4957559", peso_kg=24320, bultos=34)


class OdooV18:
    def __init__(self, pedido_mes=None):
        self.llamadas, self.sig = [], 1000
        self.vehiculos = [{"id": 4155, "license_plate": "8734KDW", "driver_id": [9928, "JESÚS CLAVIJO LLEBRE"]},
                          {"id": 3356, "license_plate": "R8896BDF", "driver_id": False},
                          {"id": 190, "license_plate": "R8896BDF", "driver_id": False},
                          {"id": 4735, "license_plate": "1222HTP", "driver_id": [9161, "GUERRERO"]},
                          {"id": 8337, "license_plate": "R3403BBP", "driver_id": False}]
        self.pedidos = {} if pedido_mes is None else {pedido_mes["id"]: dict(pedido_mes)}
        self.lineas, self.bultos, self.equipos = {}, {}, {"NPWU4954328": 52461}
        self.seq = iter(["PK2627659", "PK2627660"])  # el primero ya existe (choque de la v18)
        self.bultos[1] = {"id": 1, "name": "PK2627659", "partner_id": 1, "unload_note": ""}
        self.checkpoints = set()

    def __call__(self, modelo, metodo, args, **kw):
        assert (modelo, metodo) in O.PERMITIDO, (modelo, metodo)
        self.llamadas.append((modelo, metodo))
        if modelo == "fleet.vehicle":
            dom = args[0]
            if isinstance(dom[0], list) and dom[0][1] == "=":
                return [v for v in self.vehiculos if v["license_plate"] == dom[0][2]]
            import fnmatch
            pats = [c[2].replace("_", "?") for c in dom if isinstance(c, list)]
            return [v for v in self.vehiculos if any(fnmatch.fnmatch(v["license_plate"], p) for p in pats)]
        if modelo == "sale.order.line" and metodo == "search_read":
            dom = dict((c[0], c[2]) for c in args[0])
            if "tractor_id" in dom or "trailer_id" in dom:
                return [{"id": 1, "trailer_id": [3356, "R8896BDF"], "tractor_id": [1, ""]}] if "trailer_id" in dom else []
            return [dict(l, id=i) for i, l in self.lineas.items()
                    if l["order_id"][0] == dom["order_id"] and set(dom.get("tms_package_ids", [])) & set(l["pkg"])]
        if modelo == "sale.order" and metodo == "search_read":
            return [p for p in self.pedidos.values() if p["ref"] == args[0][2][2]]
        if modelo == "sale.order" and metodo == "create":
            self.sig += 1
            self.pedidos[self.sig] = {"id": self.sig, "name": f"S{self.sig}", "ref": args[0]["client_order_ref"],
                                      "state": "draft", "locked": False}
            assert "division_id" not in args[0]
            return self.sig
        if modelo == "sale.order" and metodo == "write":
            p = self.pedidos[args[0][0]]
            if p["locked"]:
                raise RuntimeError("You cannot create/modify a task related with a invoiced, locked or cancel sale order line")
            for _, _, l in args[1]["order_line"]:
                assert "carrier_tracking_ref" not in l and "goods_id" not in l  # en la v18 van en el bulto
                self.sig += 1
                pkg = l["tms_package_ids"][0][2]
                self.lineas[self.sig] = {"order_id": [p["id"], p["name"]], "pkg": pkg, "vals": l,
                                         "task_id": [self.sig + 5000, "t"] if p["state"] == "sale" else False}
            return True
        if metodo == "action_confirm":
            p = self.pedidos[args[0][0]]
            p["state"], p["locked"] = "sale", True  # en la v18 se bloquea solo al confirmar
            for l in self.lineas.values():
                if l["order_id"][0] == p["id"]:
                    l["task_id"] = [id(l) % 100000, "t"]
            return True
        if metodo == "action_unlock":
            self.pedidos[args[0][0]]["locked"] = False
            return True
        if modelo == "ir.sequence":
            return next(self.seq)
        if modelo == "tms.package" and metodo == "search_count":
            return sum(1 for b in self.bultos.values() if b["name"] == args[0][0][2])
        if modelo == "tms.package" and metodo == "search_read":
            texto = args[0][1][2]
            return [b for b in self.bultos.values() if texto in (b.get("unload_note") or "")]
        if modelo == "tms.package" and metodo == "create":
            assert args[0]["name"] not in {b["name"] for b in self.bultos.values()}
            self.sig += 1
            self.bultos[self.sig] = dict(args[0], id=self.sig)
            return self.sig
        if modelo == "tms.package" and metodo == "write":
            self.bultos[args[0][0]].update(args[1])
            return True
        if modelo == "tms.equipment" and metodo == "search_read":
            n = args[0][0][2]
            return [{"id": self.equipos[n], "name": n}] if n in self.equipos else []
        if modelo == "tms.equipment" and metodo == "create":
            self.sig += 1
            self.equipos[args[0]["name"]] = self.sig
            return self.sig
        if metodo == "fill_checkpoints":
            self.checkpoints.add(args[0][0])
            return True
        if modelo == "sale.order.line" and metodo == "write":
            return True
        raise AssertionError((modelo, metodo, args))


def test_primer_viaje_del_mes_crea_confirma_y_desbloquea():
    odoo = OdooV18()
    r = O.registrar(odoo, JEREZ)
    assert r["pedido_nuevo"] and not r["duplicado"] and r["bulto"] == "PK2627660"   # salta el nombre repetido
    pedido = next(iter(odoo.pedidos.values()))
    assert pedido["ref"] == "PR NOVIEMBRE" and pedido["state"] == "sale" and pedido["locked"] is False
    linea = next(iter(odoo.lineas.values()))["vals"]
    assert (linea["product_id"], linea["product_uom_qty"], linea["price_unit"], linea["shipping_origin_id"]) == (719, 24.8, 8.82, 7274)
    assert (linea["tractor_id"], linea["trailer_id"], linea["vendor_id"], linea["driver_id"]) == (4155, 3356, 9928, 9928)
    assert linea["pickup_date"] == "2026-11-09 12:35:00"
    bulto = odoo.bultos[r["bulto_id"]]
    assert (bulto["carrier_tracking_ref"], bulto["shipping_weight"], bulto["number_of_packages"]) == ("S261104001499", 24800, 21)
    assert R.leer_observaciones(bulto["unload_note"])["albaran"] == "592611000006"
    assert r["tarea_id"] in odoo.checkpoints   # orden con lugares de carga y descarga
    orden = [m for _, m in odoo.llamadas]
    assert orden.index("create") < orden.index("write") < orden.index("action_confirm") < orden.index("action_unlock")


def test_pedido_existente_bloqueado_se_desbloquea_y_no_se_duplica():
    odoo = OdooV18({"id": 7, "name": "S26099981", "ref": "PR NOVIEMBRE", "state": "sale", "locked": True})
    r = O.registrar(odoo, LEOPROEX)
    assert not r["pedido_nuevo"] and r["pedido"] == "S26099981"
    linea = next(iter(odoo.lineas.values()))["vals"]
    assert linea["product_id"] == 712 and linea["shipping_origin_id"] == 15296 and linea["equipment_id"]  # contenedor nuevo
    assert odoo.bultos[r["bulto_id"]]["carrier_tracking_ref"] is False   # código Paper pendiente
    r2 = O.registrar(odoo, LEOPROEX)
    assert r2["duplicado"] and r2["bulto"] == r["bulto"] and len(odoo.lineas) == 1


def test_errores_claros():
    with pytest.raises(O.ErrorPR, match="Punto de carga nuevo"):
        O.registrar(OdooV18(), dict(JEREZ, punto=None))
    with pytest.raises(O.ErrorPR, match="no está en Flota"):
        O.registrar(OdooV18(), dict(JEREZ, tractora="1222LTP"))


def test_matricula_parecida():
    odoo = OdooV18()
    assert O.parecidas(odoo, "1222LTP") == ["1222HTP"]
    c = O.comprobar_vehiculos(odoo, "1222LTP", "R3403BBP")
    assert c["tractora"] == {"matricula": "1222LTP", "existe": False, "parecidas": ["1222HTP"]} and c["remolque"]["existe"]
    assert O.vehiculo(odoo, "R8896BDF")["id"] == 3356   # repetido en Flota: el último usado


def test_actualizar_codigo_paper_peso_y_tren():
    odoo = OdooV18()
    r = O.registrar(odoo, dict(LEOPROEX, peso_kg=None))
    b = dict(odoo.bultos[r["bulto_id"]], sale_line_ids=[r["linea_id"]], shipping_origin_id=[15296, "LEOPROEX"])
    hechos = O.actualizar(odoo, b, {"codigo_paper": "S 261104001502", "peso_kg": "24.320", "caja_movil": "ICR102043"})
    assert hechos == ["código Paper", "peso", "caja_movil"]
    nuevo = odoo.bultos[r["bulto_id"]]
    assert nuevo["carrier_tracking_ref"] == "S261104001502" and nuevo["shipping_weight"] == 24320
    obs = R.leer_observaciones(nuevo["unload_note"])
    assert obs["albaran"] == "26371" and obs["codigo_paper"] == "S261104001502" and obs["caja_movil"] == "ICR102043"
    with pytest.raises(O.ErrorPR, match="S seguido de 12"):
        O.actualizar(odoo, b, {"codigo_paper": "1502"})
