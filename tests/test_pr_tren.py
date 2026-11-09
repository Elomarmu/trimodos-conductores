"""Tren de PR: composición (oficina), asignación de cargas a cajas (oficina/almacén) y las dos Excel."""
import base64
import io
import json

import openpyxl
import pytest

from app.pr import pr_excel as X
from app.pr import pr_odoo as O
from app.pr import pr_reglas as R

CAJAS = [{"orden": 1, "vagon": "388049630055", "segunda": False, "caja_movil": "CRPU0910069", "tara_kg": 5600},
         {"orden": 1, "vagon": "388049630055", "segunda": True, "caja_movil": "CTEA25000377", "tara_kg": 4400}]
VIAJE = {"bulto_id": 11, "bulto": "PK2627659", "pedido": "S26099981", "fecha": "2026-11-09", "tractora": "8734KDW",
         "remolque": "R8896BDF", "colaborador": "JESÚS CLAVIJO LLEBRE", "lugar": "SAICA NATUR - JEREZ", "bultos": 21,
         "peso_kg": 24800, "toneladas": 24.8, "codigo_paper": "S261104001499", "albaran": "592611000006", "tipo": "1.05",
         "ler": "191201", "contenedor": "", "tren": "", "convoy": "", "fecha_tren": "", "vagon": "", "caja_movil": "",
         "comprobaciones": "", "estado": "En stock"}
STOCK = dict(VIAJE, bulto_id=12, bulto="PK2627661", albaran="502611T00064", codigo_paper="S261106002151", peso_kg=None,
             bultos=None, lugar="MERCADONA - HUEVAR")


class OdooTren:
    def __init__(self, con_pedido=True):
        self.adjuntos, self.sig = [], 500
        self.bultos = {11: {"id": 11, "name": "PK2627659", "unload_note": "[PR SAICA]\nAlbarán salida: 592611000006"},
                       12: {"id": 12, "name": "PK2627661", "unload_note": "[PR SAICA]\nAlbarán salida: 502611T00064\nNota a mano"}}
        self.con_pedido = con_pedido

    def __call__(self, modelo, metodo, args, **kw):
        assert (modelo, metodo) in O.PERMITIDO, (modelo, metodo)
        if modelo == "sale.order":
            return [{"id": 7, "name": "S26099981", "locked": False}] if self.con_pedido else []
        if modelo == "ir.attachment" and metodo == "search_read":
            campo, op, valor = args[0][1]
            ok = [a for a in self.adjuntos if (a["name"] == valor if op == "=" else a["name"].startswith("PR_TREN_"))]
            return sorted(ok, key=lambda a: -a["id"])[: kw.get("limit", 99)]
        if modelo == "ir.attachment" and metodo == "create":
            self.sig += 1
            self.adjuntos.append(dict(args[0], id=self.sig))
            return self.sig
        if modelo == "ir.attachment" and metodo == "write":
            next(a for a in self.adjuntos if a["id"] == args[0][0]).update(args[1])
            return True
        if modelo == "tms.package" and metodo == "search_read":
            return [self.bultos[args[0][0][2]]] if args[0][0][2] in self.bultos else []
        if modelo == "tms.package" and metodo == "write":
            self.bultos[args[0][0]].update(args[1])
            return True
        raise AssertionError((modelo, metodo))

    def viajes(self):  # lo que devolvería O.viajes leyendo las observaciones
        res = []
        for v in (VIAJE, STOCK):
            o = R.leer_observaciones(self.bultos[v["bulto_id"]]["unload_note"])
            res.append(dict(v, **{k: o.get(k, "") for k in O.CLAVES_TREN},
                            estado=f"Tren {o['tren']}" if o.get("caja_movil") else "En stock"))
        return res


def test_composicion_se_guarda_en_odoo_y_se_sustituye():
    odoo = OdooTren()
    O.guardar_composicion(odoo, 86, "90107", "2026-11-09", CAJAS)
    O.guardar_composicion(odoo, 86, "90107", "2026-11-09", CAJAS[:1])   # se vuelve a cargar: se sustituye
    assert len(odoo.adjuntos) == 1 and odoo.adjuntos[0]["name"] == "PR_TREN_86.json" and odoo.adjuntos[0]["res_id"] == 7
    t = O.tren_actual(odoo)
    assert t["tren"] == 86 and t["convoy"] == "90107" and len(t["cajas"]) == 1
    with pytest.raises(O.ErrorPR, match="ningún pedido"):
        O.guardar_composicion(OdooTren(con_pedido=False), 86, "", "2026-11-09", CAJAS)


