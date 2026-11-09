"""Almacén: número de tren, asignación de viajes a cajas y las dos Excel."""
import io

import openpyxl
import pytest

from app.pr import pr_excel as X
from app.pr import pr_odoo as O
from app.pr import pr_reglas as R

VIAJE = {"bulto_id": 11, "bulto": "PK2627659", "pedido": "S26099981", "fecha": "2026-11-09", "tractora": "8734KDW",
         "remolque": "R8896BDF", "colaborador": "JESÚS CLAVIJO LLEBRE", "lugar": "SAICA NATUR - JEREZ", "bultos": 21,
         "peso_kg": 24800, "toneladas": 24.8, "codigo_paper": "S261104001499", "albaran": "592611000006", "tipo": "1.05",
         "ler": "191201", "contenedor": "", "tren": "", "convoy": "", "fecha_tren": "", "vagon": "", "caja_movil": "",
         "comprobaciones": "", "estado": "En stock"}
STOCK = dict(VIAJE, bulto_id=12, bulto="PK2627661", albaran="502611T00064", codigo_paper="", peso_kg=None, bultos=None,
             lugar="MERCADONA - HUEVAR")
CAJAS = [{"orden": 1, "vagon": "388049630055", "segunda": False, "caja_movil": "CRPU0910069", "tara_kg": 5600, "bulto_id": 11,
          "comprobaciones": {"dist_balas": True, "lonas": True, "puertas": True, "precinto": False}},
         {"orden": 1, "vagon": "388049630055", "segunda": True, "caja_movil": "CTEA25000377", "tara_kg": 4400}]


def test_comprobaciones_ida_y_vuelta():
    t = R.texto_comprobaciones({"dist_balas": True, "lonas": True, "puertas": False, "precinto": False})
    assert t == "Dist. balas OK, Lonas OK, Puertas NO, Precinto NO"
    assert R.leer_comprobaciones(t) == {"dist_balas": "OK", "lonas": "OK", "puertas": "NO", "precinto": "NO"}


def test_siguiente_tren(monkeypatch):
    monkeypatch.delenv("PR_ULTIMO_TREN", raising=False)
    assert O.siguiente_tren([]) == 86                       # el último hecho a mano fue el 85
    assert O.siguiente_tren([{"tren": "86"}, {"tren": "87"}, {"tren": ""}]) == 88


class OdooBultos:
    def __init__(self):
        self.bultos = {11: {"id": 11, "name": "PK2627659", "unload_note": "[PR SAICA]\nAlbarán salida: 592611000006"},
                       12: {"id": 12, "name": "PK2627661", "unload_note": "[PR SAICA]\nAlbarán salida: 502611T00064"}}

    def __call__(self, modelo, metodo, args, **kw):
        assert (modelo, metodo) in O.PERMITIDO
        if metodo == "search_read":
            return [self.bultos[i] for i in args[0][0][2] if i in self.bultos]
        if metodo == "write":
            self.bultos[args[0][0]].update(args[1])
            return True
        raise AssertionError(metodo)


def test_guardar_tren_en_observaciones():
    odoo = OdooBultos()
    assert O.guardar_tren(odoo, 86, "90107", "2026-11-09", CAJAS) == ["PK2627659"]
    obs = R.leer_observaciones(odoo.bultos[11]["unload_note"])
    assert obs == {"albaran": "592611000006", "tren": "86", "convoy": "90107", "fecha_tren": "2026-11-09",
                   "vagon": "388049630055", "caja_movil": "CRPU0910069",
                   "comprobaciones": "Dist. balas OK, Lonas OK, Puertas OK, Precinto NO"}
    assert "Tren" not in odoo.bultos[12]["unload_note"]      # el que no va en el tren sigue en stock
    with pytest.raises(O.ErrorPR, match="dos cajas"):
        O.guardar_tren(odoo, 86, "", "2026-11-09", [CAJAS[0], dict(CAJAS[1], bulto_id=11)])
    with pytest.raises(O.ErrorPR, match="ningún viaje"):
        O.guardar_tren(odoo, 86, "", "2026-11-09", [CAJAS[1]])


def _abrir(datos: bytes):
    return openpyxl.load_workbook(io.BytesIO(datos)).active


def test_excel_carton_paja_tren():
    ws = _abrir(X.carton_paja_tren(86, "90107", "2026-11-09", CAJAS, {11: VIAJE}))
    assert ws["Q1"].value == 86 and ws["Q2"].value == "90107"
    assert [c.value for c in ws[5]][:10] == ["ord", "Fecha", "Matrícula", "Balas", "Peso", "Lugar de carga", "ORDEN",
                                              "Nº Viaje", "Vagón", "Caja móvil"]
    fila = [c.value for c in ws[6]]
    assert fila[0] == 1 and fila[2] == "8734KDW" and fila[4] == 24800 and fila[6] == "S261104001499"
    assert fila[7] == "592611000006" and fila[8] == "388049630055" and fila[9] == "CRPU0910069" and fila[10] == 5600
    assert fila[11] == "1.05" and fila[14:18] == ["OK", "OK", "OK", "NO"]
    vacia = [c.value for c in ws[7]]
    assert vacia[0] == "“" and vacia[8] == "“" and vacia[9] == "CTEA25000377" and vacia[2] is None and vacia[14] is None
    assert "cargadas: 1" in ws.cell(9, 1).value and "vacías: 1" in ws.cell(9, 1).value


def test_excel_registro():
    cargado = dict(VIAJE, estado="Tren 86", tren="86", caja_movil="CRPU0910069")
    ws = _abrir(X.registro([cargado, STOCK]))
    cab = [c.value for c in ws[4]]
    assert cab[:3] == ["Fecha carga", "Estado", "Nº albarán"] and cab[-1] == "Avisos"
    assert ws.cell(5, 2).value == "Tren 86" and ws.cell(6, 2).value == "En stock"
    assert ws.cell(6, len(cab)).value == "Falta: código Paper, peso, balas"
    assert "1 en stock" in ws["A2"].value
