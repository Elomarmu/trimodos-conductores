"""Bobinas de un convoy de SAICA: limpieza de la lectura, totales por clase, Excel y API (convoy 24130 del 11/11/2026)."""
import base64
import io

import openpyxl
import pytest

from app.pr import pr_bobinas as B

CONVOY_24130 = [
    ("7366034", "CRPU0910069", 11, ["8 DS-150 140 203", "3 DS-150 140 218"]),
    ("7366175", "MXMU1450", 9, ["9 HS-160 140 218"]),
    ("7366197", "ICR111106", 10, ["3 DS-150 140 203", "4 DS-150 140 218", "3 HS-160 140 250"]),
    ("7366200", "ICR111150", 10, ["1 HPS-190 140 187", "2 NK-215 140 187", "1 DS-120 140 218", "2 DS-150 140 218",
                                  "2 NK-215 140 218", "1 DS-120 140 233", "1 NK-215 140 250"]),
    ("7366201", "CTEA25004", 9, ["2 HS-160 140 187", "1 HS-160 140 250", "6 NK-165 140 250"]),
    ("7366205", "CTEA25003", 10, ["3 HS-160 140 187", "6 DS-150 140 233", "1 NK-165 140 250"]),
    ("7366209", "ICR111183", 10, ["10 HS-105 140 203"]),
    ("7366210", "ICR111113", 10, ["1 HS-105 140 187", "9 HS-127 140 203"]),
    ("7366211", "ICR102004", 9, ["1 IN-165 125 187", "8 HS-127 140 233"]),
    ("7366212", "ICR102028", 9, ["3 HS-127 125 250", "6 HS-127 140 250"]),
    ("7366213", "CTEA25001", 9, ["9 SKB-170 140 203"]),
    ("7366215", "CTEA25003", 10, ["1 SKB-125 140 203", "7 DS-100 125 218", "2 SKB-125 140 233"]),
    ("7366216", "MXMU1450", 10, ["6 HS-127 140 187", "4 DS-120 140 187"]),
    ("7366217", "MXMU1450", 9, ["4 DS-120 140 218", "5 HS-105 140 233"]),
    ("7366219", "ICR111163", 8, ["1 SKB-170 140 203", "1 IN-165 125 233", "3 DS-120 140 250", "3 SKB-170 125 250"]),
    ("7366224", "MXMU1450", 10, ["3 HS-105 140 187", "2 BLS-120 140 187", "1 HS-127 140 203", "2 HS-160 140 203",
                                 "2 BLS-120 140 203"]),
    ("7366292", "ICR111114", 9, ["1 IN-165 125 187", "1 HS-105 140 203", "1 HS-127 140 233", "1 IN-135 125 233",
                                 "1 DS-120 140 250", "2 SKB-125 125 250", "1 IN-135 125 250", "1 IN-165 125 250"]),
    ("7366387", "ICR102030", 10, ["3 DS-120 125 180", "7 HS-145 140 247"]),
    ("7366404", "ICR102043", 11, ["3 DS-120 140 180", "3 DS-150 140 205", "3 DS-150 140 212", "2 DS-150 140 220"]),
    ("7366405", "CTEA25001", 10, ["2 DS-120 140 194", "3 DS-120 140 205", "1 HS-145 140 212", "2 DS-120 140 234",
                                  "2 HS-145 140 247"]),
    ("7367378", "ICR102056", 10, ["2 HS-145 140 180", "8 HS-145 140 220"]),
]
LEIDO = {"convoy": "24130", "centro": "SAICA2", "destino": "SEVILLA", "fecha_llegada": "2026-11-11", "vagones": 21, "dudas": [],
         "cajas": [{"expedicion": e, "caja_movil": c, "bobinas": n, "peso_t": 23.5, "lineas": l} for e, c, n, l in CONVOY_24130]}


