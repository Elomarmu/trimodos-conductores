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
MIN_CODIGO = 8                # caracteres mínimos para tomar el código de la etiqueta como identificador del palet (SSCC de 18 o código de fábrica de 10)
DIAS_ALBARAN_FUTURO = 3       # un albarán fechado como mucho 3 días después de hoy es normal (se emite para el viaje de mañana)
DIAS_ALBARAN_ANTIGUO = 30     # un albarán más viejo que esto se avisa
DIAS_ESTOCAJE_AVISO = 30      # mercancía que lleva en el almacén más de un mes (solo se avisa: no hay FIFO obligatorio)

ENTRADA_PENDIENTE, ENTRADA_DESCARGANDO, ENTRADA_CERRADA, ENTRADA_ANULADA = "pendiente", "descargando", "cerrada", "anulada"
CONTROL_ABIERTO, CONTROL_CERRADO = "abierto", "cerrado"
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


UBICACIONES = [("Estanterías", [f"E{n}" for n in range(1, 7)]), ("Suelo", [f"S{n}" for n in range(1, 11)])]   # zonas del almacén de Burriana


def limpiar_ubicacion(texto) -> str:
    """Zona del almacén donde se deja un palet: una de UBICACIONES (E1…E6 estanterías, S1…S10 suelo) o vacío. Si no existe, ErrorStock."""
    u = "".join(str(texto or "").split()).upper()
    if u and u not in {z for _, zs in UBICACIONES for z in zs}:
        raise ErrorStock(f"La ubicación «{texto}» no existe: elige una de la lista (estanterías E1-E6 o suelo S1-S10).")
    return u


def resumen_ubicaciones(palets: list[dict]) -> list[tuple[str, int]]:
    """[(ubicación, nº de palets)] de más a menos palets; los que no tienen ubicación salen como «sin ubicación»."""
    cuenta: dict[str, int] = {}
    for p in palets:
        u = p.get("ubicacion") or "sin ubicación"
        cuenta[u] = cuenta.get(u, 0) + 1
    return sorted(cuenta.items(), key=lambda x: (x[0] == "sin ubicación", -x[1], x[0]))


def es_sin_codigo(texto) -> bool:
    """«SIN CODIGO» escrito donde va el código del palet (mercancía no normativa, sin etiqueta con código)."""
    t = unicodedata.normalize("NFD", str(texto or "").upper())
    return re.sub(r"[^A-Z]", "", t) == "SINCODIGO"


def codigo_interno(id_palet: str) -> str:
    """Código que se da a un palet sin código para poder controlarlo y sacarlo: SC + su id (SCI2026000103)."""
    return sscc("SC" + id_palet)


def sscc(texto) -> str:
    """Código del palet de la etiqueta, comparable venga como venga: SSCC de 18 cifras (puede traer «(00)» o espacios),
    código de fábrica de 10 cifras, o código con letras como el «LP26-0236823» de Aliplast. Sin letras se devuelve solo con
    cifras; con letras, en mayúsculas y sin guiones, espacios ni signos (LP260236823), así que escaneado, escrito a mano o
    elegido de la lista da lo mismo. Con menos de MIN_CODIGO caracteres no sirve de identificador y el palet se reconoce
    por referencia y lote. Un texto que es una dirección web (el QR de la etiqueta ambiental) no es un código de palet."""
    t = str(texto or "").strip()
    if re.match(r"(?i)(https?://|www\.)", t):
        return ""
    if re.search(r"[A-Za-z]", t):
        return re.sub(r"[^A-Z0-9]", "", t.upper())
    t = re.sub(r"\D", "", t)
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


def textos_cliente(albaran: dict) -> list[str]:
    """Lo que el albarán dice del CLIENTE DEL ALMACÉN: la empresa que emite y envía (el operador logístico). El «cliente final»
    (propietario de la mercancía según el albarán) es solo informativo y no sirve para reconocer al cliente."""
    return [albaran.get("remitente")]


def limpiar_esperados(lista) -> list[dict]:
    """Palets que el albarán dice que vienen: [{codigo, ref, kg, lote, fecha_prod}] (sin repetidos). El lote es el de producción o, si el albarán
    no lo trae, el código de «Entrada»."""
    vistos, res = set(), []
    for x in lista or []:
        c = sscc(x.get("codigo"))
        if len(c) < MIN_CODIGO or c in vistos:
            continue
        vistos.add(c)
        res.append({"codigo": c, "ref": str(x.get("referencia") or x.get("ref") or "").strip(), "kg": entero(x.get("kg")),
                    "lote": str(x.get("lote") or x.get("entrada") or "").strip(), "fecha_prod": fecha(x.get("fecha_produccion") or x.get("fecha_prod"))})
    return res


