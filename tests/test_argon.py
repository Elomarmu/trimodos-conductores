"""Pruebas del escritor ARGON con un Odoo simulado (no se conecta a nada)."""
import datetime as dt
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
        if modelo in ("product.product", "res.partner", "account.analytic.account", "fleet.vehicle"):
            return []  # referencias de la Excel: sin datos en el Odoo simulado
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


def test_clave_con_punto_medio_en_latin1(monkeypatch):
    """n8n manda «·» en latin-1 (1 byte): también debe valer."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import argon_api as A
    monkeypatch.setenv("ARGON_CLAVE", "Argon·Prueba")
    app = FastAPI(); app.include_router(A.router); c = TestClient(app)
    assert c.post("/api/argon/procesar", content=b"x", headers={"x-clave": "Argon·Prueba".encode("latin-1")}).status_code == 400
    assert c.post("/api/argon/procesar", content=b"x", headers={"x-clave": "Argon·Prueba".encode("utf-8")}).status_code == 400
    assert c.post("/api/argon/procesar", content=b"x", headers={"x-clave": b"Argon.Prueba"}).status_code == 401


def test_excel_en_base64(monkeypatch):
    """n8n envía la Excel como JSON {"excel_b64": ...}."""
    import base64, json as _json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import argon_api as A
    monkeypatch.setenv("ARGON_CLAVE", "secreto")
    monkeypatch.setattr(E, "Odoo", lambda *a, **k: OdooFalso())
    for v, x in (("ARGON_ODOO_URL", "https://stinsa18.x"), ("ARGON_ODOO_DB", "d"), ("ARGON_ODOO_USER", "u"), ("ARGON_ODOO_PASSWORD", "p")):
        monkeypatch.setenv(v, x)
    app = FastAPI(); app.include_router(A.router); c = TestClient(app)
    cuerpo = _json.dumps({"excel_b64": base64.b64encode(Path(EXCEL).read_bytes()).decode()})
    r = c.post("/api/argon/procesar?modo=prueba&hoy=2026-10-30", content=cuerpo, headers={"x-clave": "secreto", "content-type": "application/json"})
    assert r.status_code == 200, r.text
    assert c.post("/api/argon/procesar", content=b'{"otra":1}', headers={"x-clave": "secreto"}).status_code == 400


def test_enlaces_en_el_correo(monkeypatch):
    """Nº de pedido con enlace a Odoo y datos que faltan con enlace a su celda de la Excel."""
    import base64, json as _json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import argon_api as A

    class OdooConEnlace(OdooFalso):
        def enlace(self, i):
            return f"https://stinsa18.x/odoo/action-sale.action_orders/{i}"

    o = OdooConEnlace()
    r = E.procesar(EXCEL, CFG, real=True, odoo=o, limite_s=999)
    assert all(p["url"].endswith(f"/{p['pedido'][1:]}") for p in r["pedidos_creados"])
    for x in r["pendientes_de_datos"]:
        assert x["celda"] and all(i["celda"] and "!" in i["celda"] for i in x["ir"]), x
    monkeypatch.setenv("ARGON_CLAVE", "secreto")
    monkeypatch.setattr(E, "Odoo", lambda *a, **k: OdooConEnlace())
    for v, x in (("ARGON_ODOO_URL", "https://stinsa18.x"), ("ARGON_ODOO_DB", "d"), ("ARGON_ODOO_USER", "u"), ("ARGON_ODOO_PASSWORD", "p")):
        monkeypatch.setenv(v, x)
    app = FastAPI(); app.include_router(A.router); c = TestClient(app)
    cuerpo = _json.dumps({"excel_b64": base64.b64encode(Path(EXCEL).read_bytes()).decode(),
                          "url_excel": "https://sp.example/Doc.aspx?sourcedoc=%7BX%7D&file=ARGON.xlsx"})
    j = c.post("/api/argon/procesar?modo=real&hoy=2026-10-30", content=cuerpo, headers={"x-clave": "secreto"}).json()
    assert 'href="https://stinsa18.x/odoo/action-sale.action_orders/' in j["correo_html"]
    if j["odoo"]["pendientes_de_datos"]:
        assert "activeCell=" in j["correo_html"]


def test_referencias_completan_la_excel():
    """Con la referencia se completan nombre e ID; con el ID, la referencia. Ambiguas → aviso."""
    import argon_facturacion as F
    import argon_referencias as R
    F.COLS.clear()
    F.COLS.update({"TARIFAS": {"Servicio": "A", "Producto en Odoo": "E", "ID producto": "F", "Proveedor": "G", "ID proveedor": "H"},
                   "PRODUCTOS": {"Código": "A", "Producto en Odoo": "E", "ID producto": "F", "Proveedor por defecto": "J", "ID proveedor": "K"},
                   "CONDUCTORES": {"Código": "A", "ID proveedor": "G"}, "RESERVAS": {"Proveedor": "B", "ID proveedor": "C"}})
    t1 = {"_fila": 4, "Servicio": "TREN", "Producto en Odoo": "[2250] LLAGOSTA-AMBERES", "ID producto": None, "Proveedor": None, "ID proveedor": 18310}
    t2 = {"_fila": 5, "Servicio": "TREN", "Ref. producto": "1", "Ref./NIF proveedor": "B12345678"}
    tablas = {"tarifas": [t1, t2], "productos": {}, "conductores": {}, "reservas": []}

    class O:
        def __call__(self, modelo, metodo, args, **kw):
            dom = args[0]
            if modelo == "product.product":
                if dom[0][0] == "default_code":
                    return [{"id": 1, "default_code": "1", "display_name": "[1] A"}, {"id": 2, "default_code": "1", "display_name": "[1] B"}]
                return []
            if dom[0] == "|":
                return [{"id": 7, "ref": False, "vat": "ESB12345678", "display_name": "EMPRESA SL", "parent_id": False, "supplier_rank": 1},
                        {"id": 8, "ref": False, "vat": "ESB12345678", "display_name": "EMPRESA SL, Almacén", "parent_id": [7, ""], "supplier_rank": 0}]
            return [{"id": 18310, "ref": False, "vat": "CHE123", "display_name": "HUPAC INTERMODAL SA", "parent_id": False, "supplier_rank": 1}]

    r = R.completar(tablas, O())
    celdas = {(e["hoja"], e["celda"]): e["valor"] for e in r["escrituras"]}
    ref_p, ref_v = F.COLS["TARIFAS"]["Ref. producto"], F.COLS["TARIFAS"]["Ref./NIF proveedor"]
    assert celdas[("TARIFAS", f"{ref_p}3")] == "Ref. producto"           # cabecera nueva
    assert t2["ID proveedor"] == 7 and t2["Proveedor"] == "EMPRESA SL"   # por NIF, la empresa principal
    assert celdas[("TARIFAS", f"{ref_v}4")] == "CHE123"                  # referencia desde el ID
    assert any("repetido" in a["texto"] for a in r["avisos"])            # producto [1] ambiguo
    assert any("[2250] no existe" in a["texto"] for a in r["avisos"])      # «[2250]» escrito en el nombre se busca


def _mov(fecha, tipo, ubic, texto, **kw):
    return {"contenedor": "TONU 1", "codigo_corto": "1", "fecha": fecha, "tipo": tipo, "ubicacion": ubic, "texto": texto,
            "carga": "CARGADO", "bloqueado": False, "referencia": None, "iniciales": None, "chasis": None, "origen": None,
            "celda": "B1", **kw}


def test_cr_a_es_circulando_y_reexp_es_otro_tren():
    import argon_facturacion as F
    tipos = {"AMBERES": "TERMINAL FERROVIARIA", "MORROT": "TERMINAL FERROVIARIA", "MADRID": "TERMINAL FERROVIARIA"}
    movs = [_mov("2026-11-02", "SALIDA_TREN", "AMBERES", "SL AMBERES"),
            _mov("2026-11-03", "CIRCULANDO", "PORTBOU", "CR A PORT BOU"),
            _mov("2026-11-04", "SITUACION", "MORROT", "MORROT REEXP", reexpedicion=True),
            _mov("2026-11-05", "SITUACION", "MADRID", "MADRID"),
            _mov("2026-11-06", "DESCARGA", None, "DESCARGA")]
    trenes = [(s.origen, s.destino) for s in F.construir(movs, tipos) if s.servicio == "TREN"]
    assert trenes == [("AMBERES", "MORROT"), ("MORROT", "MADRID")]
    assert not [s for s in F.construir(movs, tipos) if s.servicio == "CARRETERA"]
    # «CR A» sin tren delante tampoco es un servicio
    assert not F.construir([_mov("2026-11-03", "CIRCULANDO", "PORTBOU", "CR A PORT BOU")], tipos)
    # REEXP y al día siguiente «SL MORROT»: un solo tramo desde MORROT
    movs2 = movs[:3] + [_mov("2026-11-05", "SALIDA_TREN", "MORROT", "SL MORROT")] + movs[3:]
    trenes2 = [(s.origen, s.destino) for s in F.construir(movs2, tipos) if s.servicio == "TREN"]
    assert trenes2 == [("AMBERES", "MORROT"), ("MORROT", "MADRID")]


def test_variantes_de_rex():
    import argon_lector as L
    import argon_facturacion as F
    for t in ("MORROT REX", "MORROT (REX)", "MORROT(REX)", "MORROT-REX", "MORROT REEXP", "LA LLAGOSTA(REX)"):
        assert L.RE_REEX.search(L.norm(t)), t
    for t in ("REXACH", "GENT W", "SL MORROT 914616"):
        assert not L.RE_REEX.search(L.norm(t)), t
    # varios días «MORROT (REX)» seguidos: un solo tramo nuevo, desde el último
    tipos = {"AMBERES": "TERMINAL FERROVIARIA", "MORROT": "TERMINAL FERROVIARIA", "MADRID": "TERMINAL FERROVIARIA"}
    movs = [_mov("2026-11-02", "SALIDA_TREN", "AMBERES", "SL AMBERES"),
            _mov("2026-11-04", "SITUACION", "MORROT", "MORROT (REX)", reexpedicion=True),
            _mov("2026-11-05", "SITUACION", "MORROT", "MORROT (REX)", reexpedicion=True),
            _mov("2026-11-06", "SITUACION", "MADRID", "MADRID"),
            _mov("2026-11-07", "DESCARGA", None, "DESCARGA")]
    trenes = [(s.origen, s.destino, s.fecha) for s in F.construir(movs, tipos) if s.servicio == "TREN"]
    assert trenes == [("AMBERES", "MORROT", "2026-11-02"), ("MORROT", "MADRID", "2026-11-05")]


def test_bloqueo_largo_cierra_el_pedido_y_el_corto_no():
    from types import SimpleNamespace as N
    import argon_facturacion as F

    def e(fecha, texto, rojo=False, cancelada=False):
        return N(contenedor="TONU 1", fecha=fecha, texto=texto, bloqueado=rojo, cancelada=cancelada, forzar=None,
                 celda="B1", ubicacion=None)
    estados = [e("2026-11-02", "SL AMBERES"), e("2026-11-04", "MORROT"),
               e("2026-11-05", "CRYO", True), e("2026-11-06", "MORROT"),              # taller 1 día: no corta
               e("2026-11-09", "SL MORROT"), e("2026-11-10", "SL CANCELADA", True, True),  # cancelada: no cuenta
               e("2026-11-12", "TALLER", True), e("2026-11-16", "TALLER", True), e("2026-11-24", "MADRID"),  # 12 días: corta
               e("2026-11-25", "SL MADRID")]
    bloq = F.bloqueos(estados, hoy=dt.date(2026, 11, 30))
    assert [(t["inicio"], t["dias"], t["corta"]) for t in bloq["TONU 1"]] == [("2026-11-05", 1, False), ("2026-11-12", 12, True)]
    movs = [_mov("2026-11-02", "SALIDA_TREN", "AMBERES", "SL AMBERES"), _mov("2026-11-04", "SITUACION", "MORROT", "MORROT"),
            _mov("2026-11-09", "SALIDA_TREN", "MORROT", "SL MORROT"), _mov("2026-11-24", "SITUACION", "MADRID", "MADRID"),
            _mov("2026-11-25", "SALIDA_TREN", "MADRID", "SL MADRID")]
    tipos = {"AMBERES": "TERMINAL FERROVIARIA", "MORROT": "TERMINAL FERROVIARIA", "MADRID": "TERMINAL FERROVIARIA"}
    s = [x for x in F.construir(movs, tipos) if x.servicio == "TREN"] + F.servicios_bloqueo(bloq)
    F.rotaciones(s, bloq)
    rot = {(x.servicio, x.fecha): x.rotacion.split("|")[1] for x in s}
    assert rot[("TREN", "2026-11-09")] == "2026-11-02"      # el taller corto no rompe el viaje
    assert rot[("BLOQUEO", "2026-11-05")] == "2026-11-02"   # va como línea de seguimiento en el mismo pedido
    assert rot[("BLOQUEO", "2026-11-12")] == "2026-11-02"   # el bloqueo largo cierra este pedido…
    assert rot[("TREN", "2026-11-25")] == "2026-11-25"      # …y lo siguiente va a otro


def test_formatear_mantiene_lo_que_lee_el_automatismo(monkeypatch, tmp_path):
    import base64, json as _json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import argon_api as A
    import argon_facturacion as F
    monkeypatch.setenv("ARGON_CLAVE", "secreto")
    app = FastAPI(); app.include_router(A.router); c = TestClient(app)
    cuerpo = _json.dumps({"excel_b64": base64.b64encode(Path(EXCEL).read_bytes()).decode()})
    assert c.post("/api/argon/formatear", content=cuerpo, headers={"x-clave": "mala"}).status_code == 401
    j = c.post("/api/argon/formatear", content=cuerpo, headers={"x-clave": "secreto"}).json()
    nuevo = tmp_path / "n.xlsx"; nuevo.write_bytes(base64.b64decode(j["excel_b64"]))
    clave = lambda r: [(s.id_servicio, s.precio, s.producto_id, s.proveedor_id, s.estado) for s in F.calcular(r)]  # noqa: E731
    assert clave(str(nuevo)) == clave(EXCEL)
    assert next(x for x in j["resumen"] if x["hoja"] == "TARIFAS")["columnas"][:5] == ["Servicio", "Origen", "Destino", "Cargado/Vacío", "¿Se factura?"]


def test_nombres_ubicaciones_y_cuentas():
    """Con el nombre (sin referencia) también se completa; ubicaciones y cuentas analíticas igual; dudas → aviso con enlaces."""
    import argon_facturacion as F
    import argon_referencias as R
    F.COLS.clear()
    F.COLS.update({"TARIFAS": {"Servicio": "A", "Producto en Odoo": "B", "ID producto": "C", "Proveedor": "D", "ID proveedor": "E",
                               "Cuenta analítica (ID)": "F"},
                   "PRODUCTOS": {}, "CONDUCTORES": {}, "RESERVAS": {},
                   "UBICACIONES": {"Texto escrito en la Excel": "A", "Ubicación normalizada": "B", "ID en Odoo": "G"}})
    t = {"_fila": 4, "Servicio": "TREN", "Producto en Odoo": "llagosta-amberes", "Proveedor": "Hupac Intermodal SA",
         "Cuenta analítica": "TECO + LAR"}
    u1 = {"_fila": 4, "Ubicación normalizada": "LA NEGRILLA", "Nombre en Odoo": "TERMINAL SEVILLA LA NEGRILLA"}
    u2 = {"_fila": 5, "Ubicación normalizada": "MORROT", "Nombre en Odoo": "MORROT"}
    tablas = {"tarifas": [t], "productos": {}, "conductores": {}, "reservas": [], "ubicaciones": [u1, u2], "ids_ubic": {}}

    class O:
        url = "https://stinsa18.x"

        def __call__(self, modelo, metodo, args, **kw):
            dom = args[0]
            if modelo == "product.product":
                return [{"id": 9, "default_code": "2250", "name": "LLAGOSTA-AMBERES", "display_name": "[2250] LLAGOSTA-AMBERES"}]
            if modelo == "account.analytic.account":
                return [{"id": 88082, "code": "TECO", "name": "ST FERROCARRIL", "display_name": "[TECO] ST FERROCARRIL"},
                        {"id": 95387, "code": False, "name": "LAR", "display_name": "LAR"}]
            nombre = dom[0][2] if dom and dom[0][0] == "name" else ""
            if nombre.upper() == "HUPAC INTERMODAL SA":
                return [{"id": 18310, "ref": False, "vat": "CHE123", "name": "HUPAC INTERMODAL SA", "display_name": "HUPAC INTERMODAL SA",
                         "parent_id": False, "supplier_rank": 1}]
            if nombre == "TERMINAL SEVILLA LA NEGRILLA":
                return [{"id": 9420, "ref": False, "vat": False, "name": nombre, "display_name": nombre, "parent_id": False, "supplier_rank": 0}]
            if nombre == "MORROT":
                return [{"id": i, "ref": False, "vat": False, "name": "MORROT", "display_name": f"MORROT {i}", "parent_id": False,
                         "supplier_rank": 0} for i in (1, 2)]
            return []

    r = R.completar(tablas, O())
    assert t["ID producto"] == 9 and t["Ref. producto"] == "2250" and t["Producto en Odoo"] == "[2250] LLAGOSTA-AMBERES"
    assert t["ID proveedor"] == 18310 and t["Ref./NIF proveedor"] == "CHE123"
    assert t["Cuenta analítica (ID)"] == "88082,95387"
    assert u1["ID en Odoo"] == 9420 and tablas["ids_ubic"] == {"LA NEGRILLA": 9420}
    av = [a for a in r["avisos"] if "MORROT" in a["texto"]]
    assert av and av[0]["odoo"][0]["url"] == "https://stinsa18.x/odoo/res.partner/1" and av[0]["celda"].startswith(F.COLS["UBICACIONES"]["Nombre en Odoo"])


def test_tractora_por_matricula_rellena_conductor_y_va_en_el_acarreo():
    import argon_facturacion as F
    import argon_odoo as AO
    import argon_referencias as R
    F.COLS.clear()
    F.COLS.update({"TARIFAS": {}, "PRODUCTOS": {}, "RESERVAS": {}, "UBICACIONES": {},
                   "CONDUCTORES": {"Código": "A", "Nombre": "C", "ID conductor en Odoo": "E", "Tractora habitual": "F", "ID tractora": "G", "ID proveedor": "I"},
                   "CHASIS": {"Nº en la Excel": "A", "Matrícula": "B", "ID remolque en Odoo": "C"}})
    c = {"_fila": 4, "Código": "AU", "Tipo": "Propio", "Tractora habitual": "9684 KVJ"}
    ch = {"_fila": 4, "Nº en la Excel": "6704", "Matrícula": "R6704BCH"}
    tablas = {"tarifas": [], "productos": {}, "conductores": {"AU": c}, "reservas": [], "chasis": {"6704": ch}}

    class O:
        def __call__(self, modelo, metodo, args, **kw):
            if modelo == "fleet.vehicle":
                return [{"id": 6845, "license_plate": "9684KVJ", "display_name": "9684KVJ", "driver_id": [9581, "AURELIO RIDAO RAMÍREZ"], "vehicle_type": "tractor"},
                        {"id": 8330, "license_plate": "R6704BCH", "display_name": "R6704BCH", "driver_id": False, "vehicle_type": "trailer"}]
            return []

    R.completar(tablas, O())
    assert c["ID tractora"] == 6845 and c["ID conductor en Odoo"] == 9581 and c["Nombre"] == "AURELIO RIDAO RAMÍREZ"
    assert ch["ID remolque en Odoo"] == 8330
    s = F.Servicio(id_servicio="x", fecha="2026-11-02", fecha_fin=None, contenedor="TONU 1", codigo_corto="1", servicio="TRAYECTO",
                   origen="NEG", destino="ALG", via=None, expedicion=None, cargado="Cargado", cantidad=120, unidad="km",
                   conductor_codigo="AU", chasis="6704", texto="6704 NEG-ALG AU", producto_id=2461, precio=1.22,
                   conductor="AURELIO", conductor_id=9581, tractora_id=6845, remolque_id=8330, estado="LISTO PARA ODOO")
    l = AO.linea_de(s, {})
    assert (l["tractor_id"], l["trailer_id"], l["driver_id"], l["vendor_id"]) == (6845, 8330, 9581, 9581)


def test_llega_y_sale_el_mismo_dia():
    """«LLAG/ SL LLAGOSTA»: llega a LA LLAGOSTA y el mismo día sale en otro tren → dos tramos."""
    import argon_lector as L
    import argon_facturacion as F
    alias = {"LLAGOSTA": "LA LLAGOSTA", "LA LLAGOSTA": "LA LLAGOSTA", "MORROT": "MORROT", "AMBERES": "AMBERES"}
    assert L.llega_y_sale("LLAG/ SL LLagosta", alias)[1] == "SL LLAGOSTA"
    assert L.llega_y_sale("MORROT/SL 927108", alias) == ("MORROT", "SL MORROT 927108")
    assert L.llega_y_sale("SL AMBERES", alias) == (None, "SL AMBERES")
    tipos = {"AMBERES": "TERMINAL FERROVIARIA", "LA LLAGOSTA": "TERMINAL DE PASO", "LA NEGRILLA": "TERMINAL FERROVIARIA"}
    movs = [_mov("2026-10-16", "SALIDA_TREN", "AMBERES", "SL AMBERES"),
            _mov("2026-10-19", "CIRCULANDO", "PORTBOU", "CR A PORT BOU"),
            _mov("2026-10-20", "SALIDA_TREN", "LA LLAGOSTA", "LLAG/ SL LLAGOSTA", llegada="LA LLAGOSTA"),
            _mov("2026-10-21", "DISPONIBLE", "LA NEGRILLA", "NEGRILLA 18:00"),
            _mov("2026-10-22", "DESCARGA", "LA NEGRILLA", "DESCARGA")]
    trenes = [(s.origen, s.destino, s.fecha, s.fecha_fin) for s in F.construir(movs, tipos) if s.servicio == "TREN"]
    assert trenes == [("AMBERES", "LA LLAGOSTA", "2026-10-16", "2026-10-20"), ("LA LLAGOSTA", "LA NEGRILLA", "2026-10-20", "2026-10-21")]


def test_no_lleva_a_odoo_la_planificacion():
    """Las celdas de hoy en adelante son planificación: no generan servicios; un tren en camino espera a llegar."""
    import argon_facturacion as F
    hoy = dt.date(2026, 10, 25)
    s = F.calcular(EXCEL, dt.date(2026, 10, 1), hoy=hoy)
    assert s and all(x.fecha < hoy.isoformat() for x in s)
    assert all((x.fecha_fin or x.fecha) < hoy.isoformat() for x in s if x.servicio == "TREN" and x.destino)


# ------------------------------------------------------------------ histórico para el cuadro de mandos
def _copia_con_hoja(tmp_path, nombre=None, a1=None):
    import openpyxl
    wb = openpyxl.load_workbook(EXCEL)
    if nombre:
        ws = wb.create_sheet(nombre)
        if a1:
            ws["A1"] = a1
    ruta = tmp_path / "argon.xlsx"
    wb.save(ruta)
    return str(ruta)


def test_historico_tablas_y_solo_pasado():
    import argon_historico as H
    hoy = dt.date(2026, 10, 31)
    d = H.datos(EXCEL, hoy)
    for t in ("ROTACIONES", "EQUIPOS", "RUTAS", "LUGARES", "MESES", "DIAS"):
        assert d[t], t
    assert all(r["inicio"] < hoy.isoformat() for r in d["ROTACIONES"])
    assert all(r["fecha_descarga"] == "" or r["fecha_descarga"] >= "2026-01-01" for r in d["ROTACIONES"])
    cerradas = [r for r in d["ROTACIONES"] if r["estado"] == "Cerrada" and r["fecha_descarga"] >= "2026-01-01"]
    assert sum(r["rotaciones"] for r in d["EQUIPOS"]) == len(cerradas)
    assert all(0 <= (r["pct_cargado"] or 0) <= 100 for r in d["EQUIPOS"])
    assert sum(m["descargas"] for m in d["MESES"]) == sum(x["descargas"] for x in d["LUGARES"])


def test_historico_matriz_borra_lo_anterior():
    import argon_historico as H
    hoy = dt.date(2026, 10, 31)
    m = H.matriz(H.datos(EXCEL, hoy), hoy)
    assert len(m) >= H.FILAS and all(len(f) == H.COLS for f in m)
    assert str(m[0][0]).startswith(H.TITULO)
    assert ["#TABLA", "EQUIPOS"] == m[2][:2]


def test_historico_usa_hoja1_y_la_renombra_la_noche_siguiente(tmp_path):
    import argon_historico as H
    hoy = dt.date(2026, 10, 31)
    assert H.escrituras(_copia_con_hoja(tmp_path), hoy) == []  # ni DASHBOARD ni Hoja1: no hace nada
    w = H.escrituras(_copia_con_hoja(tmp_path, "Hoja1"), hoy)
    assert w[0]["hoja"] == "Hoja1" and w[0]["celda"].startswith("A1:N") and len(w[0]["valores"]) >= H.FILAS
    w = H.escrituras(_copia_con_hoja(tmp_path, "Hoja1", H.TITULO + " — ..."), hoy)
    assert w == [{"hoja": "Hoja1", "ruta": "worksheets('Hoja1')", "cuerpo": {"name": "DASHBOARD"}}]
    assert H.escrituras(_copia_con_hoja(tmp_path, "Hoja1", "otra cosa"), hoy) == []
    w = H.escrituras(_copia_con_hoja(tmp_path, "DASHBOARD", H.TITULO), hoy)
    assert w[0]["hoja"] == "DASHBOARD" and "valores" in w[0]


def test_api_historico_solo_si_n8n_lo_pide(monkeypatch):
    import argon_api as A
    import argon_historico as H
    llamadas = []
    monkeypatch.setattr(H, "escrituras", lambda ruta, dia: llamadas.append(dia) or [{"hoja": "DASHBOARD", "celda": "A1:N2", "valores": [[1], [2]]}])
    monkeypatch.setattr(A.E, "procesar", lambda *a, **k: {"restantes": 0, "excel": {"escrituras": []}, "errores": [],
                                                           "pedidos_creados": [], "pedidos_ampliados": [], "lineas_nuevas": 0})
    monkeypatch.setattr(A, "correo", lambda *a, **k: "")
    r = A._procesar(EXCEL, "prueba", dt.date(2026, 10, 31))
    assert r["escrituras_excel"] == [] and not llamadas
    r = A._procesar(EXCEL, "prueba", dt.date(2026, 10, 31), historico=True)
    assert r["escrituras_excel"][0]["hoja"] == "DASHBOARD"


def test_correo_empieza_con_enlaces_al_dashboard_y_a_la_excel():
    import argon_api as A
    res = {"pedidos_creados": [], "pedidos_ampliados": [], "lineas_nuevas": 0, "errores": [], "modo": "real",
           "pendientes_de_datos": [], "bloqueos_largos": [], "excel": {"avisos": [], "escrituras": []}}
    av = {k: [] for k in ("bloqueados", "sin_anotar", "sl_sin_numero", "revisar", "vencimientos")}
    h = A.correo(dt.date(2026, 11, 3), res, av, "https://sp/ARGON.xlsx")
    assert h.index("Abrir el cuadro de mandos") < h.index("<!--ODOO-->")
    assert "https://sp/ARGON.xlsx" in h[: h.index("<!--ODOO-->")] and A.DASHBOARD_URL in h
