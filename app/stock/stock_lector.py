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
- numero_albaran, fecha (AAAA-MM-DD), transportista y matricula (tractora y remolque tal cual, si salen).
- remitente: la empresa que envía la mercancía (cabecera / «Cliente» / «Expedidor»); destinatario: a quién va (debería
  ser STINSA, Almacén de Burriana o similar).
- lineas: una por cada referencia de producto con su descripción, nº de palets, bultos (cajas, sacos, bobinas…), kg y lote
  si aparece. Si el albarán da los palets por línea, úsalos; si solo da un total de palets, ponlo en total_palets y deja
  el de las líneas en null. No inventes.
- total_palets y total_kg si el albarán los declara en un total.
- observaciones: anotaciones a mano, incidencias o condiciones especiales.

Reglas: los pesos usan punto de miles («24.800» son 24800 kg); da los números enteros. Si un dato no aparece, null.
En «dudas» explica brevemente cualquier dato que no se lea con seguridad."""

ESQUEMA_ALBARAN = {
    "type": "object",
    "properties": {
        "numero_albaran": {"type": ["string", "null"]}, "fecha": {"type": ["string", "null"], "description": "AAAA-MM-DD"},
        "remitente": {"type": ["string", "null"]}, "destinatario": {"type": ["string", "null"]},
        "transportista": {"type": ["string", "null"]}, "matricula": {"type": ["string", "null"]},
        "total_palets": {"type": ["integer", "null"]}, "total_kg": {"type": ["integer", "null"]},
        "lineas": {"type": "array", "items": {"type": "object", "properties": {
            "referencia": {"type": ["string", "null"]}, "descripcion": {"type": ["string", "null"]},
            "palets": {"type": ["integer", "null"]}, "bultos": {"type": ["integer", "null"]},
            "kg": {"type": ["integer", "null"]}, "lote": {"type": ["string", "null"]}}}},
        "observaciones": {"type": ["string", "null"]},
        "dudas": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["numero_albaran", "fecha", "remitente", "lineas", "dudas"],
}

PROMPT_ETIQUETA = """Esta es la foto de la etiqueta de UN palet de mercancía (etiqueta logística tipo GS1 o de fábrica).
Lee: sscc (código de 18 cifras, suele ir bajo el código de barras o tras «(00)»; si no hay, null), referencia o código
de artículo, descripcion, lote, kg (peso bruto o neto que ponga; el número entero), bultos (cajas/unidades por palet),
caducidad (AAAA-MM-DD, si hay) y cliente (propietario o fabricante que figura en la etiqueta).
No inventes: lo que no se vea es null. En «dudas» anota lo que no se lea con seguridad (un número borroso, dos etiquetas
en la foto…)."""

ESQUEMA_ETIQUETA = {
    "type": "object",
    "properties": {
        "sscc": {"type": ["string", "null"]}, "referencia": {"type": ["string", "null"]}, "descripcion": {"type": ["string", "null"]},
        "lote": {"type": ["string", "null"]}, "kg": {"type": ["integer", "null"]}, "bultos": {"type": ["integer", "null"]},
        "caducidad": {"type": ["string", "null"]}, "cliente": {"type": ["string", "null"]},
        "dudas": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["sscc", "referencia", "lote", "dudas"],
}

PROMPT_PETICION = """Esta es una petición de salida de mercancía de un cliente de un almacén (foto, captura o PDF de un correo
o de un pedido). Lee: cliente (quién la pide), referencia_cliente (su nº de pedido o de petición), destino (dónde se envía),
fecha (AAAA-MM-DD, de la petición o de la recogida) y lineas: una por cada tipo de palet pedido con su referencia o código
de artículo, descripcion y nº de palets (si lo dan en bultos o kg y no en palets, pon palets null y explícalo en dudas).
No inventes. En «dudas» anota lo que no se lea con seguridad."""

ESQUEMA_PETICION = {
    "type": "object",
    "properties": {
        "cliente": {"type": ["string", "null"]}, "referencia_cliente": {"type": ["string", "null"]},
        "destino": {"type": ["string", "null"]}, "fecha": {"type": ["string", "null"]},
        "lineas": {"type": "array", "items": {"type": "object", "properties": {
            "referencia": {"type": ["string", "null"]}, "descripcion": {"type": ["string", "null"]}, "palets": {"type": ["integer", "null"]}}}},
        "dudas": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["lineas", "dudas"],
}


def leer_albaran(archivos: list[tuple[bytes, str]], cliente: httpx.Client | None = None) -> dict:
    return L._preguntar(archivos, PROMPT_ALBARAN, "albaran_entrada", ESQUEMA_ALBARAN, cliente, max_tokens=4000, timeout=50)


def leer_etiqueta(contenido: bytes, tipo: str, cliente: httpx.Client | None = None) -> dict:
    return L._preguntar([(contenido, tipo)], PROMPT_ETIQUETA, "etiqueta_palet", ESQUEMA_ETIQUETA, cliente, max_tokens=1500, timeout=25)


def leer_peticion(archivos: list[tuple[bytes, str]], cliente: httpx.Client | None = None) -> dict:
    return L._preguntar(archivos, PROMPT_PETICION, "peticion_salida", ESQUEMA_PETICION, cliente, max_tokens=3000, timeout=40)
