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
    monkeypatch.delenv("APP_ACCESS_CODE", raising=False)  # el .env real trae uno: los tests no deben depender de él


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


def test_regla_editable_solo_en_nuevo_o_en_desarrollo():
    r = service.resolve(MockOdoo(), get_settings(), order_name="S2600001", company="transargi")     # etapa «Hecho»
    assert r["editable"] is False and "«Hecho»" in r["motivo_no_editable"] and "«Nuevo» o «En desarrollo»" in r["motivo_no_editable"]
    assert service.resolve(MockOdoo(), get_settings(), order_name="S2621473", company="transargi")["editable"] is True   # «En desarrollo»
    assert service.resolve(MockOdoo(), get_settings(), order_name="S2621473", company="stinsa")["editable"] is True      # «Nuevo»


@pytest.mark.parametrize("etapa, esperado", [
    ("Nuevo", True), ("En desarrollo", True), ("EN DESARROLLO", True), ("  nuevo ", True), ("En desarrolló", True),   # sin importar mayúsculas ni acentos
    ("Hecho", False), ("Cancelado", False), ("En pruebas", False), ("Nuevo pedido", False), ("", False),
])
def test_is_editable_por_etapa(etapa, esperado):
    assert service.is_editable({"stage_id": [1, etapa]})[0] is esperado


def test_is_editable_sin_etapa_y_etapas_configurables():
    assert service.is_editable({"stage_id": False})[0] is False
    assert service.is_editable({})[0] is False
    assert service.is_editable({"stage_id": [9, "En ruta"]}, stages=("en ruta",))[0] is True
    assert "«En ruta»" in service.is_editable({"stage_id": [9, "Hecho"]}, stages=("en ruta",))[1]


def test_las_etapas_editables_se_leen_del_entorno(monkeypatch):
    assert get_settings().editable_stages == ["nuevo", "en desarrollo"]
    monkeypatch.setenv("EDITABLE_STAGES", "nuevo, en ruta")
    assert get_settings().editable_stages == ["nuevo", "en ruta"]


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
    odoo.tasks[204067]["stage_id"] = [9, "Hecho"]  # cambia de etapa después de cargar
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
    def __init__(self, major=15):
        self.calls = []
        self.major = major

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/jsonrpc":
            p = json.loads(request.content)["params"]
            if p["service"] == "common":
                if p["method"] == "version":
                    return httpx.Response(200, json={"result": {"server_version": f"{self.major}.0", "server_version_info": [self.major, 0, 0, "final", 0, ""]}})
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


# --- tareas con is_transport_order = False pero con su orden de transporte (caso real S2621553)
def test_pedido_con_tarea_sin_marca_pero_con_orden_generada_se_encuentra():
    r = service.resolve(MockOdoo(), get_settings(), order_name="S2621553", company="transargi")
    assert r["tarea"]["id"] == 300003 and r["tarea"]["pedido"] == "S2621553" and len(r["bultos"]) == 1


def test_qr_de_tarea_sin_marca_funciona():
    qr_ = qr(900005, "55555555-aaaa-bbbb-cccc-000000000005")
    r = service.resolve(MockOdoo(), get_settings(), qr_text=qr_)
    assert r["tarea"]["id"] == 300003 and r["verificado_por_qr"] is True and r["version_antigua"] is False


def test_tarea_sin_marca_ni_orden_generada_sigue_rechazada():
    odoo = MockOdoo()
    odoo.orders[("S2600009", 7)] = [300004]
    with pytest.raises(AppError):
        service.resolve(odoo, get_settings(), order_name="S2600009", company="transargi")
    assert service.is_transport_task({"is_transport_order": False, "tms_transport_order_attachment_id": False}) is False
    assert service.is_transport_task({"is_transport_order": True}) is True


def test_se_puede_guardar_en_tarea_sin_marca():
    odoo = MockOdoo()
    r = service.resolve(odoo, get_settings(), order_name="S2621553", company="transargi")
    out = service.update(odoo, get_settings(), r["sesion"], 300003, [_cambio(number_of_packages=5)])
    assert out["ok"] and odoo.prints == [300003]


