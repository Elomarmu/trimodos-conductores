"""Servicio web ARGON (FastAPI) para el servidor de la app de conductores (Vercel).

POST /api/argon/procesar   cuerpo = la Excel (.xlsx) tal cual o JSON {"excel_b64": ...}; cabecera X-Clave = ARGON_CLAVE
    ?modo=prueba|real   (prueba: lo calcula todo pero no escribe en Odoo)
    ?hoy=AAAA-MM-DD     (opcional, para pruebas)
Responde JSON: resumen de Odoo, restantes (si hay que volver a llamar), correo de avisos (HTML) y recordatorios.

Variables de entorno: ARGON_CLAVE, ARGON_ODOO_URL, ARGON_ODOO_DB, ARGON_ODOO_USER, ARGON_ODOO_PASSWORD (clave API) y opcionalmente
ARGON_CONFIG (JSON con cliente_id, empresa_id, tipo_pedido_id, fecha_inicio).
"""
from __future__ import annotations

import base64
import datetime as dt
import html
import hmac
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent))

from fastapi import APIRouter, HTTPException, Request  # noqa: E402

import argon_diario as D  # noqa: E402
import argon_escritor as E  # noqa: E402
import argon_lector as L  # noqa: E402

CFG_DEFECTO = {"cliente_id": 1585, "empresa_id": 1, "tipo_pedido_id": 41, "fecha_inicio": "2026-10-15"}
router = APIRouter(prefix="/api/argon", tags=["argon"])


def config() -> dict:
    cfg = dict(CFG_DEFECTO)
    if os.environ.get("ARGON_CONFIG"):
        cfg.update(json.loads(os.environ["ARGON_CONFIG"]))
    return cfg


def correo(hoy: dt.date, odoo_res: dict, av: dict, url_excel: str | None = None) -> str:
    e = html.escape
    td = 'style="padding:3px 8px;border-bottom:1px solid #ddd"'
    th = 'style="padding:4px 8px;background:#1F4E79;color:#fff;text-align:left"'
    f = lambda d: dt.date.fromisoformat(d).strftime("%d/%m")  # noqa: E731

    def tabla(cab, filas):
        if not filas:
            return "<p style='color:#555'>Nada.</p>"
        h = "".join(f"<th {th}>{e(c)}</th>" for c in cab)
        celda = lambda v: v[1] if isinstance(v, tuple) and v and v[0] == "html" else e(str(v if v is not None else ""))  # noqa: E731
        b = "".join("<tr>" + "".join(f"<td {td}>{celda(v)}</td>" for v in fila) + "</tr>" for fila in filas)
        return f"<table style='border-collapse:collapse;font:10pt Calibri,Arial'><tr>{h}</tr>{b}</table>"

    def ped(x):  # nº de pedido con enlace a Odoo
        return ("html", f'<a href="{e(x["url"])}">{e(x["pedido"])}</a>') if x.get("url") else x["pedido"]

    def a_excel(texto, celda):  # enlace que abre la Excel de SharePoint en esa celda
        if not (url_excel and celda):
            return e(texto)
        sep = "&" if "?" in url_excel else "?"
        return f'<a href="{e(url_excel + sep + "activeCell=" + quote(celda, safe=""))}">{e(texto)}</a>'

    h3 = lambda t: f"<h3 style='font:bold 11pt Calibri,Arial;margin:14px 0 4px'>{e(t)}</h3>"  # noqa: E731
    modo = "" if odoo_res.get("modo") == "real" else " (MODO PRUEBA: no se ha escrito nada)"
    p = ["<!--ODOO-->", f"<p style='font:11pt Calibri,Arial'><b>ARGON · lectura del {hoy:%d/%m/%Y}{modo}</b><br>"
         f"Odoo: {len(odoo_res['pedidos_creados'])} pedidos nuevos, {len(odoo_res['pedidos_ampliados'])} ampliados, "
         f"{odoo_res['lineas_nuevas']} líneas nuevas.</p>"]
    if odoo_res["errores"]:
        p.append(h3("Errores al escribir en Odoo") + tabla(["Detalle"], [[x] for x in odoo_res["errores"]]))
    p.append(h3("Pedidos nuevos") + tabla(["Pedido", "Contenedor", "Líneas", "Importe €"],
                                          [(ped(x), x["contenedor"], x["lineas"], x["importe"]) for x in odoo_res["pedidos_creados"]]))
    p.append(h3("Pedidos ampliados") + tabla(["Pedido", "Contenedor", "Líneas nuevas"],
                                             [(ped(x), x["contenedor"], x["lineas"]) for x in odoo_res["pedidos_ampliados"]]))
    p.append("<!--/ODOO-->")
    p.append(h3("Servicios que no han pasado a Odoo por falta de datos") +
             tabla(["Fecha", "Contenedor", "Servicio", "Texto", "Falta"],
                   [(f(x["fecha"]), x["contenedor"], x["servicio"], ("html", a_excel(x["texto"], x.get("celda"))),
                     ("html", "; ".join(a_excel(i["dato"], i["celda"]) for i in x.get("ir", [])) or e(x["falta"])))
                    for x in odoo_res["pendientes_de_datos"]]))
    no_encontradas = odoo_res.get("excel", {}).get("avisos", [])
    if no_encontradas:
        p.append(h3("Referencias de la Excel que no se encuentran en Odoo") +
                 tabla(["Hoja", "Dato"], [(x["hoja"], ("html", a_excel(x["texto"], f"'{x['hoja']}'!{x['celda']}")))
                                          for x in no_encontradas]))
    escritas = odoo_res.get("excel", {}).get("escrituras", [])
    if escritas:
        p.append(f"<p style='font:10pt Calibri,Arial;color:#555'>Se completan {len(escritas)} celdas de la Excel "
                 "(nombres, ID y referencias de productos y proveedores).</p>")
    p.append(h3("Bloqueados hoy") + tabla(["Contenedor", "Motivo"], [(k.matricula, u.texto) for k, u in av["bloqueados"]]))
    p.append(h3(f"Sin anotar desde hace {D.DIAS_SIN_ANOTAR} días o más") +
             tabla(["Contenedor", "Última anotación", "Fecha", "Días"],
                   [(k.matricula, u.texto, f(u.fecha), d) for k, u, d in sorted(av["sin_anotar"], key=lambda x: -x[2])]))
    p.append(h3("Salidas en tren sin nº de expedición (últimos 7 días)") +
             tabla(["Fecha", "Contenedor", "Celda", "Texto"], [(f(x.fecha), x.contenedor, x.celda, x.texto) for x in av["sl_sin_numero"]]))
    p.append(h3("Para revisar en la Excel (últimos 7 días)") +
             tabla(["Fecha", "Contenedor", "Celda", "Texto", "Aviso"],
                   [(f(x.fecha), x.contenedor, x.celda, x.texto, "; ".join(x.avisos)) for x in av["revisar"]]))
    venc = [v for v in av["vencimientos"] if v["estado"] != "OK"]
    p.append(h3("Vencimientos ADR / ITV") + tabla(["Elemento", "Tipo", "Vence", "Días", "Estado"],
                                                 [(f"{v['elemento']} {v['id']}", v["tipo"], f(v["vence"]), v["dias"], v["estado"])
                                                  for v in sorted(venc, key=lambda x: x["dias"])]))
    return "\n".join(p)