def test_convoy_24130_cuadra():
    d = B.limpiar(LEIDO)
    assert d["avisos"] == [] and len(d["cajas"]) == 21
    clases = B.agrupar(d["cajas"])
    assert sum(c["bobinas"] for c in clases) == 203 and len(clases) == 49
    assert dict(B.por_tipo(clases)) == {"HS": 94, "DS": 68, "SKB": 18, "NK": 12, "IN": 6, "BLS": 4, "HPS": 1}
    assert clases[0]["tipo"] == "HS"   # el tipo con más bobinas primero
    assert {"tipo": "DS", "gramaje": 150, "diametro": 140, "altura": 203, "bobinas": 11} in clases


def test_avisos_lineas_raras_repetidas_y_faltan_cajas():
    leido = {"convoy": "1", "vagones": 3, "dudas": ["Caja 2 borrosa"], "cajas": [
        {"expedicion": "7366034", "bobinas": 11, "lineas": ["TRA 8 DS-150 140 203", "3 DS – 150 140 218"]},
        {"expedicion": "7366034", "bobinas": 11, "lineas": ["8 DS-150 140 203"]},     # misma caja en dos capturas
        {"expedicion": "7366175", "bobinas": 9, "lineas": ["8 HS-160 140 218", "HS 160"]}]}
    d = B.limpiar(leido)
    assert len(d["cajas"]) == 2 and d["cajas"][0]["lineas"][1]["gramaje"] == 150
    texto = " ".join(d["avisos"])
    assert "borrosa" in texto and "«HS 160»" in texto and "suman 8 bobinas y la cabecera dice 9" in texto
    assert "3 vagones y se han leído 2 cajas" in texto


def test_excel():
    d = B.limpiar(LEIDO)
    wb = openpyxl.load_workbook(io.BytesIO(B.excel(d)))
    assert wb.sheetnames == ["RESUMEN", "DETALLE CAJAS"]
    ws = wb["RESUMEN"]
    assert [c.value for c in ws[4][:5]] == ["Tipo", "Gramaje", "Diámetro", "Altura", "Bobinas"]
    assert ws.cell(5 + 49, 1).value == "TOTAL" and ws.cell(5 + 49, 5).value == 203
    assert ws["G5"].value == "HS" and ws["H5"].value == 94
    assert wb["DETALLE CAJAS"].max_row == 3 + sum(len(x[3]) for x in CONVOY_24130)
    assert B.nombre_excel(d) == "BOBINAS CONVOY 24130.xlsx"


@pytest.fixture
def cliente(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setenv("ODOO_MODE", "mock")
    monkeypatch.setenv("APP_ACCESS_CODE", "")
    return TestClient(app)


def test_api_leer_y_enviar(cliente, monkeypatch):
    from app.pr import pr_api
    monkeypatch.setattr(B, "leer", lambda archivos: LEIDO)
    enviados = []
    monkeypatch.setattr(pr_api, "enviar_n8n", lambda datos: enviados.append(datos) or True)
    monkeypatch.setenv("PR_CORREO_PARA", "sevilla@stinsa.com")
    monkeypatch.setenv("DRY_RUN", "false")
    r = cliente.post("/api/pr/bobinas/leer", json={"archivos": [{"archivo_b64": base64.b64encode(b"img").decode(), "tipo": "image/jpeg"}]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["total"] == 203 and len(d["clases"]) == 49
    convoy = {k: v for k, v in d.items() if k not in ("clases", "por_tipo", "total")}
    r = cliente.post("/api/pr/bobinas/enviar", json=convoy)
    assert r.status_code == 200, r.text
    assert r.json()["enviado"] and r.json()["para"] == "sevilla@stinsa.com"
    e = enviados[0]
    assert e["evento"] == "bobinas" and "203 bobinas" in e["asunto"] and e["archivos"][0]["nombre"] == "BOBINAS CONVOY 24130.xlsx"


def test_lector_pide_mas_tokens(monkeypatch):
    import httpx, json
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    vistos = []

    def manejar(req):
        vistos.append(json.loads(req.content))
        return httpx.Response(200, json={"content": [{"type": "tool_use", "name": "detalle_convoy", "input": LEIDO}]})
    assert B.leer([(b"img", "image/png")], httpx.Client(transport=httpx.MockTransport(manejar))) == LEIDO
    assert vistos[0]["max_tokens"] == 8000 and vistos[0]["tools"][0]["name"] == "detalle_convoy"
