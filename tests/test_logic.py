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
    assert verify_session("k", t, now=1001) == {"task_id": 5, "write_dates": {1: "2026-01-01 10:00:00"}}
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
