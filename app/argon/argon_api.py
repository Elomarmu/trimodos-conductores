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
import logging
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).parent))

from fastapi import APIRouter, HTTPException, Request  # noqa: E402

import argon_diario as D  # noqa: E402
import argon_escritor as E  # noqa: E402
import argon_formato as FM  # noqa: E402
import argon_historico as H  # noqa: E402
import argon_lector as L  # noqa: E402

CFG_DEFECTO = {"cliente_id": 1585, "empresa_id": 1, "tipo_pedido_id": 41, "fecha_inicio": "2026-10-15"}
log = logging.getLogger("argon")
router = APIRouter(prefix="/api/argon", tags=["argon"])


def config() -> dict:
    cfg = dict(CFG_DEFECTO)
    if os.environ.get("ARGON_CONFIG"):
        cfg.update(json.loads(os.environ["ARGON_CONFIG"]))
    return cfg


DASHBOARD_URL = "https://claude.ai/artifact/9Ah8EWNdwMsUS7oRYALREp"  # cuadro de mandos «Tráfico ARGON»


def enlaces(url_excel: str | None = None) -> str:
    """Cabecera del correo: el cuadro de mandos (para dejarlo como app en el móvil) y la Excel."""
    e = html.escape
    dash = os.environ.get("ARGON_DASHBOARD_URL", DASHBOARD_URL)
    boton = ("display:inline-block;padding:6px 12px;margin:0 6px 6px 0;border-radius:4px;background:#1F4E79;color:#fff;"
             "text-decoration:none;font:bold 10pt Calibri,Arial")
    partes = [f'<a href="{e(dash)}" style="{boton}">Abrir el cuadro de mandos</a>']
    if url_excel:
        partes.append(f'<a href="{e(url_excel)}" style="{boton};background:#217346">Abrir la Excel ARGON</a>')
    return ("<div style='margin:0 0 10px'>" + "".join(partes) +
            "<p style='font:9pt Calibri,Arial;color:#555;margin:2px 0 0'>Para tenerlo como app en el móvil: abre el cuadro "
            "de mandos y en el menú del navegador elige «Añadir a pantalla de inicio» (Android) o «Compartir → Añadir a "
            "pantalla de inicio» (iPhone).</p></div>")


