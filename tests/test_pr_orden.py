"""Orden del tren: el vagón que empieza a cargarse va arriba, la 2.ª caja se suma sin moverlo, subir/bajar, corregir
caja y vagón, y la Excel en el orden del tren. También el correo de la mañana."""
import datetime as dt
import io

import openpyxl
import pytest

from app.pr import pr_api as A
from app.pr import pr_excel as X
from app.pr import pr_odoo as O
from app.pr import pr_reglas as R
from tests.test_pr_tren import STOCK, VIAJE, OdooTren

V1, V2, V3 = "388049630055", "318045960016", "318045960024"
CAJAS = [{"orden": 1, "vagon": V1, "segunda": False, "caja_movil": "CRPU0910069", "tara_kg": 5600},
         {"orden": 1, "vagon": V1, "segunda": True, "caja_movil": "CTEA25000377", "tara_kg": 4400},
         {"orden": 2, "vagon": V2, "segunda": False, "caja_movil": "ICR102043", "tara_kg": 5900},
         {"orden": 3, "vagon": V3, "segunda": False, "caja_movil": "MXMU1450334", "tara_kg": 5600}]


class OdooOrden(OdooTren):
    def __init__(self):
        super().__init__()
        for i in (13, 14):
            self.bultos[i] = {"id": i, "name": f"PK00{i}", "unload_note": f"[PR SAICA]\nAlbarán salida: ALB{i}"}

    def viajes(self):
        res = []
        for v in (VIAJE, STOCK, dict(STOCK, bulto_id=13, bulto="PK0013", albaran="ALB13"),
                  dict(STOCK, bulto_id=14, bulto="PK0014", albaran="ALB14")):
            o = R.leer_observaciones(self.bultos[v["bulto_id"]]["unload_note"])
            res.append(dict(v, **{k: o.get(k, "") for k in O.CLAVES_TREN},
                            estado=f"Tren {o['tren']}" if o.get("caja_movil") else "En stock"))
        return res


def _cargar(odoo, bulto, caja):
    O.asignar(odoo, O.tren_actual(odoo), odoo.viajes(), bulto, caja)
    return O.tren_actual(odoo)


def test_vagon_nuevo_arriba_y_segunda_caja_sin_mover():
    odoo = OdooOrden()
    O.guardar_composicion(odoo, 86, "90107", "2026-11-09", CAJAS)
    t = _cargar(odoo, 11, "MXMU1450334")           # se carga de atrás hacia delante
    assert t["orden"] == [V3]
    t = _cargar(odoo, 12, "CRPU0910069")
    assert t["orden"] == [V1, V3]
    t = _cargar(odoo, 13, "ICR102043")
    assert t["orden"] == [V2, V1, V3]
    t = _cargar(odoo, 14, "CTEA25000377")          # 2.ª caja de V1: se suma a su vagón sin subirlo
    assert t["orden"] == [V2, V1, V3]
    e = O.estado_tren(odoo, t, odoo.viajes())
    assert [g["vagon"] for g in e["grupos"]] == [V2, V1, V3]
    assert [c["caja_movil"] for c in e["grupos"][1]["cajas"]] == ["CRPU0910069", "CTEA25000377"]
    assert [c["caja_movil"] for c in e["cajas"]][:3] == ["ICR102043", "CRPU0910069", "CTEA25000377"]


def test_quitar_saca_el_vagon_solo_si_se_queda_vacio():
    odoo = OdooOrden()
    O.guardar_composicion(odoo, 86, "90107", "2026-11-09", CAJAS)
    _cargar(odoo, 11, "CRPU0910069")
    t = _cargar(odoo, 12, "CTEA25000377")
    O.quitar(odoo, 11, t, odoo.viajes())
    assert O.tren_actual(odoo)["orden"] == [V1]
    t = O.tren_actual(odoo)
    O.quitar(odoo, 12, t, odoo.viajes())
    assert O.tren_actual(odoo)["orden"] == []