def cliente_por_codigo(clientes: list[dict], codigo: str) -> dict | None:
    return next((c for c in clientes if c.get("codigo") == codigo), None)


def claves_catalogo(cliente: dict | None) -> dict[str, str]:
    """{referencia normalizada → referencia principal} del catálogo del cliente, con sus códigos alternativos
    (p. ej. «F05182S-MV» y el código numérico de cruce valen como «F05182S»)."""
    res: dict[str, str] = {}
    for r in (cliente or {}).get("referencias") or []:
        principal = str(r.get("ref") or "").strip()
        if not principal:
            continue
        res[referencia(principal)] = principal
        for a in r.get("alias") or []:
            if referencia(a):
                res.setdefault(referencia(a), principal)
    return res


def canonica(cliente: dict | None, ref) -> str:
    """La referencia principal del catálogo si `ref` es una de sus equivalentes; si no, la misma referencia."""
    t = str(ref or "").strip()
    return claves_catalogo(cliente).get(referencia(t), t) if t else t


def unificar_lineas(lineas: list[dict], esperados: list[dict], cliente: dict | None, clave_ref: str = "ref") -> list[dict]:
    """Con el catálogo del cliente: pone la referencia principal en las líneas y en los palets de la lista, junta las líneas
    de la misma referencia y, si hay lista de palets, cuenta los palets de cada línea por esa lista. Las líneas sin palets y
    sin ninguna fila en la lista (p. ej. el reparto reciclado / no reciclado de un albarán) se descartan."""
    for x in esperados:
        x["ref"] = canonica(cliente, x.get("ref"))
    juntas: dict[str, dict] = {}
    for l in lineas:
        l["ref"] = canonica(cliente, l.get(clave_ref))
        k = referencia(l["ref"]) or norm(l.get("descripcion"))
        if k in juntas:
            j = juntas[k]
            for c in ("palets", "bultos", "kg"):
                if l.get(c) is not None:
                    j[c] = (j.get(c) or 0) + l[c]
            j["lote"] = j.get("lote") or l.get("lote") or ""
        else:
            juntas[k] = l
    res = list(juntas.values())
    if esperados:
        for l in res:
            cuenta = sum(1 for x in esperados if referencia(x["ref"]) == referencia(l["ref"]))
            if cuenta:
                l["palets"] = cuenta
        res = [l for l in res if l.get("palets")]
    return res


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
            "cliente_final": str(leido.get("cliente_final") or "").strip(), "codigo_cliente_final": str(leido.get("codigo_cliente_final") or "").strip(),
            "observaciones": str(leido.get("observaciones") or "").strip(), "lineas": lineas,
            "dudas": [str(d) for d in leido.get("dudas") or []]}


def entrada_repetida(numero: str | None, cliente: dict | None, entradas: list[dict]) -> dict | None:
    """La entrada ya cargada con ese mismo nº de albarán y el mismo cliente (las anuladas y los inventarios no cuentan)."""
    if not numero or not cliente:
        return None
    return next((e for e in entradas if e.get("estado") != ENTRADA_ANULADA and e.get("tipo") != "inventario"
                 and e.get("cliente", {}).get("codigo") == cliente["codigo"] and norm(e["albaran"].get("numero")) == norm(numero)), None)


