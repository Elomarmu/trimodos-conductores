"""Viajes de PR: reglas, limpieza de lo leído y lector (con la API de Claude simulada; no se conecta a nada)."""
import datetime as dt
import json

import httpx
import pytest

from app.pr import pr_lector as L
from app.pr import pr_reglas as R

HOY = dt.date(2026, 11, 9)

# Lo que debería devolver la lectura de los 4 albaranes de prueba (09/11/2026).
JEREZ = {"tipo_albaran": "saica_natur", "emisor_albaran": "SAICA NATUR", "numero_albaran": "592611000006",
         "fecha": "2026-11-09", "hora": "13:35", "punto_carga_nombre": "SAICA NATUR, S.L.",
         "punto_carga_direccion": "CL SUDAFRICA, NAVE 18 - P.I.EL PORTAL 11408 JEREZ DE LA FRONTERA CADIZ",
         "transportista": "STINSA B12041950", "matricula_tractora": "R8896BDF 8734KDW", "matricula_remolque": None,
         "producto": "110104 CARTON PAJA 100% 1.05.00", "ler": "191201", "peso_neto_kg": 24800, "bultos": 21,
         "peso_bruto_kg": 39880, "tara_kg": 15080, "codigo_carga_paper": "S261104001499",
         "codigo_carga_natur": "5926000419", "contenedor": None, "dudas": []}
ALCALA = {"tipo_albaran": "saica_natur", "numero_albaran": "502611000025", "fecha": "2026-11-06", "hora": "08:15",
          "punto_carga_nombre": "SAICA NATUR, S.L.", "punto_carga_direccion": "CL LA RED 11, 6 - POL.IND. LA RED 41500 ALCALA DE GUADAIRA",
          "matricula_tractora": "3658JCG", "matricula_remolque": "R5158BDR", "producto": "110104 CARTON PAJA 100% 1.05.00",
          "ler": "191201", "peso_neto_kg": "24.820", "bultos": 54, "peso_bruto_kg": 39420, "tara_kg": 14600,
          "codigo_carga_paper": "S261104000884", "codigo_carga_natur": "5026004214", "dudas": []}
MERCADONA = {"tipo_albaran": "saica_natur", "emisor_albaran": "SAICA NATUR", "numero_albaran": "502611T00064",
             "fecha": "2026-11-09", "hora": None, "punto_carga_nombre": "MERCADONA, S.A.",
             "punto_carga_direccion": "CR AUTOVIA SEVILLA-HUELVA, A49, KM.21 41830 HUEVAR SEVILLA",
             "matricula_tractora": "7033JNX", "matricula_remolque": "R3860BCJ", "producto": "110104 CARTON PAJA 100% 1.05.00",
             "ler": "150101", "peso_neto_kg": None, "bultos": None, "codigo_carga_paper": "S261106002151",
             "codigo_carga_natur": "5026004272", "dudas": []}
MARTINEZ_CANO = {"tipo_albaran": "martinez_cano", "emisor_albaran": "MARTINEZ CANO CANARIAS, S.A.", "numero_albaran": "26371",
                 "fecha": "2026-11-09", "punto_carga_nombre": "LEOPROEX", "matricula_tractora": "1222HTP",
                 "matricula_remolque": "R-3403BBP", "producto": "1.05.01 CARTON PAJA 100%", "peso_neto_kg": 24320,
                 "bultos": 34, "codigo_carga_paper": "S 261104001502", "contenedor": "NPWU 495755-4", "dudas": []}


def test_jerez():
    v = R.limpiar(JEREZ, HOY)
    assert (v.punto.clave, v.tractora, v.remolque) == ("JEREZ", "8734KDW", "R8896BDF")
    assert (v.peso_kg, v.bultos, v.codigo_paper, v.tipo, v.ler) == (24800, 21, "S261104001499", "1.05", "191201")
    assert v.avisos == [] and v.pendientes == []
    assert v.a_dict()["toneladas"] == 24.8


def test_alcala_peso_con_punto_de_miles():
    v = R.limpiar(ALCALA, HOY)
    assert v.punto.clave == "SEVILLA" and v.punto.producto_id == 712 and v.peso_kg == 24820
    assert v.avisos == []


def test_mercadona_sin_peso_ni_balas():
    v = R.limpiar(MERCADONA, HOY)
    assert v.punto.clave == "MERCADONA"           # el albarán es de Saica Natur, pero se carga en Mercadona
    assert v.pendientes == ["peso", "balas"]
    assert v.a_dict()["toneladas"] == 21.0         # se factura cantidad fija
    assert v.ler == "150101"


def test_martinez_cano_leoproex_con_contenedor():
    v = R.limpiar(MARTINEZ_CANO, HOY)
    assert v.punto.clave == "LEOPROEX" and v.punto.origen_id == 15296
    assert (v.contenedor, v.remolque, v.codigo_paper, v.tipo) == ("NPWU4957554", "R3403BBP", "S261104001502", "1.05")
    # Caso real: lo escrito a mano («NPWU 495755-4») no cumple el dígito de control (debería ser 9): se avisa.
    assert v.avisos == ["Revisa el número de contenedor (no cuadra el dígito de control)."]
    assert R.limpiar(dict(MARTINEZ_CANO, contenedor="NPWU4957559"), HOY).avisos == []


def test_martinez_cano_sin_codigo_paper_queda_pendiente():
    v = R.limpiar(dict(MARTINEZ_CANO, codigo_carga_paper=None), HOY)
    assert v.pendientes == ["código carga Paper"]