def test_mover_y_excel_en_orden_con_vacios_al_final():
    odoo = OdooOrden()
    O.guardar_composicion(odoo, 86, "90107", "2026-11-09", CAJAS)
    _cargar(odoo, 11, "MXMU1450334")
    t = _cargar(odoo, 12, "CRPU0910069")            # orden: V1, V3
    O.mover_vagon(odoo, t, V3, "subir")
    t = O.tren_actual(odoo)
    assert t["orden"] == [V3, V1]
    O.mover_vagon(odoo, t, V3, "subir")             # ya está arriba: no cambia
    assert O.tren_actual(odoo)["orden"] == [V3, V1]
    with pytest.raises(O.ErrorPR, match="ninguna caja asignada"):
        O.mover_vagon(odoo, t, V2, "bajar")
    cajas = O.cajas_en_orden(O.tren_actual(odoo))
    assert [(c["ord"], c["caja_movil"], c["segunda"]) for c in cajas] == [
        (1, "MXMU1450334", False), (2, "CRPU0910069", False), (2, "CTEA25000377", True), (3, "ICR102043", False)]
    lista = odoo.viajes()
    por_caja = {v["caja_movil"]: v for v in lista if v.get("caja_movil")}
    ws = openpyxl.load_workbook(io.BytesIO(X.carton_paja_tren(86, "90107", "2026-11-09", cajas, por_caja)))["TREN 86"]
    assert [ws.cell(f, 10).value for f in range(6, 10)] == ["MXMU1450334", "CRPU0910069", "CTEA25000377", "ICR102043"]
    assert [ws.cell(f, 1).value for f in range(6, 10)] == [1, 2, "“", 3]


def test_editar_caja_y_vagon():
    odoo = OdooOrden()
    O.guardar_composicion(odoo, 86, "90107", "2026-11-09", CAJAS)
    t = _cargar(odoo, 11, "CRPU0910069")
    assert O.editar(odoo, t, odoo.viajes(), "CRPU0910069", nueva_caja="crpu 0910070") == [11]
    t = O.tren_actual(odoo)
    assert "CRPU0910070" in [c["caja_movil"] for c in t["cajas"]]
    assert R.leer_observaciones(odoo.bultos[11]["unload_note"])["caja_movil"] == "CRPU0910070"
    nuevo = "388049630063"
    cifras, ok = R.vagon_excel(nuevo)
    if not ok:   # se busca un número con el dígito de control bueno
        nuevo = next(n for n in (f"38804963006{d}" for d in range(10)) if R.vagon_excel(n)[1])
    O.editar(odoo, t, odoo.viajes(), "CTEA25000377", nuevo_vagon=nuevo)
    t = O.tren_actual(odoo)
    assert [c["vagon"] for c in t["cajas"]][:2] == [nuevo, nuevo] and t["orden"] == [nuevo]
    assert R.leer_observaciones(odoo.bultos[11]["unload_note"])["vagon"] == nuevo
    with pytest.raises(O.ErrorPR, match="ya está en este tren"):
        O.editar(odoo, t, odoo.viajes(), "CRPU0910070", nueva_caja="ICR102043")
    with pytest.raises(O.ErrorPR, match="ya está en este tren"):
        O.editar(odoo, t, odoo.viajes(), "CRPU0910070", nuevo_vagon=V2)
    with pytest.raises(O.ErrorPR, match="12 cifras"):
        O.editar(odoo, t, odoo.viajes(), "CRPU0910070", nuevo_vagon="1234")


def test_volver_a_cargar_la_composicion_conserva_el_orden():
    odoo = OdooOrden()
    O.guardar_composicion(odoo, 86, "90107", "2026-11-09", CAJAS)
    _cargar(odoo, 11, "MXMU1450334")
    _cargar(odoo, 12, "CRPU0910069")
    O.guardar_composicion(odoo, 86, "90107", "2026-11-09", CAJAS[:3])   # sin el vagón V3
    assert O.tren_actual(odoo)["orden"] == [V1]


