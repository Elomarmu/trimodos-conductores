"""Base de la futura facturación del almacén de Burriana en Odoo (todavía NO se envía nada a Odoo).

Cada palet verificado en una entrada y cada palet cargado en una salida es un movimiento. De ahí salen las tres cosas que se
facturarán al cliente: manipulación de entrada, manipulación de salida y estocaje (palets × días en el almacén).
Cuando llegue el momento, `lineas_facturables` es lo que se convierte en líneas de pedido/factura de Odoo (cliente →
`odoo_partner_id` de su ficha en `config/clientes`; concepto → producto de servicio). Las tarifas viven fuera de este
módulo (por cliente: {"entrada_palet": €, "salida_palet": €, "estocaje_palet_dia": €}); sin tarifa, el importe queda vacío.
"""
from __future__ import annotations

import datetime as dt

from . import stock_reglas as R


def movimientos(entradas: list[dict], salidas: list[dict]) -> list[dict]:
    """Una fila por palet: {fecha, tipo «ENTRADA»|«SALIDA», cliente, ref, lote, sscc, palet_id, kg, documento}."""
    res = []
    for e in entradas:
        if e["estado"] != R.ENTRADA_CERRADA:
            continue
        for p in R.activos(e):
            if p["estado"] == "ok" or p.get("aceptado"):
                res.append({"fecha": e["cerrada"][:10], "tipo": "ENTRADA", "cliente": e["cliente"]["codigo"], "ref": p["ref"], "lote": p["lote"],
                            "sscc": p["sscc"], "palet_id": p["id"], "kg": p["kg"], "documento": e["id"]})
    for s in salidas:
        if s["estado"] != R.SALIDA_CARGADA:
            continue
        for p in s["palets"]:
            if p.get("palet_id") and p.get("estado_alta") != "quitado" and (p["estado"] == "ok" or p.get("aceptado")):
                res.append({"fecha": s["cargada"][:10], "tipo": "SALIDA", "cliente": s["cliente"]["codigo"], "ref": p["ref"], "lote": p["lote"],
                            "sscc": p["sscc"], "palet_id": p["palet_id"], "kg": p["kg"], "documento": s["id"]})
    return sorted(res, key=lambda m: (m["fecha"], m["tipo"] != "ENTRADA", m["documento"], m["palet_id"] or ""))


def estocaje(entradas: list[dict], salidas: list[dict], desde: str, hasta: str) -> dict[str, int]:
    """Palets-día por cliente entre dos fechas (ambas incluidas). Un palet cuenta desde el día de su entrada hasta el
    día anterior al de su salida (el día de entrada cuenta, el de salida no)."""
    mov = movimientos(entradas, salidas)
    entra = {m["palet_id"]: m for m in mov if m["tipo"] == "ENTRADA"}
    sale = {m["palet_id"]: m for m in mov if m["tipo"] == "SALIDA"}
    ini, fin = dt.date.fromisoformat(desde), dt.date.fromisoformat(hasta)
    res: dict[str, int] = {}
    for pid, m in entra.items():
        a = max(ini, dt.date.fromisoformat(m["fecha"]))
        s = sale.get(pid)
        b = min(fin, dt.date.fromisoformat(s["fecha"]) - dt.timedelta(days=1)) if s else fin
        if b >= a:
            res[m["cliente"]] = res.get(m["cliente"], 0) + (b - a).days + 1
    return res


def lineas_facturables(entradas: list[dict], salidas: list[dict], desde: str, hasta: str, tarifas: dict[str, dict] | None = None) -> list[dict]:
    """[{cliente, concepto, cantidad, unidad, precio, importe}] del periodo (el importe es None si el cliente no tiene tarifa)."""
    tarifas = tarifas or {}
    cuentas: dict[tuple[str, str], int] = {}
    for m in movimientos(entradas, salidas):
        if desde <= m["fecha"] <= hasta:
            clave = (m["cliente"], "entrada_palet" if m["tipo"] == "ENTRADA" else "salida_palet")
            cuentas[clave] = cuentas.get(clave, 0) + 1
    for cli, dias in estocaje(entradas, salidas, desde, hasta).items():
        cuentas[(cli, "estocaje_palet_dia")] = dias
    nombres = {"entrada_palet": ("Manipulación de entrada", "palet"), "salida_palet": ("Manipulación de salida", "palet"),
               "estocaje_palet_dia": ("Estocaje", "palet·día")}
    res = []
    for (cli, concepto), cantidad in sorted(cuentas.items()):
        precio = (tarifas.get(cli) or {}).get(concepto)
        res.append({"cliente": cli, "concepto": nombres[concepto][0], "clave": concepto, "cantidad": cantidad, "unidad": nombres[concepto][1],
                    "precio": precio, "importe": round(cantidad * precio, 2) if precio is not None else None})
    return res
