"""Pruebas del escritor ARGON con un Odoo simulado (no se conecta a nada)."""
import os
from pathlib import Path

import pytest

import app.argon  # noqa: F401  (añade la carpeta al path)
import argon_escritor as E  # noqa: E402

# La Excel real no se sube al repositorio (datos de clientes): ARGON_EXCEL_PRUEBA=ruta para lanzar estas pruebas.
EXCEL = os.environ.get("ARGON_EXCEL_PRUEBA", "")
pytestmark = pytest.mark.skipif(not EXCEL or not Path(EXCEL).exists(), reason="sin ARGON_EXCEL_PRUEBA")
CFG = {"cliente_id": 1585, "empresa_id": 1, "tipo_pedido_id": 41, "fecha_inicio": "2026-10-15"}


class OdooFalso:
    def __init__(self):
        self.pedidos, self.lineas, self.eq, self.llamadas, self.sig = {}, {}, {"AIRU6001460": 30558}, 0, 1000

    def __call__(self, modelo, metodo, args, **kw):
        assert (modelo, metodo) in E.PERMITIDO, (modelo, metodo)
        self.llamadas += 1
        if modelo == "tms.equipment" and metodo == "search_read":
            return [{"id": i, "name": n} for n, i in self.eq.items() if n in args[0][0][2]]
        if modelo == "tms.equipment" and metodo == "create":
            self.sig += 1; self.eq[args[0]["name"]] = self.sig; return self.sig
        if modelo == "sale.order" and metodo == "search_read":
            dom = args[0]
            if dom[0][0] == "id":
                return [{"name": self.pedidos[dom[0][2]]["name"]}]
            return [dict(p, order_line=[l for l, v in self.lineas.items() if v["order_id"][0] == p["id"]])
                    for p in sorted(self.pedidos.values(), key=lambda p: -p["id"])]
        if modelo == "sale.order.line" and metodo == "search_read":
            return [dict(self.lineas[i], id=i) for i in args[0][0][2]]
        if modelo == "sale.order" and metodo == "create":
            self.sig += 1; oid = self.sig
            self.pedidos[oid] = {"id": oid, "name": f"S{oid}", "origin": args[0]["origin"], "state": "draft", "invoice_status": "no"}
            self._lineas(oid, args[0]["order_line"]); return oid
        if modelo == "sale.order" and metodo == "write":
            self._lineas(args[0][0], args[1]["order_line"]); return True
        if modelo == "sale.order" and metodo == "action_confirm":
            for i in args[0]: self.pedidos[i]["state"] = "sale"
            return True
        raise AssertionError((modelo, metodo))

    def _lineas(self, oid, cmds):
        for _, _, v in cmds:
            self.sig += 1
            self.lineas[self.sig] = {"order_id": [oid, ""], "product_id": [v["product_id"], ""],
                                     "pickup_date": v["pickup_date"], "equipment_id": [v["equipment_id"], ""] if v["equipment_id"] else False}


def test_crea_y_no_duplica():
    o = OdooFalso()
    r1 = E.procesar(EXCEL, CFG, real=True, odoo=o, limite_s=999)
    assert r1["pedidos_creados"] and not r1["errores"], r1["errores"]
    n = len(o.lineas)
    r2 = E.procesar(EXCEL, CFG, real=True, odoo=o, limite_s=999)
    assert r2["lineas_nuevas"] == 0 and len(o.lineas) == n and not r2["pedidos_creados"]
    # todas las líneas desde el 15/10 y con contenedor
    assert all(v["pickup_date"] >= "2026-10-15" for v in o.lineas.values())
    assert all(v["equipment_id"] for v in o.lineas.values())


def test_continuacion_si_facturado():
    o = OdooFalso()
    E.procesar(EXCEL, CFG, real=True, odoo=o, limite_s=999)
    pid = next(iter(o.pedidos)); o.pedidos[pid]["invoice_status"] = "invoiced"
    # una línea del pedido facturado desaparece del Odoo simulado → se vuelve a crear, pero en un pedido «| 2»
    lid = next(i for i, v in o.lineas.items() if v["order_id"][0] == pid)
    del o.lineas[lid]
    r = E.procesar(EXCEL, CFG, real=True, odoo=o, limite_s=999)
    assert any(p["origin"].endswith("|2") for p in o.pedidos.values()), r


def test_modo_prueba_no_escribe():
    o = OdooFalso()
    r = E.procesar(EXCEL, CFG, real=False, odoo=o, limite_s=999)
    assert r["pedidos_creados"] and not o.pedidos and not o.lineas


def test_seguridad():
    with pytest.raises(RuntimeError):
        E.Odoo("https://www.stinsa.com", "prod", "x", "y", 1)


def test_api_prueba(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import argon_api as A
    monkeypatch.setenv("ARGON_CLAVE", "secreto")
    monkeypatch.setattr(E, "Odoo", lambda *a, **k: OdooFalso())
    monkeypatch.setenv("ARGON_ODOO_URL", "https://stinsa18.tecnativa.net"); monkeypatch.setenv("ARGON_ODOO_DB", "d"); monkeypatch.setenv("ARGON_ODOO_USER", "u"); monkeypatch.setenv("ARGON_ODOO_PASSWORD", "p")
    app = FastAPI(); app.include_router(A.router); c = TestClient(app)
    datos = Path(EXCEL).read_bytes()
    assert c.post("/api/argon/procesar", content=datos, headers={"x-clave": "mala"}).status_code == 401
    r = c.post("/api/argon/procesar?modo=prueba&hoy=2026-10-30", content=datos, headers={"x-clave": "secreto"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] and "ARGON · lectura del 30/10/2026" in j["correo_html"] and j["odoo"]["modo"] == "prueba"


def test_api_sin_variables_argon_explica_el_fallo(monkeypatch):
    """Con solo las ODOO_* de producción (app de conductores) no escribe y dice qué falta."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import argon_api as A
    monkeypatch.setenv("ARGON_CLAVE", "secreto")
    monkeypatch.setenv("ODOO_URL", "https://www.stinsa.com")
    for v in ("ARGON_ODOO_URL", "ARGON_ODOO_DB", "ARGON_ODOO_USER", "ARGON_ODOO_PASSWORD"):
        monkeypatch.delenv(v, raising=False)
    app = FastAPI(); app.include_router(A.router); c = TestClient(app)
    r = c.post("/api/argon/procesar?modo=prueba", content=Path(EXCEL).read_bytes(), headers={"x-clave": "secreto"})
    assert r.status_code == 500 and "ARGON_ODOO_URL" in r.json()["detail"]


def test_clave_con_tildes(monkeypatch):
    """Una ARGON_CLAVE con «ñ» o tildes no debe romper el servicio (antes: 500 TypeError)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import argon_api as A
    monkeypatch.setenv("ARGON_CLAVE", "Contraseña-Camión")
    app = FastAPI(); app.include_router(A.router); c = TestClient(app)
    mala = c.post("/api/argon/procesar", content=b"PK", headers={"x-clave": "otra"})
    assert mala.status_code == 401
    buena = c.post("/api/argon/procesar", content=b"no-excel", headers={"x-clave": "Contraseña-Camión".encode("utf-8")})
    assert buena.status_code == 400  # la clave pasa; falla solo porque el cuerpo no es una Excel
