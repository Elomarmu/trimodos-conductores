"""Control de stock del almacén de Burriana: reglas, almacenamiento, Excel, facturación y la API de punta a punta
(con la lectura de Claude y n8n simuladas; no se conecta a nada)."""
import base64
import datetime as dt
import io

import openpyxl
import pytest

from app.stock import stock_api as A
from app.stock import stock_excel as X
from app.stock import stock_facturacion as F
from app.stock import stock_reglas as R
from app.stock import stock_store as S

HOY = dt.date(2026, 11, 17)
KERAKOLL = {"codigo": "KERAKOLL", "nombre": "Kerakoll Ibérica, S.A.U.", "alias": ["KERAKOLL", "ALMAZORA"]}
ALIPLAST = {"codigo": "ALIPLAST", "nombre": "Aliplast, S.A.", "alias": ["ALIPLAST"]}
CLIENTES = [KERAKOLL, ALIPLAST]

LEIDO_ALBARAN = {"numero_albaran": "AL-5521", "fecha": "2026-11-16", "remitente": "KERAKOLL IBERICA SAU - Almazora", "destinatario": "STINSA Almacén Burriana",
                 "transportista": "Transportes Gil", "matricula": "1234 ABC", "total_palets": 5, "total_kg": 6000,
                 "lineas": [{"referencia": "KK-100", "descripcion": "Mortero 25 kg", "palets": 3, "kg": 3600, "lote": "L77"},
                            {"referencia": "KK-200", "descripcion": "Adhesivo 20 kg", "palets": 2, "kg": 2400}], "dudas": []}


def etiqueta(ref, sscc, lote="", kg=1200, cliente="Kerakoll"):
    return {"sscc": sscc, "referencia": ref, "descripcion": "", "lote": lote, "kg": kg, "bultos": 48, "cliente": cliente, "dudas": []}


SSCC = [f"3400000000000000{n:02d}" for n in range(1, 30)]


# ---- reglas ---------------------------------------------------------------------------------------------------------

def test_normalizacion():
    assert R.norm("Kerakoll, S.p.A.") == "KERAKOLL S P A"
    assert R.referencia(" kk-100 ") == "KK100"
    assert R.entero("24.800") == 24800 and R.entero("1200,0") == 1200 and R.entero("") is None
    assert R.sscc("(00) 3400 0000 0000 0000 01") == "340000000000000001"
    # códigos con letras (Aliplast): escaneado, escrito a mano o de la lista dan lo mismo; el QR (una web) no es un código de palet
    assert R.sscc("LP26-0236823") == R.sscc(" lp26 0236823 ") == "LP260236823"
    assert R.sscc("https://www.aliplastspa.com/etichettatura-ambientale") == "" and R.sscc("2600051470") == "2600051470"
    assert R.fecha("16/11/26") == "2026-11-16" and R.fecha("32/13/2026") == ""
    assert R.siguiente_id("E", ["E-2026-0001", "E-2026-0007", "E-2025-0099"], 2026) == "E-2026-0008"
    assert R.siguiente_id("S", [], 2026) == "S-2026-0001"


def test_sugerir_cliente():
    res = R.sugerir_clientes(CLIENTES, "KERAKOLL IBERICA SAU - Almazora", "STINSA")
    assert res[0][0]["codigo"] == "KERAKOLL" and len(res) == 1
    assert R.sugerir_clientes(CLIENTES, "Empresa desconocida") == []


def albaran():
    return R.limpiar_albaran(LEIDO_ALBARAN, HOY)


def test_conflictos_entrada():
    a = albaran()
    assert [x for x in R.conflictos_entrada(a, KERAKOLL, CLIENTES, [], HOY) if x["nivel"] == "error"] == []
    sin_cliente = R.conflictos_entrada(a, None, [ALIPLAST], [], HOY)
    assert any("No reconozco al cliente" in x["texto"] for x in sin_cliente)
    mal = dict(a, total_palets=6)
    assert any("suman 5" in x["texto"] for x in R.conflictos_entrada(mal, KERAKOLL, CLIENTES, [], HOY))
    otro = R.conflictos_entrada(a, ALIPLAST, CLIENTES, [], HOY)
    assert any("parece de Kerakoll" in x["texto"] for x in otro)
    previa = {"id": "E-2026-0001", "estado": "pendiente", "cliente": KERAKOLL, "albaran": a, "lineas": a["lineas"], "palets": []}
    dup = R.conflictos_entrada(a, KERAKOLL, CLIENTES, [previa], HOY)
    assert any(x["nivel"] == "error" and "ya está cargado como entrada E-2026-0001" in x["texto"] for x in dup)
    futuro = dict(a, fecha="2026-12-01")
    assert any("futura" in x["texto"] for x in R.conflictos_entrada(futuro, KERAKOLL, CLIENTES, [], HOY))
    otro_dest = dict(a, destinatario="Almacén de Valencia")
    assert any("Burriana" in x["texto"] for x in R.conflictos_entrada(otro_dest, KERAKOLL, CLIENTES, [], HOY))


def entrada_base():
    a = albaran()
    return {"id": "E-2026-0001", "estado": "pendiente", "creada": "2026-11-17T09:00", "cliente": KERAKOLL, "albaran": a, "lineas": a["lineas"], "palets": []}


def poner(e, et, en_stock=frozenset()):
    p = R.verificar_palet(e, R.limpiar_etiqueta(et), set(en_stock))
    p["id"] = f"{e['id']}-{len(e['palets']) + 1:02d}"
    e["palets"].append(p)
    return p


def test_verificar_palets_y_discrepancias():
    e = entrada_base()
    for i in range(3):
        assert poner(e, etiqueta("KK-100", SSCC[i], "L77"))["estado"] == "ok"
    assert poner(e, etiqueta("KK-200", SSCC[3]))["estado"] == "ok"
    assert R.discrepancias_entrada(e) == ["Faltan 1 palets de KK-200 (albarán 2, verificados 1)."]
    sobra = poner(e, etiqueta("KK-100", SSCC[4], "L77"))
    assert sobra["estado"] == "discrepancia" and "Sobra" in sobra["motivo"]
    ajeno = poner(e, etiqueta("ZZ-9", SSCC[5]))
    assert "no figura en el albarán" in ajeno["motivo"]
    repetido = poner(e, etiqueta("KK-200", SSCC[3]))
    assert "repetido" in repetido["motivo"]
    lote = R.verificar_palet(entrada_base(), R.limpiar_etiqueta(etiqueta("KK-100", SSCC[9], "L99")), set())
    assert "Lote distinto" in lote["motivo"]
    de_otro = R.verificar_palet(entrada_base(), R.limpiar_etiqueta(etiqueta("KK-100", SSCC[10], "L77", cliente="Aliplast")), set())
    assert "no de Kerakoll" in de_otro["motivo"]
    ya_en_stock = R.verificar_palet(entrada_base(), R.limpiar_etiqueta(etiqueta("KK-100", SSCC[11], "L77")), {SSCC[11]})
    assert "repetido" in ya_en_stock["motivo"]


def entrada_cerrada(ident="E-2026-0001", dia="2026-11-17", ref="KK-100", n=3, desde=0, cliente=KERAKOLL):
    e = entrada_base()
    e.update(id=ident, cliente=cliente, estado="cerrada", cerrada=f"{dia}T10:00")
    e["lineas"] = [{"ref": ref, "descripcion": "Mortero", "palets": n, "kg": 1200 * n, "lote": ""}]
    for i in range(n):
        poner(e, etiqueta(ref, SSCC[desde + i]))
    return e


def salida_base(lineas, cliente=KERAKOLL, ident="S-2026-0001"):
    return {"id": ident, "estado": "pedida", "creada": "2026-11-18T08:00", "cliente": cliente, "referencia": "PED-9", "destino": "Valencia",
            "lineas": lineas, "palets": [], "avisos": []}