def test_asignar_quitar_y_errores():
    odoo = OdooTren()
    t = O.guardar_composicion(odoo, 86, "90107", "2026-11-09", CAJAS)
    assert O.asignar(odoo, t, odoo.viajes(), 11, "CRPU0910069") == "PK2627659"
    obs = R.leer_observaciones(odoo.bultos[11]["unload_note"])
    assert obs["tren"] == "86" and obs["vagon"] == "388049630055" and obs["caja_movil"] == "CRPU0910069"
    assert obs["comprobaciones"] == "Dist. balas OK, Lonas OK, Puertas OK, Precinto OK"
    e = O.estado_tren(odoo, t, odoo.viajes())
    assert e["cajas"][0]["carga"]["codigo_paper"] == "S261104001499" and e["cajas"][1]["carga"] is None
    assert [c["bulto_id"] for c in e["stock"]] == [12]
    with pytest.raises(O.ErrorPR, match="ya lleva la carga S261104001499"):
        O.asignar(odoo, t, odoo.viajes(), 12, "CRPU0910069")
    with pytest.raises(O.ErrorPR, match="ya va en el tren 86"):
        O.asignar(odoo, t, odoo.viajes(), 11, "CTEA25000377")
    with pytest.raises(O.ErrorPR, match="no está en el tren"):
        O.asignar(odoo, t, odoo.viajes(), 12, "ZZZZ0000000")
    O.quitar(odoo, 11)                                       # vuelve a stock
    assert "Tren" not in odoo.bultos[11]["unload_note"] and "Albarán salida: 592611000006" in odoo.bultos[11]["unload_note"]
    O.asignar(odoo, t, odoo.viajes(), 12, "CTEA25000377")
    assert "Nota a mano" in odoo.bultos[12]["unload_note"]   # lo escrito a mano se respeta


def test_comprobaciones_ida_y_vuelta():
    t = R.texto_comprobaciones({"dist_balas": True, "lonas": True, "puertas": False, "precinto": False})
    assert R.leer_comprobaciones(t) == {"dist_balas": "OK", "lonas": "OK", "puertas": "NO", "precinto": "NO"}


def test_siguiente_tren(monkeypatch):
    monkeypatch.delenv("PR_ULTIMO_TREN", raising=False)
    assert O.siguiente_tren([]) == 86
    assert O.siguiente_tren([{"tren": "86"}, {"tren": "87"}, {"tren": ""}]) == 88


def _abrir(datos: bytes):
    return openpyxl.load_workbook(io.BytesIO(datos)).active


def test_excel_carton_paja_tren_siempre_ok():
    ws = _abrir(X.carton_paja_tren(86, "90107", "2026-11-09", CAJAS, {"CRPU0910069": VIAJE}))
    assert ws["Q1"].value == 86 and ws["Q2"].value == "90107"
    fila = [c.value for c in ws[6]]
    assert fila[0] == 1 and fila[2] == "8734KDW" and fila[6] == "S261104001499" and fila[9] == "CRPU0910069"
    assert fila[14:18] == ["OK", "OK", "OK", "OK"]
    vacia = [c.value for c in ws[7]]
    assert vacia[0] == "“" and vacia[9] == "CTEA25000377" and vacia[2] is None and vacia[14] is None


def test_excel_registro():
    ws = _abrir(X.registro([dict(VIAJE, estado="Tren 86", tren="86", caja_movil="CRPU0910069"), STOCK]))
    cab = [c.value for c in ws[4]]
    assert ws.cell(5, 2).value == "Tren 86" and ws.cell(6, 2).value == "En stock"
    assert ws.cell(6, len(cab)).value == "Falta: peso, balas"


def test_libro_del_tren_con_hoja_composicion():
    libro = openpyxl.load_workbook(io.BytesIO(X.carton_paja_tren(86, "90107", "2026-11-09", CAJAS, {})))
    assert libro.sheetnames == ["TREN 86", "COMPOSICION"]
    ws = libro["COMPOSICION"]
    assert ws["A1"].value == "COMPOSICIÓN DEL TREN 86" and "09-11-2026" in ws["A2"].value
    assert [c.value for c in ws[5]] == [1, "388049630055", "CRPU0910069", 5600]
    assert [c.value for c in ws[6]] == ["“", "“", "CTEA25000377", 4400]
    assert all(libro["TREN 86"].cell(f, 3).value is None for f in (6, 7))   # recién cargado: cajas vacías


def test_composicion_repetida():
    odoo = OdooTren()
    O.guardar_composicion(odoo, 86, "90107", "2026-11-09", CAJAS)
    assert "El tren 86 ya está cargado" in O.composiciones_repetidas(odoo, 86, "", CAJAS)[0]
    assert "El convoy 90107 ya se cargó como tren 86" in O.composiciones_repetidas(odoo, 87, "90107", CAJAS[:1])[0]
    assert "coinciden (2 de 2) con el tren 86" in O.composiciones_repetidas(odoo, 87, "90200", CAJAS)[0]
    otras = [dict(c, caja_movil=c["caja_movil"] + "X") for c in CAJAS]
    assert O.composiciones_repetidas(odoo, 87, "90200", otras) == []          # tren nuevo de verdad: sin aviso


def test_api_composicion_repetida_pide_confirmar(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.pr import pr_api as A
    odoo = OdooTren()
    O.guardar_composicion(odoo, 86, "90107", "2026-11-09", CAJAS)
    monkeypatch.setenv("ODOO_MODE", "mock")
    monkeypatch.setenv("APP_ACCESS_CODE", "")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setattr(A, "odoo_pr", lambda: odoo)
    cuerpo = {"tren": 87, "convoy": "90107", "fecha": "2026-11-10", "cajas": CAJAS}
    r = TestClient(app).post("/api/pr/tren/composicion", json=cuerpo)
    assert r.status_code == 409 and r.json()["codigo"] == "tren_repetido" and "¿Quieres guardarla igualmente?" in r.json()["error"]
    r = TestClient(app).post("/api/pr/tren/composicion", json=dict(cuerpo, confirmado=True))
    assert r.status_code == 200
