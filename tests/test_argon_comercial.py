"""Comercial del pedido y opción de dejarlo en presupuesto (sin Excel: Odoo simulado y servicios mínimos)."""
from types import SimpleNamespace

import app.argon  # noqa: F401  (añade la carpeta al path)
import argon_escritor as E  # noqa: E402
import argon_odoo as AO  # noqa: E402

ROT = {"AIRU 600146-0|2026-11-02": [SimpleNamespace(producto_id=3092, fecha="2026-11-03")]}


class OdooMin:
    def __init__(self):
        self.creados, self.confirmados, self.sig = [], [], 100
        self.uid = 7

    def __call__(self, modelo, metodo, args, **kw):
        if (modelo, metodo) == ("tms.equipment", "search_read"):
            return [{"id": 55, "name": "AIRU6001460"}]
        if (modelo, metodo) == ("sale.order", "search_read"):
            return [{"name": "S1"}] if args[0][0][0] == "id" else []
        if (modelo, metodo) == ("sale.order", "create"):
            self.creados.append(args[0]); self.sig += 1; return self.sig
        if (modelo, metodo) == ("sale.order", "action_confirm"):
            self.confirmados.append(args[0]); return True
        raise AssertionError((modelo, metodo))


def _linea(s, cfg):
    return {"product_id": s.producto_id, "product_uom_qty": 1, "price_unit": 0, "pickup_date": s.fecha}


def _lanza(monkeypatch, cfg):
    monkeypatch.setattr(AO, "linea_de", _linea)
    monkeypatch.setattr(AO, "utc_mediodia", lambda f: f)
    o = OdooMin()
    r = E.escribir(o, ROT, cfg, real=True, limite_s=999)
    assert not r["errores"], r["errores"]
    return o


def test_comercial_y_presupuesto(monkeypatch):
    o = _lanza(monkeypatch, {"cliente_id": 1585, "empresa_id": 1, "tipo_pedido_id": 41, "comercial_id": 7, "confirmar": False})
    assert len(o.creados) == 1 and o.creados[0]["user_id"] == 7
    assert o.confirmados == []


def test_por_defecto_confirma_y_sin_comercial(monkeypatch):
    o = _lanza(monkeypatch, {"cliente_id": 1585, "empresa_id": 1, "tipo_pedido_id": 41})
    assert "user_id" not in o.creados[0]
    assert len(o.confirmados) == 1


def test_analitica_por_producto_con_etiqueta():
    cfg = {"analitica_por_producto": True, "etiqueta_ids": [95389], "_dist_producto": {1309: {"95674": 100}}}
    assert AO.analitica_de_producto(1309, cfg) == {"95674,95389": 100}
    assert AO.analitica_de_producto(999, cfg) == {"95389": 100}   # sin modelo: solo la etiqueta
    assert AO.analitica_de_producto(1309, {"_dist_producto": {1309: {"95674": 100}}}) == {"95674": 100}
    assert AO.analitica_de_producto(5, {}) is None