def test_dias_del_resumen():
    assert A.dias_resumen(dt.date(2026, 11, 10)) == (dt.date(2026, 11, 9), dt.date(2026, 11, 9))    # martes
    assert A.dias_resumen(dt.date(2026, 11, 9)) == (dt.date(2026, 11, 7), dt.date(2026, 11, 8))     # lunes: sáb y dom
    assert R.fecha_madrid("2026-11-09 23:30:00") == "2026-11-10" and R.fecha_madrid(None) == ""


def test_correo_de_la_manana(monkeypatch):
    odoo = OdooOrden()
    O.guardar_composicion(odoo, 86, "90107", "2026-11-09", CAJAS)
    _cargar(odoo, 11, "CRPU0910069")
    lista = [dict(v, pedido_id=7, tarea_id=100 + v["bulto_id"], registrado="2026-11-09" if v["bulto_id"] in (11, 13) else "2026-11-05")
             for v in odoo.viajes()]
    monkeypatch.setattr(A, "odoo_pr", lambda: odoo)
    monkeypatch.setattr(O, "viajes", lambda o, desde=None: lista)
    monkeypatch.setattr(A, "enlace_odoo", lambda m, i: f"https://odoo/web#id={i}&model={m}" if i else "")
    r = A.resumen(hoy="2026-11-10")
    h = r["html"]
    assert r["excel_tren"] == "CARTON PAJA TREN 86/CARTON PAJA TREN 86.xlsx" and A.URL_EXCEL_TREN in h
    assert "Pedido S26099981" in h and "id=7&amp;model=sale.order" in h
    assert "llegaron a stock de la nave (09/11): 2" in h
    assert "Viajes cargados en el tren 86 (convoy 90107) · cargando: 1" in h   # todas las del tren, sin mirar fechas
    assert "quedan en stock: 3" in h
    assert "id=111&amp;model=project.task" in h and ">592611000006</a>" in h
    assert "<script" not in h


def test_caja_vacia_tiene_su_sitio_en_el_orden():
    odoo = OdooOrden()
    O.guardar_composicion(odoo, 86, "90107", "2026-11-09", CAJAS)
    _cargar(odoo, 11, "MXMU1450334")                                   # V3 (cola)
    O.marcar_vacia(odoo, O.tren_actual(odoo), odoo.viajes(), "ICR102043")   # V2 va vacío
    t = O.tren_actual(odoo)
    assert t["orden"] == [V2, V3] and t["vacias"] == ["ICR102043"]
    with pytest.raises(O.ErrorPR, match="ya está marcada"):
        O.marcar_vacia(odoo, t, odoo.viajes(), "ICR102043")
    with pytest.raises(O.ErrorPR, match="ya lleva la carga"):
        O.marcar_vacia(odoo, t, odoo.viajes(), "MXMU1450334")
    e = O.estado_tren(odoo, t, odoo.viajes())
    assert [g["vagon"] for g in e["grupos"]] == [V2, V3] and e["grupos"][0]["cajas"][0]["vacia"]
    cajas = O.cajas_en_orden(t)
    ws = openpyxl.load_workbook(io.BytesIO(X.carton_paja_tren(86, "", "2026-11-09", cajas,
                                                              {v["caja_movil"]: v for v in odoo.viajes() if v["caja_movil"]})))["TREN 86"]
    assert ws.cell(6, 10).value == "ICR102043" and ws.cell(6, 6).value == "CAJA VACÍA"
    # si después sí se carga, deja de estar vacía y su vagón no se mueve
    t = _cargar(odoo, 12, "ICR102043")
    assert t["vacias"] == [] and t["orden"] == [V2, V3]
    # quitar la vacía saca el vagón si no lleva nada más
    O.marcar_vacia(odoo, t, odoo.viajes(), "CRPU0910069")
    t = O.tren_actual(odoo)
    assert t["orden"] == [V1, V2, V3]
    O.quitar_vacia(odoo, t, odoo.viajes(), "CRPU0910069")
    assert O.tren_actual(odoo)["orden"] == [V2, V3]
    # una carga quitada de un vagón que tiene otra caja vacía: el vagón sigue
    O.marcar_vacia(odoo, O.tren_actual(odoo), odoo.viajes(), "CTEA25000377")
    t = _cargar(odoo, 13, "CRPU0910069")
    O.quitar(odoo, 13, t, odoo.viajes())
    assert V1 in O.tren_actual(odoo)["orden"]