def test_bulto_vacio_sin_tocar_no_bloquea_el_guardado_de_otro_bulto():
    odoo = MockOdoo()
    odoo.packages[287103].update({"goods_id": False, "number_of_packages": 0, "euro_pallet_qty": 0, "shipping_weight": 0.0})  # bulto vacío, como PK2627590
    r = service.resolve(odoo, get_settings(), order_name="S2621473", company="transargi")
    out = service.update(odoo, get_settings(), r["sesion"], 204067, [
        _cambio(number_of_packages=30),
        {"package_id": 287103, "goods_id": None, "number_of_packages": 0, "euro_pallet_qty": 0, "shipping_weight": 0.0},
    ])
    assert out["bultos_modificados"] == 1 and odoo.writes == [(287102, {"number_of_packages": 30})]


def test_si_se_cambia_el_peso_de_un_bulto_vacio_se_valida_ese_campo():
    odoo = MockOdoo()
    odoo.packages[287103].update({"goods_id": False, "number_of_packages": 0, "euro_pallet_qty": 0, "shipping_weight": 0.0})
    r = service.resolve(odoo, get_settings(), order_name="S2621473", company="transargi")
    with pytest.raises(AppError) as e:
        service.update(odoo, get_settings(), r["sesion"], 204067, [{"package_id": 287103, "number_of_packages": 3, "shipping_weight": -5.0}])
    assert e.value.code == "dato_invalido"
    out = service.update(odoo, get_settings(), r["sesion"], 204067, [{"package_id": 287103, "goods_id": 3, "number_of_packages": 3, "euro_pallet_qty": 2, "shipping_weight": 1500.0}])
    assert out["bultos_modificados"] == 1


# --- mercancía en blanco
def test_vaciar_la_mercancia_se_guarda_como_en_blanco_y_queda_en_la_nota():
    odoo = MockOdoo()
    r = _sesion(odoo)
    out = service.update(odoo, get_settings(), r["sesion"], 204067, [_cambio(goods_id=0)])
    assert out["ok"] and odoo.writes == [(287102, {"goods_id": False})]
    assert odoo.packages[287102]["goods_id"] is False
    assert "Mercancía: Cartón y papel → (en blanco)" in odoo.notes[0][1]
    again = service.resolve(odoo, get_settings(), order_name="S2621473", company="transargi")
    assert again["bultos"][0]["mercancia"] is None


def test_dejar_en_blanco_una_mercancia_que_ya_esta_en_blanco_no_es_un_cambio():
    odoo = MockOdoo()
    odoo.packages[287102]["goods_id"] = False
    r = _sesion(odoo)
    with pytest.raises(AppError) as e:
        service.update(odoo, get_settings(), r["sesion"], 204067, [_cambio(goods_id=0)])
    assert e.value.code == "sin_cambios"
    out = service.update(odoo, get_settings(), r["sesion"], 204067, [_cambio(goods_id=0, number_of_packages=3)])
    assert odoo.writes == [(287102, {"number_of_packages": 3})] and out["ok"]   # la mercancía en blanco no se vuelve a escribir


def test_poner_mercancia_a_un_bulto_en_blanco():
    odoo = MockOdoo()
    odoo.packages[287102]["goods_id"] = False
    r = _sesion(odoo)
    service.update(odoo, get_settings(), r["sesion"], 204067, [_cambio(goods_id=3)])
    assert odoo.writes == [(287102, {"goods_id": 3})] and "(en blanco) → Bebidas" in odoo.notes[0][1]


def test_el_cliente_real_puede_vaciar_la_mercancia(monkeypatch):
    c, srv = _real_client(monkeypatch)
    c.write_package(287102, {"goods_id": False})
    assert srv.calls[-1][:3] == ("tms.package", "write", [[287102], {"goods_id": False}])


def test_la_pagina_tiene_titulo_y_vista_previa_para_compartir():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "public"
    html = (root / "index.html").read_text(encoding="utf-8")
    assert "<title>Modificación DECA - Trimodos</title>" in html
    assert 'property="og:title" content="Modificación DECA - Trimodos"' in html
    assert 'property="og:image" content="https://' in html          # absoluta: WhatsApp/Teams no resuelven rutas relativas
    assert (root / "img" / "compartir.png").exists()