def correo(hoy: dt.date, odoo_res: dict, av: dict, url_excel: str | None = None, url_relanzar: str | None = None) -> str:
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
    p = [enlaces(url_excel), "<!--ODOO-->", f"<p style='font:11pt Calibri,Arial'><b>ARGON · lectura del {hoy:%d/%m/%Y}{modo}</b><br>"
         f"Odoo: {len(odoo_res['pedidos_creados'])} pedidos nuevos, {len(odoo_res['pedidos_ampliados'])} ampliados, "
         f"{odoo_res['lineas_nuevas']} líneas nuevas"
         + (f", {len(odoo_res.get('lineas_corregidas', []))} corregidas y {len(odoo_res.get('lineas_anuladas', []))} anuladas "
            "por cambios en la Excel" if odoo_res.get("lineas_corregidas") or odoo_res.get("lineas_anuladas") else "")
         + ".</p>"]
    if odoo_res["errores"]:
        p.append(h3("Errores al escribir en Odoo") + tabla(["Detalle"], [[x] for x in odoo_res["errores"]]))
    p.append(h3("Pedidos nuevos") + tabla(["Pedido", "Contenedor", "Líneas", "Importe €"],
                                          [(ped(x), x["contenedor"], x["lineas"], x["importe"]) for x in odoo_res["pedidos_creados"]]))
    p.append(h3("Pedidos ampliados") + tabla(["Pedido", "Contenedor", "Líneas nuevas"],
                                             [(ped(x), x["contenedor"], x["lineas"]) for x in odoo_res["pedidos_ampliados"]]))
    p.append("<!--/ODOO-->")
    # lo ya pasado a Odoo que ha cambiado en la Excel (solo lo no facturado se toca)
    cab_c = ["Pedido", "Contenedor", "Fecha", "Línea", "Qué ha cambiado"]
    fila_c = lambda x: (ped(x), x["contenedor"], f(x["fecha"]), x["linea"], x["cambio"])  # noqa: E731
    if odoo_res.get("lineas_corregidas"):
        p.append(h3("Líneas corregidas en Odoo porque ha cambiado la Excel") +
                 tabla(cab_c, [fila_c(x) for x in odoo_res["lineas_corregidas"]]))
    if odoo_res.get("lineas_anuladas"):
        p.append(h3("Líneas anuladas en Odoo (cantidad 0): ya no salen de la Excel") +
                 "<p style='font:10pt Calibri,Arial;color:#555'>Odoo no deja borrar líneas de un pedido confirmado: se dejan "
                 "a 0 con «ANULADA» delante. Si el servicio ha cambiado de producto, la línea buena se ha añadido en el mismo pedido.</p>" +
                 tabla(cab_c, [fila_c(x) for x in odoo_res["lineas_anuladas"]]))
    if odoo_res.get("anulaciones_frenadas"):
        boton = ("" if not url_relanzar else
                 f"<p><a href=\"{html.escape(url_relanzar)}\" style=\"display:inline-block;padding:8px 14px;border-radius:4px;"
                 "background:#C00000;color:#fff;text-decoration:none;font:bold 10pt Calibri,Arial\">Revisado: relanzar y aplicar</a></p>")
        p.append(h3(f"PENDIENTE DE CONFIRMAR: {len(odoo_res['anulaciones_frenadas'])} líneas para anular") +
                 "<p style='font:10pt Calibri,Arial;color:#555'>Son demasiadas para una noche y no se ha anulado ninguna "
                 "(freno de seguridad). Revisadlas: si alguna está mal, corregid la Excel. Después pulsad el botón: el proceso "
                 "vuelve a leer la Excel con los cambios y anula lo que siga sin salir de ella; lo demás se da por bueno.</p>" +
                 tabla(cab_c, [fila_c(x) for x in odoo_res["anulaciones_frenadas"]]) + boton)
    if odoo_res.get("cambios_facturados"):
        p.append(h3("REVISAR A MANO: cambios en servicios ya facturados") +
                 "<p style='font:10pt Calibri,Arial;color:#555'>La Excel ya no coincide con lo facturado. No se ha tocado nada: "
                 "habría que hacer una rectificativa o corregir la celda.</p>" +
                 tabla(cab_c, [fila_c(x) for x in odoo_res["cambios_facturados"]]))
    p.append(h3("Servicios que no han pasado a Odoo por falta de datos") +
             tabla(["Fecha", "Contenedor", "Servicio", "Texto", "Falta"],
                   [(f(x["fecha"]), x["contenedor"], x["servicio"], ("html", a_excel(x["texto"], x.get("celda"))),
                     ("html", "; ".join(a_excel(i["dato"], i["celda"]) for i in x.get("ir", [])) or e(x["falta"])))
                    for x in odoo_res["pendientes_de_datos"]]))
    largos = odoo_res.get("bloqueos_largos", [])
    if largos:
        p.append(h3("Pedidos cerrados por bloqueo de más de 7 días") +
                 "<p style='font:10pt Calibri,Arial;color:#555'>El contenedor lleva más de 7 días en rojo (taller, embarcado a "
                 "destino…): su pedido se ha cerrado ahí y lo siguiente que haga va a un pedido nuevo.</p>" +
                 tabla(["Contenedor", "Motivo", "Desde", "Hasta", "Días"],
                       [(x["contenedor"], ("html", a_excel(x["texto"], x["celda"])), f(x["inicio"]),
                         f(x["salida"]) if x["salida"] else "sigue", x["dias"]) for x in largos]))
    no_encontradas = odoo_res.get("excel", {}).get("avisos", [])
    if no_encontradas:
        def en_odoo(x):
            ls = [f'<a href="{e(o["url"])}">{e(o["nombre"] or "")}</a>' if o.get("url") else e(o["nombre"] or "")
                  for o in x.get("odoo", [])]
            return ("html", "<br>".join(ls) or "—")
        p.append(h3("Datos de las tablas que no se encuentran en Odoo o están repetidos") +
                 tabla(["Hoja", "Dato (enlace a la celda)", "En Odoo"],
                       [(x["hoja"], ("html", a_excel(x["texto"], f"'{x['hoja']}'!{x['celda']}")), en_odoo(x))
                        for x in no_encontradas]))
    escritas = odoo_res.get("excel", {}).get("escrituras", [])
    if escritas:
        p.append(f"<p style='font:10pt Calibri,Arial;color:#555'>Se completan {len(escritas)} celdas de la Excel "
                 "(nombres, referencias e ID de productos, proveedores, ubicaciones y cuentas analíticas).</p>")
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
    cuerpo, url_excel, historico, extra = await request.body(), None, False, {}
    if cuerpo[:1] == b"{":  # n8n: {"excel_b64": "...", "url_excel": "..."} (su ejecutor de código no envía bien los binarios)
        try:
            datos = json.loads(cuerpo)
            cuerpo, url_excel = base64.b64decode(datos["excel_b64"]), datos.get("url_excel")
            historico = bool(datos.get("historico"))  # n8n sabe escribir rangos enteros (hoja HISTORICO)
            # botón del correo «relanzar y aplicar»: esta vez se anula sin freno de seguridad
            extra = {"sin_freno": bool(datos.get("sin_freno")), "url_relanzar": datos.get("url_relanzar")}
        except Exception:
            raise HTTPException(status_code=400, detail="JSON sin «excel_b64» válido") from None
    if not cuerpo.startswith(b"PK"):
        raise HTTPException(status_code=400, detail="El cuerpo debe ser la Excel (.xlsx)")
    dia = dt.date.fromisoformat(hoy) if hoy else dt.datetime.now().date()
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as t:
        t.write(cuerpo)
        ruta = t.name
    try:
        return _procesar(ruta, modo, dia, url_excel, historico, **extra)
    except Exception as e:  # el detalle vuelve a n8n y sale en el error del nodo (no contiene claves)
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e
    finally:
        os.unlink(ruta)