def test_el_registro_se_sube_al_asignar_y_quitar(monkeypatch):
    odoo = OdooOrden()
    O.guardar_composicion(odoo, 86, "90107", "2026-11-09", CAJAS)
    enviados = []
    monkeypatch.setattr(A, "odoo_pr", lambda: odoo)
    monkeypatch.setattr(O, "viajes", lambda o, desde=None: o.viajes())
    monkeypatch.setattr(A, "sincronizar_foto", lambda *a, **k: None)
    monkeypatch.setattr(A, "enviar_n8n", lambda d: enviados.append(d) or True)
    monkeypatch.setattr(A, "get_settings", lambda: type("S", (), {"dry_run": False, "odoo_url": ""})())
    A.tren_asignar(A.Asignar(bulto_id=11, caja_movil="CRPU0910069"))
    A.tren_quitar(A.Asignar(bulto_id=11, caja_movil="CRPU0910069"))
    assert [(d["evento"], d["correo"], d["archivos"][0]["nombre"]) for d in enviados] == [
        ("composicion", False, "CARTON PAJA TREN 86.xlsx"), ("registro", False, "REGISTRO VIAJES PR.xlsx")] * 2
    monkeypatch.setattr(O, "viajes", lambda o, desde=None: (_ for _ in ()).throw(RuntimeError("sin Odoo")))
    A.subir_registro(odoo)   # nunca para el trabajo


def test_correo_de_pedido_nuevo_con_enlace(monkeypatch):
    enviados = []
    monkeypatch.setattr(A, "enviar_n8n", lambda d: enviados.append(d) or True)
    monkeypatch.setattr(A, "enlace_odoo", lambda m, i: f"https://odoo/web#id={i}&model={m}&view_type=form" if i else "")
    A.avisar("pedido_nuevo", {"pedido": "S26100001", "referencia": "PR DICIEMBRE"},
             [("Pedido S26100001", A.enlace_odoo("sale.order", 77)), ("Primer viaje", A.enlace_odoo("project.task", None))])
    h = enviados[0]["html"]
    assert "href='https://odoo/web#id=77&amp;model=sale.order&amp;view_type=form'" in h and ">Pedido S26100001</a>" in h
    assert "Primer viaje" not in h            # sin tarea no sale el botón
    A.avisar("punto_nuevo", {"punto": "X", "albaran": "1", "fecha": "2026-11-10"})
    assert "<a " not in enviados[1]["html"]


def test_convoy_de_salida():
    odoo = OdooOrden()
    O.guardar_composicion(odoo, 86, "90107", "2026-11-09", CAJAS)
    t = _cargar(odoo, 11, "CRPU0910069")
    assert O.convoy_tren(t) == "90107"                                         # sin salida: el de entrada
    assert R.leer_observaciones(odoo.bultos[11]["unload_note"])["convoy"] == "90107"
    assert O.cambiar_salida(odoo, t, odoo.viajes(), "  91234 ") == [11]        # se cambia también en las cargas
    t = O.tren_actual(odoo)
    assert t["convoy"] == "90107" and t["convoy_salida"] == "91234" and O.convoy_tren(t) == "91234"
    assert R.leer_observaciones(odoo.bultos[11]["unload_note"])["convoy"] == "91234"
    t = _cargar(odoo, 12, "CTEA25000377")                                       # las nuevas ya van con el de salida
    assert R.leer_observaciones(odoo.bultos[12]["unload_note"])["convoy"] == "91234"
    e = O.estado_tren(odoo, t, odoo.viajes())
    assert e["convoy"] == "90107" and e["convoy_salida"] == "91234"
    ws = openpyxl.load_workbook(io.BytesIO(X.carton_paja_tren(86, "91234", "2026-11-09", O.cajas_en_orden(t), {}, "90107")))
    assert ws["TREN 86"]["Q2"].value == "91234" and "de entrada: 90107" in ws["COMPOSICION"]["A2"].value