@router.post("/procesar")
async def procesar(request: Request, modo: str = "prueba", hoy: str | None = None):
    clave = os.environ.get("ARGON_CLAVE", "")
    # Comparación en bytes: la clave puede llevar tildes, «ñ» o «·». Según quién llame, esos caracteres viajan
    # en UTF-8 (2 bytes) o en latin-1 (1 byte, así lo hace n8n); se aceptan las dos formas.
    recibida = request.headers.get("x-clave", "").encode("latin-1", "replace")
    formas = [clave.encode("utf-8")] + ([clave.encode("latin-1")] if all(ord(c) < 256 for c in clave) else [])
    if not clave or not any(hmac.compare_digest(recibida, f) for f in formas):
        raise HTTPException(status_code=401, detail="Clave incorrecta")
    cuerpo, url_excel = await request.body(), None
    if cuerpo[:1] == b"{":  # n8n: {"excel_b64": "...", "url_excel": "..."} (su ejecutor de código no envía bien los binarios)
        try:
            datos = json.loads(cuerpo)
            cuerpo, url_excel = base64.b64decode(datos["excel_b64"]), datos.get("url_excel")
        except Exception:
            raise HTTPException(status_code=400, detail="JSON sin «excel_b64» válido") from None
    if not cuerpo.startswith(b"PK"):
        raise HTTPException(status_code=400, detail="El cuerpo debe ser la Excel (.xlsx)")
    dia = dt.date.fromisoformat(hoy) if hoy else dt.datetime.now().date()
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as t:
        t.write(cuerpo)
        ruta = t.name
    try:
        return _procesar(ruta, modo, dia, url_excel)
    except Exception as e:  # el detalle vuelve a n8n y sale en el error del nodo (no contiene claves)
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e
    finally:
        os.unlink(ruta)


def _procesar(ruta: str, modo: str, dia: dt.date, url_excel: str | None = None) -> dict:
    cfg = config()
    res = E.procesar(ruta, cfg, real=(modo == "real"), limite_s=18.0)
    conts, estados = L.leer(ruta)
    av = D.avisos(conts, estados, [], dia)
    desde7 = (dia - dt.timedelta(days=7)).isoformat()
    av["sl_sin_numero"] = [x for x in estados if x.tipo == "SALIDA_TREN" and not x.referencia and desde7 <= x.fecha <= dia.isoformat()]
    av["vencimientos"] = D.vencimientos(ruta, dia)
    return {"ok": True, "hoy": dia.isoformat(), "odoo": res, "restantes": res["restantes"],
            "escrituras_excel": res.get("excel", {}).get("escrituras", []),
            "correo_html": correo(dia, res, av, url_excel), "recordatorios": D.recordatorios(av["vencimientos"], dia)}
