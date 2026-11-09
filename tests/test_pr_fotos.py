"""Foto del albarán: Odoo → SharePoint › Automatizacion › CARTON PAJA TREN <n> con el nombre «código Paper - caja»."""
import pytest

from app.pr import pr_api as A
from app.pr import pr_odoo as O

BASE = {"bulto_id": 11, "bulto": "PK1", "tarea_id": 204234, "codigo_paper": "S261104001499", "albaran": "592611000006",
        "tren": "86", "caja_movil": "CRPU0910069", "foto": ""}


@pytest.fixture
def eventos(monkeypatch):
    enviados, apuntes = [], {}
    monkeypatch.setattr(A, "enviar_n8n", lambda d: enviados.append(d) or True)
    monkeypatch.setattr(O, "apuntar_foto", lambda odoo, bulto, ruta: apuntes.__setitem__(bulto, ruta))
    monkeypatch.setattr(O, "leer_foto", lambda odoo, tarea: {"b64": "QUJD", "tipo": "image/jpeg"} if tarea else None)
    return enviados, apuntes


def test_al_asignar_se_sube_con_su_nombre(eventos):
    enviados, apuntes = eventos
    A.sincronizar_foto(None, dict(BASE), lista=[dict(BASE)])
    assert enviados == [{"evento": "foto_subir", "correo": False, "carpeta": "CARTON PAJA TREN 86",
                         "nombre": "S261104001499 - CRPU0910069.jpg", "tipo": "image/jpeg", "b64": "QUJD"}]
    assert apuntes[11] == "CARTON PAJA TREN 86/S261104001499 - CRPU0910069.jpg"


def test_nombre_repetido_en_la_carpeta(eventos):
    enviados, _ = eventos
    otro = dict(BASE, bulto_id=12, foto="CARTON PAJA TREN 86/ALBARAN 26371 - ICR102043.jpg")
    v = dict(BASE, codigo_paper="", albaran="26371", caja_movil="ICR102043")
    A.sincronizar_foto(None, v, lista=[v, otro])
    assert enviados[0]["nombre"] == "ALBARAN 26371 - ICR102043 (2).jpg"


def test_cambia_el_codigo_paper_se_renombra(eventos):
    enviados, apuntes = eventos
    v = dict(BASE, codigo_paper="S261104001502", foto="CARTON PAJA TREN 86/ALBARAN 592611000006 - CRPU0910069.jpg")
    A.sincronizar_foto(None, v, lista=[v])
    assert enviados == [{"evento": "foto_renombrar", "correo": False, "carpeta": "CARTON PAJA TREN 86",
                         "viejo": "ALBARAN 592611000006 - CRPU0910069.jpg", "nuevo": "S261104001502 - CRPU0910069.jpg"}]
    assert apuntes[11].endswith("S261104001502 - CRPU0910069.jpg")


def test_sin_cambios_no_se_toca(eventos):
    enviados, _ = eventos
    v = dict(BASE, foto="CARTON PAJA TREN 86/S261104001499 - CRPU0910069.jpg")
    A.sincronizar_foto(None, v, lista=[v])
    assert enviados == []


def test_foto_nueva_sustituye(eventos):
    enviados, _ = eventos
    v = dict(BASE, foto="CARTON PAJA TREN 86/S261104001499 - CRPU0910069.jpg")
    A.sincronizar_foto(None, v, foto_nueva=True, lista=[v])
    assert [e["evento"] for e in enviados] == ["foto_subir"] and enviados[0]["nombre"] == "S261104001499 - CRPU0910069.jpg"


def test_cambia_codigo_y_foto_nueva_borra_la_vieja(eventos):
    enviados, _ = eventos
    v = dict(BASE, codigo_paper="S261104001502", foto="CARTON PAJA TREN 86/ALBARAN 592611000006 - CRPU0910069.jpg")
    A.sincronizar_foto(None, v, foto_nueva=True, lista=[v])
    assert [e["evento"] for e in enviados] == ["foto_borrar", "foto_subir"]
    assert enviados[0]["nombre"] == "ALBARAN 592611000006 - CRPU0910069.jpg"


def test_viaje_sin_foto_no_hace_nada(eventos, monkeypatch):
    enviados, apuntes = eventos
    monkeypatch.setattr(O, "leer_foto", lambda odoo, tarea: None)
    A.sincronizar_foto(None, dict(BASE), lista=[dict(BASE)])
    assert enviados == [] and apuntes == {}


def test_guardar_foto_en_la_tarea():
    llamadas = []

    def odoo(modelo, metodo, args, **kw):
        assert (modelo, metodo) in O.PERMITIDO
        llamadas.append((metodo, args))
        return [{"id": 9}] if metodo == "search_read" and len(llamadas) > 2 else ([] if metodo == "search_read" else 5)
    O.guardar_foto(odoo, 204234, "592611000006", "QUJD", "image/jpeg")
    assert llamadas[1][0] == "create" and llamadas[1][1][0]["name"] == "Foto albarán 592611000006.jpg"
    assert llamadas[1][1][0]["res_model"] == "project.task" and llamadas[1][1][0]["res_id"] == 204234
    O.guardar_foto(odoo, 204234, "592611000006", "REVG", "image/jpeg")   # foto nueva: se sustituye
    assert llamadas[3][0] == "write" and llamadas[3][1][0] == [9]