def test_tren_terminado_bloqueado_hasta_modificar(monkeypatch):
    odoo = OdooOrden()
    O.guardar_composicion(odoo, 86, "90107", "2026-11-09", CAJAS)
    monkeypatch.setattr(A, "odoo_pr", lambda: odoo)
    monkeypatch.setattr(O, "viajes", lambda o, desde=None: o.viajes())
    monkeypatch.setattr(A, "sincronizar_foto", lambda *a, **k: None)
    monkeypatch.setattr(A, "leer_foto_o_nada", lambda *a, **k: None)
    monkeypatch.setattr(A, "enviar_n8n", lambda d: True)
    monkeypatch.setattr(A, "get_settings", lambda: type("S", (), {"dry_run": False, "odoo_url": ""})())
    A.tren_asignar(A.Asignar(bulto_id=11, caja_movil="CRPU0910069"))
    A.tren_enviar()
    assert O.estado_tren(odoo, O.tren_actual(odoo), odoo.viajes())["terminado"]
    with pytest.raises(A.AppError, match="está terminado"):
        A.tren_asignar(A.Asignar(bulto_id=12, caja_movil="CTEA25000377"))
    with pytest.raises(A.AppError, match="está terminado"):
        A.tren_mover(A.Mover(vagon=V1, hacia="bajar"))
    e = A.tren_reabrir()                                   # «Modificar el tren»
    assert not e["terminado"]
    A.tren_asignar(A.Asignar(bulto_id=12, caja_movil="CTEA25000377"))


def test_no_se_carga_otra_composicion_con_el_tren_abierto(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    odoo = OdooOrden()
    O.guardar_composicion(odoo, 86, "90107", "2026-11-09", CAJAS)
    monkeypatch.setenv("ODOO_MODE", "mock")
    monkeypatch.setenv("APP_ACCESS_CODE", "")
    monkeypatch.setattr(A, "odoo_pr", lambda: odoo)
    r = TestClient(app).post("/api/pr/tren/composicion", json={"tren": 87, "convoy": "1", "fecha": "2026-11-12", "cajas": CAJAS})
    assert r.status_code == 409 and r.json()["codigo"] == "tren_abierto" and "El tren 86 todavía no está terminado" in r.json()["error"]


def test_volver_a_cargar_la_composicion_mantiene_las_cargas_y_saca_las_de_cajas_que_ya_no_estan(monkeypatch):
    odoo = OdooOrden()
    O.guardar_composicion(odoo, 86, "90107", "2026-11-09", CAJAS)
    _cargar(odoo, 11, "CRPU0910069")
    _cargar(odoo, 12, "MXMU1450334")
    enviados, borrados = [], []
    monkeypatch.setattr(A, "odoo_pr", lambda: odoo)
    monkeypatch.setattr(O, "viajes", lambda o, desde=None: o.viajes())
    monkeypatch.setattr(A, "enviar_n8n", lambda d: enviados.append(d) or True)
    monkeypatch.setattr(A, "borrar_foto", lambda ruta: borrados.append(ruta))
    monkeypatch.setattr(A, "get_settings", lambda: type("S", (), {"dry_run": False, "odoo_url": ""})())
    sin_v3 = [A.CajaTren(**c) for c in CAJAS[:3]]                    # la caja MXMU1450334 ya no está
    r = A.tren_composicion(A.TrenIn(tren=86, convoy="90107", fecha="2026-11-11", cajas=sin_v3, confirmado=True))
    assert r["mantenidas"] == 1 and r["a_stock"] == ["S261106002151"]
    assert "Caja móvil" not in odoo.bultos[12]["unload_note"]                       # vuelve a stock
    assert R.leer_observaciones(odoo.bultos[11]["unload_note"])["caja_movil"] == "CRPU0910069"
    libro = openpyxl.load_workbook(io.BytesIO(__import__("base64").b64decode(
        next(d for d in enviados if d["evento"] == "composicion")["archivos"][0]["b64"])))["TREN 86"]
    assert any(libro.cell(f, 10).value == "CRPU0910069" and libro.cell(f, 7).value for f in range(6, 10))   # la Excel la lleva
