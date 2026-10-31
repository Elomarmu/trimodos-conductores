"""Paso a Odoo de los servicios facturables de la Excel ARGON (pedidos de venta TMS).

Modo simulación (por defecto): no se conecta a Odoo; escribe en un JSON las llamadas exactas que haría.
Modo real (fase 2, solo contra stinsa18 y con permiso): --real con ODOO_URL, ODOO_DB, ODOO_USER, ODOO_PASSWORD.

Uso:  python argon_odoo.py ARGON.xlsx config.json salida.json [--desde 2026-10-01] [--hasta 2026-10-31] [--real]

Reglas:
- Un pedido de venta por contenedor y rotación (cada carga abre rotación), origin «ARGON|<contenedor>|ROT <fecha>».
  Si ese pedido ya está facturado, las líneas nuevas van a un pedido de continuación. Cada línea lleva una marca
  [ARGON-xxxxxxxxxx]: si ya existe no se repite (idempotente).
- Solo servicios «LISTO PARA ODOO». Los demás se quedan en el informe de pendientes.
- Línea: analítica (analytic_distribution), producto, cantidad, precio, coste (purchase_price), proveedor (vendor_id), origen/destino, fechas.
- Tarea TMS del pedido: tractora, remolque y conductor si la celda trae chasis y código de conductor.
- Contenedor: ficha de tms.equipment por matrícula (campo de enlace a confirmar en stinsa18: config «campo_equipo»).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import argon_facturacion as F  # noqa: E402


def utc_mediodia(fecha: str | None) -> str | None:
    return f"{fecha} 10:00:00" if fecha else None  # 11:00/12:00 en Madrid: no cambia de día por la zona horaria


def marca(s: F.Servicio) -> str:
    return "ARGON-" + hashlib.sha1(s.id_servicio.encode()).hexdigest()[:10]


def linea_de(s: F.Servicio, cfg: dict) -> dict:
    nombre = {"TREN": f"Tramo ferroviario {s.origen} → {s.destino}", "CARRETERA": f"Sale por carretera de {s.origen or ''} a {s.destino} (CVA)",
              "DESCARGA": f"Descarga en {s.destino or ''}", "CARGA": f"Carga en {s.destino or ''}",
              "TRAYECTO": f"Traslado {s.origen} → {s.destino}", "ESPERA": f"Espera en carga {s.destino}: {s.cantidad:g} h"}.get(
        s.servicio, f"{s.producto or s.servicio} {s.destino or ''}")
    if s.servicio == "TREN" and s.via:
        pasos = [re.sub(r"^CR A\s+", "", v).strip() for v in s.via.split(" · ")]
        pasos = [p for p in dict.fromkeys(pasos) if p and p not in (s.origen, s.destino)]
        if pasos:
            nombre += " (por " + ", ".join(pasos) + ")"
    if s.estado == "INFORMATIVO":
        nombre = f"INFO · {nombre}" + (" · CANCELADA" if s.cancelada else "")
    nombre = (f"{nombre} · {dt.date.fromisoformat(s.fecha):%d/%m/%Y}" + (f" · exp. {s.expedicion}" if s.expedicion else "")
              + (f" · {s.cargado.lower()}" if s.cargado else "") + (f" · {s.conductor}" if s.conductor else "")
              )  # sin marcas técnicas: este texto sale en la factura al cliente
    linea = {"product_id": s.producto_id, "name": nombre, "product_uom_qty": s.cantidad, "price_unit": s.precio,
             "tax_id": [(6, 0, [cfg["impuesto_id"]])] if cfg.get("impuesto_id") else None,
             "purchase_price": s.coste, "vendor_id": s.proveedor_id,
             "shipping_origin_id": s.origen_id, "shipping_destination_id": s.destino_id,
             "pickup_date": utc_mediodia(s.fecha), "forecast_unload_date": utc_mediodia(s.fecha_fin or s.fecha),
             "analytic_distribution": s.analitica}
    return {k: v for k, v in linea.items() if v not in (None, "")}


def llamadas_rotacion(rot: str, servs: list[F.Servicio], cfg: dict) -> list[dict]:
    """Un pedido por contenedor, de descarga a descarga, con todas las líneas en orden de fecha: las que se facturan y
    las informativas («INFO ·», producto de seguimiento a 0 €, no entran en factura). Si el pedido ya está facturado,
    las líneas nuevas van a un pedido de continuación («… | 2»)."""
    cont, inicio = rot.split("|")
    origin = f"ARGON|{cont}|ROT {inicio}"
    pedido = {"partner_id": cfg["cliente_id"], "company_id": cfg["empresa_id"], "origin": origin,
              "client_order_ref": f"{cont} · desde {dt.date.fromisoformat(inicio):%d/%m/%Y} hasta la descarga",
              "type_id": cfg.get("tipo_pedido_id")}
    pedido = {k: v for k, v in pedido.items() if v not in (None, "")}
    res = [{"paso": "equipo (contenedor) en TMS", "model": "tms.equipment", "method": "search",
            "args": [[["name", "=", cont.replace(" ", "").replace("-", "")]]], "kwargs": {"limit": 1}},
           {"paso": "buscar el pedido de la rotación", "model": "sale.order", "method": "search_read",
            "args": [[["origin", "=like", origin + "%"], ["state", "!=", "cancel"]]],
            "kwargs": {"fields": ["name", "origin", "state", "invoice_status"], "order": "id desc", "limit": 1}},
           {"paso": "si no hay pedido, o el último ya está facturado: crear (con «| 2», «| 3»… si es continuación)",
            "model": "sale.order", "method": "create", "args": [pedido]}]
    for s in sorted(servs, key=lambda x: (x.fecha, x.id_servicio)):  # en orden de fecha: el pedido cuenta el recorrido
        m = marca(s)
        res.append({"paso": f"línea {s.servicio} {s.fecha} (se salta si ya existe)", "model": "sale.order.line",
                    "method": "search_count", "args": [[["order_id.origin", "=like", origin + "%"], ["product_id", "=", s.producto_id],
                                                       ["pickup_date", "=", utc_mediodia(s.fecha)], ["equipment_id.name", "=", cont.replace(" ", "").replace("-", "")]]]})
        linea = linea_de(s, cfg)
        if cfg.get("campo_equipo"):
            linea[cfg["campo_equipo"]] = "<id equipo>"
        res.append({"paso": "añadir línea", "model": "sale.order", "method": "write",
                    "args": [["<id pedido>"], {"order_line": [(0, 0, linea)]}]})
        tarea = {k: v for k, v in {"tractor_id": s.tractora_id, "trailer_id": s.remolque_id, "driver_id": s.conductor_id}.items() if v}
        if tarea:
            res.append({"paso": "tractora, remolque y conductor en la tarea de esa línea", "model": "project.task",
                        "method": "write", "args": [["<tarea con sale_line_id = línea>"], tarea]})
    res.append({"paso": "confirmar si está en presupuesto", "model": "sale.order", "method": "action_confirm",
                "args": [["<id pedido>"]]})
    return res


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 1
    ruta, cfg, salida = Path(argv[1]), json.loads(Path(argv[2]).read_text(encoding="utf-8")), Path(argv[3])
    desde = dt.date.fromisoformat(argv[argv.index("--desde") + 1]) if "--desde" in argv else None
    hasta = dt.date.fromisoformat(argv[argv.index("--hasta") + 1]) if "--hasta" in argv else None
    servs = F.calcular(ruta, desde, hasta)
    listos = [s for s in servs if s.estado == "LISTO PARA ODOO"]
    info = [s for s in servs if s.estado == "INFORMATIVO" and s.producto_id]
    if "--real" in argv:
        if not os.environ.get("ODOO_URL", "").startswith("https://stinsa18"):
            print("Modo real solo contra stinsa18 en esta fase.")
            return 2
        raise SystemExit("Modo real: se activa en la fase 2, con permiso expreso.")
    por_rot = {}
    for s in listos:
        por_rot.setdefault(s.rotacion, []).append(s)
    for s in info:  # lo informativo solo entra en pedidos que ya tienen algo que facturar
        if s.rotacion in por_rot:
            por_rot[s.rotacion].append(s)
    plan = {"generado": dt.datetime.now().isoformat(timespec="seconds"), "modo": "simulación",
            "servicios_listos": len(listos), "pendientes": len(servs) - len(listos), "pedidos": len(por_rot),
            "rotaciones": [{"rotacion": r, "lineas": [{k: v for k, v in asdict(x).items() if k != "_reserva"} for x in lst],
                            "llamadas": llamadas_rotacion(r, lst, cfg)} for r, lst in sorted(por_rot.items())]}
    salida.write_text(json.dumps(plan, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"listos": len(listos), "informativas": sum(1 for l in por_rot.values() for x in l if x.estado == "INFORMATIVO"),
                      "pendientes": sum(1 for s in servs if s.estado == "FALTAN DATOS"), "pedidos": len(por_rot)}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
