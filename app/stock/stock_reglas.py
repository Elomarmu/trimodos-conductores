"""Control de stock del almacén de Burriana: reglas puras (sin red, sin Odoo), como `pr_reglas` en los viajes de PR.

Modelo (todo son diccionarios JSON; lo guarda `stock_store`):
  entrada  E-2026-0001  albarán del cliente (lo carga la oficina) + palets verificados con la etiqueta (almacén)
  salida   S-2026-0001  petición de salida del cliente (oficina) + palets cargados fotografiando su etiqueta (almacén)
  stock    = palets de las entradas cerradas que no han salido en una salida cerrada (se calcula, no se guarda)
Cada palet verificado y cada palet cargado es un MOVIMIENTO con cliente, referencia, kg y fecha: es la base de la
futura facturación en Odoo (entradas, salidas y estocaje), ver `stock_facturacion`.
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata

ALMACEN = "STINSA · Almacén de Burriana"
TOLERANCIA_KG = 0.02          # diferencia de peso admitida entre albarán y etiquetas (2 %)
DIAS_ALBARAN_ANTIGUO = 30     # un albarán más viejo que esto se avisa

ENTRADA_PENDIENTE, ENTRADA_DESCARGANDO, ENTRADA_CERRADA, ENTRADA_ANULADA = "pendiente", "descargando", "cerrada", "anulada"
SALIDA_PEDIDA, SALIDA_PREPARANDO, SALIDA_CARGADA, SALIDA_ANULADA = "pedida", "preparando", "cargada", "anulada"


class ErrorStock(Exception):
    """Mensaje claro para oficina o almacén (se muestra tal cual)."""


# ---- normalización -------------------------------------------------------------------------------------------------

def norm(texto) -> str:
    """Mayúsculas, sin tildes ni signos: «Kerakoll, S.p.A.» → «KERAKOLL S P A»."""
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]+", " ", t).strip()


def referencia(texto) -> str:
    """Referencia de producto comparable: sin espacios, guiones ni puntos («KK-1234 » = «kk1234»)."""
    return re.sub(r"[^A-Z0-9]", "", norm(texto))


def entero(valor) -> int | None:
    """«24.800» → 24800 · «24800,0» → 24800 · vacío → None."""
    if valor in (None, ""):
        return None
    if isinstance(valor, (int, float)):
        return int(round(valor))
    t = str(valor).strip().replace(" ", "")
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", t):
        t = t.replace(".", "")
    t = t.replace(",", ".")
    try:
        return int(round(float(t)))
    except ValueError:
        return None


def sscc(texto) -> str:
    """Código SSCC de la etiqueta logística (18 cifras; la lectura puede traer «(00)» o espacios). Si no hay 18 cifras
    se devuelve lo que haya (solo cifras) y el palet se identifica por referencia y lote."""
    t = re.sub(r"\D", "", str(texto or ""))
    if len(t) == 20 and t.startswith("00"):
        t = t[2:]
    return t


def fecha(texto, hoy: dt.date | None = None) -> str:
    """AAAA-MM-DD o «» (acepta DD/MM/AAAA y DD-MM-AA)."""
    t = str(texto or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", t) or None
    try:
        if m:
            return dt.date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})", t)
        if m:
            anio = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
            return dt.date(anio, int(m[2]), int(m[1])).isoformat()
    except ValueError:
        pass
    return ""


def dias_entre(desde: str, hasta: str) -> int:
    try:
        return max(0, (dt.date.fromisoformat(hasta[:10]) - dt.date.fromisoformat(desde[:10])).days)
    except ValueError:
        return 0


def siguiente_id(prefijo: str, existentes: list[str], anio: int) -> str:
    """E-2026-0001, E-2026-0002… (el siguiente al mayor que haya ese año)."""
    patron = re.compile(rf"^{re.escape(prefijo)}-{anio}-(\d+)$")
    mayor = max([int(m[1]) for x in existentes if (m := patron.match(x))] or [0])
    return f"{prefijo}-{anio}-{mayor + 1:04d}"


# ---- clientes ------------------------------------------------------------------------------------------------------
# Un cliente: {"codigo": "KERAKOLL", "nombre": "Kerakoll Iberica, S.A.U.", "alias": ["KERAKOLL", "ALMAZORA"], "odoo_partner_id": None}
# `odoo_partner_id` queda vacío hasta la integración con la facturación de Odoo.

def sugerir_clientes(clientes: list[dict], *textos) -> list[tuple[dict, int]]:
    """Clientes que encajan con los textos del albarán (remitente, destinatario, cabecera), de más a menos probable.
    Puntúa 3 si el nombre o un alias aparece entero, 1 por cada palabra larga del nombre que coincide."""
    leidos = [norm(t) for t in textos if t]
    res = []
    for c in clientes:
        if c.get("activo") is False:
            continue
        claves = {norm(c.get("nombre")), norm(c.get("codigo"))} | {norm(a) for a in c.get("alias") or []}
        claves.discard("")
        puntos = 0
        for t in leidos:
            if any(f" {k} " in f" {t} " for k in claves):
                puntos += 3
            else:
                palabras = {p for k in claves for p in k.split() if len(p) >= 4}
                puntos += sum(1 for p in palabras if f" {p} " in f" {t} ")
        if puntos:
            res.append((c, puntos))
    return sorted(res, key=lambda x: -x[1])


def cliente_por_codigo(clientes: list[dict], codigo: str) -> dict | None:
    return next((c for c in clientes if c.get("codigo") == codigo), None)


# ---- entradas: conflictos al cargar el albarán ---------------------------------------------------------------------

def aviso(nivel: str, texto: str) -> dict:
    """nivel «error» (hay que revisarlo antes de seguir) o «aviso» (se puede seguir)."""
    return {"nivel": nivel, "texto": texto}


def limpiar_albaran(leido: dict, hoy: dt.date | None = None) -> dict:
    """Lo que devuelve el lector → albarán de entrada con datos normalizados (la oficina lo revisa en pantalla)."""
    lineas = []
    for l in leido.get("lineas") or []:
        ref, desc = str(l.get("referencia") or "").strip(), str(l.get("descripcion") or "").strip()
        if not (ref or desc):
            continue
        lineas.append({"ref": ref, "descripcion": desc, "palets": entero(l.get("palets")), "bultos": entero(l.get("bultos")),
                       "kg": entero(l.get("kg")), "lote": str(l.get("lote") or "").strip()})
    return {"numero": str(leido.get("numero_albaran") or "").strip(), "fecha": fecha(leido.get("fecha"), hoy),
            "remitente": str(leido.get("remitente") or "").strip(), "destinatario": str(leido.get("destinatario") or "").strip(),
            "transportista": str(leido.get("transportista") or "").strip(),
            "matricula": re.sub(r"[^A-Z0-9]", "", norm(leido.get("matricula"))),
            "total_palets": entero(leido.get("total_palets")), "total_kg": entero(leido.get("total_kg")),
            "observaciones": str(leido.get("observaciones") or "").strip(), "lineas": lineas,
            "dudas": [str(d) for d in leido.get("dudas") or []]}


def conflictos_entrada(albaran: dict, cliente: dict | None, clientes: list[dict], entradas: list[dict],
                       hoy: dt.date | None = None) -> list[dict]:
    """Avisos de una entrada antes de guardarla. `entradas` = las ya guardadas (para duplicados e historial del cliente)."""
    hoy = hoy or dt.date.today()
    av: list[dict] = []
    sugeridos = sugerir_clientes(clientes, albaran.get("remitente"), albaran.get("destinatario"))
    if not cliente:
        if not sugeridos:
            av.append(aviso("error", "No reconozco al cliente del albarán: elígelo de la lista (o da de alta uno nuevo)."))
        elif len(sugeridos) > 1 and sugeridos[0][1] == sugeridos[1][1]:
            av.append(aviso("error", f"El albarán encaja con varios clientes ({sugeridos[0][0]['nombre']}, {sugeridos[1][0]['nombre']}): elige uno."))
    elif sugeridos and sugeridos[0][0]["codigo"] != cliente["codigo"]:
        av.append(aviso("aviso", f"Has elegido {cliente['nombre']}, pero el albarán parece de {sugeridos[0][0]['nombre']}."))
    elif not sugeridos:
        av.append(aviso("aviso", f"El albarán no nombra a {cliente['nombre']}: comprueba que es suyo."))
    num = albaran.get("numero")
    vivas = [e for e in entradas if e.get("estado") != ENTRADA_ANULADA]
    if not num:
        av.append(aviso("aviso", "No se lee el número de albarán: escríbelo a mano."))
    else:
        for e in vivas:
            if norm(e["albaran"].get("numero")) == norm(num):
                mismo = cliente and e.get("cliente", {}).get("codigo") == cliente["codigo"]
                av.append(aviso("error" if mismo else "aviso", f"El albarán {num} ya está cargado como entrada {e['id']}"
                                + ("" if mismo else f" (de {e['cliente']['nombre']})") + "."))
    f = albaran.get("fecha")
    if not f:
        av.append(aviso("aviso", "No se lee la fecha del albarán."))
    else:
        d = dt.date.fromisoformat(f)
        if d > hoy:
            av.append(aviso("aviso", f"La fecha del albarán ({f}) es futura."))
        elif (hoy - d).days > DIAS_ALBARAN_ANTIGUO:
            av.append(aviso("aviso", f"El albarán es de hace {(hoy - d).days} días."))
    dest = norm(albaran.get("destinatario"))
    if dest and not any(k in dest for k in ("BURRIANA", "STINSA", "TRIMODOS")):
        av.append(aviso("aviso", f"El destinatario del albarán es «{albaran['destinatario']}»: ¿viene de verdad a Burriana?"))
    lineas = albaran.get("lineas") or []
    if not lineas:
        av.append(aviso("error", "El albarán no tiene líneas de mercancía."))
    for i, l in enumerate(lineas, start=1):
        if not l.get("palets"):
            av.append(aviso("error", f"La línea {i} ({l.get('ref') or l.get('descripcion')}) no tiene nº de palets."))
        if not l.get("ref"):
            av.append(aviso("aviso", f"La línea {i} no tiene referencia: la etiqueta se casará por la descripción."))
    suma = sum(l.get("palets") or 0 for l in lineas)
    if albaran.get("total_palets") and albaran["total_palets"] != suma:
        av.append(aviso("error", f"El albarán dice {albaran['total_palets']} palets en total y las líneas suman {suma}."))
    suma_kg = sum(l.get("kg") or 0 for l in lineas)
    if albaran.get("total_kg") and suma_kg and abs(albaran["total_kg"] - suma_kg) > TOLERANCIA_KG * albaran["total_kg"]:
        av.append(aviso("aviso", f"El albarán dice {albaran['total_kg']} kg en total y las líneas suman {suma_kg} kg."))
    if cliente:   # mercancía que ese cliente nunca ha traído
        conocidas = {referencia(l["ref"]) for e in vivas if e.get("cliente", {}).get("codigo") == cliente["codigo"]
                     and e["estado"] == ENTRADA_CERRADA for l in e["lineas"] if l.get("ref")}
        if conocidas:
            for l in lineas:
                if l.get("ref") and referencia(l["ref"]) not in conocidas:
                    av.append(aviso("aviso", f"La referencia {l['ref']} no la había traído {cliente['nombre']} antes."))
    av += [aviso("aviso", f"Duda al leer: {d}") for d in albaran.get("dudas") or []]
    return av


# ---- entradas: verificación de palets con la etiqueta --------------------------------------------------------------

def limpiar_etiqueta(leida: dict) -> dict:
    return {"sscc": sscc(leida.get("sscc")), "ref": str(leida.get("referencia") or "").strip(),
            "descripcion": str(leida.get("descripcion") or "").strip(), "lote": str(leida.get("lote") or "").strip(),
            "kg": entero(leida.get("kg")), "bultos": entero(leida.get("bultos")),
            "cliente_texto": str(leida.get("cliente") or "").strip(), "caducidad": fecha(leida.get("caducidad")),
            "dudas": [str(d) for d in leida.get("dudas") or []]}


def _linea_de(lineas: list[dict], etiqueta: dict) -> int | None:
    """Línea del albarán a la que pertenece la etiqueta: misma referencia (y mismo lote si la línea lo trae)."""
    ref = referencia(etiqueta.get("ref"))
    candidatas = [i for i, l in enumerate(lineas) if ref and referencia(l.get("ref")) == ref]
    if not candidatas and not ref and etiqueta.get("descripcion"):   # sin referencia: por descripción
        candidatas = [i for i, l in enumerate(lineas) if norm(l.get("descripcion")) == norm(etiqueta["descripcion"])]
    con_lote = [i for i in candidatas if lineas[i].get("lote") and norm(lineas[i]["lote"]) == norm(etiqueta.get("lote"))]
    return (con_lote or candidatas or [None])[0]


def verificar_palet(entrada: dict, etiqueta: dict, sscc_en_stock: set[str]) -> dict:
    """Casa la etiqueta de un palet con el albarán. Devuelve el palet listo para guardar:
    estado «ok» o «discrepancia» (con el motivo). Nunca se rechaza una etiqueta: queda anotada y se resuelve al cerrar."""
    lineas = entrada["lineas"]
    idx = _linea_de(lineas, etiqueta)
    palet = {**etiqueta, "linea": idx, "estado": "ok", "motivo": ""}
    ya = [p for p in entrada["palets"] if p.get("estado_alta") != "quitado"]
    if etiqueta.get("sscc") and len(etiqueta["sscc"]) >= 18 and (
            any(p.get("sscc") == etiqueta["sscc"] for p in ya) or etiqueta["sscc"] in sscc_en_stock):
        palet.update(estado="discrepancia", motivo="Esta etiqueta (SSCC) ya está registrada: palet repetido.")
    elif idx is None:
        palet.update(estado="discrepancia", motivo="Este palet no figura en el albarán"
                     + (f" (referencia {etiqueta['ref']})." if etiqueta.get("ref") else " (no se lee la referencia)."))
    else:
        esperados = lineas[idx].get("palets") or 0
        verificados = sum(1 for p in ya if p.get("linea") == idx)
        l = lineas[idx]
        if verificados >= esperados:
            palet.update(estado="discrepancia", motivo=f"Sobra: el albarán trae {esperados} palets de {l['ref'] or l['descripcion']} y ya están todos.")
        elif l.get("lote") and etiqueta.get("lote") and norm(l["lote"]) != norm(etiqueta["lote"]):
            palet.update(estado="discrepancia", motivo=f"Lote distinto: etiqueta {etiqueta['lote']}, albarán {l['lote']}.")
    cl = entrada.get("cliente") or {}
    if palet["estado"] == "ok" and etiqueta.get("cliente_texto") and cl and not sugerir_clientes([cl], etiqueta["cliente_texto"]):
        palet.update(estado="discrepancia", motivo=f"La etiqueta es de «{etiqueta['cliente_texto']}», no de {cl.get('nombre')}.")
    return palet


def activos(entrada: dict) -> list[dict]:
    return [p for p in entrada["palets"] if p.get("estado_alta") != "quitado"]


def estado_lineas(entrada: dict) -> list[dict]:
    """Por línea del albarán: palets esperados / verificados y kg del albarán / de las etiquetas."""
    res = []
    for i, l in enumerate(entrada["lineas"]):
        mios = [p for p in activos(entrada) if p.get("linea") == i and p["estado"] == "ok"]
        res.append({"linea": i, "ref": l.get("ref"), "descripcion": l.get("descripcion"), "lote": l.get("lote"),
                    "esperados": l.get("palets") or 0, "verificados": len(mios), "kg_albaran": l.get("kg"),
                    "kg_etiquetas": sum(p.get("kg") or 0 for p in mios) or None})
    return res


def discrepancias_entrada(entrada: dict) -> list[str]:
    """Todo lo que no cuadra al terminar la descarga (vacío = se puede cerrar sin más)."""
    res = [f"{p['ref'] or p['descripcion'] or 'Palet'}: {p['motivo']}" for p in activos(entrada) if p["estado"] == "discrepancia"]
    for e in estado_lineas(entrada):
        nombre = e["ref"] or e["descripcion"]
        if e["verificados"] < e["esperados"]:
            res.append(f"Faltan {e['esperados'] - e['verificados']} palets de {nombre} (albarán {e['esperados']}, verificados {e['verificados']}).")
        ka, ke = e["kg_albaran"], e["kg_etiquetas"]
        if ka and ke and e["verificados"] == e["esperados"] and abs(ka - ke) > TOLERANCIA_KG * ka:
            res.append(f"{nombre}: el albarán dice {ka} kg y las etiquetas suman {ke} kg.")
    return res


# ---- stock y salidas ------------------------------------------------------------------------------------------------

def stock(entradas: list[dict], salidas: list[dict]) -> list[dict]:
    """Palets en el almacén: los verificados de entradas cerradas menos los de salidas cargadas.
    Palet de stock: {id, cliente, ref, descripcion, lote, sscc, kg, bultos, entrada, albaran, fecha_entrada}."""
    fuera = {p["palet_id"] for s in salidas if s["estado"] == SALIDA_CARGADA for p in s["palets"]
             if p.get("palet_id") and p.get("estado_alta") != "quitado" and (p["estado"] == "ok" or p.get("aceptado"))}
    res = []
    for e in sorted(entradas, key=lambda e: e["id"]):
        if e["estado"] != ENTRADA_CERRADA:
            continue
        for p in activos(e):
            if p["estado"] != "ok" and not p.get("aceptado"):
                continue
            if p["id"] in fuera:
                continue
            res.append({"id": p["id"], "cliente": e["cliente"]["codigo"], "cliente_nombre": e["cliente"]["nombre"], "ref": p["ref"],
                        "descripcion": p["descripcion"], "lote": p["lote"], "sscc": p["sscc"], "kg": p["kg"], "bultos": p.get("bultos"),
                        "entrada": e["id"], "albaran": e["albaran"].get("numero"), "fecha_entrada": e["cerrada"][:10]})
    return res


def reservados(salidas: list[dict], salvo: str = "") -> set[str]:
    """Palets ya elegidos en otras salidas todavía abiertas (para no cargar el mismo dos veces)."""
    return {p["palet_id"] for s in salidas if s["id"] != salvo and s["estado"] in (SALIDA_PEDIDA, SALIDA_PREPARANDO)
            for p in s["palets"] if p.get("palet_id") and p["estado"] == "ok"}


def limpiar_peticion(leida: dict) -> dict:
    """Lo que devuelve el lector con la petición de salida → {cliente_texto, referencia, destino, fecha, lineas}."""
    lineas = [{"ref": str(l.get("referencia") or "").strip(), "descripcion": str(l.get("descripcion") or "").strip(),
               "palets": entero(l.get("palets"))} for l in leida.get("lineas") or [] if (l.get("referencia") or l.get("descripcion"))]
    return {"cliente_texto": str(leida.get("cliente") or "").strip(), "referencia": str(leida.get("referencia_cliente") or "").strip(),
            "destino": str(leida.get("destino") or "").strip(), "fecha": fecha(leida.get("fecha")), "lineas": lineas,
            "dudas": [str(d) for d in leida.get("dudas") or []]}


def conflictos_salida(salida: dict, existencias: list[dict]) -> list[dict]:
    """Avisos al cargar la petición: pide más de lo que hay en stock de ese cliente, o algo que no tiene."""
    av = []
    suyo = [p for p in existencias if p["cliente"] == salida["cliente"]["codigo"]]
    for i, l in enumerate(salida["lineas"], start=1):
        if not l.get("palets"):
            av.append(aviso("error", f"La línea {i} ({l.get('ref') or l.get('descripcion')}) no tiene nº de palets."))
            continue
        hay = sum(1 for p in suyo if referencia(p["ref"]) == referencia(l.get("ref")) or (not l.get("ref") and norm(p["descripcion"]) == norm(l.get("descripcion"))))
        if hay == 0:
            av.append(aviso("error", f"{salida['cliente']['nombre']} no tiene en stock {l.get('ref') or l.get('descripcion')}."))
        elif hay < l["palets"]:
            av.append(aviso("aviso", f"Piden {l['palets']} palets de {l.get('ref') or l.get('descripcion')} y solo hay {hay} en stock."))
    return av


def _linea_salida(lineas: list[dict], palet: dict) -> int | None:
    ref = referencia(palet.get("ref"))
    for i, l in enumerate(lineas):
        if (ref and referencia(l.get("ref")) == ref) or (not ref and not l.get("ref") and norm(l.get("descripcion")) == norm(palet.get("descripcion"))):
            return i
    return None


def cargar_palet(salida: dict, etiqueta: dict, existencias: list[dict], otras_salidas: list[dict]) -> dict:
    """Casa la etiqueta de un palet que el almacén va a cargar con el stock y con lo pedido.
    Devuelve el palet de la salida: {palet_id, sscc, ref, lote, kg, linea, estado «ok»|«discrepancia», motivo, aviso}."""
    cod = salida["cliente"]["codigo"]
    ya = [p for p in salida["palets"] if p.get("estado_alta") != "quitado"]
    tomados = {p.get("palet_id") for p in ya if p["estado"] == "ok"} | reservados(otras_salidas, salida["id"])
    candidatos = [p for p in existencias if p["id"] not in tomados]
    if etiqueta.get("sscc") and len(etiqueta["sscc"]) >= 18:
        hallado = next((p for p in existencias if p["sscc"] == etiqueta["sscc"]), None)
        if hallado and (hallado["id"] in tomados):
            return _salida_palet(None, etiqueta, "discrepancia", "Este palet ya está cargado o reservado en otra salida.")
    else:
        hallado = next((p for p in candidatos if referencia(p["ref"]) == referencia(etiqueta.get("ref"))
                        and (not etiqueta.get("lote") or norm(p["lote"]) == norm(etiqueta["lote"])) and p["cliente"] == cod), None)
    if not hallado:
        return _salida_palet(None, etiqueta, "discrepancia", "Este palet no está en el stock del almacén (¿no se dio de alta en la entrada?).")
    if hallado["cliente"] != cod:
        return _salida_palet(hallado, etiqueta, "discrepancia", f"Este palet es de {hallado['cliente_nombre']}, no de {salida['cliente']['nombre']}.")
    idx = _linea_salida(salida["lineas"], hallado)
    if idx is None:
        return _salida_palet(hallado, etiqueta, "discrepancia", f"No se ha pedido {hallado['ref'] or hallado['descripcion']}.")
    cargados = sum(1 for p in ya if p.get("linea") == idx and p["estado"] == "ok")
    if cargados >= (salida["lineas"][idx].get("palets") or 0):
        return _salida_palet(hallado, etiqueta, "discrepancia", f"Sobra: ya están los {salida['lineas'][idx]['palets']} palets pedidos de {hallado['ref'] or hallado['descripcion']}.", idx)
    p = _salida_palet(hallado, etiqueta, "ok", "", idx)
    antiguo = [x for x in candidatos if x["id"] != hallado["id"] and referencia(x["ref"]) == referencia(hallado["ref"])
               and x["fecha_entrada"] < hallado["fecha_entrada"]]
    if antiguo:
        p["aviso"] = f"Hay un palet más antiguo de la misma referencia ({antiguo[0]['id']}, entrada del {antiguo[0]['fecha_entrada']})."
    return p


def _salida_palet(stock_palet: dict | None, etiqueta: dict, estado: str, motivo: str, linea: int | None = None) -> dict:
    base = stock_palet or {}
    return {"palet_id": base.get("id"), "sscc": etiqueta.get("sscc") or base.get("sscc") or "", "ref": etiqueta.get("ref") or base.get("ref") or "",
            "descripcion": base.get("descripcion") or etiqueta.get("descripcion") or "", "lote": etiqueta.get("lote") or base.get("lote") or "",
            "kg": base.get("kg") or etiqueta.get("kg"), "linea": linea, "estado": estado, "motivo": motivo, "aviso": ""}


def estado_lineas_salida(salida: dict) -> list[dict]:
    res = []
    for i, l in enumerate(salida["lineas"]):
        cargados = [p for p in salida["palets"] if p.get("estado_alta") != "quitado" and p.get("linea") == i and p["estado"] == "ok"]
        res.append({"linea": i, "ref": l.get("ref"), "descripcion": l.get("descripcion"), "pedidos": l.get("palets") or 0,
                    "cargados": len(cargados), "kg": sum(p.get("kg") or 0 for p in cargados) or None})
    return res


def discrepancias_salida(salida: dict) -> list[str]:
    res = [f"{p['ref'] or p['descripcion'] or 'Palet'}: {p['motivo']}" for p in salida["palets"]
           if p.get("estado_alta") != "quitado" and p["estado"] == "discrepancia"]
    for e in estado_lineas_salida(salida):
        if e["cargados"] < e["pedidos"]:
            res.append(f"Faltan {e['pedidos'] - e['cargados']} palets de {e['ref'] or e['descripcion']} (pedidos {e['pedidos']}, cargados {e['cargados']}).")
    return res
