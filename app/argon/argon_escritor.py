"""Escritura real en Odoo de los servicios de la Excel ARGON (pedidos de venta de Carburos).

- Un pedido por contenedor, de descarga a descarga (origin «ARGON|<contenedor>|ROT <fecha>»).
- Líneas en orden de fecha: las de pago y las «INFO ·» (producto de seguimiento a 0 €).
- Solo entran servicios desde FECHA_INICIO (por defecto 15/10/2026) y pedidos con algo que facturar.
- No duplica: una línea ya existe si el pedido de la rotación tiene el mismo producto, contenedor y fecha.
- Si el pedido de la rotación ya está facturado, las líneas nuevas van a un pedido de continuación («| 2»).
- Solo se permite escribir en Odoo de pruebas (stinsa18) salvo ARGON_PERMITIR_PRODUCCION=1.
- Lista blanca de modelos y métodos: no puede borrar nada.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.request
from collections import defaultdict

import argon_facturacion as F
import argon_odoo as AO

PERMITIDO = {
    ("sale.order", "search_read"), ("sale.order", "create"), ("sale.order", "write"), ("sale.order", "action_confirm"),
    ("sale.order.line", "search_read"), ("tms.equipment", "search_read"), ("tms.equipment", "create"),
}


class Odoo:
    def __init__(self, url: str, db: str, usuario: str, clave: str, empresa_id: int):
        if "stinsa18" not in url and os.environ.get("ARGON_PERMITIR_PRODUCCION") != "1":
            raise RuntimeError(f"Por seguridad solo se escribe en Odoo de pruebas (stinsa18), no en {url}")
        self.url, self.db, self.clave, self.empresa = url.rstrip("/"), db, clave, empresa_id
        self.uid = self._rpc("common", "authenticate", [db, usuario, clave, {}])
        if not self.uid:
            raise RuntimeError("Odoo no acepta el usuario o la clave API")
        self.llamadas = 0

    def enlace(self, pedido_id: int) -> str:
        """Dirección del pedido en Odoo (para el hipervínculo del correo)."""
        return f"{self.url}/odoo/action-sale.action_orders/{pedido_id}"

    def _rpc(self, servicio, metodo, args):
        datos = json.dumps({"jsonrpc": "2.0", "method": "call", "params": {"service": servicio, "method": metodo, "args": args}})
        req = urllib.request.Request(self.url + "/jsonrpc", data=datos.encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=25) as r:
            res = json.loads(r.read())
        if res.get("error"):
            e = res["error"].get("data", {}).get("message") or res["error"].get("message")
            raise RuntimeError(f"Odoo: {e}")
        return res.get("result")

    def __call__(self, modelo, metodo, args, **kw):
        if (modelo, metodo) not in PERMITIDO:
            raise PermissionError(f"{modelo}.{metodo} no está permitido")
        self.llamadas += 1
        kw.setdefault("context", {"allowed_company_ids": [self.empresa], "lang": "es_ES", "tz": "Europe/Madrid"})
        return self._rpc("object", "execute_kw", [self.db, self.uid, self.clave, modelo, metodo, args, kw])


def preparar(servicios: list[F.Servicio], desde: dt.date) -> dict[str, list[F.Servicio]]:
    """Rotaciones con algo que facturar → sus servicios desde la fecha de inicio (pago + informativos)."""
    por_rot = defaultdict(list)
    for s in servicios:
        if s.fecha < desde.isoformat() or not s.rotacion:
            continue
        if s.estado == "LISTO PARA ODOO" or (s.estado == "INFORMATIVO" and s.producto_id):
            por_rot[s.rotacion].append(s)
    return {r: sorted(l, key=lambda x: (x.fecha, x.id_servicio)) for r, l in por_rot.items()
            if any(x.estado == "LISTO PARA ODOO" for x in l)}


def enlace(odoo, pedido_id):
    return odoo.enlace(pedido_id) if hasattr(odoo, "enlace") else None


def clave_linea(product_id, fecha, equipo_id):
    return (int(product_id), str(fecha)[:10], int(equipo_id or 0))


def escribir(odoo, rotaciones: dict, cfg: dict, real: bool, limite_s: float = 20.0) -> dict:
    t0 = time.time()
    res = {"pedidos_creados": [], "pedidos_ampliados": [], "lineas_nuevas": 0, "lineas_ya_estaban": 0,
           "errores": [], "restantes": 0, "modo": "real" if real else "prueba"}
    if not rotaciones:
        return res
    # Contenedores: una sola lectura
    nombres = sorted({r.split("|")[0].replace(" ", "").replace("-", "") for r in rotaciones})
    eq = {e["name"]: e["id"] for e in odoo("tms.equipment", "search_read", [[["name", "in", nombres]]], fields=["name"])}
    # Pedidos ya creados por el automatismo: una sola lectura
    previos = odoo("sale.order", "search_read", [[["origin", "=like", "ARGON|%"], ["state", "!=", "cancel"],
                                                  ["partner_id", "=", cfg["cliente_id"]]]],
                   fields=["name", "origin", "state", "invoice_status", "order_line"], order="id desc")
    lineas_prev = defaultdict(set)
    ids_lineas = [i for p in previos for i in p["order_line"]]
    if ids_lineas:
        for l in odoo("sale.order.line", "search_read", [[["id", "in", ids_lineas]]],
                      fields=["order_id", "product_id", "pickup_date", "equipment_id"]):
            if l["product_id"]:
                lineas_prev[l["order_id"][0]].add(clave_linea(l["product_id"][0], l["pickup_date"] or "",
                                                              l["equipment_id"] and l["equipment_id"][0]))
    pendientes = list(rotaciones.items())
    for n, (rot, servs) in enumerate(pendientes):
        if time.time() - t0 > limite_s:
            res["restantes"] = len(pendientes) - n
            break
        cont, inicio = rot.split("|")
        nombre_eq = cont.replace(" ", "").replace("-", "")
        try:
            if nombre_eq not in eq:
                eq[nombre_eq] = odoo("tms.equipment", "create", [{"name": nombre_eq, "equipment_type": "container"}]) if real else 0
            eid = eq[nombre_eq]
            base = f"ARGON|{cont}|ROT {inicio}"
            de_rot = [p for p in previos if p["origin"] == base or p["origin"].startswith(base + "|")]
            ya = set().union(*(lineas_prev[p["id"]] for p in de_rot)) if de_rot else set()
            nuevas = []
            for s in servs:
                if clave_linea(s.producto_id, AO.utc_mediodia(s.fecha), eid) in ya:
                    res["lineas_ya_estaban"] += 1
                    continue
                l = AO.linea_de(s, cfg)
                l.pop("tax_id", None)  # el impuesto lo pone el producto
                l["equipment_id"] = eid or False
                nuevas.append(l)
            if not nuevas:
                continue
            abierto = next((p for p in de_rot if p["invoice_status"] != "invoiced"), None)
            if abierto:
                if real:
                    odoo("sale.order", "write", [[abierto["id"]], {"order_line": [(0, 0, l) for l in nuevas]}])
                    if abierto["state"] in ("draft", "sent"):
                        odoo("sale.order", "action_confirm", [[abierto["id"]]])
                res["pedidos_ampliados"].append({"pedido": abierto["name"], "contenedor": cont, "lineas": len(nuevas),
                                                 "url": enlace(odoo, abierto["id"])})
            else:
                sufijo = f"|{len(de_rot) + 1}" if de_rot else ""
                pedido = {"partner_id": cfg["cliente_id"], "company_id": cfg["empresa_id"], "origin": base + sufijo,
                          "client_order_ref": f"{cont} · desde {dt.date.fromisoformat(inicio):%d/%m/%Y} hasta la descarga",
                          "order_line": [(0, 0, l) for l in nuevas]}
                if cfg.get("tipo_pedido_id"):
                    pedido["type_id"] = cfg["tipo_pedido_id"]
                nombre, oid = "(prueba)", None
                if real:
                    oid = odoo("sale.order", "create", [pedido])
                    odoo("sale.order", "action_confirm", [[oid]])
                    nombre = odoo("sale.order", "search_read", [[["id", "=", oid]]], fields=["name"])[0]["name"]
                res["pedidos_creados"].append({"pedido": nombre, "contenedor": cont, "lineas": len(nuevas),
                                               "url": enlace(odoo, oid) if oid else None,
                                               "importe": round(sum(l.get("price_unit", 0) * l.get("product_uom_qty", 1)
                                                                    for l in nuevas), 2)})
            res["lineas_nuevas"] += len(nuevas)
        except Exception as e:  # un contenedor con problemas no para a los demás
            res["errores"].append(f"{cont}: {e}")
    res["llamadas_odoo"] = getattr(odoo, "llamadas", 0)
    res["segundos"] = round(time.time() - t0, 1)
    return res


def procesar(ruta_excel: str, cfg: dict, real: bool, odoo=None, desde: dt.date | None = None, limite_s: float = 20.0):
    desde = desde or dt.date.fromisoformat(cfg.get("fecha_inicio", "2026-10-15"))
    servicios = F.calcular(ruta_excel, desde - dt.timedelta(days=60))  # 60 días antes: para saber dónde empieza cada rotación
    rot = preparar(servicios, desde)
    if odoo is None:
        # Variables propias de ARGON: las ODOO_* de Vercel son las de producción de la app de conductores
        faltan = [v for v in ("ARGON_ODOO_URL", "ARGON_ODOO_DB", "ARGON_ODOO_USER", "ARGON_ODOO_PASSWORD") if not os.environ.get(v)]
        if faltan:
            raise RuntimeError("Faltan variables en Vercel: " + ", ".join(faltan) + " (datos del Odoo 18 de pruebas)")
        odoo = Odoo(os.environ["ARGON_ODOO_URL"], os.environ["ARGON_ODOO_DB"], os.environ["ARGON_ODOO_USER"],
                    os.environ["ARGON_ODOO_PASSWORD"], cfg["empresa_id"])
    res = escribir(odoo, rot, cfg, real, limite_s)
    res["pendientes_de_datos"] = [{"fecha": s.fecha, "contenedor": s.contenedor, "servicio": s.servicio,
                                   "texto": s.texto, "falta": "; ".join(s.falta),
                                   "celda": f"'{F.L.HOJA}'!{s.celda}" if s.celda else None,
                                   "ir": [{"dato": f, "celda": s.ir.get(f)} for f in s.falta]}
                                  for s in servicios if s.fecha >= desde.isoformat() and s.estado == "FALTAN DATOS"]
    return res
