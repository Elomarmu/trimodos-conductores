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
import datetime as dt
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

def ya_registrado(odoo, albaran: str) -> dict | None:
    if not albaran:
        return None
    texto = f"Albarán salida: {albaran}"
    ps = odoo("tms.package", "search_read", [[["partner_id", "=", R.CLIENTE_ID], ["unload_note", "ilike", texto]]],
              fields=["name", "task_ids", "sale_line_ids", "unload_note"], limit=5)
    for p in ps:  # comprobación exacta (ilike también encontraría un número más largo que empiece igual)
        if R.leer_observaciones(p.get("unload_note")).get("albaran") == albaran:
            return p
    return None


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
    if v.get("contenedor") and not R.contenedor(v["contenedor"])[1]:
        raise ErrorPR("El contenedor no es correcto (no cuadra el último dígito). Corrígelo o déjalo en blanco.")
    previo = ya_registrado(odoo, v.get("albaran"))
    if previo:
        return {"duplicado": True, "bulto": previo["name"], "tarea_id": (previo.get("task_ids") or [None])[0],
                "linea_id": (previo.get("sale_line_ids") or [None])[0]}

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

    cuando = R.fecha_hora_utc(v["fecha"], v.get("hora"))
    bulto = {"name": nombre_bulto(odoo), "partner_id": R.CLIENTE_ID, "company_id": R.EMPRESA_ID,
             "shipping_origin_id": punto.origen_id, "shipping_destination_id": R.DESTINO_ID, "pickup_date": cuando,
             "goods_id": GOODS_PAPEL_RECUPERADO, "carrier_tracking_ref": v.get("codigo_paper") or False,
             "shipping_weight": v.get("peso_kg") or 0, "number_of_packages": v.get("bultos") or 0,
             "unload_note": R.observaciones(datos_observaciones(v))}
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
        vals_bulto["number_of_packages"] = int(cambios["bultos"])
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
                      "equipment_id", "task_id", "tms_package_ids", "shipping_origin_id"], order="pickup_date, id")
    ps = odoo("tms.package", "search_read", [[["id", "in", [i for l in ls for i in l["tms_package_ids"]]]]],
              fields=["name", "carrier_tracking_ref", "shipping_weight", "number_of_packages", "unload_note"])
    por_id = {p["id"]: p for p in ps}
    res = []
    for l in ls:
        p = por_id.get(l["tms_package_ids"][0], {})
        o = R.leer_observaciones(p.get("unload_note"))
        origen = (l.get("shipping_origin_id") or [None])[0]
        punto = next((x for x in R.PUNTOS_CARGA if x.origen_id == origen), None)
        res.append({"linea_id": l["id"], "pedido": l["order_id"][1], "tarea_id": (l.get("task_id") or [None])[0],
                    "bulto": p.get("name"), "bulto_id": p.get("id"), "fecha": (l.get("pickup_date") or "")[:10],
                    "tractora": _placa(l.get("tractor_id")), "remolque": _placa(l.get("trailer_id")),
                    "colaborador": (l.get("vendor_id") or [None, ""])[1], "lugar": punto.nombre if punto else "",
                    "linea": l["name"], "toneladas": l.get("product_uom_qty"), "peso_kg": p.get("shipping_weight") or None,
                    "bultos": p.get("number_of_packages") or None, "codigo_paper": p.get("carrier_tracking_ref") or "",
                    "contenedor": (l.get("equipment_id") or [None, ""])[1],
                    **{k: o.get(k, "") for k in ("albaran", "codigo_natur", "ler", "calidad", "tren", "convoy", "fecha_tren",
                                                 "vagon", "caja_movil", "comprobaciones")},
                    "tipo": R.tipo_calidad(o.get("calidad")),
                    "estado": f"Tren {o['tren']}" if o.get("caja_movil") and o.get("tren") else "En stock"})
    return res


def siguiente_tren(lista: list[dict]) -> int:
    """Número de tren propio (correlativo). El último que se hizo a mano fue el 85 (PR_ULTIMO_TREN)."""
    nums = [int(v["tren"]) for v in lista if str(v.get("tren") or "").isdigit()]
    return max(nums + [int(os.environ.get("PR_ULTIMO_TREN", "85"))]) + 1