def _procesar(ruta: str, modo: str, dia: dt.date, url_excel: str | None = None, historico: bool = False,
              sin_freno: bool = False, url_relanzar: str | None = None) -> dict:
    cfg = config() | {"sin_freno": sin_freno}
    res = E.procesar(ruta, cfg, real=(modo == "real"), limite_s=18.0, hoy=dia)
    conts, estados = L.leer(ruta)
    av = D.avisos(conts, estados, [], dia)
    desde7 = (dia - dt.timedelta(days=7)).isoformat()
    av["sl_sin_numero"] = [x for x in estados if x.tipo == "SALIDA_TREN" and not x.referencia and desde7 <= x.fecha <= dia.isoformat()]
    av["vencimientos"] = D.vencimientos(ruta, dia)
    escr = list(res.get("excel", {}).get("escrituras", []))
    if historico and not res["restantes"]:  # solo en la última vuelta: el histórico del año para el cuadro de mandos
        try:
            escr += H.escrituras(ruta, dia)
        except Exception as ex:  # el histórico nunca debe parar los pedidos ni el correo
            log.exception("histórico ARGON")
            res.setdefault("errores", []).append(f"No se ha podido calcular el histórico: {ex}")
    return {"ok": True, "hoy": dia.isoformat(), "odoo": res, "restantes": res["restantes"],
            "escrituras_excel": escr,
            "correo_html": correo(dia, res, av, url_excel, url_relanzar), "recordatorios": D.recordatorios(av["vencimientos"], dia)}


@router.post("/formatear")
async def formatear(request: Request):
    """Ordena las columnas de las tablas (PRODUCTOS, TARIFAS, CONDUCTORES, RESERVAS) y les da a todas el mismo formato.
    Cuerpo: JSON {"excel_b64": ...}. Responde la Excel nueva en base64 (n8n la vuelve a subir a SharePoint)."""
    import openpyxl
    clave = os.environ.get("ARGON_CLAVE", "")
    recibida = request.headers.get("x-clave", "").encode("latin-1", "replace")
    formas = [clave.encode("utf-8")] + ([clave.encode("latin-1")] if all(ord(c) < 256 for c in clave) else [])
    if not clave or not any(hmac.compare_digest(recibida, f) for f in formas):
        raise HTTPException(status_code=401, detail="Clave incorrecta")
    try:
        cuerpo = base64.b64decode(json.loads(await request.body())["excel_b64"])
    except Exception:
        raise HTTPException(status_code=400, detail="JSON sin «excel_b64» válido") from None
    if not cuerpo.startswith(b"PK"):
        raise HTTPException(status_code=400, detail="El cuerpo debe ser la Excel (.xlsx)")
    with tempfile.TemporaryDirectory() as d:
        ruta = os.path.join(d, "ARGON.xlsx")
        with open(ruta, "wb") as fh:
            fh.write(cuerpo)
        try:
            wb = openpyxl.load_workbook(ruta)
            resumen = FM.ordenar(wb)
            wb.save(ruta)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e
        with open(ruta, "rb") as fh:
            nuevo = fh.read()
    return {"ok": True, "resumen": resumen, "excel_b64": base64.b64encode(nuevo).decode()}