def conflictos_entrada(albaran: dict, cliente: dict | None, clientes: list[dict], entradas: list[dict],
                       hoy: dt.date | None = None, esperados: list[dict] | None = None) -> list[dict]:
    """Avisos de una entrada antes de guardarla. `entradas` = las ya guardadas (para duplicados e historial del cliente)."""
    hoy = hoy or dt.date.today()
    av: list[dict] = []
    sugeridos = sugerir_clientes(clientes, *textos_cliente(albaran))
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
    vivas = [e for e in entradas if e.get("estado") != ENTRADA_ANULADA and e.get("tipo") != "inventario"]
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
        if d > hoy + dt.timedelta(days=DIAS_ALBARAN_FUTURO):
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
    if esperados:   # la lista de palets (packing list / Excel) tiene que cuadrar con los totales del albarán
        if albaran.get("total_palets") and albaran["total_palets"] != len(esperados):
            av.append(aviso("error", f"El albarán dice {albaran['total_palets']} palets y la lista trae {len(esperados)} códigos: puede que falte alguna fila al leerla."))
        kg_lista = sum(x.get("kg") or 0 for x in esperados)
        if albaran.get("total_kg") and kg_lista and abs(albaran["total_kg"] - kg_lista) > 0.005 * albaran["total_kg"]:
            av.append(aviso("error", f"El albarán dice {albaran['total_kg']} kg y los pesos de la lista suman {kg_lista} kg: revisa la lectura de los pesos."))
    if cliente and cliente.get("referencias"):   # catálogo de referencias del cliente
        catalogo = set(claves_catalogo(cliente))
        for l in lineas:
            if l.get("ref") and referencia(l["ref"]) not in catalogo:
                av.append(aviso("aviso", f"La referencia {l['ref']} no está en el catálogo de {cliente['nombre']}: ¿está bien escrita o es un producto nuevo?"))
    elif cliente:   # sin catálogo: mercancía que ese cliente nunca ha traído
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
            "descripcion": str(leida.get("descripcion") or "").strip(),
            "lote": str(leida.get("lote") or leida.get("entrada") or "").strip(),   # sin lote, el código de «Entrada» hace de lote
            "kg": entero(leida.get("kg")), "bultos": entero(leida.get("bultos")),
            "cliente_texto": str(leida.get("cliente") or "").strip(), "caducidad": fecha(leida.get("caducidad")),
            "fecha_prod": fecha(leida.get("fecha_produccion") or leida.get("fecha_prod")),
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
    estado «ok» o «discrepancia» (con el motivo). Nunca se rechaza una etiqueta: queda anotada y se resuelve al cerrar.
    Si el albarán trae la lista de palets (código de cada uno) se casa por código; si no, por referencia y lote."""
    if entrada.get("tipo") == "inventario":
        return _verificar_inventario(entrada, etiqueta, sscc_en_stock)
    lineas = entrada["lineas"]
    codigo = etiqueta.get("sscc") if len(etiqueta.get("sscc") or "") >= MIN_CODIGO else ""
    esperados = {x["codigo"]: x for x in entrada.get("esperados") or []}
    exp = esperados.get(codigo) if codigo else None
    idx = _linea_de(lineas, etiqueta if etiqueta.get("ref") or not exp else {**etiqueta, "ref": exp.get("ref")})
    palet = {**etiqueta, "linea": idx, "estado": "ok", "motivo": ""}
    ya = [p for p in entrada["palets"] if p.get("estado_alta") != "quitado"]
    if exp and not palet.get("kg") and exp.get("kg"):
        palet["kg"] = exp["kg"]   # la etiqueta no trae peso: se toma el del albarán
    if exp and not palet.get("lote") and exp.get("lote"):
        palet["lote"] = exp["lote"]
    if exp and not palet.get("fecha_prod") and exp.get("fecha_prod"):
        palet["fecha_prod"] = exp["fecha_prod"]
    if codigo and (any(p.get("sscc") == codigo for p in ya) or codigo in sscc_en_stock):
        palet.update(estado="discrepancia", duplicado=True, motivo="Esta etiqueta (código de palet) ya está registrada: palet repetido (se descarta al cerrar).")
    elif idx is None:
        palet.update(estado="discrepancia", motivo="Este palet no figura en el albarán"
                     + (f" (referencia {etiqueta['ref']})." if etiqueta.get("ref") else " (no se lee la referencia)."))
    elif esperados and codigo and not exp:
        palet.update(estado="discrepancia", motivo=f"El código {codigo} no está en la lista de palets del albarán (referencia {etiqueta.get('ref') or '—'}).")
    elif exp and exp.get("ref") and etiqueta.get("ref") and referencia(exp["ref"]) != referencia(etiqueta["ref"]):
        palet.update(estado="discrepancia", motivo=f"El albarán dice que el palet {codigo} es de {exp['ref']} y la etiqueta es de {etiqueta['ref']}.")
    else:
        pedidos = lineas[idx].get("palets") or 0
        verificados = sum(1 for p in ya if p.get("linea") == idx and p["estado"] == "ok")   # solo cuentan los buenos: un repetido o un fuera de lista no hace «sobrar» al siguiente
        l = lineas[idx]
        if verificados >= pedidos and not exp:   # un palet que figura en la lista del albarán es bueno aunque las cuentas de la línea no cuadren
            palet.update(estado="discrepancia", motivo=f"Sobra: el albarán trae {pedidos} palets de {l['ref'] or l['descripcion']} y ya están todos.")
        elif l.get("lote") and etiqueta.get("lote") and norm(l["lote"]) != norm(etiqueta["lote"]):
            palet.update(estado="discrepancia", motivo=f"Lote distinto: etiqueta {etiqueta['lote']}, albarán {l['lote']}.")
        elif exp and exp.get("lote") and etiqueta.get("lote") and norm(exp["lote"]) != norm(etiqueta["lote"]):
            palet.update(estado="discrepancia", motivo=f"El albarán dice que el palet {codigo} viene de la entrada {exp['lote']} y la etiqueta es de {etiqueta['lote']}.")
    cl = entrada.get("cliente") or {}
    texto = etiqueta.get("cliente_texto")
    if palet["estado"] == "ok" and texto and cl and not sugerir_clientes([cl], texto):
        palet.update(estado="discrepancia", motivo=f"La etiqueta es de «{texto}», no de {cl.get('nombre')}.")
    return palet


def _verificar_inventario(entrada: dict, etiqueta: dict, sscc_en_stock: set[str]) -> dict:
    """Entrada manual de inventario (mercancía que ya estaba en el almacén o aparece sin albarán): no hay albarán con el que casar.
    Se acepta el palet con lo que diga su etiqueta; solo se avisa si repite un código o no tiene referencia."""
    sin_codigo = bool(etiqueta.get("sin_codigo"))   # «SIN CODIGO»: mercancía no normativa; se acepta si tiene ubicación
    codigo = "" if sin_codigo else (etiqueta.get("sscc") if len(etiqueta.get("sscc") or "") >= MIN_CODIGO else "")
    ya = [p for p in entrada["palets"] if p.get("estado_alta") != "quitado"]
    lineas = entrada["lineas"]
    ref = etiqueta.get("ref") or ""
    idx = next((i for i, l in enumerate(lineas) if (ref and referencia(l.get("ref")) == referencia(ref)) or (not ref and norm(l.get("descripcion")) == norm(etiqueta.get("descripcion")))), None)
    if idx is None and (ref or etiqueta.get("descripcion")):
        lineas.append({"ref": ref, "descripcion": etiqueta.get("descripcion") or "", "palets": 0, "bultos": None, "kg": None, "lote": ""})
        idx = len(lineas) - 1
    palet = {**etiqueta, "linea": idx, "estado": "ok", "motivo": ""}
    if codigo and (any(p.get("sscc") == codigo for p in ya) or codigo in sscc_en_stock):
        palet.update(estado="discrepancia", duplicado=True, motivo="Este código de palet ya está registrado: palet repetido (se descarta al cerrar).")
    elif idx is None:
        palet.update(estado="discrepancia", motivo="Falta la referencia del palet: escríbela o haz foto de su etiqueta.")
    elif sin_codigo and not etiqueta.get("ubicacion"):
        palet.update(estado="discrepancia", motivo="Palet sin código: hace falta indicar su ubicación para aceptarlo.")
    elif not codigo and not sin_codigo:
        palet.update(estado="discrepancia", motivo="Falta el código del palet (hace falta para poder controlarlo y sacarlo después). Si no tiene, escribe SIN CODIGO.")
    return palet


def recontar_inventario(entrada: dict) -> None:
    """En una entrada de inventario, los palets de cada línea son los que se han apuntado (no hay albarán que los espere)."""
    for i, l in enumerate(entrada["lineas"]):
        l["palets"] = sum(1 for p in activos(entrada) if p.get("linea") == i)


def quitar_lineas_vacias(entrada: dict) -> None:
    """Entrada de inventario: una referencia que se ha quedado sin palets (corregida o quitada) deja de salir en la lista."""
    if any(not l["palets"] for l in entrada["lineas"]):   # una referencia que se ha quedado sin palets (corregida o quitada) ya no sale
        nuevo = {}
        for i, l in enumerate(entrada["lineas"]):
            if l["palets"]:
                nuevo[i] = len(nuevo)
        entrada["lineas"] = [l for l in entrada["lineas"] if l["palets"]]
        for p in entrada["palets"]:
            p["linea"] = nuevo.get(p.get("linea")) if p.get("estado_alta") != "quitado" else None


def activos(entrada: dict) -> list[dict]:
    return [p for p in entrada["palets"] if p.get("estado_alta") != "quitado"]


def pendientes_entrada(entrada: dict) -> list[dict]:
    """Códigos de la lista del albarán que todavía no se han etiquetado (para elegirlos de una lista desplegable)."""
    hechos = {p.get("sscc") for p in activos(entrada) if p.get("sscc")}
    return [{"codigo": x["codigo"], "ref": x.get("ref") or "", "kg": x.get("kg"), "lote": x.get("lote") or ""}
            for x in entrada.get("esperados") or [] if x["codigo"] not in hechos]


def disponibles_salida(salida: dict, existencias: list[dict], otras_salidas: list[dict], tope: int = 300) -> list[dict]:
    """Palets del stock del cliente que se pueden cargar en esta salida (lista desplegable): de las referencias pedidas
    (o de la lista de códigos de la petición, si la trae), sin los ya cargados ni reservados en otras salidas; los más
    antiguos primero (FIFO)."""
    cod = salida["cliente"]["codigo"]
    tomados = {p.get("palet_id") for p in salida["palets"] if p.get("estado_alta") != "quitado" and p.get("palet_id")} | reservados(otras_salidas, salida["id"])
    lista = {x["codigo"] for x in salida.get("esperados") or []} if salida.get("por_palet") else set()
    refs = {referencia(l.get("ref")) for l in salida["lineas"] if l.get("ref")}
    res = [p for p in existencias if p["cliente"] == cod and p["id"] not in tomados and p.get("sscc")
           and ((p["sscc"] in lista) if lista else (not refs or referencia(p["ref"]) in refs))]
    res.sort(key=lambda p: (p["fecha_entrada"], p["id"]))
    return [{"codigo": p["sscc"], "ref": p["ref"], "lote": p.get("lote") or "", "kg": p.get("kg"), "palet": p["id"], "fecha_entrada": p["fecha_entrada"],
             "ubicacion": p.get("ubicacion") or "", "sin_codigo": bool(p.get("sin_codigo")), "linea": _linea_salida(salida["lineas"], p)}
            for p in res[:tope]]


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
    vistos = {p.get("sscc") for p in activos(entrada) if p["estado"] == "ok"}
    faltan = [x["codigo"] for x in entrada.get("esperados") or [] if x["codigo"] not in vistos]
    if faltan and len(faltan) <= 12:
        res.append("Códigos del albarán sin etiquetar: " + ", ".join(faltan) + ".")
    for e in estado_lineas(entrada):
        nombre = e["ref"] or e["descripcion"]
        ka, ke = e["kg_albaran"], e["kg_etiquetas"]
        if ka and ke and e["verificados"] == e["esperados"] and abs(ka - ke) > TOLERANCIA_KG * ka:
            res.append(f"{nombre}: el albarán dice {ka} kg y las etiquetas suman {ke} kg.")
    return res


# ---- stock y salidas ------------------------------------------------------------------------------------------------

def fecha_entrada(e: dict) -> str:
    """Día en que la mercancía entra en el almacén (base del estocaje): la que se puso a mano, si no la del albarán,
    si no el día del cierre."""
    return e.get("fecha_entrada") or (e.get("cerrada") or e.get("creada") or "")[:10]


def stock(entradas: list[dict], salidas: list[dict], controles: list[dict] | None = None) -> list[dict]:
    """Palets en el almacén: los verificados de entradas cerradas menos los de salidas cargadas y los dados de baja en un control
    de inventario cerrado.
    Palet de stock: {id, cliente, ref, descripcion, lote, sscc, kg, bultos, entrada, albaran, fecha_entrada}."""
    fuera = {p["palet_id"] for s in salidas if s["estado"] == SALIDA_CARGADA for p in s["palets"]
             if p.get("palet_id") and p.get("estado_alta") != "quitado" and (p["estado"] == "ok" or p.get("aceptado"))}
    fuera |= bajas_control(controles)
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
                        "fecha_prod": p.get("fecha_prod") or "", "ubicacion": p.get("ubicacion") or "", "sin_codigo": bool(p.get("sin_codigo")), "inventario": e.get("tipo") == "inventario", "entrada": e["id"], "albaran": e["albaran"].get("numero"), "fecha_entrada": fecha_entrada(e),
                        "cliente_final": e["albaran"].get("cliente_final") or ""})
    return res


def bajas_control(controles: list[dict] | None) -> set[str]:
    """Palets que un control de inventario cerrado ha dado de baja (estaban en el stock y no se encontraron)."""
    return {pid for c in controles or [] if c.get("estado") == CONTROL_CERRADO for pid in c.get("bajas") or []}


def antiguedad(palets: list[dict], hoy: str, dias: int = DIAS_ESTOCAJE_AVISO) -> list[dict]:
    """Palets del stock que llevan en el almacén más de `dias` días (solo se avisa, no hay FIFO obligatorio)."""
    return [p for p in palets if dias_entre(p["fecha_entrada"], hoy) > dias]


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
    """Avisos al cargar la petición: pide más de lo que hay en stock de ese cliente, o algo que no tiene.
    Con clientes que exigen palets concretos (`por_palet`), la petición tiene que traer los códigos."""
    av = []
    if salida.get("por_palet") and not salida.get("esperados"):
        av.append(aviso("error", f"{salida['cliente']['nombre']} exige cargar los palets concretos que pide y la petición no trae sus códigos (LP…): "
                                 "súbela otra vez con la lista de códigos o apúntalos a mano."))
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
    if etiqueta.get("sscc") and len(etiqueta["sscc"]) >= MIN_CODIGO:
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
    if salida.get("por_palet") and salida.get("esperados") and hallado.get("sscc") not in {x["codigo"] for x in salida["esperados"]}:
        return _salida_palet(hallado, etiqueta, "discrepancia", f"El palet {hallado.get('sscc')} no está en la lista de palets de la petición.")
    idx = _linea_salida(salida["lineas"], hallado)
    if idx is None:
        return _salida_palet(hallado, etiqueta, "discrepancia", f"No se ha pedido {hallado['ref'] or hallado['descripcion']}.")
    cargados = sum(1 for p in ya if p.get("linea") == idx and p["estado"] == "ok")
    if cargados >= (salida["lineas"][idx].get("palets") or 0):
        return _salida_palet(hallado, etiqueta, "discrepancia", f"Sobra: ya están los {salida['lineas'][idx]['palets']} palets pedidos de {hallado['ref'] or hallado['descripcion']}.", idx)
    p = _salida_palet(hallado, etiqueta, "ok", "", idx)
    return p


def _salida_palet(stock_palet: dict | None, etiqueta: dict, estado: str, motivo: str, linea: int | None = None) -> dict:
    base = stock_palet or {}
    return {"palet_id": base.get("id"), "sscc": etiqueta.get("sscc") or base.get("sscc") or "", "ref": etiqueta.get("ref") or base.get("ref") or "",
            "descripcion": base.get("descripcion") or etiqueta.get("descripcion") or "", "lote": etiqueta.get("lote") or base.get("lote") or "",
            "kg": base.get("kg") or etiqueta.get("kg"), "ubicacion": base.get("ubicacion") or "", "linea": linea, "estado": estado, "motivo": motivo, "aviso": ""}


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
    cargados = {p.get("sscc") for p in salida["palets"] if p.get("estado_alta") != "quitado" and p["estado"] == "ok"}
    faltan = [x["codigo"] for x in salida.get("esperados") or [] if x["codigo"] not in cargados] if salida.get("por_palet") else []
    if faltan:
        res.append("Códigos pedidos sin cargar: " + ", ".join(faltan[:60]) + ("…" if len(faltan) > 60 else "") + ".")
    return res


# ---- control de inventario -----------------------------------------------------------------------------------------

def control_resultado(control: dict, existencias: list[dict]) -> dict:
    """Compara lo que hay en el stock (del cliente del control, o de todos) con los palets contados.
    «faltan»: están en el stock y no se han contado; «sobran»: se han contado y no están en el stock (o son de otro cliente)."""
    cod = (control.get("cliente") or {}).get("codigo")
    esperados = [p for p in existencias if not cod or p["cliente"] == cod]
    contados = {p["sscc"] for p in control["palets"] if p.get("estado_alta") != "quitado"}
    en_stock = {p["sscc"]: p for p in existencias if p.get("sscc")}
    faltan = [p for p in esperados if p.get("sscc") not in contados]
    sobran = [p for p in control["palets"] if p.get("estado_alta") != "quitado" and p["sscc"] not in {x["sscc"] for x in esperados}]
    return {"esperados": len(esperados), "contados": len(contados), "bien": len(contados) - len(sobran),
            "faltan": [{"palet": p["id"], "sscc": p["sscc"], "ref": p["ref"], "lote": p["lote"], "kg": p["kg"], "cliente": p["cliente_nombre"]} for p in faltan],
            "sobran": [{"sscc": p["sscc"], "motivo": (f"es de {en_stock[p['sscc']]['cliente_nombre']}, no de {control['cliente']['nombre']}" if p["sscc"] in en_stock
                                                        else "no está en el stock del sistema")} for p in sobran]}
