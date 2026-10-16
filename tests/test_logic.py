import pytest

from app import service
from app.config import get_settings
from app.errors import AppError
from app.mock_odoo import TOKEN_ACTUAL, TOKEN_ANTIGUO, MockOdoo
from app.qr import normalize_order_name, parse_qr
from app.tokens import sign_session, verify_session

HOSTS = {"www.stinsa.com"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ODOO_MODE", "mock")
    monkeypatch.setenv("ODOO_URL", "https://www.stinsa.com")
    monkeypatch.setenv("DRY_RUN", "false")


def qr(att_id, token):
    return f"https://www.stinsa.com/web/content/{att_id}?access_token={token}&download=true"


# --- parseo del QR
def test_parse_qr_ok():
    d = parse_qr(qr(900002, TOKEN_ACTUAL), HOSTS)
    assert d.attachment_id == 900002 and d.access_token == TOKEN_ACTUAL


@pytest.mark.parametrize("text", [
    "", "hola", "https://www.stinsa.com/web/content/abc?access_token=x",
    "https://www.stinsa.com/web/content/5", "https://malo.example.com/web/content/5?access_token=x",
    "javascript:alert(1)", "https://www.stinsa.com/otra/ruta/5?access_token=x",
])
def test_parse_qr_invalid(text):
    with pytest.raises(AppError):
        parse_qr(text, HOSTS)


def test_order_name():
    assert normalize_order_name(" s2621473 ") == "S2621473"
    for bad in ["", "2621473", "S12", "S26214a3", "S2621473; drop"]:
        with pytest.raises(AppError):
            normalize_order_name(bad)


# --- resolver
def test_resolve_qr_actual():
    r = service.resolve(MockOdoo(), get_settings(), qr_text=qr(900002, TOKEN_ACTUAL))
    assert r["tarea"]["id"] == 204067 and len(r["bultos"]) == 2
    assert r["version_antigua"] is False and r["verificado_por_qr"] is True


def test_resolve_qr_version_antigua_avisa_pero_muestra_datos():
    r = service.resolve(MockOdoo(), get_settings(), qr_text=qr(900001, TOKEN_ANTIGUO))
    assert r["version_antigua"] is True and r["aviso"] == "Esta orden tiene una versión más nueva."
    assert r["bultos"][0]["peso"] == 8450.0


def test_resolve_qr_token_incorrecto():
    with pytest.raises(AppError) as e:
        service.resolve(MockOdoo(), get_settings(), qr_text=qr(900002, TOKEN_ANTIGUO))
    assert e.value.status == 404


def test_resolve_qr_adjunto_inexistente():
    with pytest.raises(AppError):
        service.resolve(MockOdoo(), get_settings(), qr_text=qr(1, "x"))


def test_resolve_por_pedido():
    r = service.resolve(MockOdoo(), get_settings(), order_name="s2621473", company="transargi")
    assert r["tarea"]["id"] == 204067 and r["tarea"]["pedido"] == "S2621473" and r["verificado_por_qr"] is False


def test_mismo_pedido_en_otra_empresa_da_otra_tarea():
    r = service.resolve(MockOdoo(), get_settings(), order_name="S2621473", company="stinsa")
    assert r["tarea"]["id"] == 300001


def test_pedido_a_mano_exige_empresa():
    for c in (None, "", "otra"):
        with pytest.raises(AppError) as e:
            service.resolve(MockOdoo(), get_settings(), order_name="S2621473", company=c)
        assert e.value.code == "falta_empresa"


def test_regla_editable():
    r = service.resolve(MockOdoo(), get_settings(), order_name="S2600001", company="transargi")
    assert r["editable"] is False and "cerrada" in r["motivo_no_editable"]
    assert "facturada" in service.is_editable({"is_closed": False, "invoice_count": 2}, [])[1]
    ok = service.resolve(MockOdoo(), get_settings(), order_name="S2621473", company="transargi")
    assert ok["editable"] is True
    for state, expected in [("done", False), ("cancel", False), ("transit", True), ("loading", True)]:
        assert service.is_editable({"is_closed": False, "invoice_count": 0}, [{"state": state}])[0] is expected
    assert service.is_editable({"is_closed": True, "invoice_count": 0}, [])[0] is False


def test_resolve_pedido_inexistente_y_vacio():
    with pytest.raises(AppError):
        service.resolve(MockOdoo(), get_settings(), order_name="S9999999", company="stinsa")
    with pytest.raises(AppError):
        service.resolve(MockOdoo(), get_settings())


# --- sesión firmada
def test_sesion_ok_manipulada_y_caducada():
    t = sign_session("k", 5, {1: "2026-01-01 10:00:00"}, now=1000)
    assert verify_session("k", t, now=1001) == {"task_id": 5, "write_dates": {1: "2026-01-01 10:00:00"}, "verified": False}
    with pytest.raises(AppError):
        verify_session("otra", t, now=1001)
    with pytest.raises(AppError):
        verify_session("k", t[:-2] + "xx", now=1001)
    with pytest.raises(AppError) as e:
        verify_session("k", t, now=1000 + 3600)
    assert e.value.code == "sesion_caducada"


# --- lista blanca del cliente real
def test_cliente_real_rechaza_operaciones_no_permitidas():
    from app.odoo_client import OdooClient
    c = OdooClient(get_settings())
    for model, method in [("res.users", "read"), ("tms.package", "unlink"), ("project.task", "write"), ("ir.attachment", "search_read")]:
        with pytest.raises(PermissionError):
            c._call(model, method, [[1]])


def test_api_resolve_y_goods():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    assert c.get("/api/health").json()["ok"] is True
    r = c.post("/api/resolve-qr", json={"order_name": "S2621473", "company": "transargi"})
    assert r.status_code == 200 and r.json()["tarea"]["id"] == 204067
    r = c.post("/api/resolve-qr", json={"qr_text": "basura"})
    assert r.status_code == 400 and "error" in r.json()
    assert len(c.get("/api/goods", params={"q": "a"}).json()["resultados"]) <= 10



# --- guardado
def _sesion(odoo, **kw):
    return service.resolve(odoo, get_settings(), order_name="S2621473", company="transargi")


def _cambio(**kw):
    base = {"package_id": 287102, "goods_id": 2, "number_of_packages": 12, "euro_pallet_qty": 10, "shipping_weight": 8450.0}
    base.update(kw)
    return base


def test_update_ok_genera_pdf_y_cambia_datos():
    odoo = MockOdoo()
    r = _sesion(odoo)
    out = service.update(odoo, get_settings(), r["sesion"], 204067, [_cambio(number_of_packages=20, shipping_weight=9000.5)])
    assert out["bultos_modificados"] == 1 and out["simulado"] is True
    assert odoo.packages[287102]["number_of_packages"] == 20 and odoo.packages[287102]["shipping_weight"] == 9000.5
    att_id = int(out["pdf_url"].split("/")[-1].split("?")[0])
    token = out["pdf_url"].split("token=")[1]
    assert odoo.get_pdf(att_id, token).startswith(b"%PDF")
    assert odoo.get_pdf(att_id, "otro") is None
    assert odoo.tasks[204067]["tms_transport_order_attachment_id"][0] == att_id


@pytest.mark.parametrize("cambio, code", [
    ({"number_of_packages": -1}, "dato_invalido"),
    ({"euro_pallet_qty": -5}, "dato_invalido"),
    ({"shipping_weight": 0}, "dato_invalido"),
    ({"shipping_weight": -3.0}, "dato_invalido"),
    ({"shipping_weight": float("nan")}, "dato_invalido"),
    ({"goods_id": 999999}, "dato_invalido"),
    ({"package_id": 287103, "number_of_packages": 1}, "bulto_ajeno"),   # existe pero no es de la sesión... ver abajo
])
def test_update_validaciones(cambio, code):
    odoo = MockOdoo()
    r = _sesion(odoo)
    # 287103 pertenece a la tarea, así que para probar "ajeno" usamos un id que no es de la tarea
    if cambio.get("package_id") == 287103:
        cambio = {"package_id": 1, "number_of_packages": 1}
    with pytest.raises(AppError) as e:
        service.update(odoo, get_settings(), r["sesion"], 204067, [_cambio(**cambio)])
    assert e.value.code == code


def test_update_sin_cambios():
    odoo = MockOdoo()
    r = _sesion(odoo)
    with pytest.raises(AppError) as e:
        service.update(odoo, get_settings(), r["sesion"], 204067, [_cambio()])
    assert e.value.code == "sin_cambios"


def test_update_sesion_de_otra_tarea_o_manipulada():
    odoo = MockOdoo()
    r = _sesion(odoo)
    with pytest.raises(AppError):
        service.update(odoo, get_settings(), r["sesion"], 300001, [_cambio(number_of_packages=1)])
    with pytest.raises(AppError):
        service.update(odoo, get_settings(), "x.y", 204067, [_cambio(number_of_packages=1)])


def test_update_detecta_cambios_hechos_por_otro():
    odoo = MockOdoo()
    r = _sesion(odoo)
    odoo.packages[287102]["write_date"] = "2026-01-05 11:00:00"  # alguien lo tocó después
    with pytest.raises(AppError) as e:
        service.update(odoo, get_settings(), r["sesion"], 204067, [_cambio(number_of_packages=1)])
    assert e.value.code == "datos_cambiados" and e.value.status == 409


def test_update_rechaza_tarea_no_editable():
    odoo = MockOdoo()
    r = service.resolve(odoo, get_settings(), order_name="S2621473", company="transargi")
    odoo.tasks[204067]["is_closed"] = True  # se cierra después de cargar
    with pytest.raises(AppError) as e:
        service.update(odoo, get_settings(), r["sesion"], 204067, [_cambio(number_of_packages=1)])
    assert e.value.code == "no_editable"


def test_update_peso_alto_avisa_pero_guarda():
    odoo = MockOdoo()
    r = _sesion(odoo)
    out = service.update(odoo, get_settings(), r["sesion"], 204067, [_cambio(shipping_weight=45000)])
    assert out["avisos"] and odoo.packages[287102]["shipping_weight"] == 45000


def test_api_update_y_pdf():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    r = c.post("/api/resolve-qr", json={"order_name": "S2621473", "company": "transargi"}).json()
    body = {"task_id": r["tarea"]["id"], "sesion": r["sesion"], "cambios": [_cambio(euro_pallet_qty=11)]}
    u = c.post("/api/update", json=body)
    assert u.status_code == 200, u.text
    p = c.get(u.json()["pdf_url"])
    assert p.status_code == 200 and p.content.startswith(b"%PDF") and p.headers["content-type"] == "application/pdf"
    assert c.get("/api/pdf/1?token=x").status_code == 404
    bad = c.post("/api/update", json={"task_id": "x"})
    assert bad.status_code == 400 and "error" in bad.json()
import json

import httpx
from app.odoo_client import OdooClient


# --- fase 3: nota, DRY_RUN, reintento
def test_update_publica_nota_con_antes_y_ahora_y_imprime_una_vez():
    odoo = MockOdoo()
    r = _sesion(odoo)
    out = service.update(odoo, get_settings(), r["sesion"], 204067, [_cambio(number_of_packages=20, shipping_weight=9000, goods_id=3)])
    assert odoo.writes == [(287102, {"goods_id": 3, "number_of_packages": 20, "shipping_weight": 9000.0})]
    assert odoo.prints == [204067] and len(odoo.notes) == 1
    note = odoo.notes[0][1]
    assert "Modificado desde la app de conductores" in note and "PK2627486" in note
    assert "Elementos: 12 → 20" in note and "Peso: 8.450 kg → 9.000 kg" in note and "Mercancía: Cartón y papel → Bebidas" in note
    assert "sin QR" in note  # abierta por número de pedido
    assert out["adjunto_id"] == odoo.tasks[204067]["tms_transport_order_attachment_id"][0]


def test_nota_escapa_html():
    odoo = MockOdoo()
    assert "<script>" not in service._note_html([("<script>", ["x"])], True)


def test_dry_run_no_escribe_ni_imprime_ni_publica(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    odoo = MockOdoo()
    r = _sesion(odoo)
    out = service.update(odoo, get_settings(), r["sesion"], 204067, [_cambio(number_of_packages=99)])
    assert odoo.writes == [] and odoo.notes == [] and odoo.prints == []
    assert odoo.packages[287102]["number_of_packages"] == 12
    assert out["simulado"] is True and "DRY_RUN" in out["modo_aviso"] and out["adjunto_id"] == 900002


def test_fallo_al_imprimir_deja_datos_guardados_y_permite_reintentar():
    class Falla(MockOdoo):
        def print_order(self, *a):
            raise RuntimeError("odoo caído")
    odoo = Falla()
    r = _sesion(odoo)
    with pytest.raises(AppError) as e:
        service.update(odoo, get_settings(), r["sesion"], 204067, [_cambio(number_of_packages=7)])
    assert e.value.code == "pdf_fallo" and "guardado" in e.value.message
    assert odoo.packages[287102]["number_of_packages"] == 7 and len(odoo.notes) == 1
    odoo2 = MockOdoo()
    out = service.reprint(odoo2, get_settings(), r["sesion"], 204067)
    assert odoo2.prints == [204067] and out["pdf_url"]


def test_fallo_al_guardar_no_imprime():
    class Falla(MockOdoo):
        def write_package(self, *a):
            raise RuntimeError("sin permisos")
    odoo = Falla()
    r = _sesion(odoo)
    with pytest.raises(AppError) as e:
        service.update(odoo, get_settings(), r["sesion"], 204067, [_cambio(number_of_packages=7)])
    assert e.value.code == "guardado_fallo" and odoo.prints == [] and odoo.notes == []


def test_fallo_al_publicar_nota_no_impide_el_pdf():
    class Falla(MockOdoo):
        def post_note(self, *a):
            raise RuntimeError("x")
    odoo = Falla()
    r = _sesion(odoo)
    out = service.update(odoo, get_settings(), r["sesion"], 204067, [_cambio(number_of_packages=7)])
    assert out["ok"] and odoo.prints == [204067]


def test_fmt_num():
    assert service.fmt_num(8450) == "8.450" and service.fmt_num(1234567.5) == "1.234.567,5" and service.fmt_num(0) == "0"


# --- cliente real contra un Odoo falso (sin red)
class FakeOdooServer:
    def __init__(self):
        self.calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/jsonrpc":
            p = json.loads(request.content)["params"]
            if p["service"] == "common":
                return httpx.Response(200, json={"result": 7})
            model, method, args, kw = p["args"][3], p["args"][4], p["args"][5], p["args"][6]
            self.calls.append((model, method, args, kw))
            result = True
            if (model, method) == ("project.task", "read"):
                result = [{"id": 204067, "tms_transport_order_attachment_id": [555, "x.pdf"]}]
            if (model, method) == ("ir.attachment", "read"):
                result = [{"id": 555, "res_model": "project.task", "res_id": 204067, "access_token": "tok", "name": "x.pdf"}]
            return httpx.Response(200, json={"result": result})
        if path == "/web/session/authenticate":
            self.calls.append(("web", "authenticate"))
            return httpx.Response(200, json={"result": {"uid": 7}})
        if path.startswith("/report/pdf/"):
            self.calls.append(("report", path, request.url.params["context"]))
            return httpx.Response(200, content=b"%PDF-1.4 fake")
        if path == "/web/content/555":
            ok = request.url.params.get("access_token") == "tok"
            self.calls.append(("content", ok))
            return httpx.Response(200 if ok else 403, content=b"%PDF-1.4 fake" if ok else b"no")
        return httpx.Response(404)


def _real_client(monkeypatch):
    monkeypatch.setenv("ODOO_URL", "https://odoo.test")
    monkeypatch.setenv("ODOO_PASSWORD", "x")
    c = OdooClient(get_settings())
    srv = FakeOdooServer()
    c._http = httpx.Client(transport=httpx.MockTransport(srv.handler))
    c._anon = httpx.Client(transport=httpx.MockTransport(srv.handler))
    return c, srv


def test_cliente_real_escribe_solo_campos_permitidos(monkeypatch):
    c, srv = _real_client(monkeypatch)
    for bad in [{"state": "done"}, {"name": "x"}, {}, {"number_of_packages": 1, "state": "done"}]:
        with pytest.raises(PermissionError):
            c.write_package(1, bad)
    assert srv.calls == []
    c.write_package(287102, {"number_of_packages": 5})
    assert srv.calls[-1][:3] == ("tms.package", "write", [[287102], {"number_of_packages": 5}])


def test_cliente_real_nota_interna_en_la_tarea(monkeypatch):
    c, srv = _real_client(monkeypatch)
    c.post_note(204067, "<p>hola</p>")
    model, method, args, kw = srv.calls[-1]
    assert (model, method, args) == ("project.task", "message_post", [[204067]])
    assert kw["subtype_xmlid"] == "mail.mt_note" and kw["body"] == "<p>hola</p>"
    assert kw["context"]["allowed_company_ids"] == [1, 7]


def test_cliente_real_imprime_con_contexto_de_compania(monkeypatch):
    c, srv = _real_client(monkeypatch)
    att = c.print_order(204067, 7)
    assert att["id"] == 555
    rep = next(x for x in srv.calls if x[0] == "report")
    assert rep[1] == "/report/pdf/tms_reporting.tms_transportation_order/204067"
    assert json.loads(rep[2]) == {"allowed_company_ids": [7]}
    assert ("web", "authenticate") in srv.calls


def test_cliente_real_pdf_exige_token(monkeypatch):
    c, srv = _real_client(monkeypatch)
    assert c.get_pdf(555, "tok").startswith(b"%PDF")
    assert c.get_pdf(555, "malo") is None
    assert c.get_pdf(555, "") is None


# --- código de acceso compartido
@pytest.fixture
def con_codigo(monkeypatch):
    monkeypatch.setenv("APP_ACCESS_CODE", "clave-de-prueba")
    monkeypatch.setattr("app.main.FAIL_DELAY", 0)


def _cliente():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def test_todas_las_rutas_api_exigen_codigo_salvo_health():
    from app.main import app, require_access
    rutas = [r for r in app.routes if getattr(r, "path", "").startswith("/api/")]
    assert len(rutas) >= 6
    for r in rutas:
        protegida = require_access in [d.call for d in r.dependant.dependencies]
        assert protegida == (r.path != "/api/health"), r.path


def test_codigo_obligatorio_y_correcto(con_codigo):
    c = _cliente()
    cuerpo = {"order_name": "S2621473", "company": "transargi"}
    assert c.post("/api/resolve-qr", json=cuerpo).status_code == 401                                    # sin cabecera
    assert c.post("/api/resolve-qr", json=cuerpo, headers={"X-Codigo-Acceso": "otra"}).status_code == 401
    mal = c.post("/api/resolve-qr", json=cuerpo, headers={"X-Codigo-Acceso": "otra"}).json()
    assert mal["codigo"] == "codigo_invalido" and "clave" not in str(mal)
    ok = {"X-Codigo-Acceso": "clave-de-prueba"}
    assert c.post("/api/resolve-qr", json=cuerpo, headers=ok).status_code == 200
    assert c.post("/api/login", headers=ok).status_code == 200
    assert c.post("/api/login", headers={"X-Codigo-Acceso": "otra"}).status_code == 401
    assert c.get("/api/goods", params={"q": "a"}).status_code == 401
    assert c.get("/api/goods", params={"q": "a"}, headers=ok).status_code == 200
    assert c.get("/api/pdf/900002?token=x").status_code == 401
    assert c.post("/api/update", json={"task_id": 1, "sesion": "x", "cambios": []}).status_code == 401
    assert c.post("/api/reprint", json={"task_id": 1, "sesion": "x"}).status_code == 401


def test_health_es_publico_e_indica_si_hace_falta_codigo(con_codigo):
    h = _cliente().get("/api/health").json()
    assert h["requiere_codigo"] is True and "clave" not in str(h)


def test_sin_codigo_en_modo_simulado_no_se_pide():
    c = _cliente()
    assert c.get("/api/health").json()["requiere_codigo"] is False
    assert c.post("/api/login").status_code == 200


@pytest.mark.parametrize("codigo", ["", "corto"])
def test_modo_real_sin_codigo_suficiente_se_niega_a_funcionar(monkeypatch, codigo):
    monkeypatch.setenv("ODOO_MODE", "real")
    monkeypatch.setenv("SECRET_KEY", "x" * 20)
    monkeypatch.setenv("APP_ACCESS_CODE", codigo)
    c = _cliente()
    r = c.post("/api/resolve-qr", json={"order_name": "S2621473", "company": "transargi"}, headers={"X-Codigo-Acceso": codigo})
    assert r.status_code == 503 and r.json()["codigo"] == "sin_configurar"
    assert c.get("/api/health").json()["requiere_codigo"] is True


def test_modo_real_con_codigo_valido_pasa_la_barrera(monkeypatch):
    from app.main import require_access
    monkeypatch.setenv("ODOO_MODE", "real")
    monkeypatch.setenv("SECRET_KEY", "x" * 20)
    monkeypatch.setenv("APP_ACCESS_CODE", "codigo-largo-ok")
    assert require_access("codigo-largo-ok") is None
    with pytest.raises(AppError):
        monkeypatch.setattr("app.main.FAIL_DELAY", 0)
        require_access("codigo-largo-mal")
