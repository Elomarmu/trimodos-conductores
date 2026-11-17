"""Lectura con Claude (visión) de los documentos del almacén de Burriana: albarán de entrada del cliente, etiqueta de
palet y petición de salida. Reutiliza la llamada a la API de `pr_lector` (mismo ANTHROPIC_API_KEY y mismo modelo)."""
from __future__ import annotations

import httpx

from ..pr import pr_lector as L

LecturaError = L.LecturaError

PROMPT_ALBARAN = """Lee este albarán de entrega de un cliente de un almacén logístico (Almacén STINSA de Burriana, Castellón).
Es una foto hecha con el móvil (o un PDF): puede estar torcida, con sellos y anotaciones a mano. Puede constar de varias
páginas/fotos del MISMO albarán: júntalas.

Datos que necesito:
- remitente: la empresa que EMITE el albarán y envía la mercancía (membrete, p. ej. «Grupo …»). Es el CLIENTE del almacén.
- cliente_final y codigo_cliente_final: el cliente de ese cliente (campo «Nombre» junto a «Código cliente», «Su cliente»…).
  Es solo informativo. destinatario: a quién va («Datos Envío», debería ser STINSA, Almacén de Burriana o similar).
- numero_albaran, fecha (AAAA-MM-DD, la del albarán), transportista y matricula (tractora y remolque, si salen).
- lineas: una por cada referencia de producto con su descripción, nº de palets, bultos (cajas, sacos, bobinas…), kg y lote
  si aparece. Si el albarán da los palets por línea, úsalos; si solo da un total de palets, ponlo en total_palets y deja
  el de las líneas en null. No inventes.
- total_palets y total_kg si el albarán los declara en un total.
- palets_detalle: si el albarán lista los palets uno a uno (una fila por palet con su código/SKU/SSCC, columna «Entrada»,
  cantidad y peso), una entrada por palet con codigo (solo cifras), referencia (la de la línea a la que pertenece),
  entrada (el código de la columna «Entrada», p. ej. ECS26-3266) y kg (peso del palet). No necesito las unidades. Copia los códigos cifra a cifra; si no hay lista, déjalo vacío.
  Las líneas de resumen (referencia con el total de palets) van en «lineas»; no cuentes dos veces.
- observaciones: anotaciones a mano, incidencias o condiciones especiales.

Reglas: los pesos usan punto de miles («24.800» son 24800 kg); da los números enteros. Si un dato no aparece, null.
En «dudas» explica brevemente cualquier dato que no se lea con seguridad. NO anotes como duda: que falten transportista o
matrícula, que el remitente salga del membrete, ni nada de bultos o unidades (no se controlan). Solo dudas que afecten a
códigos de palet, referencias, kg, fechas o número de albarán."""