def test_stock_y_salidas():
    e1, e2 = entrada_cerrada("E-2026-0001", "2026-11-10", n=2), entrada_cerrada("E-2026-0002", "2026-11-15", n=2, desde=5)
    existencias = R.stock([e1, e2], [])
    assert [p["id"] for p in existencias] == ["E-2026-0001-01", "E-2026-0001-02", "E-2026-0002-01", "E-2026-0002-02"]
    s = salida_base([{"ref": "KK-100", "descripcion": "", "palets": 2}])
    assert R.conflictos_salida(s, existencias) == []
    pide_mas = salida_base([{"ref": "KK-100", "descripcion": "", "palets": 9}, {"ref": "XX", "descripcion": "", "palets": 1}])
    texto = " ".join(a["texto"] for a in R.conflictos_salida(pide_mas, existencias))
    assert "solo hay 4" in texto and "no tiene en stock XX" in texto
    # FIFO: se carga primero un palet de la entrada nueva → aviso de que hay uno más antiguo
    p = R.cargar_palet(s, R.limpiar_etiqueta(etiqueta("KK-100", SSCC[5])), existencias, [])
    assert p["estado"] == "ok" and p["palet_id"] == "E-2026-0002-01" and p["aviso"] == ""
    s["palets"].append(p)
    assert R.cargar_palet(s, R.limpiar_etiqueta(etiqueta("KK-100", SSCC[5])), existencias, [])["estado"] == "discrepancia"   # ya cargado
    s["palets"].append(R.cargar_palet(s, R.limpiar_etiqueta(etiqueta("KK-100", SSCC[0])), existencias, []))
    sobra = R.cargar_palet(s, R.limpiar_etiqueta(etiqueta("KK-100", SSCC[1])), existencias, [])
    assert sobra["estado"] == "discrepancia" and "Sobra" in sobra["motivo"]
    assert R.cargar_palet(s, R.limpiar_etiqueta(etiqueta("KK-100", SSCC[20])), existencias, [])["motivo"].startswith("Este palet no está en el stock")
    assert R.discrepancias_salida(s) == []
    s["estado"] = "cargada"
    assert [p["id"] for p in R.stock([e1, e2], [s])] == ["E-2026-0001-02", "E-2026-0002-02"]
    # otra salida abierta reserva los palets
    otra = salida_base([{"ref": "KK-100", "descripcion": "", "palets": 1}], ident="S-2026-0002")
    otra["palets"].append(R.cargar_palet(otra, R.limpiar_etiqueta(etiqueta("KK-100", SSCC[1])), R.stock([e1, e2], [s]), [s]))
    assert otra["palets"][0]["estado"] == "ok"
    tercera = salida_base([{"ref": "KK-100", "descripcion": "", "palets": 1}], ident="S-2026-0003")
    assert "reservado" in R.cargar_palet(tercera, R.limpiar_etiqueta(etiqueta("KK-100", SSCC[1])), R.stock([e1, e2], [s]), [s, otra])["motivo"]


def test_palet_de_otro_cliente_en_salida():
    e = entrada_cerrada(n=1)
    s = salida_base([{"ref": "KK-100", "descripcion": "", "palets": 1}], cliente=ALIPLAST)
    p = R.cargar_palet(s, R.limpiar_etiqueta(etiqueta("KK-100", SSCC[0])), R.stock([e], []), [])
    assert p["estado"] == "discrepancia" and "es de Kerakoll" in p["motivo"]


def test_facturacion():
    e = entrada_cerrada("E-2026-0001", "2026-11-10", n=2)
    s = salida_base([{"ref": "KK-100", "descripcion": "", "palets": 1}])
    s["palets"].append(R.cargar_palet(s, R.limpiar_etiqueta(etiqueta("KK-100", SSCC[0])), R.stock([e], []), []))
    s.update(estado="cargada", cargada="2026-11-15T12:00")
    mov = F.movimientos([e], [s])
    assert [(m["fecha"], m["tipo"]) for m in mov] == [("2026-11-10", "ENTRADA"), ("2026-11-10", "ENTRADA"), ("2026-11-15", "SALIDA")]
    # palet 1: del 10 al 14 = 5 días; palet 2: del 10 al 30 = 21 días
    assert F.estocaje([e], [s], "2026-11-01", "2026-11-30") == {"KERAKOLL": 26}
    lineas = {l["clave"]: l for l in F.lineas_facturables([e], [s], "2026-11-01", "2026-11-30", {"KERAKOLL": {"entrada_palet": 2.5, "estocaje_palet_dia": 0.5}})}
    assert lineas["entrada_palet"]["cantidad"] == 2 and lineas["entrada_palet"]["importe"] == 5.0
    assert lineas["salida_palet"]["importe"] is None and lineas["estocaje_palet_dia"]["importe"] == 13.0


# ---- almacenamiento ---------------------------------------------------------------------------------------------------

class OdooFalso:
    """Adjuntos de Odoo mínimos: search_read con «=» y «=like», create y write."""

    def __init__(self):
        self.adjuntos, self.sig = [], 100

    def __call__(self, modelo, metodo, args, **kw):
        assert modelo == "ir.attachment"
        if metodo == "search_read":
            filtros = args[0]
            res = self.adjuntos
            for campo, op, valor in filtros:
                if campo == "name":
                    res = [a for a in res if (a["name"] == valor if op == "=" else a["name"].startswith(valor.split("%")[0]) and a["name"].endswith(valor.split("%")[-1]))]
                if campo == "res_model":
                    res = [a for a in res if a["res_model"] == valor]
            return [dict(a) for a in sorted(res, key=lambda a: a["name"])][: kw.get("limit", 999)]
        if metodo == "create":
            self.sig += 1
            self.adjuntos.append(dict(args[0], id=self.sig))
            return self.sig
        if metodo == "write":
            next(a for a in self.adjuntos if a["id"] == args[0][0]).update(args[1])
            return True
        raise AssertionError(metodo)


def test_store_odoo_adjuntos():
    from app.pr import pr_odoo as O
    assert ("ir.attachment", "search_read") in O.PERMITIDO and ("ir.attachment", "create") in O.PERMITIDO and ("ir.attachment", "write") in O.PERMITIDO
    s = S.OdooAdjuntosStore(OdooFalso())
    s.escribir("entrada", "E-2026-0001", {"id": "E-2026-0001", "x": 1})
    s.escribir("entrada", "E-2026-0001", {"id": "E-2026-0001", "x": 2})   # sustituye, no duplica
    s.escribir("entrada", "E-2026-0002", {"id": "E-2026-0002"})
    s.escribir("salida", "S-2026-0001", {"id": "S-2026-0001"})
    assert s.leer("entrada", "E-2026-0001")["x"] == 2 and s.leer("entrada", "E-9") is None
    assert [d["id"] for d in s.listar("entrada")] == ["E-2026-0001", "E-2026-0002"] and len(s.listar("salida")) == 1
    s.guardar_foto("E-2026-0001", "01 KK-100.jpg", b"abc", "image/jpeg")
    s.guardar_foto("E-2026-0001", "01 KK-100.jpg", b"abcd", "image/jpeg")
    s.guardar_foto("E-2026-0002", "01.jpg", b"z", "image/jpeg")
    fotos = s.leer_fotos("E-2026-0001")
    assert [(f["nombre"], base64.b64decode(f["b64"])) for f in fotos] == [("01 KK-100.jpg", b"abcd")]


# ---- API de punta a punta ---------------------------------------------------------------------------------------------