@pytest.mark.parametrize("major, esperado", [(15, False), (18, True)])
def test_nota_interna_declara_html_solo_en_odoo_18(monkeypatch, major, esperado):
    c, srv = _real_client(monkeypatch)
    srv.major = major
    c.post_note(204067, "<p>hola</p>")
    kw = srv.calls[-1][3]
    assert ("body_is_html" in kw) is esperado and (kw.get("body_is_html") is True if esperado else True)
    assert kw["subtype_xmlid"] == "mail.mt_note"


def test_tarea_con_bultos_pero_sin_orden_generada_ni_marca_se_acepta():
    """Como el pedido S2621553 tras recrearse la tarea: en «Nuevo», con su bulto, sin PDF de orden todavía."""
    odoo = MockOdoo()
    odoo.tasks[300004]["tms_package_ids"] = [287103]
    odoo.orders[("S2600010", 7)] = [300004]
    r = service.resolve(odoo, get_settings(), order_name="S2600010", company="transargi")
    assert r["tarea"]["id"] == 300004 and r["editable"] is True and len(r["bultos"]) == 1
    out = service.update(odoo, get_settings(), r["sesion"], 300004, [{"package_id": 287103, "number_of_packages": 9}])
    assert out["ok"] and odoo.prints == [300004]        # se genera la primera orden de transporte


def test_impresion_prohibida_deja_rastro_del_motivo_en_el_log(monkeypatch, caplog):
    """Si Odoo responde 403 al imprimir (usuario sin permiso), la app falla con mensaje claro y deja el motivo en el log."""
    import logging
    c, srv = _real_client(monkeypatch)
    original = srv.handler

    def con_403(request):
        if request.url.path.startswith("/report/pdf/"):
            return httpx.Response(403, content=b"<html>Forbidden</html>", headers={"content-type": "text/html"})
        return original(request)
    c._http = httpx.Client(transport=httpx.MockTransport(con_403))
    with caplog.at_level(logging.WARNING, logger="conductores"):
        with pytest.raises(AppError) as e:
            c.print_order(204067, 7)
    assert e.value.code == "pdf_fallo"
    assert "HTTP 403" in caplog.text and "no tiene permiso" in caplog.text


def test_sesion_web_que_no_abre_deja_rastro(monkeypatch, caplog):
    import logging
    c, srv = _real_client(monkeypatch)
    c._http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"result": {"uid": None}})))
    with caplog.at_level(logging.WARNING, logger="conductores"):
        with pytest.raises(AppError):
            c.print_order(204067, 7)
    assert "sesión web no se abrió" in caplog.text


# --- órdenes con PDF pero sin bultos (p. ej. contenedores): se leen, pero no se pueden modificar
def test_orden_sin_bultos_se_lee_pero_no_es_editable_y_dice_por_que():
    odoo = MockOdoo()
    r = service.resolve(odoo, get_settings(), order_name="S26099887", company="stinsa")
    assert r["bultos"] == [] and r["editable"] is False
    assert "responsable de tráfico" in r["motivo_no_editable"] and "bultos" in r["motivo_no_editable"]
    qr_ = qr(900006, "66666666-aaaa-bbbb-cccc-000000000006")
    r2 = service.resolve(odoo, get_settings(), qr_text=qr_)           # también por QR
    assert r2["tarea"]["id"] == 300005 and r2["editable"] is False and r2["verificado_por_qr"] is True


def test_no_se_puede_guardar_en_una_orden_sin_bultos():
    odoo = MockOdoo()
    r = service.resolve(odoo, get_settings(), order_name="S26099887", company="stinsa")
    with pytest.raises(AppError) as e:
        service.update(odoo, get_settings(), r["sesion"], 300005, [{"package_id": 287102, "number_of_packages": 3}])
    assert e.value.code == "sin_bultos" and e.value.status == 409
    assert odoo.writes == [] and odoo.prints == [] and odoo.notes == []