ESQUEMA_ALBARAN = {
    "type": "object",
    "properties": {
        "numero_albaran": {"type": ["string", "null"]}, "fecha": {"type": ["string", "null"], "description": "AAAA-MM-DD"},
        "remitente": {"type": ["string", "null"]}, "destinatario": {"type": ["string", "null"]},
        "cliente_final": {"type": ["string", "null"]}, "codigo_cliente_final": {"type": ["string", "null"]},
        "transportista": {"type": ["string", "null"]}, "matricula": {"type": ["string", "null"]},
        "total_palets": {"type": ["integer", "null"]}, "total_kg": {"type": ["integer", "null"]},
        "lineas": {"type": "array", "items": {"type": "object", "properties": {
            "referencia": {"type": ["string", "null"]}, "descripcion": {"type": ["string", "null"]},
            "palets": {"type": ["integer", "null"]}, "bultos": {"type": ["integer", "null"]},
            "kg": {"type": ["integer", "null"]}, "lote": {"type": ["string", "null"]}}}},
        "palets_detalle": {"type": "array", "items": {"type": "object", "properties": {
            "codigo": {"type": "string"}, "referencia": {"type": ["string", "null"]},
            "entrada": {"type": ["string", "null"]}, "kg": {"type": ["integer", "null"]}}, "required": ["codigo"]}},
        "observaciones": {"type": ["string", "null"]},
        "dudas": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["numero_albaran", "fecha", "remitente", "lineas", "dudas"],
}

PROMPT_ETIQUETA = """Esta es la foto de la etiqueta de UN palet de mercancía (etiqueta logística tipo GS1 o de fábrica).
Lee: sscc = el código que identifica este palet, el número impreso bajo el código de barras (SSCC de 18 cifras, a veces
tras «(00)», o un código de fábrica más corto, p. ej. 10 cifras); solo cifras. Referencia o código de artículo (suele ir
en grande arriba, seguido de la descripcion), lote (null si «Lote:» está vacío), entrada (el código tras «Entrada:», p. ej.
ECS26-3696), kg (peso, solo si la etiqueta lo trae; si no, null), caducidad (AAAA-MM-DD, si hay), cliente = la empresa del
membrete o logotipo de la etiqueta (p. ej. «Grupo …») y cliente_final = el nombre junto a «Identificador», si hay.
NO me interesan las unidades ni el «Total».
No inventes: lo que no se vea es null. En «dudas» anota lo que no se lea con seguridad (un número borroso, dos etiquetas
en la foto…)."""

ESQUEMA_ETIQUETA = {
    "type": "object",
    "properties": {
        "sscc": {"type": ["string", "null"]}, "referencia": {"type": ["string", "null"]}, "descripcion": {"type": ["string", "null"]},
        "lote": {"type": ["string", "null"]}, "entrada": {"type": ["string", "null"]}, "kg": {"type": ["integer", "null"]},
        "caducidad": {"type": ["string", "null"]}, "cliente": {"type": ["string", "null"]}, "cliente_final": {"type": ["string", "null"]},
        "dudas": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["sscc", "referencia", "lote", "dudas"],
}

PROMPT_PETICION = """Esta es una petición de salida de mercancía de un cliente de un almacén (foto, captura o PDF de un correo
o de un pedido). Lee: cliente (quién la pide), referencia_cliente (su nº de pedido o de petición), destino (dónde se envía),
fecha (AAAA-MM-DD, de la petición o de la recogida) y lineas: una por cada tipo de palet pedido con su referencia o código
de artículo, descripcion y nº de palets (si lo dan en bultos o kg y no en palets, pon palets null y explícalo en dudas).
Si la petición lista los palets uno a uno (con su código), ponlos en palets_detalle (codigo solo cifras, referencia).
No inventes. En «dudas» anota lo que no se lea con seguridad."""

ESQUEMA_PETICION = {
    "type": "object",
    "properties": {
        "cliente": {"type": ["string", "null"]}, "referencia_cliente": {"type": ["string", "null"]},
        "destino": {"type": ["string", "null"]}, "fecha": {"type": ["string", "null"]},
        "lineas": {"type": "array", "items": {"type": "object", "properties": {
            "referencia": {"type": ["string", "null"]}, "descripcion": {"type": ["string", "null"]}, "palets": {"type": ["integer", "null"]}}}},
        "palets_detalle": {"type": "array", "items": {"type": "object", "properties": {
            "codigo": {"type": "string"}, "referencia": {"type": ["string", "null"]}}, "required": ["codigo"]}},
        "dudas": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["lineas", "dudas"],
}


def leer_albaran(archivos: list[tuple[bytes, str]], cliente: httpx.Client | None = None) -> dict:
    return L._preguntar(archivos, PROMPT_ALBARAN, "albaran_entrada", ESQUEMA_ALBARAN, cliente, max_tokens=8000, timeout=60)


def leer_etiqueta(contenido: bytes, tipo: str, cliente: httpx.Client | None = None) -> dict:
    return L._preguntar([(contenido, tipo)], PROMPT_ETIQUETA, "etiqueta_palet", ESQUEMA_ETIQUETA, cliente, max_tokens=1500, timeout=25)


def leer_peticion(archivos: list[tuple[bytes, str]], cliente: httpx.Client | None = None) -> dict:
    return L._preguntar(archivos, PROMPT_PETICION, "peticion_salida", ESQUEMA_PETICION, cliente, max_tokens=3000, timeout=40)


TIPOS_EXCEL = ("spreadsheetml", "ms-excel")


def es_excel(contenido: bytes, tipo: str) -> bool:
    return any(t in (tipo or "") for t in TIPOS_EXCEL) or (contenido[:2] == b"PK" and "pdf" not in (tipo or "") and "image" not in (tipo or ""))


def leer_excel_albaran(contenido: bytes) -> dict:
    """Excel de detalle del albarán que manda el cliente (una fila por palet: Nº producto, Descripción, SKU = código del palet, kg brutos…).
    Se lee sin IA: devuelve el mismo formato que `leer_albaran` con las líneas y la lista de palets (código = SKU)."""
    import io
    import openpyxl
    from . import stock_reglas as R
    try:
        ws = openpyxl.load_workbook(io.BytesIO(contenido), data_only=True, read_only=True).worksheets[0]
        filas = [list(f) for f in ws.iter_rows(values_only=True)]
    except Exception:
        raise LecturaError("No puedo abrir el Excel del albarán. Mándalo como .xlsx.")
    cab = next((i for i, f in enumerate(filas[:15]) if "SKU" in {R.norm(c) for c in f if c}), None)
    if cab is None:
        raise LecturaError("El Excel no tiene las columnas esperadas (Nº producto, Descripción, Cantidad, SKU).")
    col = {R.norm(c): i for i, c in enumerate(filas[cab]) if c}
    def g(f, *nombres):
        for n in nombres:
            i = col.get(R.norm(n))
            if i is not None and i < len(f) and f[i] not in (None, ""):
                return f[i]
        return None
    lineas: dict[str, dict] = {}
    palets = []
    for f in filas[cab + 1:]:
        codigo, ref = g(f, "SKU"), g(f, "Nº producto")
        if not codigo or not ref:
            continue
        ref, kg = str(ref).strip(), R.entero(g(f, "kg brutos"))
        l = lineas.setdefault(ref, {"referencia": ref, "descripcion": str(g(f, "Descripción") or "").strip(), "palets": 0,
                                    "bultos": None, "kg": 0, "lote": str(g(f, "Nº lote") or "").strip()})
        l["palets"] += 1; l["kg"] += kg or 0
        palets.append({"codigo": str(codigo), "referencia": ref, "kg": kg, "entrada": str(g(f, "Nº documento entrada") or "").strip()})
    if not palets:
        raise LecturaError("El Excel no trae ningún palet (columna SKU vacía).")
    return {"numero_albaran": None, "fecha": None, "remitente": None, "destinatario": None, "lineas": list(lineas.values()),
            "total_palets": len(palets), "total_kg": sum(p["kg"] or 0 for p in palets) or None, "palets_detalle": palets,
            "dudas": []}