@pytest.fixture
def cliente(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setenv("ODOO_MODE", "mock")
    monkeypatch.setenv("APP_ACCESS_CODE", "")
    monkeypatch.setenv("APP_OFICINA_CODE", "")
    A.set_store(S.MemoriaStore())
    yield TestClient(app)
    A.set_store(None)


@pytest.fixture
def enviados(monkeypatch):
    lista = []
    monkeypatch.setattr(A, "enviar_n8n", lambda datos: lista.append(datos) or True)
    return lista


FOTO = {"archivo_b64": base64.b64encode(b"imagen").decode(), "tipo": "image/jpeg"}


def _cerrar(cliente, ident, cuerpo):
    """Cierra una entrada del albarán: antes hace falta la foto del albarán firmado (en inventario no)."""
    cliente.post(f"/api/stock/entradas/{ident}/albaran-firmado", json={"fotos": [FOTO]})
    return cliente.post(f"/api/stock/entradas/{ident}/cerrar", json=cuerpo)


def leer_con(monkeypatch, albaran=None, etiquetas=(), peticion=None):
    monkeypatch.setattr(A.L, "leer_albaran", lambda archivos, cli=None: albaran or LEIDO_ALBARAN)
    cola = list(etiquetas)
    monkeypatch.setattr(A.L, "leer_etiqueta", lambda datos, tipo, cli=None: cola.pop(0))
    monkeypatch.setattr(A.L, "leer_peticion", lambda archivos, cli=None: peticion)


def dar_de_alta(cliente):
    for c in CLIENTES:
        assert cliente.post("/api/stock/clientes", json={"codigo": c["codigo"], "nombre": c["nombre"], "alias": c["alias"]}).status_code == 200


def test_flujo_entrada_completo(cliente, monkeypatch, enviados):
    dar_de_alta(cliente)
    etiquetas = [etiqueta("KK-100", SSCC[0], "L77"), etiqueta("KK-100", SSCC[1], "L77"), etiqueta("KK-100", SSCC[2], "L77"), etiqueta("KK-200", SSCC[3])]
    leer_con(monkeypatch, etiquetas=etiquetas)
    # oficina: foto del albarán → cliente recomendado y sin conflictos
    r = cliente.post("/api/stock/entradas/leer", json={"archivos": [FOTO]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["cliente_sugerido"] == "KERAKOLL" and not [a for a in d["avisos"] if a["nivel"] == "error"]
    r = cliente.post("/api/stock/entradas", json={"cliente": "KERAKOLL", "albaran": d["albaran"], "fotos": [FOTO]})
    assert r.status_code == 200, r.text
    ident = r.json()["id"]
    assert ident.startswith("E-") and r.json()["estado"] == "pendiente"
    # el mismo albarán otra vez → error, ni confirmando se puede declarar dos veces
    for extra in ({}, {"confirmado": True}):
        r = cliente.post("/api/stock/entradas", json={"cliente": "KERAKOLL", "albaran": d["albaran"], **extra})
        assert r.status_code == 409 and r.json()["codigo"] == "albaran_repetido"
    assert len(cliente.get("/api/stock/entradas?abiertas=true").json()["entradas"]) == 1
    # almacén: 4 etiquetas
    for _ in range(4):
        r = cliente.post(f"/api/stock/entradas/{ident}/etiqueta", json={"foto": FOTO})
        assert r.status_code == 200, r.text
    det = cliente.get(f"/api/stock/entradas/{ident}").json()
    assert det["estado"] == "descargando" and det["verificados"] == 4 and det["esperados"] == 5
    # cerrar con un palet que falta → pide confirmar
    r = _cerrar(cliente, ident, {})
    assert r.status_code == 409 and r.json()["codigo"] == "discrepancias" and "Faltan 1 palets de KK-200" in r.json()["error"]
    assert enviados == []
    # palet a mano para el que falta → cuadra
    r = cliente.post(f"/api/stock/entradas/{ident}/palet", json={"ref": "KK-200", "kg": 1200, "sscc": SSCC[6]})
    assert r.status_code == 200 and r.json()["palet"]["manual"] is True
    r = _cerrar(cliente, ident, {})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["entrada"]["estado"] == "cerrada" and res["entrada"]["discrepancias"] == 0 and res["enviado"] is True
    # un solo envío a n8n: Excel de la entrada (con correo), libro de control (solo SharePoint) y las fotos
    assert len(enviados) == 1
    e = enviados[0]
    assert e["evento"] == "entrada_cerrada" and e["carpeta"] == f"ENTRADA {ident}" and "AL-5521" in e["asunto"]
    assert [(a["nombre"], a["correo"]) for a in e["archivos"]] == [(f"ENTRADA {ident}.xlsx", True), ("CONTROL ALMACEN BURRIANA.xlsx", False)]
    assert {f["nombre"] for f in e["fotos"]} >= {"albaran 1.jpg", "01 KK-100.jpg"} and all(f["carpeta"] == e["carpeta"] for f in e["fotos"])
    libro = openpyxl.load_workbook(io.BytesIO(base64.b64decode(e["archivos"][1]["b64"])))
    assert libro.sheetnames == ["RESUMEN STOCK", "STOCK", "ENTRADAS", "SALIDAS", "MOVIMIENTOS", "CLIENTES"]
    assert libro["STOCK"].max_row == 4 + 5 and libro["MOVIMIENTOS"].max_row == 4 + 5
    # y ya no se puede tocar
    assert cliente.post(f"/api/stock/entradas/{ident}/etiqueta", json={"foto": FOTO}).status_code == 409
    st = cliente.get("/api/stock/stock").json()
    assert st["total_palets"] == 5 and {(g["ref"], g["palets"]) for g in st["resumen"]} == {("KK-100", 3), ("KK-200", 2)}


def test_entrada_con_discrepancias_aceptadas(cliente, monkeypatch, enviados):
    dar_de_alta(cliente)
    leer_con(monkeypatch, etiquetas=[etiqueta("ZZ-9", SSCC[0])])
    d = cliente.post("/api/stock/entradas/leer", json={"archivos": [FOTO]}).json()
    ident = cliente.post("/api/stock/entradas", json={"cliente": "KERAKOLL", "albaran": d["albaran"]}).json()["id"]
    r = cliente.post(f"/api/stock/entradas/{ident}/etiqueta", json={"foto": FOTO})
    assert r.json()["palet"]["estado"] == "discrepancia"
    assert _cerrar(cliente, ident, {}).status_code == 409
    r = _cerrar(cliente, ident, {"confirmado": True, "nota": "Falta el resto, llega mañana"})
    assert r.status_code == 200 and r.json()["entrada"]["discrepancias"] == 3   # palet ajeno + faltan 3 de KK-100 + faltan 2 de KK-200
    assert "Falta el resto" in enviados[0]["html"] and "Discrepancias aceptadas" in enviados[0]["html"]
    assert cliente.get("/api/stock/stock").json()["total_palets"] == 1   # el palet físico entra aunque no estuviera en el albarán
    wb = openpyxl.load_workbook(io.BytesIO(base64.b64decode(enviados[0]["archivos"][0]["b64"])))
    assert "Discrepancias aceptadas" in " ".join(str(c.value) for f in wb["ENTRADA"].iter_rows() for c in f if c.value)


def test_flujo_salida_completo(cliente, monkeypatch, enviados):
    dar_de_alta(cliente)
    s0 = A.get_store()
    s0.escribir("entrada", "E-2026-0001", entrada_cerrada("E-2026-0001", "2026-11-10", n=3))
    leer_con(monkeypatch, etiquetas=[etiqueta("KK-100", SSCC[0]), etiqueta("KK-100", SSCC[1]), etiqueta("KK-100", SSCC[2])],
             peticion={"cliente": "Kerakoll", "referencia_cliente": "PED-9", "destino": "Valencia", "fecha": "2026-11-18",
                       "lineas": [{"referencia": "KK-100", "palets": 2}], "dudas": []})
    p = cliente.post("/api/stock/salidas/leer", json={"archivos": [FOTO]}).json()
    assert p["cliente_sugerido"] == "KERAKOLL" and p["peticion"]["lineas"][0]["palets"] == 2
    # pedir algo que no hay → conflicto
    mal = {"cliente": "KERAKOLL", "lineas": [{"ref": "NOHAY", "palets": 1}]}
    assert cliente.post("/api/stock/salidas", json=mal).status_code == 409
    r = cliente.post("/api/stock/salidas", json={"cliente": "KERAKOLL", "referencia": "PED-9", "destino": "Valencia", "lineas": [{"ref": "KK-100", "palets": 2}], "foto": FOTO})
    assert r.status_code == 200, r.text
    ident = r.json()["id"]
    r = cliente.post(f"/api/stock/salidas/{ident}/etiqueta", json={"foto": FOTO})
    assert r.status_code == 200 and r.json()["salida"]["cargados"] == 1 and r.json()["salida"]["estado"] == "preparando"
    # sin completar → discrepancia (faltan) y no se cierra sin confirmar
    r = cliente.post(f"/api/stock/salidas/{ident}/cerrar", json={})
    assert r.status_code == 409 and "Faltan 1 palets de KK-100" in r.json()["error"]
    r = cliente.post(f"/api/stock/salidas/{ident}/etiqueta", json={"foto": FOTO})
    assert r.json()["salida"]["cargados"] == 2
    # un tercer palet no pedido: sobra → se puede quitar
    r = cliente.post(f"/api/stock/salidas/{ident}/etiqueta", json={"foto": FOTO})
    assert r.json()["palet"]["estado"] == "discrepancia"
    sobra = r.json()["palet"]["n"]
    assert cliente.post(f"/api/stock/salidas/{ident}/quitar", json={"palet": str(sobra)}).json()["palets"][-1]["n"] == 2
    r = cliente.post(f"/api/stock/salidas/{ident}/cerrar", json={})
    assert r.status_code == 200, r.text
    assert r.json()["salida"]["estado"] == "cargada" and r.json()["enviado"]
    e = enviados[-1]
    assert e["evento"] == "salida_cargada" and e["archivos"][0]["nombre"] == f"SALIDA {ident}.xlsx" and any(f["nombre"] == "peticion.jpg" for f in e["fotos"])
    wb = openpyxl.load_workbook(io.BytesIO(base64.b64decode(e["archivos"][0]["b64"])))
    assert wb.sheetnames == ["SALIDA"] and "TOTAL: 2 palets" in " ".join(str(c.value) for f in wb["SALIDA"].iter_rows() for c in f if c.value)
    assert cliente.get("/api/stock/stock").json()["total_palets"] == 1   # quedan 1 de los 3


def test_etiqueta_ilegible_y_reenvio(cliente, monkeypatch, enviados):
    dar_de_alta(cliente)
    leer_con(monkeypatch, etiquetas=[{"sscc": None, "referencia": None, "descripcion": None, "lote": None, "dudas": []}])
    d = cliente.post("/api/stock/entradas/leer", json={"archivos": [FOTO]}).json()
    ident = cliente.post("/api/stock/entradas", json={"cliente": "KERAKOLL", "albaran": d["albaran"]}).json()["id"]
    r = cliente.post(f"/api/stock/entradas/{ident}/etiqueta", json={"foto": FOTO})
    assert r.status_code == 422 and r.json()["codigo"] == "etiqueta_ilegible"
    assert cliente.post(f"/api/stock/entradas/{ident}/reenviar").status_code == 409   # aún abierta
    assert cliente.post("/api/stock/libro").json()["enviado"] is True and enviados[-1]["evento"] == "libro" and enviados[-1]["correo"] is False


def test_codigo_de_oficina_y_fotos_invalidas(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setenv("ODOO_MODE", "mock")
    monkeypatch.setenv("APP_ACCESS_CODE", "conductores1")
    monkeypatch.setenv("APP_OFICINA_CODE", "oficina-1234")
    monkeypatch.setattr(A, "FAIL_DELAY", 0, raising=False)
    import app.main as M
    monkeypatch.setattr(M, "FAIL_DELAY", 0)
    A.set_store(S.MemoriaStore())
    c = TestClient(app)
    assert c.get("/api/stock/stock", headers={"X-Codigo-Acceso": "conductores1"}).status_code == 403   # un conductor no entra
    assert c.get("/api/stock/stock", headers={"X-Codigo-Acceso": "oficina-1234"}).status_code == 200
    r = c.post("/api/stock/entradas/leer", headers={"X-Codigo-Acceso": "oficina-1234"}, json={"archivos": [{"archivo_b64": "no es base64!!", "tipo": "image/jpeg"}]})
    assert r.status_code == 400 and r.json()["codigo"] == "archivo_invalido"
    A.set_store(None)


def test_enviar_n8n_solo_en_real(monkeypatch):
    enviado = []
    monkeypatch.setattr(A.httpx, "post", lambda url, json, timeout, headers: enviado.append((url, json, headers)) or type("R", (), {"status_code": 200})())
    monkeypatch.setenv("STOCK_AVISOS_WEBHOOK", "https://n8n.example/webhook/stock-burriana")
    monkeypatch.setenv("STOCK_AVISOS_CLAVE", "clave")
    monkeypatch.setattr(A, "real", lambda: False)
    assert A.enviar_n8n({"evento": "x"}) is False and enviado == []
    monkeypatch.setattr(A, "real", lambda: True)
    monkeypatch.delenv("STOCK_CORREO_PARA", raising=False)
    assert A.enviar_n8n({"evento": "x"}) is True
    assert enviado[0][1]["para"] == "carmelomartinez@stinsa.com" and enviado[0][2] == {"X-Clave": "clave"}
    monkeypatch.setenv("STOCK_CORREO_PARA", "traficolevante@stinsa.com")
    A.enviar_n8n({"evento": "y"})
    assert enviado[1][1]["para"] == "traficolevante@stinsa.com"


def test_excel_libro_vacio():
    wb = openpyxl.load_workbook(io.BytesIO(X.libro_control([], [], CLIENTES, HOY)))
    assert wb.sheetnames == ["RESUMEN STOCK", "STOCK", "ENTRADAS", "SALIDAS", "MOVIMIENTOS", "CLIENTES"]
    assert wb["CLIENTES"]["A5"].value == "KERAKOLL"


# ---- primer cliente: Raminatrans Castellón (operador) con albaranes de su cliente BECRISA ------------------------------

def _excel_albaran(codigos, ref="6030100800103", kg=348, entrada=""):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Nº producto", "Descripción", "Cantidad", "SKU", "Nº lote", "kg brutos", "Nº documento entrada"])
    for c in codigos:
        ws.append([ref, "TAZA WC TRIPOLI BLANCO DUAL", 12, c, "", kg, entrada])
    b = io.BytesIO(); wb.save(b)
    return b.getvalue()


def test_excel_del_cliente_se_lee_sin_ia_y_da_la_lista_de_palets_con_su_peso():
    from app.stock import stock_lector as L
    ex = L.leer_excel_albaran(_excel_albaran(["2600044888", "2600044889"]))
    assert ex["lineas"][0]["palets"] == 2 and ex["lineas"][0]["kg"] == 696
    assert [(p["codigo"], p["kg"]) for p in ex["palets_detalle"]] == [("2600044888", 348), ("2600044889", 348)]


def test_etiqueta_de_10_cifras_se_casa_por_codigo_y_no_controla_unidades():
    cli = {"codigo": "RAMINATRANS", "nombre": "Raminatrans Castellón, S.L.", "alias": ["GRUPO RAMINATRANS"]}
    esp = R.limpiar_esperados([{"codigo": "2600044888", "referencia": "6030100800103", "kg": 348, "entrada": "ECS26-3266"},
                               {"codigo": "2600044889", "referencia": "6030100800103", "kg": 348, "entrada": "ECS26-3267"}])
    e = {"id": "E-1", "cliente": cli, "albaran": {"remitente": "Grupo Raminatrans"}, "esperados": esp, "palets": [],
         "lineas": [{"ref": "6030100800103", "descripcion": "TAZA", "palets": 2, "kg": 696, "lote": ""}]}
    def et(cod, entrada=None, uds=12, cliente="Grupo Raminatrans"):
        return R.limpiar_etiqueta({"sscc": cod, "referencia": "6030100800103", "bultos": uds, "entrada": entrada, "cliente": cliente, "dudas": []})
    p1 = R.verificar_palet(e, et("2600044888", "ECS26-3266"), set())
    assert p1["estado"] == "ok" and p1["kg"] == 348 and p1["lote"] == "ECS26-3266"      # el peso sale del albarán, el lote es la «Entrada»
    e["palets"].append(p1)
    assert "repetido" in R.verificar_palet(e, et("2600044888", "ECS26-3266"), set())["motivo"]
    assert "no está en la lista" in R.verificar_palet(e, et("2600051441", "ECS26-3696"), set())["motivo"]
    assert "entrada ECS26-3267" in R.verificar_palet(e, et("2600044889", "ECS26-3999"), set())["motivo"]
    assert R.verificar_palet(e, et("2600044889", "ECS26-3267", uds=3), set())["estado"] == "ok"     # las unidades de la etiqueta no se controlan
    assert R.verificar_palet(e, et("2600044889", "ECS26-3267", cliente="OTRA EMPRESA"), set())["estado"] == "discrepancia"
    assert any("2600044889" in d for d in R.discrepancias_entrada(e))                              # falta etiquetar el segundo


def test_el_cliente_es_el_operador_y_el_cliente_final_es_solo_informativo():
    cli = [{"codigo": "RAMINATRANS", "nombre": "Raminatrans Castellón, S.L.", "alias": ["RAMINATRANS"]}]
    alb = R.limpiar_albaran({"numero_albaran": "SRCS26-04809", "fecha": "2026-11-18", "remitente": "Grupo Raminatrans", "destinatario": "STINSA",
                             "cliente_final": "BECRISA 2011 S.L.U.", "codigo_cliente_final": "03783",
                             "lineas": [{"referencia": "6030100800103", "descripcion": "TAZA", "palets": 30}]})
    assert alb["cliente_final"] == "BECRISA 2011 S.L.U."
    assert R.sugerir_clientes(cli, *R.textos_cliente(alb))[0][0]["codigo"] == "RAMINATRANS"
    assert R.conflictos_entrada(alb, cli[0], cli, [], dt.date(2026, 11, 17)) == []       # albarán de mañana: sin aviso de fecha


def test_catalogo_de_referencias_avisa_de_referencias_desconocidas():
    cli = {"codigo": "RAMINATRANS", "nombre": "Raminatrans", "referencias": [{"ref": "6030100800103", "descripcion": "TAZA"}]}
    alb = R.limpiar_albaran({"numero_albaran": "1", "fecha": "2026-11-17", "lineas": [{"referencia": "603010080010", "descripcion": "TAZA", "palets": 3}]})
    assert any("603010080010" in a["texto"] and "catálogo" in a["texto"] for a in R.conflictos_entrada(alb, cli, [cli], [], dt.date(2026, 11, 17)))


def test_primer_cliente_precargado_con_su_catalogo():
    st = S.MemoriaStore()
    cs = A.clientes(st)
    assert cs[0]["codigo"] == "RAMINATRANS" and len(cs[0]["referencias"]) == 7 and "6030100800103" in {r["ref"] for r in cs[0]["referencias"]}


def test_entrada_con_pdf_y_excel_fecha_editable_observaciones_y_salida_con_codigos(cliente, monkeypatch, enviados):
    codigos = ["2600044888", "2600044889", "2600044860"]
    xlsx = {"archivo_b64": base64.b64encode(_excel_albaran(codigos)).decode(), "tipo": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
    pdf = {"archivo_b64": base64.b64encode(b"%PDF-1.4").decode(), "tipo": "application/pdf"}
    pdf_leido = {"numero_albaran": "SRCS26-04809", "fecha": "2026-11-18", "remitente": "Grupo Raminatrans", "destinatario": "STINSA",
                 "cliente_final": "BECRISA 2011 S.L.U.", "codigo_cliente_final": "03783", "lineas": [], "total_palets": 3,
                 "palets_detalle": [{"codigo": c, "entrada": f"ECS26-{3266 + i}"} for i, c in enumerate(codigos)], "dudas": []}
    etiquetas = [{"sscc": c, "referencia": "6030100800103", "entrada": f"ECS26-{3266 + i}", "cliente": "Grupo Raminatrans", "dudas": []} for i, c in enumerate(codigos)]
    leer_con(monkeypatch, albaran=pdf_leido, etiquetas=etiquetas)
    rr = cliente.post("/api/stock/entradas/leer", json={"archivos": [pdf, xlsx]})   # el primer cliente ya viene dado de alta
    assert rr.status_code == 200, rr.text
    d = rr.json()
    assert d["cliente_sugerido"] == "RAMINATRANS" and d["albaran"]["cliente_final"] == "BECRISA 2011 S.L.U." and d["fecha_entrada"] == "2026-11-18"
    assert [x["lote"] for x in d["esperados"]] == ["ECS26-3266", "ECS26-3267", "ECS26-3268"] and d["albaran"]["lineas"][0]["palets"] == 3
    assert not d["avisos"]
    r = cliente.post("/api/stock/entradas", json={"cliente": "RAMINATRANS", "albaran": d["albaran"], "esperados": d["esperados"], "fecha_entrada": "2026-11-19", "fotos": [pdf, xlsx]})
    assert r.status_code == 200, r.text
    ident = r.json()["id"]
    assert r.json()["fecha_entrada"] == "2026-11-19"                                           # se descarga un día después del albarán
    assert cliente.post(f"/api/stock/entradas/{ident}/fecha", json={"fecha": "2026-11-20"}).json()["fecha_entrada"] == "2026-11-20"
    for _ in codigos:
        assert cliente.post(f"/api/stock/entradas/{ident}/etiqueta", json={"foto": FOTO}).json()["palet"]["estado"] == "ok"
    assert cliente.post(f"/api/stock/entradas/{ident}/observaciones", json={"texto": "Un palet con el film roto"}).status_code == 200
    r = _cerrar(cliente, ident, {})
    assert r.status_code == 200, r.text
    e = enviados[-1]
    assert e["asunto"].startswith("⚠ ") and "film roto" in e["html"]
    st = cliente.get("/api/stock/stock").json()
    assert sum(p["kg"] for p in st["palets"]) == 3 * 348 and {p["fecha_entrada"] for p in st["palets"]} == {"2026-11-20"}
    assert {p["cliente_final"] for p in st["palets"]} == {"BECRISA 2011 S.L.U."}
    # salida con lista de códigos (Raminatrans no los exige)
    monkeypatch.setattr(A.L, "leer_etiqueta", lambda datos, tipo, cli=None: etiquetas.pop(0))
    etiquetas[:] = [{"sscc": "2600044888", "referencia": "6030100800103", "cliente": "Grupo Raminatrans", "dudas": []},
                    {"sscc": "2600044860", "referencia": "6030100800103", "cliente": "Grupo Raminatrans", "dudas": []}]
    pet = cliente.post("/api/stock/salidas/leer", json={"archivos": [{"archivo_b64": base64.b64encode(_excel_albaran(["2600044888", "2600044889"])).decode(),
                                                                       "tipo": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}]}).json()
    assert [x["codigo"] for x in pet["esperados"]] == ["2600044888", "2600044889"]
    s = cliente.post("/api/stock/salidas", json={"cliente": "RAMINATRANS", "lineas": [{"ref": "6030100800103", "palets": 2}], "esperados": pet["esperados"]}).json()
    assert cliente.post(f"/api/stock/salidas/{s['id']}/etiqueta", json={"foto": FOTO}).json()["palet"]["estado"] == "ok"
    p2 = cliente.post(f"/api/stock/salidas/{s['id']}/etiqueta", json={"foto": FOTO}).json()["palet"]
    # Raminatrans: el palet concreto NO es obligatorio (basta la referencia), así que uno fuera de la lista también vale
    assert p2["estado"] == "ok"
    sin_palet = cliente.post("/api/stock/salidas", json={"cliente": "RAMINATRANS", "lineas": [{"ref": "6030100800103", "palets": 1}],
                                                         "esperados": [{"codigo": "2600099999", "referencia": "6030100800103"}]})
    assert sin_palet.status_code == 200


def test_correo_del_buzon_prepara_la_entrada_para_revisar_y_no_se_duplica(cliente, monkeypatch, enviados):
    monkeypatch.setenv("STOCK_CORREO_CLAVE", "clave-de-prueba-123")
    h = {"X-Clave": "clave-de-prueba-123"}
    codigos = ["2600044888", "2600044889"]
    xlsx = {"nombre": "TRASLADO.xlsx", "tipo": "application/octet-stream", "archivo_b64": base64.b64encode(_excel_albaran(codigos)).decode()}
    pdf = {"nombre": "TRASLADO.pdf", "tipo": "application/pdf", "archivo_b64": base64.b64encode(b"%PDF-1.4").decode()}
    leido = {"numero_albaran": "SRCS26-04809", "fecha": "2026-11-18", "remitente": "Grupo Raminatrans", "destinatario": "STINSA",
             "cliente_final": "BECRISA 2011 S.L.U.", "lineas": [], "palets_detalle": [], "dudas": []}
    leer_con(monkeypatch, albaran=leido, etiquetas=[{"sscc": c, "referencia": "6030100800103", "cliente": "Grupo Raminatrans", "dudas": []} for c in codigos],
             peticion={"cliente": "Raminatrans", "referencia_cliente": "REC-1", "lineas": [{"referencia": "6030100800103", "palets": 1}], "dudas": []})
    correo = {"mensaje_id": "AAMk-1", "de": "logistica@raminatrans.com", "asunto": "1º traslado STINSA", "cuerpo": "Os remito el albarán.", "adjuntos": [pdf, xlsx]}
    assert cliente.post("/api/stock/correo", json=correo).status_code == 401                      # sin clave no entra
    r = cliente.post("/api/stock/correo", json=correo, headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["resultado"] == "creada" and d["tipo"] == "entrada" and d["cliente"] == "Raminatrans Castellón, S.L." and d["palets"] == 2 and d["albaran"] == "SRCS26-04809"
    ident = d["id"]
    assert cliente.get(f"/api/stock/entradas/{ident}").json()["por_revisar"] is True
    assert cliente.post("/api/stock/correo", json=correo, headers=h).json()["id"] == ident          # mismo correo: no duplica
    assert len(cliente.get("/api/stock/entradas").json()["entradas"]) == 1
    for _ in codigos:
        cliente.post(f"/api/stock/entradas/{ident}/etiqueta", json={"foto": FOTO})
    r = _cerrar(cliente, ident, {})
    assert r.status_code == 409 and r.json()["codigo"] == "sin_revisar"                              # hasta que la oficina la revise
    assert cliente.post(f"/api/stock/entradas/{ident}/revisar").json()["por_revisar"] is False
    assert _cerrar(cliente, ident, {}).status_code == 200
    # el mismo albarán en otro correo → no se crea, se avisa a la oficina
    r = cliente.post("/api/stock/correo", json={**correo, "mensaje_id": "AAMk-2"}, headers=h).json()
    assert r["resultado"] == "no_procesada" and "ya está cargado" in r["motivo"]
    # sin adjuntos útiles
    r = cliente.post("/api/stock/correo", json={"mensaje_id": "AAMk-3", "asunto": "hola", "adjuntos": [{"nombre": "firma.gif", "tipo": "image/gif", "archivo_b64": "AAAA"}]}, headers=h).json()
    assert r["resultado"] == "no_procesada"
    # el correo habla de recoger mercancía → salida, con los palets que no hay en stock avisados
    r = cliente.post("/api/stock/correo", json={"mensaje_id": "AAMk-4", "de": "x", "asunto": "Recogida mañana", "cuerpo": "", "adjuntos": [pdf]}, headers=h).json()
    assert r["resultado"] == "creada" and r["tipo"] == "salida" and r["id"].startswith("S-")
    assert cliente.get(f"/api/stock/salidas/{r['id']}").json()["por_revisar"] is True


def test_codigo_de_barras_y_lista_desplegable_en_entradas_y_salidas(cliente, monkeypatch, enviados):
    """El código leído con la cámara (o elegido de la lista) basta: el resto sale de la lista del albarán / del stock."""
    codigos = ["2600044888", "2600044889", "2600044860"]
    xlsx = {"archivo_b64": base64.b64encode(_excel_albaran(codigos)).decode(), "tipo": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
    leer_con(monkeypatch, albaran={"numero_albaran": "SRCS26-04809", "fecha": "2026-11-18", "remitente": "Grupo Raminatrans", "lineas": [], "dudas": []})
    d = cliente.post("/api/stock/entradas/leer", json={"archivos": [xlsx]}).json()
    ident = cliente.post("/api/stock/entradas", json={"cliente": "RAMINATRANS", "albaran": d["albaran"], "esperados": d["esperados"]}).json()["id"]
    det = cliente.get(f"/api/stock/entradas/{ident}").json()
    assert [x["codigo"] for x in det["pendientes"]] == codigos and all(x["ref"] and x["kg"] for x in det["pendientes"])
    # escaneado: solo llega el código, el resto sale del albarán; no cuenta como «a mano»
    r = cliente.post(f"/api/stock/entradas/{ident}/palet", json={"sscc": "2600044888", "origen": "barras"})
    assert r.status_code == 200, r.text
    p = r.json()["palet"]
    assert p["estado"] == "ok" and p["ref"] == "6030100800103" and p["kg"] == 348 and p["origen"] == "barras" and p["manual"] is False
    assert [x["codigo"] for x in r.json()["entrada"]["pendientes"]] == codigos[1:]
    # elegido de la lista (sin origen «barras»): cuenta como a mano
    p = cliente.post(f"/api/stock/entradas/{ident}/palet", json={"sscc": "2600044889"}).json()["palet"]
    assert p["estado"] == "ok" and p["manual"] is True
    # un código que no está en la lista del albarán se anota como discrepancia, no se rechaza
    p = cliente.post(f"/api/stock/entradas/{ident}/palet", json={"sscc": "9999999999", "origen": "barras"}).json()["palet"]
    assert p["estado"] == "discrepancia"
    assert cliente.post(f"/api/stock/entradas/{ident}/palet", json={}).status_code == 400
    # el mismo código dos veces → palet repetido
    p = cliente.post(f"/api/stock/entradas/{ident}/palet", json={"sscc": "2600044889", "origen": "barras"}).json()["palet"]
    assert p["estado"] == "discrepancia" and "repetido" in p["motivo"]
    # cerrar la entrada (con el palet que sobra y el repetido aceptados) para que haya stock
    cliente.post(f"/api/stock/entradas/{ident}/palet", json={"sscc": "2600044860", "origen": "barras"})
    r = _cerrar(cliente, ident, {"aceptar": True, "nota": "prueba"})
    if r.status_code == 409:
        r = _cerrar(cliente, ident, {"confirmado": True, "nota": "prueba"})
    assert r.status_code == 200, r.text
    assert cliente.get(f"/api/stock/entradas/{ident}").json()["pendientes"] == []
    # salida: la lista desplegable ofrece los palets del cliente que se pueden cargar (los más antiguos primero)
    s = cliente.post("/api/stock/salidas", json={"cliente": "RAMINATRANS", "lineas": [{"ref": "6030100800103", "palets": 2}]}).json()
    disp = cliente.get(f"/api/stock/salidas/{s['id']}/disponibles").json()["palets"]
    assert {x["codigo"] for x in disp} == set(codigos) and all(x["ref"] and x["kg"] for x in disp)
    r = cliente.post(f"/api/stock/salidas/{s['id']}/palet", json={"sscc": "2600044888", "origen": "barras"})
    assert r.status_code == 200 and r.json()["palet"]["estado"] == "ok" and r.json()["palet"]["origen"] == "barras"
    assert {x["codigo"] for x in cliente.get(f"/api/stock/salidas/{s['id']}/disponibles").json()["palets"]} == set(codigos) - {"2600044888"}


def test_palets_con_codigo_alfanumerico_tipo_aliplast_se_apuntan_y_se_casan_en_entrada_y_salida(cliente):
    """«LP26-0236823» (Code128 con letras y guion) se guarda como LP260236823 y se reconoce escrito de cualquier forma."""
    resp = cliente.post("/api/stock/entradas", json={"cliente": "RAMINATRANS", "albaran": {"numero_albaran": "ALI-1", "fecha": "2026-11-18", "remitente": "Aliplast", "lineas": [], "dudas": []}, "esperados": [], "confirmado": True})
    assert resp.status_code == 200, resp.text; ident = resp.json()["id"]
    r = cliente.post(f"/api/stock/entradas/{ident}/palet", json={"sscc": "LP26-0236823", "ref": "F05182S", "lote": "P03-014037", "kg": 376, "origen": "barras"})
    assert r.status_code == 200, r.text
    p = r.json()["palet"]
    assert p["sscc"] == "LP260236823" and p["kg"] == 376 and p["lote"] == "P03-014037" and p["origen"] == "barras"
    # el mismo palet escrito de otra forma es el mismo código → repetido
    p = cliente.post(f"/api/stock/entradas/{ident}/palet", json={"sscc": "lp26 0236823", "origen": "barras"}).json()["palet"]
    assert p["estado"] == "discrepancia" and "repetido" in p["motivo"]
    # un QR (dirección web) no se acepta como código de palet
    r = cliente.post(f"/api/stock/entradas/{ident}/palet", json={"sscc": "https://www.aliplastspa.com/etichettatura-ambientale", "origen": "barras"})
    assert r.status_code == 400


def test_aliplast_inventario_control_y_aviso_de_llegada(cliente, enviados):
    """Entrada manual de inventario (con alias de referencia), control de inventario con bajas y aviso de llegada para reenviar."""
    # inventario: mercancía que ya estaba en la nave, sin albarán; F05182S-MV es la misma referencia que F05182S
    r = cliente.post("/api/stock/inventario", json={"cliente": "ALIPLAST", "fecha_entrada": "2026-10-01"})
    assert r.status_code == 200, r.text
    ident = r.json()["id"]
    assert ident.startswith("I-") and r.json()["tipo"] == "inventario"
    for cod, ref in (("LP26-0000001", "F05182S"), ("LP26-0000002", "F05182S-MV"), ("LP26-0000003", "F02891S")):
        p = cliente.post(f"/api/stock/entradas/{ident}/palet", json={"sscc": cod, "ref": ref, "lote": "P03-014037", "kg": 376, "fecha_prod": "", "origen": "barras"})
        assert p.status_code == 200, p.text
        assert p.json()["palet"]["estado"] == "ok"
    det = cliente.get(f"/api/stock/entradas/{ident}").json()
    assert sorted((l["ref"], l["verificados"]) for l in det["lineas"]) == [("F02891S", 1), ("F05182S", 2)]
    # repetir un código se avisa
    rep = cliente.post(f"/api/stock/entradas/{ident}/palet", json={"sscc": "lp26 0000001", "ref": "F05182S"}).json()["palet"]
    assert rep["estado"] == "discrepancia"
    cliente.post(f"/api/stock/entradas/{ident}/quitar", json={"palet": rep["id"]})
    cerrada = _cerrar(cliente, ident, {}).json()
    assert cerrada["enviado"] and cerrada["aviso_cliente"] == {"activo": False}
    assert enviados[-1]["evento"] == "inventario_cerrado" and "inventario" in enviados[-1]["asunto"].lower()
    resp = cliente.get("/api/stock/stock"); assert resp.status_code == 200, resp.text
    stock = resp.json()
    assert len(stock["palets"]) == 3 and stock["antiguos"] == 3   # entraron el 1-10: llevan más de un mes
    # control: se cuentan 2 de los 3 y aparece uno que no está en el sistema
    c = cliente.post("/api/stock/controles", json={"cliente": "ALIPLAST"}).json()
    assert c["id"].startswith("C-")
    assert cliente.post("/api/stock/controles", json={"cliente": "ALIPLAST"}).json()["id"] == c["id"]   # sigue el abierto
    for cod in ("LP260000001", "LP26-0000002", "LP26-9999999"):
        assert cliente.post(f"/api/stock/controles/{c['id']}/palet", json={"sscc": cod}).status_code == 200
    assert cliente.post(f"/api/stock/controles/{c['id']}/palet", json={"sscc": "LP26 0000001"}).json()["repetido"] is True
    res = cliente.get(f"/api/stock/controles/{c['id']}").json()["resultado"]
    assert (res["esperados"], res["bien"], len(res["faltan"]), len(res["sobran"])) == (3, 2, 1, 1)
    assert res["faltan"][0]["sscc"] == "LP260000003"
    cierre = cliente.post(f"/api/stock/controles/{c['id']}/cerrar", json={"baja_faltantes": True}).json()
    assert cierre["enviado"] and enviados[-1]["evento"] == "control_cerrado" and len(cierre["control"]["bajas"]) == 1
    assert [p["sscc"] for p in cliente.get("/api/stock/stock").json()["palets"]] == ["LP260000001", "LP260000002"]
    assert cliente.post(f"/api/stock/controles/{c['id']}/palet", json={"sscc": "LP26-0000004"}).status_code == 409   # cerrado
    # aviso de llegada: va a traficolevante y a Carmelo (no al cliente), con el contacto indicado, la documentación adjunta y listo para reenviar
    assert cliente.post("/api/stock/clientes", json={"codigo": "ALIPLAST", "nombre": "Aliplast Iberia, S.L.U.", "aviso_llegada": {"activo": True, "correo": "contacto@aliplast.test", "contacto": "Marta"}}).status_code == 200
    e = cliente.post("/api/stock/entradas", json={"cliente": "ALIPLAST", "fotos": [FOTO], "confirmado": True,
                     "albaran": {"numero": "DDT26-012082", "fecha": "2026-11-18", "total_palets": 1, "total_kg": 376,
                                 "lineas": [{"ref": "F05182S", "descripcion": "Film", "palets": 1, "kg": 376, "lote": "P03-015457"}], "dudas": []}}).json()
    assert cliente.post(f"/api/stock/entradas/{e['id']}/palet", json={"sscc": "LP26-0000010", "ref": "F05182S", "lote": "P03-015457", "kg": 376}).status_code == 200
    out = _cerrar(cliente, e['id'], {"confirmado": True}).json()
    assert out["aviso_cliente"]["creado"] is True and "traficolevante@stinsa.com" in out["aviso_cliente"]["para"]
    av = [x for x in enviados if x["evento"] == "aviso_llegada"][-1]
    assert "enviar" not in av and "buzon" not in av and "traficolevante@stinsa.com" in av["para"] and "carmelomartinez@stinsa.com" in av["para"]
    assert "contacto@aliplast.test" in av["html"] and "PARA REENVIAR" in av["html"] and av["asunto"].startswith("Para reenviar a")
    assert "DDT26-012082" in av["html"] and "Marta" in av["html"] and len(av["archivos"]) == 1 and av["archivos"][0]["sharepoint"] is False



def test_entrada_repetida_da_error_y_se_puede_anular(cliente):
    cuerpo = {"cliente": "ALIPLAST", "fotos": [FOTO], "albaran": {"numero": "DDT-DUP-1", "fecha": "2026-11-18", "total_palets": 1, "total_kg": 100,
              "lineas": [{"ref": "F05182S", "descripcion": "Film", "palets": 1, "kg": 100}], "dudas": []}}
    e = cliente.post("/api/stock/entradas", json=cuerpo)
    assert e.status_code == 200, e.text
    ident = e.json()["id"]
    for extra in ({}, {"confirmado": True}):   # ni siquiera confirmando se puede repetir
        r = cliente.post("/api/stock/entradas", json={**cuerpo, **extra})
        assert r.status_code == 409 and r.json()["codigo"] == "albaran_repetido" and ident in r.text
    assert cliente.post(f"/api/stock/entradas/{ident}/anular", json={"motivo": "duplicada"}).status_code == 200
    assert cliente.post(f"/api/stock/entradas/{ident}/anular", json={}).status_code == 409   # ya anulada
    assert ident not in [x["id"] for x in cliente.get("/api/stock/entradas").json()["entradas"]]
    assert cliente.post("/api/stock/entradas", json=cuerpo).status_code == 200   # anulada: se puede volver a declarar


def test_repetido_no_hace_sobrar_al_siguiente_e_incidencia_en_el_correo(cliente, enviados):
    e = cliente.post("/api/stock/entradas", json={"cliente": "ALIPLAST", "fotos": [FOTO], "albaran": {"numero": "DDT-INC-1", "fecha": "2026-11-18", "total_palets": 2, "total_kg": 200,
                     "lineas": [{"ref": "F05182S", "descripcion": "Film", "palets": 2, "kg": 200}], "dudas": []}}).json()
    ident = e["id"]
    pal = lambda cod: cliente.post(f"/api/stock/entradas/{ident}/palet", json={"sscc": cod, "ref": "F05182S", "kg": 100, "lote": "L1"}).json()["palet"]
    a = pal("LP26-1000001")
    dup = pal("LP26-1000001")             # leído dos veces: queda como advertencia a depurar
    b = pal("LP26-1000002")               # y el siguiente NO se marca como «sobra»
    assert (a["estado"], dup["estado"], b["estado"]) == ("ok", "discrepancia", "ok") and "repetido" in dup["motivo"]
    # incidencia de un bulto con foto
    assert cliente.post(f"/api/stock/entradas/{ident}/incidencia", json={"palet": b["id"]}).status_code == 400   # vacía
    r = cliente.post(f"/api/stock/entradas/{ident}/incidencia", json={"palet": b["id"], "texto": "Film roto en una esquina", "fotos": [FOTO]})
    assert r.status_code == 200, r.text
    # al cerrar (confirmando la advertencia) el repetido se descarta y solo entran 2 palets
    out = _cerrar(cliente, ident, {"confirmado": True}).json()
    assert out["enviado"]
    pl = [x for x in cliente.get("/api/stock/stock").json()["palets"] if x["sscc"].startswith("LP261000")]
    assert sorted(x["sscc"] for x in pl) == ["LP261000001", "LP261000002"]
    env = [x for x in enviados if x["evento"] == "entrada_cerrada"][-1]
    assert "Incidencias por bulto" in env["html"] and "Film roto en una esquina" in env["html"] and "cid:inc1@stinsa" in env["html"]
    inline = [f for f in env["fotos"] if f.get("inline")]
    assert len(inline) == 1 and inline[0]["inline"] == "inc1@stinsa" and inline[0]["nombre"].startswith("incidencia")
    assert cliente.post(f"/api/stock/entradas/{ident}/incidencia", json={"palet": b["id"], "texto": "x"}).status_code == 409   # cerrada


def test_ubicacion_en_la_entrada_el_stock_y_la_salida(cliente, enviados):
    """La ubicación se informa al meter palets (y se mantiene hasta cambiarla); después sale en el stock, en la lista de palets
    disponibles de una salida y en cada palet cargado. El inventario va a traficolevante y Carmelo."""
    ident = cliente.post("/api/stock/inventario", json={"cliente": "ALIPLAST", "fecha_entrada": "2026-11-01"}).json()["id"]
    alta = lambda cod, ubic: cliente.post(f"/api/stock/entradas/{ident}/palet", json={"sscc": cod, "ref": "F05182S", "lote": "L1", "kg": 100, "ubicacion": ubic}).json()
    assert alta("LP26-2000001", " e1 ")["palet"]["ubicacion"] == "E1"
    alta("LP26-2000002", "E1")
    r = alta("LP26-2000003", "S3")        # ya no caben: se cambia de ubicación y se sigue
    assert r["palet"]["ubicacion"] == "S3" and r["entrada"]["ubicacion"] == "S3"
    mala = cliente.post(f"/api/stock/entradas/{ident}/palet", json={"sscc": "LP26-2000009", "ref": "F05182S", "ubicacion": "Z9"})
    assert mala.status_code == 400 and mala.json()["codigo"] == "ubicacion_invalida"   # solo las zonas de la lista
    alta("LP26-2000004", "")               # sin informar: queda sin ubicación
    _cerrar(cliente, ident, {})
    env = enviados[-1]
    assert env["evento"] == "inventario_cerrado" and env["para"] == "traficolevante@stinsa.com,carmelomartinez@stinsa.com"
    assert "E1 (2)" in env["html"] and "S3 (1)" in env["html"] and "sin ubicación (1)" in env["html"]
    st = cliente.get("/api/stock/stock").json()
    assert [(u["ubicacion"], u["palets"]) for u in st["resumen"][0]["ubicaciones"]] == [("E1", 2), ("S3", 1), ("sin ubicación", 1)]
    assert sorted(p["ubicacion"] for p in st["palets"]) == ["", "E1", "E1", "S3"]
    s = cliente.post("/api/stock/salidas", json={"cliente": "ALIPLAST", "lineas": [{"ref": "F05182S", "palets": 2}], "confirmado": True}).json()
    disp = cliente.get(f"/api/stock/salidas/{s['id']}/disponibles").json()["palets"]
    assert {d["codigo"]: d["ubicacion"] for d in disp}["LP262000003"] == "S3" and all(d["linea"] == 0 for d in disp)
    p = cliente.post(f"/api/stock/salidas/{s['id']}/palet", json={"sscc": "LP26-2000003", "origen": "barras"}).json()["palet"]
    assert p["estado"] == "ok" and p["ubicacion"] == "S3"


def test_ubicar_palets_del_inventario_con_confirmacion_al_cambiar(cliente, enviados):
    ident = cliente.post("/api/stock/inventario", json={"cliente": "ALIPLAST", "fecha_entrada": "2026-11-01"}).json()["id"]
    for cod, ub in (("LP26-3000001", ""), ("LP26-3000002", ""), ("LP26-3000003", "E2")):
        cliente.post(f"/api/stock/entradas/{ident}/palet", json={"sscc": cod, "ref": "F05182S", "lote": "L1", "kg": 100, "ubicacion": ub})
    _cerrar(cliente, ident, {})
    ids = {p["sscc"]: p["id"] for p in cliente.get("/api/stock/stock").json()["palets"]}
    sin = [ids["LP263000001"], ids["LP263000002"]]
    # asignar a los que no tienen: directo, sin confirmación
    assert cliente.post("/api/stock/stock/ubicacion", json={"palets": sin, "ubicacion": "S4"}).json()["cambiados"] == 2
    # cambiar uno que ya tiene ubicación: pide confirmación (409) y no toca nada hasta confirmar
    r = cliente.post("/api/stock/stock/ubicacion", json={"palets": [ids["LP263000003"], sin[0]], "ubicacion": "E5"})
    assert r.status_code == 409 and r.json()["codigo"] == "cambio_ubicacion" and "E2 (1)" in r.text and "S4 (1)" in r.text
    st = {p["sscc"]: p["ubicacion"] for p in cliente.get("/api/stock/stock").json()["palets"]}
    assert st == {"LP263000001": "S4", "LP263000002": "S4", "LP263000003": "E2"}
    ok = cliente.post("/api/stock/stock/ubicacion", json={"palets": [ids["LP263000003"], sin[0]], "ubicacion": "E5", "confirmado": True})
    assert ok.json()["cambiados"] == 2
    st = {p["sscc"]: p["ubicacion"] for p in cliente.get("/api/stock/stock").json()["palets"]}
    assert st == {"LP263000001": "E5", "LP263000002": "S4", "LP263000003": "E5"}
    assert cliente.post("/api/stock/stock/ubicacion", json={"palets": sin, "ubicacion": "X1"}).status_code == 400
    assert cliente.post("/api/stock/stock/ubicacion", json={"palets": ["E-2026-9999-01"], "ubicacion": "E1"}).status_code == 409   # no está en el stock


def test_corregir_un_palet_con_error_en_la_entrada(cliente):
    e = cliente.post("/api/stock/entradas", json={"cliente": "ALIPLAST", "fotos": [FOTO], "albaran": {"numero": "DDT-COR-1", "fecha": "2026-11-18", "total_palets": 2, "total_kg": 200,
                     "lineas": [{"ref": "F05182S", "descripcion": "Film", "palets": 2, "kg": 200, "lote": "L1"}], "dudas": []}}).json()
    ident = e["id"]
    mal = cliente.post(f"/api/stock/entradas/{ident}/palet", json={"sscc": "LP26-4000001", "ref": "F99999X", "kg": 100, "lote": "L1", "ubicacion": "E3"}).json()["palet"]
    assert mal["estado"] == "discrepancia" and "no figura" in mal["motivo"]
    cliente.post(f"/api/stock/entradas/{ident}/incidencia", json={"palet": mal["id"], "texto": "etiqueta borrosa"})
    r = cliente.post(f"/api/stock/entradas/{ident}/corregir", json={"palet": mal["id"], "ref": "F05182S", "kg": 100, "lote": "L1", "sscc": "LP26-4000001"})
    assert r.status_code == 200, r.text
    p = r.json()["palet"]
    assert p["id"] == mal["id"] and p["estado"] == "ok" and p["ref"] == "F05182S" and not p.get("motivo")
    assert p["ubicacion"] == "E3" and p["incidencias"][0]["texto"] == "etiqueta borrosa"   # conserva ubicación e incidencias
    det = r.json()["entrada"]
    assert len(det["palets"]) == 1 and det["lineas"][0]["verificados"] == 1
    # corregir un palet con su mismo código no lo marca como repetido de sí mismo
    assert cliente.post(f"/api/stock/entradas/{ident}/corregir", json={"palet": mal["id"], "ref": "F05182S", "sscc": "LP26-4000001", "kg": 120}).json()["palet"]["estado"] == "ok"
    assert cliente.post(f"/api/stock/entradas/{ident}/corregir", json={"palet": "no-existe", "ref": "F05182S"}).status_code == 404


def test_corregir_un_palet_en_la_entrada_manual_de_inventario(cliente):
    ident = cliente.post("/api/stock/inventario", json={"cliente": "ALIPLAST", "fecha_entrada": "2026-11-01"}).json()["id"]
    a = cliente.post(f"/api/stock/entradas/{ident}/palet", json={"sscc": "LP26-5000001", "ref": "F0518XX", "lote": "L1", "kg": 100, "ubicacion": "S1"}).json()["palet"]
    cliente.post(f"/api/stock/entradas/{ident}/palet", json={"sscc": "LP26-5000002", "ref": "F05182S", "lote": "L1", "kg": 100, "ubicacion": "S1"})
    r = cliente.post(f"/api/stock/entradas/{ident}/corregir", json={"palet": a["id"], "ref": "F05182S", "lote": "L1", "kg": 110, "sscc": "LP26-5000001"})
    assert r.status_code == 200, r.text
    det = r.json()["entrada"]
    assert [(l["ref"], l["verificados"]) for l in det["lineas"]] == [("F05182S", 2)]   # la referencia mal escrita ya no sale
    assert all(p["linea"] == 0 and p["estado"] == "ok" for p in det["palets"]) and det["palets"][0]["kg"] == 110
    cierre = _cerrar(cliente, ident, {}).json()
    assert cierre["entrada"]["estado"] == "cerrada"
    assert sorted(p["ref"] for p in cliente.get("/api/stock/stock").json()["palets"]) == ["F05182S", "F05182S"]


def test_palet_sin_codigo_solo_en_inventario_y_con_ubicacion(cliente):
    ident = cliente.post("/api/stock/inventario", json={"cliente": "ALIPLAST", "fecha_entrada": "2026-11-01"}).json()["id"]
    alta = lambda **kw: cliente.post(f"/api/stock/entradas/{ident}/palet", json={"ref": "VARIOS", "lote": "L1", "kg": 50, **kw})
    sin_ubic = alta(sscc="SIN CODIGO").json()["palet"]
    assert sin_ubic["estado"] == "discrepancia" and "ubicación" in sin_ubic["motivo"]   # sin ubicación no se acepta
    # se corrige poniendo la ubicación (la elegida en pantalla) y se acepta
    r = cliente.post(f"/api/stock/entradas/{ident}/corregir", json={"palet": sin_ubic["id"], "ref": "VARIOS", "lote": "L1", "kg": 50, "sscc": "", "sin_codigo": True, "ubicacion": "S7"})
    p = r.json()["palet"]
    assert p["estado"] == "ok" and p["sin_codigo"] and p["ubicacion"] == "S7" and p["sscc"].startswith("SCI")
    # directamente con ubicación
    q = alta(sin_codigo=True, ubicacion="E6").json()["palet"]
    assert q["estado"] == "ok" and q["sin_codigo"] and q["sscc"] != p["sscc"]
    assert alta(sscc="sin código", ubicacion="E6").json()["palet"]["sin_codigo"]   # escribirlo también vale
    # un palet sin nada de código ni «SIN CODIGO» sigue dando aviso
    assert alta(sscc="", ubicacion="E6").json()["palet"]["estado"] == "discrepancia"
    _cerrar(cliente, ident, {"confirmado": True})
    st = [x for x in cliente.get("/api/stock/stock").json()["palets"] if x["sin_codigo"]]
    assert len(st) == 3 and {x["ubicacion"] for x in st} == {"S7", "E6"}
    # en una entrada con albarán no vale
    e = cliente.post("/api/stock/entradas", json={"cliente": "ALIPLAST", "fotos": [FOTO], "albaran": {"numero": "DDT-SC-1", "fecha": "2026-11-18", "total_palets": 1, "total_kg": 50,
                     "lineas": [{"ref": "F05182S", "descripcion": "Film", "palets": 1, "kg": 50}], "dudas": []}}).json()
    r = cliente.post(f"/api/stock/entradas/{e['id']}/palet", json={"sscc": "SIN CODIGO", "ref": "F05182S", "ubicacion": "E1"})
    assert r.status_code == 400 and r.json()["codigo"] == "sin_codigo_no_vale"


def test_albaran_firmado_obligatorio_para_cerrar_y_va_en_el_correo(cliente, monkeypatch, enviados):
    dar_de_alta(cliente)
    leer_con(monkeypatch, etiquetas=[etiqueta("KK-100", SSCC[0], "L77")])
    d = cliente.post("/api/stock/entradas/leer", json={"archivos": [FOTO]}).json()
    ident = cliente.post("/api/stock/entradas", json={"cliente": "KERAKOLL", "albaran": d["albaran"], "fotos": [FOTO]}).json()["id"]
    cliente.post(f"/api/stock/entradas/{ident}/etiqueta", json={"foto": FOTO})
    r = cliente.post(f"/api/stock/entradas/{ident}/cerrar", json={"confirmado": True})
    assert r.status_code == 409 and r.json()["codigo"] == "falta_albaran_firmado"
    assert not enviados
    det = cliente.post(f"/api/stock/entradas/{ident}/albaran-firmado", json={"fotos": [FOTO, FOTO]}).json()
    assert len(det["albaran_firmado"]) == 2 and det["exige_albaran_firmado"] is True
    assert cliente.post(f"/api/stock/entradas/{ident}/cerrar", json={"confirmado": True}).status_code == 200
    env = [x for x in enviados if x["evento"] == "entrada_cerrada"][-1]
    assert sum(1 for f in env["fotos"] if f["nombre"].startswith("albaran firmado")) == 2 and "albarán firmado" in env["html"]


def test_envio_a_n8n_se_parte_si_hay_muchas_fotos(monkeypatch):
    llamadas = []
    monkeypatch.setenv("STOCK_AVISOS_WEBHOOK", "https://n8n.test/hook")
    monkeypatch.setattr(A, "real", lambda: True)
    monkeypatch.setattr(A, "_llamar_n8n", lambda url, datos: llamadas.append(datos) or True)
    grande = "A" * 1_000_000
    fotos = [{"nombre": f"{i:02d} KK-100.jpg", "b64": grande, "tipo": "image/jpeg"} for i in range(1, 21)] + [{"nombre": "albaran 1.jpg", "b64": grande, "tipo": "image/jpeg"}]
    assert A.enviar_n8n({"evento": "entrada_cerrada", "carpeta": "ENTRADA E-1", "archivos": [], "fotos": fotos, "html": "x"}) is True
    assert len(llamadas) > 2 and all(sum(len(f["b64"]) for f in c["fotos"]) <= A.LIMITE_ENVIO for c in llamadas)
    assert llamadas[-1]["evento"] == "entrada_cerrada" and [f["nombre"] for f in llamadas[-1]["fotos"]] == ["albaran 1.jpg"]
    assert all(c["correo"] is False for c in llamadas[:-1]) and sum(len(c["fotos"]) for c in llamadas[:-1]) == 20
