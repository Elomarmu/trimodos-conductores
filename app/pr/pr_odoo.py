"""Escritura en Odoo de los viajes de PR de SAICA PAPER (probado a mano en la v18 de pruebas el 09/11/2026).

Pasos para registrar un viaje (en este orden; si se cambia, Odoo no genera bien la orden de transporte):
1. No repetir: si ya hay un bulto de SAICA con ese albarán, se devuelve el existente.
2. Pedido del mes («PR NOVIEMBRE» del año del albarán, STINSA). Si no existe se crea, se confirma y se DESBLOQUEA
   (en la v18 los pedidos confirmados se bloquean solos y ya no admiten líneas).
3. Bulto (tms.package) con NOMBRE PUESTO POR NOSOTROS: en la v18 el nombre automático usa una numeración antigua y
   choca («este nombre de paquete ya existe»). Se pide el siguiente número de la secuencia hasta que esté libre.
4. Línea del pedido con el bulto dentro (así Odoo crea la tarea/orden de transporte con su bulto y la compra al
   colaborador). Referencia y mercancía NO se escriben en la línea: van en el bulto.
5. «Rellenar puntos de paso» (fill_checkpoints) en la tarea: sin esto la orden sale sin «Lugares carga / descarga».
6. Imprimir la orden (lo hace main/odoo_client con la sesión web).
Desde la v18 no se rellena el campo División.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re

from . import pr_reglas as R

log = logging.getLogger("pr")

PERMITIDO = {
    ("sale.order", "search_read"), ("sale.order", "create"), ("sale.order", "write"), ("sale.order", "read"),
    ("sale.order", "action_confirm"), ("sale.order", "action_unlock"),
    ("sale.order.line", "search_read"), ("sale.order.line", "read"), ("sale.order.line", "write"),
    ("project.task", "search_read"), ("project.task", "fill_checkpoints"),
    ("tms.package", "search_read"), ("tms.package", "search_count"), ("tms.package", "create"), ("tms.package", "write"),
    ("tms.equipment", "search_read"), ("tms.equipment", "create"),
    ("fleet.vehicle", "search_read"), ("ir.sequence", "next_by_code"),
    ("ir.attachment", "search_read"), ("ir.attachment", "create"), ("ir.attachment", "write"),  # composición del tren
}
GOODS_PAPEL_RECUPERADO = 233   # tms.goods «PAPEL RECUPERADO»
COSTE_VIAJE_DEFECTO = 85.0     # € por viaje al colaborador (decisión 09/11/2026); el recargo por gasóleo se añade a mano


def coste_viaje() -> float:
    """Coste de compra por viaje (pasa solo a la línea de compra del colaborador). Variable PR_COSTE_VIAJE."""
    try:
        return float((os.environ.get("PR_COSTE_VIAJE") or str(COSTE_VIAJE_DEFECTO)).replace(",", "."))
    except ValueError:
        return COSTE_VIAJE_DEFECTO


class ErrorPR(Exception):
    """Mensaje claro para el conductor u oficina (se muestra tal cual)."""


# ---- vehículos y colaborador --------------------------------------------------------------------------------------

def vehiculo(odoo, placa: str) -> dict | None:
    """Vehículo de Flota por matrícula. Si hay varios con la misma matrícula, el último usado en un pedido de SAICA."""
    if not placa:
        return None
    vs = odoo("fleet.vehicle", "search_read", [[["license_plate", "=", placa]]], fields=["license_plate", "driver_id"])
    if len(vs) <= 1:
        return vs[0] if vs else None
    for campo in ("tractor_id", "trailer_id"):
        ls = odoo("sale.order.line", "search_read", [[[campo, "in", [v["id"] for v in vs]]]],
                  fields=[campo], order="id desc", limit=1)
        if ls:
            return next(v for v in vs if v["id"] == ls[0][campo][0])
    return vs[0]


def parecidas(odoo, placa: str) -> list[str]:
    """Matrículas de Flota que se diferencian en un solo carácter (p. ej. leída 1222LTP → 1222HTP)."""
    if len(placa) < 6:
        return []
    dominio: list = []
    for i in range(len(placa)):
        dominio = (["|"] + dominio if dominio else dominio) + [["license_plate", "=like", placa[:i] + "_" + placa[i + 1:]]]
    vs = odoo("fleet.vehicle", "search_read", [dominio], fields=["license_plate"], limit=10)
    return sorted({v["license_plate"] for v in vs} - {placa})


def colaborador(odoo, tractora: dict) -> int | None:
    """El transportista (proveedor) es el conductor asignado a la tractora en Flota; si no, el del último viaje de esa tractora."""
    if tractora.get("driver_id"):
        return tractora["driver_id"][0]
    ls = odoo("sale.order.line", "search_read", [[["tractor_id", "=", tractora["id"]], ["vendor_id", "!=", False]]],
              fields=["vendor_id"], order="id desc", limit=1)
    return ls[0]["vendor_id"][0] if ls else None


def comprobar_vehiculos(odoo, tractora: str, remolque: str) -> dict:
    """Para la pantalla de revisión: ¿existen en Flota? Si no, matrículas parecidas para proponer."""
    res = {}
    for clave, placa in (("tractora", tractora), ("remolque", remolque)):
        v = vehiculo(odoo, placa)
        res[clave] = {"matricula": placa, "existe": bool(v), "parecidas": [] if v else parecidas(odoo, placa)}
        if clave == "tractora" and v:
            res["colaborador_id"] = colaborador(odoo, v)
            res["colaborador"] = v["driver_id"][1] if v.get("driver_id") else ""
    return res


# ---- pedido del mes ----------------------------------------------------------------------------------------------

def buscar_pedido_mes(odoo, fecha: str) -> dict | None:
    año = fecha[:4]
    ps = odoo("sale.order", "search_read", [[["partner_id", "=", R.CLIENTE_ID], ["company_id", "=", R.EMPRESA_ID],
                                            ["client_order_ref", "=", R.referencia_pedido(fecha)],
                                            ["date_order", ">=", f"{año}-01-01 00:00:00"], ["date_order", "<=", f"{año}-12-31 23:59:59"],
                                            ["state", "!=", "cancel"]]],
              fields=["name", "state", "locked"], order="id desc", limit=1)
    return ps[0] if ps else None


def preparar_pedido(odoo, pedido: dict) -> None:
    """Un pedido confirmado y bloqueado no admite líneas nuevas: se desbloquea."""
    if pedido.get("locked"):
        odoo("sale.order", "action_unlock", [[pedido["id"]]])


def crear_pedido_mes(odoo, fecha: str) -> dict:
    vals = {"partner_id": R.CLIENTE_ID, "partner_invoice_id": R.CLIENTE_ID, "partner_shipping_id": R.CLIENTE_ID,
            "company_id": R.EMPRESA_ID, "client_order_ref": R.referencia_pedido(fecha), "type_id": R.TIPO_VENTA_ID,
            "user_id": R.COMERCIAL_ID}
    pid = odoo("sale.order", "create", [vals])
    return {"id": pid, "nuevo": True, "confirmar": True}


# ---- bulto, línea y tarea -----------------------------------------------------------------------------------------

def ya_registrado(odoo, albaran: str, incluir_sueltos: bool = False) -> dict | None:
    """Bulto de SAICA con ese albarán. Por defecto solo cuenta si ya tiene línea de pedido; con incluir_sueltos también
    devuelve uno que se quedó sin línea (registro anterior cortado a medias), para reutilizarlo."""
    if not albaran:
        return None
    texto = f"Albarán salida: {albaran}"
    ps = odoo("tms.package", "search_read", [[["partner_id", "=", R.CLIENTE_ID], ["unload_note", "ilike", texto]]],
              fields=["name", "task_ids", "sale_line_ids", "unload_note"], order="id desc", limit=5)
    exactos = [p for p in ps if R.leer_observaciones(p.get("unload_note")).get("albaran") == albaran]  # ilike no es exacto
    con_linea = next((p for p in exactos if p.get("sale_line_ids")), None)
    if con_linea or not incluir_sueltos:
        return con_linea
    return next(iter(exactos), None)


def asegurar_puntos(odoo, tarea_id: int | None) -> None:
    """Si la orden no tiene sus lugares de carga y descarga (registro cortado a medias), se rellenan."""
    if not tarea_id:
        return
    t = odoo("project.task", "search_read", [[["id", "=", tarea_id]]], fields=["checkpoint_ids"], limit=1)
    if t and not t[0].get("checkpoint_ids"):
        odoo("project.task", "fill_checkpoints", [[tarea_id]])


def nombre_bulto(odoo) -> str:
    for _ in range(20):
        nombre = odoo("ir.sequence", "next_by_code", ["tms.package"])
        if nombre and not odoo("tms.package", "search_count", [[["name", "=", nombre]]]):
            return nombre
    raise ErrorPR("No se ha podido numerar el bulto en Odoo. Avisa a la oficina.")


def equipo(odoo, matricula: str) -> int | None:
    if not matricula:
        return None
    es = odoo("tms.equipment", "search_read", [[["name", "=", matricula]]], fields=["name"], limit=1)
    return es[0]["id"] if es else odoo("tms.equipment", "create", [{"name": matricula, "equipment_type": "container"}])


def datos_observaciones(v: dict) -> dict:
    return {"albaran": v.get("albaran"), "codigo_paper": v.get("codigo_paper"), "codigo_natur": v.get("codigo_natur"),
            "ler": v.get("ler"), "calidad": v.get("calidad"), "bruto_kg": v.get("bruto_kg"), "tara_kg": v.get("tara_kg")}


def registrar(odoo, v: dict) -> dict:
    """Registra un viaje ya revisado por el conductor. `v` = Viaje.a_dict() (con las correcciones del conductor).
    Devuelve pedido, línea, tarea y bulto. No imprime (eso lo hace quien llama)."""
    punto = next((p for p in R.PUNTOS_CARGA if v.get("punto") and p.clave == v["punto"].get("clave")), None)
    if not punto:
        raise ErrorPR("Punto de carga nuevo: la oficina tiene que darlo de alta. Ya se ha avisado.")
    if not v.get("fecha"):
        raise ErrorPR("Falta la fecha de carga.")
    if not v.get("albaran"):
        raise ErrorPR("Falta el número de albarán.")
    if v.get("contenedor") and not R.contenedor(v["contenedor"])[1]:
        raise ErrorPR("El contenedor no es correcto (no cuadra el último dígito). Corrígelo o déjalo en blanco.")
    previo = ya_registrado(odoo, v.get("albaran"), incluir_sueltos=True)
    if previo and previo.get("sale_line_ids"):
        tarea = (previo.get("task_ids") or [None])[0]
        asegurar_puntos(odoo, tarea)
        return {"duplicado": True, "bulto": previo["name"], "tarea_id": tarea, "linea_id": previo["sale_line_ids"][0]}

    tr, re_ = vehiculo(odoo, v.get("tractora")), vehiculo(odoo, v.get("remolque"))
    if not tr:
        raise ErrorPR(f"La tractora {v.get('tractora')} no está en Flota. Revisa la matrícula o avisa a la oficina.")
    vendedor = colaborador(odoo, tr)
    if not vendedor:
        raise ErrorPR(f"La tractora {v.get('tractora')} no tiene transportista asignado en Flota. Avisa a la oficina.")

    pedido = buscar_pedido_mes(odoo, v["fecha"])
    nuevo = pedido is None
    if nuevo:
        pedido = crear_pedido_mes(odoo, v["fecha"])
    else:
        preparar_pedido(odoo, pedido)
        if pedido.get("state") in ("draft", "sent"):   # alguien lo creó a mano sin confirmar: sin confirmar no hay orden
            pedido["confirmar"] = True

    cuando = R.fecha_hora_utc(v["fecha"], v.get("hora"))
    bulto = {"name": previo["name"] if previo else nombre_bulto(odoo), "partner_id": R.CLIENTE_ID, "company_id": R.EMPRESA_ID,
             "shipping_origin_id": punto.origen_id, "shipping_destination_id": R.DESTINO_ID, "pickup_date": cuando,
             "goods_id": GOODS_PAPEL_RECUPERADO, "carrier_tracking_ref": v.get("codigo_paper") or False,
             "shipping_weight": v.get("peso_kg") or 0, "number_of_packages": v.get("bultos") or 0,
             "unload_note": R.observaciones(datos_observaciones(v))}
    if previo:   # bulto suelto de un intento anterior cortado a medias: se reutiliza (con los datos de ahora)
        bulto_id = previo["id"]
        bulto["name"] = previo["name"]
        odoo("tms.package", "write", [[bulto_id], {k: v_ for k, v_ in bulto.items() if k != "name"}])
    else:
        bulto_id = odoo("tms.package", "create", [bulto])

    linea = {"product_id": punto.producto_id, "name": punto.linea, "price_unit": punto.precio_t,
             "product_uom_qty": R.toneladas(punto, v.get("peso_kg")) or 0, "pickup_date": cuando,
             "shipping_origin_id": punto.origen_id, "shipping_destination_id": R.DESTINO_ID,
             "purchase_price": coste_viaje(),  # Odoo lo pasa a la línea de compra del colaborador
             "tractor_id": tr["id"], "trailer_id": re_["id"] if re_ else False, "driver_id": vendedor, "vendor_id": vendedor,
             "tms_package_ids": [(6, 0, [bulto_id])]}
    eq = equipo(odoo, v.get("contenedor"))
    if eq:
        linea["equipment_id"] = eq
    odoo("sale.order", "write", [[pedido["id"]], {"order_line": [(0, 0, linea)]}])
    if pedido.get("confirmar"):  # pedido recién creado: confirmar crea la tarea; después se desbloquea
        odoo("sale.order", "action_confirm", [[pedido["id"]]])
        odoo("sale.order", "action_unlock", [[pedido["id"]]])

    lineas = odoo("sale.order.line", "search_read", [[["order_id", "=", pedido["id"]], ["tms_package_ids", "in", [bulto_id]]]],
                  fields=["task_id", "order_id"], limit=1)
    if not lineas or not lineas[0].get("task_id"):
        raise ErrorPR("Odoo no ha creado la orden de transporte. Avisa a la oficina.")
    tarea_id = lineas[0]["task_id"][0]
    odoo("project.task", "fill_checkpoints", [[tarea_id]])
    return {"duplicado": False, "pedido_nuevo": nuevo, "pedido": lineas[0]["order_id"][1], "pedido_id": pedido["id"],
            "linea_id": lineas[0]["id"], "tarea_id": tarea_id, "bulto": bulto["name"], "bulto_id": bulto_id}


# ---- completar un viaje después (código Paper, peso, balas, contenedor; datos del tren) ---------------------------

def buscar_viaje(odoo, texto: str) -> dict | None:
    """Por nº de albarán, código Paper o nombre de bulto."""
    t = re.sub(r"\s+", "", texto or "").upper()
    if not t:
        return None
    dom = [["partner_id", "=", R.CLIENTE_ID], "|", "|", ["name", "=", t], ["carrier_tracking_ref", "=", t],
           ["unload_note", "ilike", f"Albarán salida: {t}"]]
    ps = odoo("tms.package", "search_read", [dom],
              fields=["name", "task_ids", "sale_line_ids", "unload_note", "carrier_tracking_ref", "shipping_weight",
                      "number_of_packages", "shipping_origin_id", "pickup_date"], order="id desc", limit=1)
    return ps[0] if ps else None


def actualizar(odoo, bulto: dict, cambios: dict) -> list[str]:
    """Completa datos de un viaje ya registrado. `cambios` admite: codigo_paper, peso_kg, bultos, contenedor y los
    datos del tren (tren, vagon, caja_movil, comprobaciones). Devuelve la lista de lo cambiado."""
    hechos, vals_bulto, vals_linea = [], {}, {}
    obs = {k: cambios.get(k) for k in ("codigo_paper", "tren", "vagon", "caja_movil", "comprobaciones") if cambios.get(k)}
    if cambios.get("codigo_paper"):
        cp = R.codigo_paper(cambios["codigo_paper"])
        if not cp:
            raise ErrorPR("El código de carga Paper tiene que ser S seguido de 12 cifras.")
        vals_bulto["carrier_tracking_ref"], obs["codigo_paper"] = cp, cp
        hechos.append("código Paper")
    if cambios.get("peso_kg"):
        kg = R.kilos(cambios["peso_kg"])
        if not kg:
            raise ErrorPR("Revisa el peso.")
        vals_bulto["shipping_weight"] = kg
        hechos.append("peso")
        origen = (bulto.get("shipping_origin_id") or [None])[0]
        punto = next((p for p in R.PUNTOS_CARGA if p.origen_id == origen), None)
        if punto and not punto.toneladas_fijas:
            vals_linea["product_uom_qty"] = R.toneladas(punto, kg)
    if cambios.get("bultos") not in (None, ""):
        try:
            balas = int(cambios["bultos"])
        except (TypeError, ValueError):
            balas = -1
        if balas < 0 or balas > 500:
            raise ErrorPR("Revisa las balas.")
        vals_bulto["number_of_packages"] = balas
        hechos.append("balas")
    if obs:
        vals_bulto["unload_note"] = R.observaciones(obs, bulto.get("unload_note"))
        hechos += [k for k in ("tren", "vagon", "caja_movil", "comprobaciones") if k in obs]
    if vals_bulto:
        odoo("tms.package", "write", [[bulto["id"]], vals_bulto])
    linea_id = (bulto.get("sale_line_ids") or [None])[0]
    if cambios.get("contenedor") and linea_id:
        cont, ok = R.contenedor(cambios["contenedor"])
        if not ok:
            raise ErrorPR("Revisa el número de contenedor (no cuadra el dígito de control).")
        vals_linea["equipment_id"] = equipo(odoo, cont)
        hechos.append("contenedor")
    if vals_linea and linea_id:
        odoo("sale.order.line", "write", [[linea_id], vals_linea])
    return hechos


def desde_defecto() -> str:
    """Primer día que cuenta para el registro de viajes y el stock (los viajes anteriores no pasaron por la app)."""
    return os.environ.get("PR_DESDE", "2026-11-01")


def pedidos_pr(odoo, desde: str) -> list[dict]:
    refs = [f"PR {m}" for m in R.MESES]
    return odoo("sale.order", "search_read", [[["partner_id", "=", R.CLIENTE_ID], ["company_id", "=", R.EMPRESA_ID],
                                               ["client_order_ref", "in", refs], ["date_order", ">=", f"{desde[:7]}-01 00:00:00"],
                                               ["state", "!=", "cancel"]]], fields=["name"], order="id")


def viajes(odoo, desde: str | None = None) -> list[dict]:
    """Todos los viajes de PR (líneas con bulto de los pedidos «PR <MES>») desde esa fecha, del más antiguo al más nuevo.
    estado: «En stock» si aún no tiene caja móvil; «Tren N» si ya va en un tren."""
    desde = desde or desde_defecto()
    pedidos = {p["id"]: p["name"] for p in pedidos_pr(odoo, desde)}
    if not pedidos:
        return []
    ls = odoo("sale.order.line", "search_read", [[["order_id", "in", list(pedidos)], ["tms_package_ids", "!=", False],
                                                  ["pickup_date", ">=", f"{desde} 00:00:00"]]],
              fields=["name", "order_id", "pickup_date", "tractor_id", "trailer_id", "vendor_id", "product_uom_qty",
                      "equipment_id", "task_id", "tms_package_ids", "shipping_origin_id", "create_date"], order="pickup_date, id")
    ps = odoo("tms.package", "search_read", [[["id", "in", [i for l in ls for i in l["tms_package_ids"]]]]],
              fields=["name", "carrier_tracking_ref", "shipping_weight", "number_of_packages", "unload_note"])
    por_id = {p["id"]: p for p in ps}
    docs = documentos(odoo, [(l.get("task_id") or [None])[0] for l in ls])
    res = []
    for l in ls:
        tarea = (l.get("task_id") or [None])[0]
        p = por_id.get(l["tms_package_ids"][0], {})
        o = R.leer_observaciones(p.get("unload_note"))
        origen = (l.get("shipping_origin_id") or [None])[0]
        punto = next((x for x in R.PUNTOS_CARGA if x.origen_id == origen), None)
        res.append({"linea_id": l["id"], "pedido": l["order_id"][1], "pedido_id": l["order_id"][0],
                    "registrado": R.fecha_madrid(l.get("create_date")), "tarea_id": (l.get("task_id") or [None])[0],
                    "bulto": p.get("name"), "bulto_id": p.get("id"), "fecha": (l.get("pickup_date") or "")[:10],
                    "tractora": _placa(l.get("tractor_id")), "remolque": _placa(l.get("trailer_id")),
                    "colaborador": (l.get("vendor_id") or [None, ""])[1], "lugar": punto.nombre if punto else "",
                    "linea": l["name"], "toneladas": l.get("product_uom_qty"), "peso_kg": p.get("shipping_weight") or None,
                    "bultos": p.get("number_of_packages") or None, "codigo_paper": p.get("carrier_tracking_ref") or "",
                    "contenedor": (l.get("equipment_id") or [None, ""])[1],
                    **{k: o.get(k, "") for k in ("albaran", "codigo_natur", "ler", "calidad", "tren", "convoy", "fecha_tren", "foto",
                                                 "foto_di", "vagon", "caja_movil", "comprobaciones")},
                    **{"doc_" + d: ok for d, ok in docs.get(tarea, {d: False for d in R.DOCUMENTOS}).items()},
                    "tipo": R.tipo_calidad(o.get("calidad")),
                    "estado": f"Tren {o['tren']}" if o.get("caja_movil") and o.get("tren") else "En stock"})
    return res


def siguiente_tren(lista: list[dict]) -> int:
    """Número de tren propio (correlativo). El último que se hizo a mano fue el 85 (PR_ULTIMO_TREN)."""
    nums = [int(v["tren"]) for v in lista if str(v.get("tren") or "").isdigit()]
    return max(nums + [int(os.environ.get("PR_ULTIMO_TREN", "85"))]) + 1


# ---- foto del albarán: se guarda en Odoo, adjunta a la orden de transporte -----------------------------------------

PREFIJO_FOTO = R.DOCUMENTOS["albaran"][0]


def guardar_foto(odoo, tarea_id: int, albaran: str, b64: str, tipo: str, doc: str = "albaran") -> None:
    """La foto (o el PDF) del documento (albarán o DI de residuos) queda adjunta a la tarea. Si ya había, se sustituye."""
    prefijo = R.DOCUMENTOS[doc][0]
    previa = odoo("ir.attachment", "search_read", [[["res_model", "=", "project.task"], ["res_id", "=", tarea_id],
                                                    ["name", "=like", prefijo + "%"]]], fields=["id"], order="id desc", limit=1)
    vals = {"name": f"{prefijo} {albaran or ''}".strip() + R.extension(tipo), "datas": b64, "mimetype": tipo}
    if previa:
        odoo("ir.attachment", "write", [[previa[0]["id"]], vals])
    else:
        odoo("ir.attachment", "create", [dict(vals, res_model="project.task", res_id=tarea_id, type="binary")])


def leer_foto(odoo, tarea_id: int | None, doc: str = "albaran") -> dict | None:
    """{"b64", "tipo"} del documento de esa tarea, o None si no tiene."""
    if not tarea_id:
        return None
    a = odoo("ir.attachment", "search_read", [[["res_model", "=", "project.task"], ["res_id", "=", tarea_id],
                                               ["name", "=like", R.DOCUMENTOS[doc][0] + "%"]]], fields=["datas", "mimetype"],
             order="id desc", limit=1)
    return {"b64": a[0]["datas"], "tipo": a[0].get("mimetype") or "image/jpeg"} if a and a[0].get("datas") else None


def documentos(odoo, tareas: list[int]) -> dict[int, dict[str, bool]]:
    """Qué documentos tiene cada tarea: {tarea: {"albaran": True, "di": False}}. Una sola consulta para todas."""
    tareas = [t for t in tareas if t]
    res = {t: {d: False for d in R.DOCUMENTOS} for t in tareas}
    if not tareas:
        return res
    dom = [["res_model", "=", "project.task"], ["res_id", "in", tareas]]
    dom += ["|"] * (len(R.DOCUMENTOS) - 1) + [["name", "=like", pref + "%"] for pref, *_ in R.DOCUMENTOS.values()]
    for a in odoo("ir.attachment", "search_read", [dom], fields=["name", "res_id"]):
        for d, (pref, *_) in R.DOCUMENTOS.items():
            if (a.get("name") or "").startswith(pref) and a.get("res_id") in res:
                res[a["res_id"]][d] = True
    return res


def apuntar_foto(odoo, bulto_id: int, ruta: str | None, doc: str = "albaran") -> None:
    """Guarda (o borra) en las observaciones del bulto dónde está el documento en SharePoint («carpeta/nombre»)."""
    clave = R.DOCUMENTOS[doc][1]
    p = odoo("tms.package", "search_read", [[["id", "=", bulto_id]]], fields=["unload_note"])
    if p:
        nuevo = R.observaciones({clave: ruta}, p[0].get("unload_note")) if ruta else R.observaciones({}, p[0].get("unload_note"), quitar=(clave,))
        odoo("tms.package", "write", [[bulto_id], {"unload_note": nuevo}])


# ---- tren: composición (la carga la oficina) y asignación de cargas a cajas (oficina o almacén) -------------------

CLAVES_TREN = ("tren", "convoy", "fecha_tren", "vagon", "caja_movil", "comprobaciones", "foto", "foto_di")
TODO_OK = R.texto_comprobaciones({k: True for k, _ in R.COMPROBACIONES})   # de momento no se revisan: siempre OK


def convoy_tren(tren: dict) -> str:
    """Nº de convoy con el que sale el tren: el de salida si la oficina lo ha puesto; si no, el de entrada (el del listado)."""
    return str(tren.get("convoy_salida") or tren.get("convoy") or "")


def cambiar_salida(odoo, tren: dict, lista: list[dict], convoy_salida: str) -> list[int]:
    """Oficina: pone (o corrige) el nº de convoy de salida del tren. Se cambia también en las cargas ya asignadas."""
    tren["convoy_salida"] = (convoy_salida or "").strip()
    escribir_tren(odoo, tren)
    afectados = []
    for v in lista:
        if v.get("tren") == str(tren["tren"]) and v.get("caja_movil") and v.get("convoy") != convoy_tren(tren):
            _cambiar_obs(odoo, v["bulto_id"], {"convoy": convoy_tren(tren)})
            afectados.append(v["bulto_id"])
    return afectados


def guardar_composicion(odoo, tren: int, convoy: str, fecha: str, cajas: list[dict], convoy_salida: str = "") -> dict:
    """La composición del tren (vagones, cajas y taras) se guarda como adjunto JSON «PR_TREN_<n>.json» del pedido de PR
    del mes del tren (o del último pedido de PR). Si ya existía ese tren, se sustituye."""
    if not cajas:
        raise ErrorPR("El tren no tiene cajas.")
    pedido = buscar_pedido_mes(odoo, fecha) or (pedidos_pr(odoo, desde_defecto()) or [None])[-1]
    if not pedido:
        raise ErrorPR("Todavía no hay ningún pedido de PR en Odoo: registra antes algún viaje.")
    datos = {"tren": int(tren), "convoy": convoy, "convoy_salida": (convoy_salida or "").strip(), "fecha": fecha, "pedido_id": pedido["id"],
             "cajas": [{k: c.get(k) for k in ("orden", "vagon", "segunda", "caja_movil", "tara_kg")} for c in cajas], "orden": []}
    anterior = leer_tren(odoo, int(tren))
    if anterior:   # se vuelve a cargar el mismo tren: se conserva el orden de carga de los vagones que siguen en él
        vagones_nuevos = {c["vagon"] for c in datos["cajas"]}
        datos["orden"] = [v for v in anterior.get("orden") or [] if v in vagones_nuevos]
        cajas_nuevas = {c["caja_movil"] for c in datos["cajas"]}
        datos["vacias"] = [c for c in anterior.get("vacias") or [] if c in cajas_nuevas]
    escribir_tren(odoo, datos)
    return datos


def leer_tren(odoo, tren: int) -> dict | None:
    adj = odoo("ir.attachment", "search_read", [[["res_model", "=", "sale.order"], ["name", "=", f"PR_TREN_{int(tren)}.json"]]],
               fields=["datas"], limit=1)
    return json.loads(base64.b64decode(adj[0]["datas"]).decode()) if adj and adj[0].get("datas") else None


def escribir_tren(odoo, datos: dict) -> None:
    """Guarda el JSON del tren (composición + orden de carga de los vagones) en su adjunto del pedido de PR."""
    contenido = base64.b64encode(json.dumps(datos, ensure_ascii=False).encode()).decode()
    nombre = f"PR_TREN_{int(datos['tren'])}.json"
    previo = odoo("ir.attachment", "search_read", [[["res_model", "=", "sale.order"], ["name", "=", nombre]]], fields=["id"], limit=1)
    if previo:
        odoo("ir.attachment", "write", [[previo[0]["id"]], {"datas": contenido}])
    else:
        odoo("ir.attachment", "create", [{"name": nombre, "res_model": "sale.order", "res_id": datos["pedido_id"], "datas": contenido,
                                          "mimetype": "application/json", "type": "binary"}])


def composiciones_repetidas(odoo, tren: int, convoy: str, cajas: list[dict], ultimas: int = 40) -> list[str]:
    """Avisos si esta composición ya se cargó: mismo nº de tren, mismo nº de convoy o casi las mismas cajas (≥ 80 %)."""
    adj = odoo("ir.attachment", "search_read", [[["res_model", "=", "sale.order"], ["name", "=like", "PR_TREN_%"]]],
               fields=["name", "datas"], order="id desc", limit=ultimas)
    nuevas = {c["caja_movil"] for c in cajas if c.get("caja_movil")}
    avisos = []
    for a in adj:
        try:
            t = json.loads(base64.b64decode(a["datas"]).decode())
            num_viejo = int(t.get("tren", 0))
        except (ValueError, TypeError):
            continue
        fecha = "-".join(reversed((t.get("fecha") or "").split("-")))
        viejas = {c["caja_movil"] for c in t.get("cajas", []) if c.get("caja_movil")}
        comunes = len(nuevas & viejas)
        if num_viejo == int(tren):
            avisos.append(f"El tren {tren} ya está cargado (del {fecha}, {len(viejas)} cajas): si sigues, se sustituye su composición.")
        elif convoy and str(t.get("convoy") or "").strip() == convoy.strip():
            avisos.append(f"El convoy {convoy} ya se cargó como tren {t['tren']} el {fecha}.")
        elif nuevas and comunes >= 0.8 * max(len(nuevas), len(viejas)):
            avisos.append(f"Estas cajas coinciden ({comunes} de {len(nuevas)}) con el tren {t['tren']} del {fecha}.")
    return avisos


def tren_actual(odoo) -> dict | None:
    """El último tren cargado por la oficina."""
    adj = odoo("ir.attachment", "search_read", [[["res_model", "=", "sale.order"], ["name", "=like", "PR_TREN_%"]]],
               fields=["name", "datas"], order="write_date desc, id desc", limit=1)
    if not adj:
        return None
    return json.loads(base64.b64decode(adj[0]["datas"]).decode())


# ---- orden del tren: arriba los vagones junto a la locomotora, abajo los de cola ------------------------------------
# El almacén carga de atrás hacia delante: cada vagón que empieza a cargarse se pone ARRIBA de la lista; la segunda caja
# de un vagón ya empezado se suma a su vagón sin moverlo. Después se puede reordenar (subir/bajar) a mano.

def vagones(tren: dict) -> list[str]:
    vistos = []
    for c in tren["cajas"]:
        if c["vagon"] not in vistos:
            vistos.append(c["vagon"])
    return vistos


def orden_final(tren: dict) -> list[str]:
    """Vagones en el orden del tren: primero los cargados en el orden de la lista; luego los que no llevan carga."""
    todos = vagones(tren)
    cargados = [v for v in tren.get("orden") or [] if v in todos]
    return cargados + [v for v in todos if v not in cargados]


def cajas_en_orden(tren: dict) -> list[dict]:
    """Cajas en el orden del tren; las del mismo vagón siempre juntas. «ord» = posición del vagón; «segunda» = 2.ª caja;
    «vacia» = el almacén ha dicho que esa caja va vacía."""
    res, vacias = [], set(tren.get("vacias") or [])
    for i, vagon in enumerate(orden_final(tren), start=1):
        for j, c in enumerate(c for c in tren["cajas"] if c["vagon"] == vagon):
            res.append(dict(c, ord=i, segunda=j > 0, vacia=c["caja_movil"] in vacias))
    return res


def _poner_arriba(tren: dict, vagon: str) -> None:
    if vagon not in (tren.get("orden") or []):   # vagón que empieza a cargarse: arriba de la lista
        tren["orden"] = [vagon] + list(tren.get("orden") or [])


def _sacar_si_vacio(tren: dict, lista: list[dict], vagon: str, sin_bulto: int | None = None) -> None:
    """El vagón sale de la lista si ya no lleva ninguna carga ni ninguna caja marcada como vacía."""
    cajas = {c["caja_movil"] for c in tren["cajas"] if c["vagon"] == vagon}
    cargas = [v for v in lista or [] if v.get("tren") == str(tren["tren"]) and v.get("caja_movil") in cajas
              and v.get("bulto_id") != sin_bulto]
    if not cargas and not cajas & set(tren.get("vacias") or []):
        tren["orden"] = [v for v in tren.get("orden") or [] if v != vagon]


def marcar_vacia(odoo, tren: dict, lista: list[dict], caja_movil: str) -> None:
    """Almacén: esa caja va vacía. Ocupa su sitio en el orden del tren como si llevara carga."""
    caja = next((c for c in tren["cajas"] if c["caja_movil"] == caja_movil), None)
    if not caja:
        raise ErrorPR(f"La caja {caja_movil} no está en el tren {tren['tren']}.")
    ocupada = next((v for v in lista if v.get("tren") == str(tren["tren"]) and v.get("caja_movil") == caja_movil), None)
    if ocupada:
        raise ErrorPR(f"La caja {caja_movil} ya lleva la carga {ocupada.get('codigo_paper') or ocupada.get('albaran')}.")
    vacias = list(tren.get("vacias") or [])
    if caja_movil in vacias:
        raise ErrorPR(f"La caja {caja_movil} ya está marcada como vacía.")
    tren["vacias"] = vacias + [caja_movil]
    _poner_arriba(tren, caja["vagon"])
    escribir_tren(odoo, tren)


def quitar_vacia(odoo, tren: dict, lista: list[dict], caja_movil: str) -> None:
    if caja_movil not in (tren.get("vacias") or []):
        raise ErrorPR(f"La caja {caja_movil} no está marcada como vacía.")
    tren["vacias"] = [c for c in tren["vacias"] if c != caja_movil]
    vagon = next(c["vagon"] for c in tren["cajas"] if c["caja_movil"] == caja_movil)
    _sacar_si_vacio(tren, lista, vagon)
    escribir_tren(odoo, tren)


def estado_tren(odoo, tren: dict, lista: list[dict]) -> dict:
    """Cajas del tren con la carga que lleva cada una, los vagones ya cargados en su orden, y cargas en stock."""
    por_caja = {v["caja_movil"]: v for v in lista if v.get("tren") == str(tren["tren"]) and v.get("caja_movil")}
    cajas = [dict(c, carga=_carga(por_caja.get(c["caja_movil"]))) for c in cajas_en_orden(tren)]
    cargados = [v for v in tren.get("orden") or [] if any(c["vagon"] == v and (c["carga"] or c["vacia"]) for c in cajas)]
    grupos = [{"vagon": v, "cajas": [c for c in cajas if c["vagon"] == v]} for v in cargados]
    stock = [_carga(v) for v in lista if v["estado"] == "En stock"]
    return {**{k: tren[k] for k in ("tren", "convoy", "fecha")}, "convoy_salida": tren.get("convoy_salida") or "",
            "cajas": cajas, "grupos": grupos, "stock": stock}


def mover_vagon(odoo, tren: dict, vagon: str, hacia: str) -> None:
    """Sube o baja un vagón cargado en la lista (hacia la locomotora = «subir»)."""
    orden = [v for v in tren.get("orden") or []]
    if vagon not in orden:
        raise ErrorPR("Ese vagón todavía no tiene ninguna caja asignada.")
    i = orden.index(vagon)
    j = i - 1 if hacia == "subir" else i + 1
    if 0 <= j < len(orden):
        orden[i], orden[j] = orden[j], orden[i]
        tren["orden"] = orden
        escribir_tren(odoo, tren)


def editar(odoo, tren: dict, lista: list[dict], caja_movil: str, nueva_caja: str = "", nuevo_vagon: str = "") -> list[int]:
    """Corrige el nombre de una caja móvil o el número de su vagón (se aplica a todas las cajas de ese vagón).
    También lo cambia en las cargas ya asignadas. Devuelve los bultos afectados (para renombrar su foto)."""
    caja = next((c for c in tren["cajas"] if c["caja_movil"] == caja_movil), None)
    if not caja:
        raise ErrorPR(f"La caja {caja_movil} no está en el tren {tren['tren']}.")
    del_tren = [v for v in lista if v.get("tren") == str(tren["tren"]) and v.get("caja_movil")]
    afectados: list[int] = []
    nueva = R.matricula(nueva_caja)
    if nueva and nueva != caja_movil:
        if any(c["caja_movil"] == nueva for c in tren["cajas"]):
            raise ErrorPR(f"La caja {nueva} ya está en este tren.")
        caja["caja_movil"], caja["tara_kg"] = nueva, R.tara_caja(nueva) or caja.get("tara_kg")
        tren["vacias"] = [nueva if c == caja_movil else c for c in tren.get("vacias") or []]
        for v in del_tren:
            if v["caja_movil"] == caja_movil:
                _cambiar_obs(odoo, v["bulto_id"], {"caja_movil": nueva})
                afectados.append(v["bulto_id"])
    if nuevo_vagon:
        cifras, ok = R.vagon_excel(nuevo_vagon)
        if len(cifras) != 12:
            raise ErrorPR("El número de vagón tiene que tener 12 cifras.")
        if not ok:
            raise ErrorPR(f"El vagón {cifras} no cuadra con su dígito de control (la última cifra). Revísalo.")
        viejo = caja["vagon"]
        if cifras != viejo:
            if cifras in vagones(tren):
                raise ErrorPR(f"El vagón {cifras} ya está en este tren.")
            for c in tren["cajas"]:
                if c["vagon"] == viejo:
                    c["vagon"] = cifras
            tren["orden"] = [cifras if v == viejo else v for v in tren.get("orden") or []]
            for v in del_tren:
                if v.get("vagon") == viejo:
                    _cambiar_obs(odoo, v["bulto_id"], {"vagon": cifras})
                    afectados.append(v["bulto_id"])
    escribir_tren(odoo, tren)
    return afectados


def _cambiar_obs(odoo, bulto_id: int, datos: dict) -> None:
    p = odoo("tms.package", "search_read", [[["id", "=", bulto_id]]], fields=["unload_note"])
    if p:
        odoo("tms.package", "write", [[bulto_id], {"unload_note": R.observaciones(datos, p[0].get("unload_note"))}])


def _carga(v: dict | None) -> dict | None:
    if not v:
        return None
    return {**{k: v.get(k) for k in ("bulto_id", "bulto", "codigo_paper", "albaran", "bultos", "peso_kg", "fecha", "lugar")},
            "faltan_docs": [R.DOCUMENTOS[d][3] for d in R.DOCUMENTOS if v.get("doc_" + d) is False]}


def asignar(odoo, tren: dict, lista: list[dict], bulto_id: int, caja_movil: str) -> str:
    caja = next((c for c in tren["cajas"] if c["caja_movil"] == caja_movil), None)
    if not caja:
        raise ErrorPR(f"La caja {caja_movil} no está en el tren {tren['tren']}.")
    ocupada = next((v for v in lista if v.get("tren") == str(tren["tren"]) and v.get("caja_movil") == caja_movil), None)
    if ocupada:
        raise ErrorPR(f"La caja {caja_movil} ya lleva la carga {ocupada.get('codigo_paper') or ocupada.get('albaran')}.")
    viaje = next((v for v in lista if v.get("bulto_id") == bulto_id), None)
    if not viaje:
        raise ErrorPR("No encuentro esa carga.")
    if viaje["estado"] != "En stock":
        raise ErrorPR(f"Esa carga ya va en el {viaje['estado'].lower()} (caja {viaje.get('caja_movil')}).")
    p = odoo("tms.package", "search_read", [[["id", "=", bulto_id], ["partner_id", "=", R.CLIENTE_ID]]], fields=["name", "unload_note"])
    if not p:
        raise ErrorPR("No encuentro esa carga.")
    datos = {"tren": str(tren["tren"]), "convoy": convoy_tren(tren), "fecha_tren": tren.get("fecha"), "vagon": caja.get("vagon"),
             "caja_movil": caja_movil, "comprobaciones": TODO_OK}
    odoo("tms.package", "write", [[bulto_id], {"unload_note": R.observaciones(datos, p[0].get("unload_note"))}])
    # Dos personas a la vez con la misma caja: si ya hay otra carga en esa caja de este tren, se deshace esta.
    en_caja = odoo("tms.package", "search_read", [[["partner_id", "=", R.CLIENTE_ID], ["unload_note", "ilike", f"Caja móvil: {caja_movil}"]]],
                   fields=["unload_note"], order="id")
    mismas = [x["id"] for x in en_caja if (o := R.leer_observaciones(x.get("unload_note"))).get("caja_movil") == caja_movil
              and o.get("tren") == str(tren["tren"])]
    if len(mismas) > 1 and mismas[0] != bulto_id:
        quitar(odoo, bulto_id)
        raise ErrorPR(f"Otra persona acaba de cargar la caja {caja_movil}. Elige otra caja.")
    if caja["vagon"] not in (tren.get("orden") or []) or caja_movil in (tren.get("vacias") or []):
        _poner_arriba(tren, caja["vagon"])
        tren["vacias"] = [c for c in tren.get("vacias") or [] if c != caja_movil]   # estaba como vacía: ya lleva carga
        escribir_tren(odoo, tren)
    return p[0]["name"]


def quitar(odoo, bulto_id: int, tren: dict | None = None, lista: list[dict] | None = None) -> str:
    """Deshace la asignación: la carga vuelve a stock. Si su vagón se queda sin carga, sale de la lista de cargados."""
    p = odoo("tms.package", "search_read", [[["id", "=", bulto_id], ["partner_id", "=", R.CLIENTE_ID]]], fields=["name", "unload_note"])
    if not p:
        raise ErrorPR("No encuentro esa carga.")
    vagon = R.leer_observaciones(p[0].get("unload_note")).get("vagon")
    odoo("tms.package", "write", [[bulto_id], {"unload_note": R.observaciones({}, p[0].get("unload_note"), quitar=CLAVES_TREN)}])
    if tren and vagon and vagon in (tren.get("orden") or []):
        antes = list(tren["orden"])
        _sacar_si_vacio(tren, lista or [], vagon, sin_bulto=bulto_id)
        if tren["orden"] != antes:
            escribir_tren(odoo, tren)
    return p[0]["name"]


def _placa(valor) -> str:
    return valor[1].rsplit("/", 1)[-1] if valor else ""