def test_avisos():
    v = R.limpiar(dict(JEREZ, peso_neto_kg=42800, contenedor="NPWU4957555", fecha="2026-09-01",
                       punto_carga_nombre="RECICLADOS PEPE", punto_carga_direccion="UTRERA"), HOY)
    texto = " ".join(v.avisos)
    for frase in ("fecha", "Punto de carga nuevo", "no cuadra con bruto", "Revisa el peso", "dígito de control"):
        assert frase in texto


def test_iso6346():
    assert R.contenedor("CSQU 305438-3") == ("CSQU3054383", True)   # ejemplo de la norma
    assert R.contenedor("NPWU4954328")[1] is True                    # contenedor real de LEOPROEX en Odoo
    assert R.contenedor("NPWU 495755-4") == ("NPWU4957554", False)
    assert R.contenedor("NPWU49") == ("NPWU49", False)


def test_kilos_y_fechas():
    assert [R.kilos(x) for x in ("24.800", "24800", 24800, 24.8, "", None, "abc")] == [24800, 24800, 24800, 24800, None, None, None]
    assert R.fecha_hora_utc("2026-11-09", "13:35") == "2026-11-09 12:35:00"   # invierno: UTC+1
    assert R.fecha_hora_utc("2026-07-01", None) == "2026-07-01 08:00:00"      # verano: UTC+2, sin hora → 10:00
    assert R.referencia_pedido("2026-11-09") == "PR NOVIEMBRE"


def test_observaciones_se_actualizan_sin_perder_lo_escrito():
    obs = R.observaciones({"albaran": "26371", "codigo_paper": "", "ler": "191201"}, "Llamar antes de descargar")
    assert obs.splitlines()[0] == "[PR SAICA]" and "Albarán salida: 26371" in obs and "Llamar antes" in obs
    obs2 = R.observaciones({"codigo_paper": "S261104001502", "caja_movil": "ICR102043"}, obs)
    assert R.leer_observaciones(obs2) == {"albaran": "26371", "codigo_paper": "S261104001502", "ler": "191201",
                                         "caja_movil": "ICR102043"}
    assert obs2.count("Llamar antes") == 1


def test_tren():
    leido = {"numero_tren": "90107", "fecha": "2026-11-09", "dudas": [], "vagones": [
        {"orden": 1, "vagon": "388049630055-0", "num_utis": 2, "cajas": ["ICR111163", "ICR 111114"]},
        {"orden": 2, "vagon": "287144330944-4", "num_utis": 2, "cajas": ["CTEA25000166"]},
        {"orden": 3, "vagon": "437143786584", "cajas": ["ZZZZ1234567"]}]}
    t = R.limpiar_tren(leido)
    assert [c["caja_movil"] for c in t["cajas"]] == ["ICR111163", "ICR111114", "CTEA25000166", "ZZZZ1234567"]
    assert t["cajas"][1] == {"orden": 1, "vagon": "388049630055", "segunda": True, "caja_movil": "ICR111114", "tara_kg": 5100}
    assert any("Vagón 2" in a for a in t["avisos"]) and any("ZZZZ1234567" in a for a in t["avisos"])


def _cliente(respuesta: dict, status=200, guardar=None):
    def manejar(req: httpx.Request):
        if guardar is not None:
            guardar.append(json.loads(req.content))
        return httpx.Response(status, json=respuesta)
    return httpx.Client(transport=httpx.MockTransport(manejar))


def test_lector_fuerza_la_herramienta(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    enviados = []
    resp = {"content": [{"type": "tool_use", "name": "datos_albaran", "input": JEREZ}]}
    assert L.leer_albaran(b"\xff\xd8foto", "image/jpeg", _cliente(resp, guardar=enviados)) == JEREZ
    cuerpo = enviados[0]
    assert cuerpo["tool_choice"] == {"type": "auto"} and cuerpo["tools"][0]["name"] == "datos_albaran"
    assert cuerpo["messages"][0]["content"][0]["type"] == "image"


def test_lector_respuesta_en_texto(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    resp = {"content": [{"type": "text", "text": "Aquí están los datos:\n```json\n" + json.dumps(JEREZ) + "\n```"}]}
    assert L.leer_albaran(b"x", "image/jpeg", _cliente(resp)) == JEREZ


def test_lector_errores(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(L.LecturaError, match="ANTHROPIC_API_KEY"):
        L.leer_albaran(b"x", "image/jpeg")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    with pytest.raises(L.LecturaError):
        L.leer_albaran(b"x", "image/jpeg", _cliente({"error": "x"}, 500))
    with pytest.raises(L.LecturaError, match="Formato"):
        L.leer_albaran(b"x", "text/plain", _cliente({}))


def test_api_leer_albaran(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setenv("ODOO_MODE", "mock")
    monkeypatch.setenv("APP_ACCESS_CODE", "")
    monkeypatch.setattr(L, "leer_albaran", lambda datos, tipo: MERCADONA)
    import base64
    r = TestClient(app).post("/api/pr/leer-albaran", json={"archivo_b64": base64.b64encode(b"foto").decode(), "tipo": "image/jpeg"})
    assert r.status_code == 200
    v = r.json()["viaje"]
    assert v["punto"]["linea"] == "PR MERCADONA HUEVAR" and v["pendientes"] == ["peso", "balas"]
