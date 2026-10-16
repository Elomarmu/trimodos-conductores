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
    r = service.resolve(MockOdoo(), get_settings(), order_name="s2621473")
    assert r["tarea"]["nombre"] == "S2621473" and r["verificado_por_qr"] is False


def test_resolve_pedido_inexistente_y_vacio():
    with pytest.raises(AppError):
        service.resolve(MockOdoo(), get_settings(), order_name="S9999999")
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
    r = c.post("/api/resolve-qr", json={"order_name": "S2621473"})
    assert r.status_code == 200 and r.json()["tarea"]["id"] == 204067
    r = c.post("/api/resolve-qr", json={"qr_text": "basura"})
    assert r.status_code == 400 and "error" in r.json()
    assert len(c.get("/api/goods", params={"q": "a"}).json()["resultados"]) <= 10
