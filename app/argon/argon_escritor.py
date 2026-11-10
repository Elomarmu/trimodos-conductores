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
import argon_referencias as R

PERMITIDO = {
    ("sale.order", "search_read"), ("sale.order", "create"), ("sale.order", "write"), ("sale.order", "action_confirm"),
    ("sale.order.line", "search_read"), ("tms.equipment", "search_read"), ("tms.equipment", "create"),
    ("sale.order", "action_unlock"), ("sale.order", "action_lock"),  # corregir líneas de pedidos bloqueados
    ("product.product", "search_read"), ("res.partner", "search_read"),  # referencias de la Excel (solo lectura)
    ("account.analytic.account", "search_read"), ("fleet.vehicle", "search_read"),
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


def escribir(odoo, rotaciones: dict, cfg: dict, real: bool, limite_s: float = 20.0, protegidos: set | None = None,
             desde: dt.date = dt.date(2026, 10, 15)) -> dict:
    t0 = time.time()
    res = {"pedidos_creados": [], "pedidos_ampliados": [], "lineas_nuevas": 0, "lineas_ya_estaban": 0,
           "lineas_corregidas": [], "lineas_anuladas": [], "cambios_facturados": [],
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
            # una línea ya existe si está en cualquier pedido ARGON (mismo producto, fecha y contenedor): así no se
            # duplica aunque cambie dónde se corta el pedido (descarga o bloqueo)
            ya = set().union(*lineas_prev.values()) if lineas_prev else set()
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
    if not res["restantes"] and time.time() - t0 <= limite_s:
        try:
            conciliar(odoo, rotaciones, previos, eq, cfg, real, res, protegidos or set(), desde)
        except Exception as e:
            res["errores"].append(f"Revisión de lo ya pasado a Odoo: {e}")
    res["llamadas_odoo"] = getattr(odoo, "llamadas", 0)
    res["segundos"] = round(time.time() - t0, 1)
    return res


# Campos de la línea que el automatismo mantiene iguales a la Excel (mientras no esté facturada)
CAMPOS = {"name": "descripción", "product_uom_qty": "cantidad", "price_unit": "precio", "purchase_price": "coste",
          "vendor_id": "proveedor", "shipping_origin_id": "origen", "shipping_destination_id": "destino",
          "forecast_unload_date": "fecha de llegada", "analytic_distribution": "analítica",
          "tractor_id": "tractora", "trailer_id": "remolque", "driver_id": "conductor"}
ANULADA = "ANULADA · "
MAX_ANULAR = 10  # más de esto (o del 20 % de las líneas) en una noche: no se anula nada y se avisa


def _norm(campo, v):
    if v in (None, False, ""):
        return None
    if isinstance(v, (list, tuple)):  # many2one de Odoo: [id, nombre]
        return v[0] if v else None
    if campo in ("product_uom_qty", "price_unit", "purchase_price"):
        return round(float(v), 2)
    if campo == "forecast_unload_date":
        return str(v)[:10]
    if campo == "analytic_distribution":
        return {str(k): round(float(x), 2) for k, x in dict(v).items()} or None
    return v


def _txt(campo, v):
    if isinstance(v, (list, tuple)) and v:
        return str(v[1] if len(v) > 1 and v[1] else v[0])
    return "—" if v in (None, False, "") else str(v)


def conciliar(odoo, rotaciones: dict, previos: list, eq: dict, cfg: dict, real: bool, res: dict,
              protegidos: set, desde: dt.date):
    """Lo ya pasado a Odoo y aún no facturado se mantiene igual que la Excel:
    - si una celda pasada cambia (precio, proveedor, destino, conductor…), se corrige la línea;
    - si un servicio ya no está en la Excel, su línea se anula (cantidad 0 y «ANULADA» delante: Odoo no deja borrar
      líneas de un pedido confirmado);
    - si lo que cambia ya está facturado, no se toca y se avisa en el correo para revisarlo a mano.
    Solo toca líneas creadas por el automatismo (no las que se añaden a mano en Odoo) y nunca las de un día en el que a
    ese contenedor le faltan datos (se avisa aparte, en «falta de datos»)."""
    ids = [i for p in previos for i in p["order_line"]]
    if not ids:
        return
    uid = getattr(odoo, "uid", None)
    pedidos = {p["id"]: p for p in previos}
    lineas = odoo("sale.order.line", "search_read", [[["id", "in", ids]]],
                  fields=["order_id", "product_id", "pickup_date", "equipment_id", "qty_invoiced", "create_uid",
                          *CAMPOS])
    # lo que la Excel dice hoy: clave (producto, fecha, contenedor) → valores de la línea
    esperadas = defaultdict(list)
    for rot, servs in rotaciones.items():
        eid = eq.get(rot.split("|")[0].replace(" ", "").replace("-", ""))
        for s in servs:
            v = AO.linea_de(s, cfg)
            esperadas[clave_linea(s.producto_id, AO.utc_mediodia(s.fecha), eid)].append((s, v))
    prot = {(eq.get(c.replace(" ", "").replace("-", "")), f) for c, f in protegidos}
    cambios = defaultdict(list)  # pedido → [(1, línea, valores)]
    for l in sorted(lineas, key=lambda x: x["id"]):
        if not l["product_id"] or (uid and l.get("create_uid") and l["create_uid"][0] != uid):
            continue  # línea puesta a mano en Odoo: no se toca
        fecha = str(l["pickup_date"] or "")[:10]
        if fecha < desde.isoformat():
            continue
        eid = l["equipment_id"] and l["equipment_id"][0]
        k = clave_linea(l["product_id"][0], l["pickup_date"] or "", eid)
        ped = pedidos.get(l["order_id"][0], {})
        info = {"pedido": ped.get("name", l["order_id"][1] if len(l["order_id"]) > 1 else ""), "url": enlace(odoo, ped.get("id")) if ped else None,
                "contenedor": next((n for n, i in eq.items() if i == eid), ""), "fecha": fecha,
                "linea": str(l.get("name") or "").replace(ANULADA, "")}
        facturada = float(l.get("qty_invoiced") or 0) > 0
        if esperadas.get(k):
            s, v = esperadas[k].pop(0)
            nuevo = {c: v.get(c, False) for c in CAMPOS}
            dif = {c: x for c, x in nuevo.items() if _norm(c, l.get(c)) != _norm(c, x)}
            if not dif:
                continue
            txt = "; ".join(f"{CAMPOS[c]}: {_txt(c, l.get(c))} → {_txt(c, x)}" for c, x in dif.items() if c != "name") \
                or "descripción"
            if facturada:
                res["cambios_facturados"].append(info | {"cambio": txt})
            else:
                cambios[l["order_id"][0]].append((1, l["id"], dif))
                res["lineas_corregidas"].append(info | {"cambio": txt})
        else:
            if str(l.get("name") or "").startswith(ANULADA) and not float(l.get("product_uom_qty") or 0):
                continue  # ya anulada otra noche
            if (eid, fecha) in prot:
                continue  # ese día le faltan datos al contenedor: no se anula hasta que se completen
            if facturada:
                res["cambios_facturados"].append(info | {"cambio": "ya no está en la Excel (o ha cambiado de producto)"})
            else:
                cambios[l["order_id"][0]].append((1, l["id"], {"product_uom_qty": 0, "name": ANULADA + str(l.get("name") or "")}))
                res["lineas_anuladas"].append(info | {"cambio": "ya no está en la Excel (o ha cambiado de producto)"})
    # freno de seguridad: si de golpe hubiera que anular muchas líneas, algo raro pasa (Excel a medio editar, hoja
    # movida…). No se anula nada y se avisa en el correo.
    revisadas = sum(1 for l in lineas if l["product_id"] and str(l["pickup_date"] or "")[:10] >= desde.isoformat())
    if len(res["lineas_anuladas"]) > max(MAX_ANULAR, revisadas * 0.2):
        n = len(res["lineas_anuladas"])
        res["errores"].append(f"Habría que anular {n} líneas de {revisadas}: son demasiadas y no se ha anulado ninguna. "
                              "Revisad si la Excel está bien (filas borradas o movidas) y escribidme.")
        res["cambios_facturados"].extend(x | {"cambio": "SIN ANULAR (freno de seguridad): " + x["cambio"]}
                                         for x in res["lineas_anuladas"])
        res["lineas_anuladas"] = []
        cambios = {oid: [c for c in cmds if not str(c[2].get("name", "")).startswith(ANULADA)] for oid, cmds in cambios.items()}
        cambios = {oid: c for oid, c in cambios.items() if c}
    if not real:
        return
    for oid, cmds in cambios.items():
        try:
            odoo("sale.order", "write", [[oid], {"order_line": cmds}])
        except Exception as e:  # pedido bloqueado: se desbloquea, se corrige y se vuelve a bloquear
            if "bloque" not in str(e).lower() and "lock" not in str(e).lower():
                raise
            odoo("sale.order", "action_unlock", [[oid]])
            odoo("sale.order", "write", [[oid], {"order_line": cmds}])
            odoo("sale.order", "action_lock", [[oid]])


def procesar(ruta_excel: str, cfg: dict, real: bool, odoo=None, desde: dt.date | None = None, limite_s: float = 20.0,
             hoy: dt.date | None = None):
    """Solo lleva a Odoo lo que ya ha pasado (celdas anteriores a hoy); lo demás es planificación."""
    desde = desde or dt.date.fromisoformat(cfg.get("fecha_inicio", "2026-10-15"))
    hoy = hoy or dt.date.today()
    if odoo is None:
        # Variables propias de ARGON: las ODOO_* de Vercel son las de producción de la app de conductores
        faltan = [v for v in ("ARGON_ODOO_URL", "ARGON_ODOO_DB", "ARGON_ODOO_USER", "ARGON_ODOO_PASSWORD") if not os.environ.get(v)]
        if faltan:
            raise RuntimeError("Faltan variables en Vercel: " + ", ".join(faltan) + " (datos del Odoo 18 de pruebas)")
        odoo = Odoo(os.environ["ARGON_ODOO_URL"], os.environ["ARGON_ODOO_DB"], os.environ["ARGON_ODOO_USER"],
                    os.environ["ARGON_ODOO_PASSWORD"], cfg["empresa_id"])
    # 60 días antes: para saber dónde empieza cada rotación. Productos y proveedores desde su referencia en Odoo.
    servicios = F.calcular(ruta_excel, desde - dt.timedelta(days=60), completar=lambda t: R.completar(t, odoo), hoy=hoy)
    rot = preparar(servicios, desde)
    # días de cada contenedor con algún servicio sin datos: sus líneas de Odoo no se anulan mientras tanto
    protegidos = {(s.contenedor, s.fecha) for s in servicios
                  if s.fecha >= desde.isoformat() and s.estado not in ("LISTO PARA ODOO", "INFORMATIVO")}
    res = escribir(odoo, rot, cfg, real, limite_s, protegidos, desde)
    res["excel"] = F.calcular.excel  # celdas que n8n completa en la Excel y referencias no encontradas
    # bloqueos de más de 7 días que cierran el pedido: recientes o que siguen (para explicarlo en el correo)
    hace7 = (hoy - dt.timedelta(days=7)).isoformat()
    res["bloqueos_largos"] = [{k: t[k] for k in ("contenedor", "inicio", "salida", "dias", "texto")} |
                              {"celda": f"'{F.L.HOJA}'!{t['celda']}"}
                              for tr in F.calcular.bloqueos.values() for t in tr
                              if t["corta"] and t["inicio"] >= desde.isoformat() and (not t["salida"] or t["salida"] >= hace7)]
    res["pendientes_de_datos"] = [{"fecha": s.fecha, "contenedor": s.contenedor, "servicio": s.servicio,
                                   "texto": s.texto, "falta": "; ".join(s.falta),
                                   "celda": f"'{F.L.HOJA}'!{s.celda}" if s.celda else None,
                                   "ir": [{"dato": f, "celda": s.ir.get(f)} for f in s.falta]}
                                  for s in servicios if s.fecha >= desde.isoformat() and s.estado == "FALTAN DATOS"
                                  and "destino del tren (aún no ha llegado)" not in s.falta]  # en camino: ya llegará
    return res