# ---- tren: composición (la carga la oficina) y asignación de cargas a cajas (oficina o almacén) -------------------

CLAVES_TREN = ("tren", "convoy", "fecha_tren", "vagon", "caja_movil", "comprobaciones")
TODO_OK = R.texto_comprobaciones({k: True for k, _ in R.COMPROBACIONES})   # de momento no se revisan: siempre OK


def guardar_composicion(odoo, tren: int, convoy: str, fecha: str, cajas: list[dict]) -> dict:
    """La composición del tren (vagones, cajas y taras) se guarda como adjunto JSON «PR_TREN_<n>.json» del pedido de PR
    del mes del tren (o del último pedido de PR). Si ya existía ese tren, se sustituye."""
    if not cajas:
        raise ErrorPR("El tren no tiene cajas.")
    pedido = buscar_pedido_mes(odoo, fecha) or (pedidos_pr(odoo, desde_defecto()) or [None])[-1]
    if not pedido:
        raise ErrorPR("Todavía no hay ningún pedido de PR en Odoo: registra antes algún viaje.")
    datos = {"tren": int(tren), "convoy": convoy, "fecha": fecha, "pedido_id": pedido["id"],
             "cajas": [{k: c.get(k) for k in ("orden", "vagon", "segunda", "caja_movil", "tara_kg")} for c in cajas]}
    contenido = base64.b64encode(json.dumps(datos, ensure_ascii=False).encode()).decode()
    nombre = f"PR_TREN_{int(tren)}.json"
    previo = odoo("ir.attachment", "search_read", [[["res_model", "=", "sale.order"], ["name", "=", nombre]]], fields=["id"], limit=1)
    if previo:
        odoo("ir.attachment", "write", [[previo[0]["id"]], {"datas": contenido}])
    else:
        odoo("ir.attachment", "create", [{"name": nombre, "res_model": "sale.order", "res_id": pedido["id"], "datas": contenido,
                                          "mimetype": "application/json", "type": "binary"}])
    return datos


def tren_actual(odoo) -> dict | None:
    """El último tren cargado por la oficina."""
    adj = odoo("ir.attachment", "search_read", [[["res_model", "=", "sale.order"], ["name", "=like", "PR_TREN_%"]]],
               fields=["name", "datas"], order="id desc", limit=1)
    if not adj:
        return None
    return json.loads(base64.b64decode(adj[0]["datas"]).decode())


def estado_tren(odoo, tren: dict, lista: list[dict]) -> dict:
    """Cajas del tren con la carga que lleva cada una, y cargas en stock."""
    por_caja = {v["caja_movil"]: v for v in lista if v.get("tren") == str(tren["tren"]) and v.get("caja_movil")}
    cajas = [dict(c, carga=_carga(por_caja.get(c["caja_movil"]))) for c in tren["cajas"]]
    stock = [_carga(v) for v in lista if v["estado"] == "En stock"]
    return {**{k: tren[k] for k in ("tren", "convoy", "fecha")}, "cajas": cajas, "stock": stock}


def _carga(v: dict | None) -> dict | None:
    if not v:
        return None
    return {k: v.get(k) for k in ("bulto_id", "bulto", "codigo_paper", "albaran", "bultos", "peso_kg", "fecha", "lugar")}


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
    datos = {"tren": str(tren["tren"]), "convoy": tren.get("convoy"), "fecha_tren": tren.get("fecha"), "vagon": caja.get("vagon"),
             "caja_movil": caja_movil, "comprobaciones": TODO_OK}
    odoo("tms.package", "write", [[bulto_id], {"unload_note": R.observaciones(datos, p[0].get("unload_note"))}])
    return p[0]["name"]


def quitar(odoo, bulto_id: int) -> str:
    """Deshace la asignación: la carga vuelve a stock."""
    p = odoo("tms.package", "search_read", [[["id", "=", bulto_id], ["partner_id", "=", R.CLIENTE_ID]]], fields=["name", "unload_note"])
    if not p:
        raise ErrorPR("No encuentro esa carga.")
    odoo("tms.package", "write", [[bulto_id], {"unload_note": R.observaciones({}, p[0].get("unload_note"), quitar=CLAVES_TREN)}])
    return p[0]["name"]


def _placa(valor) -> str:
    return valor[1].rsplit("/", 1)[-1] if valor else ""


