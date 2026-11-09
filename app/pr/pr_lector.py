"""Lectura con Claude (visión) de los albaranes de PR y del listado de cajas móviles del tren.

Se llama a la API de mensajes de Anthropic con httpx (sin dependencias nuevas) y se obliga a responder con una
herramienta de esquema fijo: así la respuesta siempre es un JSON con los mismos campos.

Variables de entorno: ANTHROPIC_API_KEY (obligatoria), PR_MODELO (por defecto claude-sonnet-5-5).
"""
from __future__ import annotations

import base64
import logging
import os

import httpx

log = logging.getLogger("pr")
API_URL = "https://api.anthropic.com/v1/messages"
MODELO_DEFECTO = "claude-sonnet-5-5"
TIPOS_IMAGEN = {"image/jpeg", "image/png", "image/webp", "image/gif"}


class LecturaError(Exception):
    pass


PROMPT_ALBARAN = """Lee este albarán de carga de papel recuperado (PR) para el cliente SAICA. Es una foto hecha con el
móvil por el conductor: puede estar torcida, con sombras, sellos y anotaciones a mano.

Hay dos modelos de albarán:
1. «saica_natur»: albarán de SAICA NATUR («ALBARÁN SALIDA» con número de 12 caracteres, p. ej. 592611000006 o
   502611T00064). Trae CARGADOR, TRANSPORTISTA, MATRICULA («R8896BDF 8734KDW» = remolque y tractora), FECHA SERVICIO,
   CONCEPTO, LER, PESO, CANTIDAD (balas), CODIGO CARGA PAPER (S + 12 cifras), CODIGO CARGA NATUR, BRUTO, TARA y
   PUNTO DE CARGA (abajo a la izquierda). El punto de carga puede ser otro distinto de Saica Natur (p. ej. MERCADONA).
2. «martinez_cano»: parte de salida de MARTINEZ CANO CANARIAS (número de 5 cifras en «PARTE SALIDA · NUMERO»),
   FECHA en tres casillas (día, mes, año de 2 cifras), CALIDAD, BALAS, KILOS y matrículas escritas a mano
   (camión propio = tractora, camión ajeno = remolque, la que empieza por R). Este albarán se carga siempre en LEOPROEX:
   pon «LEOPROEX» como punto de carga. Escrito a mano junto a la calidad suele venir el contenedor del que se carga
   (4 letras + 7 cifras, p. ej. NPWU 495755-4 → NPWU4957554). Arriba, escrito a mano, puede venir el código de carga
   Paper (S + 12 cifras) que anota la oficina.

Reglas:
- Los pesos usan punto de miles: «24.800» son 24800 kg. Da los kilos como número entero.
- Si un dato no aparece o está en blanco (p. ej. peso o cantidad vacíos), devuelve null. No te lo inventes.
- Ignora los números rodeados con un círculo o sueltos tipo «555», «0555» (son referencias internas del pedido),
  las firmas, los sellos de empresas y los números escritos al pie.
- Fecha en formato AAAA-MM-DD (los años de 2 cifras son 20xx). Hora HH:MM si aparece.
- Las matrículas sin espacios ni guiones. El remolque empieza por R.
- En «dudas» explica brevemente cualquier dato que no se lea con seguridad."""

ESQUEMA_ALBARAN = {
    "type": "object",
    "properties": {
        "tipo_albaran": {"type": "string", "enum": ["saica_natur", "martinez_cano", "otro"]},
        "emisor_albaran": {"type": ["string", "null"], "description": "Empresa que emite el albarán (cabecera)"},
        "numero_albaran": {"type": ["string", "null"]},
        "fecha": {"type": ["string", "null"], "description": "AAAA-MM-DD"},
        "hora": {"type": ["string", "null"], "description": "HH:MM"},
        "punto_carga_nombre": {"type": ["string", "null"]},
        "punto_carga_direccion": {"type": ["string", "null"]},
        "transportista": {"type": ["string", "null"]},
        "matricula_tractora": {"type": ["string", "null"]},
        "matricula_remolque": {"type": ["string", "null"]},
        "producto": {"type": ["string", "null"], "description": "Concepto o calidad tal cual, p. ej. 110104 CARTON PAJA 100% 1.05.00"},
        "ler": {"type": ["string", "null"]},
        "peso_neto_kg": {"type": ["integer", "null"]},
        "bultos": {"type": ["integer", "null"], "description": "Balas (CANTIDAD o BALAS)"},
        "peso_bruto_kg": {"type": ["integer", "null"]},
        "tara_kg": {"type": ["integer", "null"]},
        "codigo_carga_paper": {"type": ["string", "null"]},
        "codigo_carga_natur": {"type": ["string", "null"]},
        "contenedor": {"type": ["string", "null"]},
        "dudas": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["tipo_albaran", "numero_albaran", "fecha", "punto_carga_nombre", "matricula_tractora",
                 "matricula_remolque", "peso_neto_kg", "bultos", "codigo_carga_paper", "dudas"],
}

PROMPT_TREN = """Esta es la composición de un tren de mercancías (documento de Captrain «DOCUMENTO DE ADMISIÓN DE TREN»
o la tabla de cajas móviles). Puede venir girada 90 grados.

- Si es la hoja de datos del tren: lee el número de tren («NÚMERO»), la fecha y, por cada vagón, el orden, el número
  de vagón («Nº Serie U.I.C.», p. ej. 388049630055-0) y el número de UTI que lleva (columna «UTI»).
- Si es la tabla «Order · WagonNr · UTI_1 … UTI_5»: por cada vagón, su orden, el número y las cajas móviles (UTI)
  en el orden en que aparecen (normalmente 1 o 2). Las cajas son matrículas tipo ICR102043, MXMU1450334, CTEA25000166.
- Si el documento trae las dos hojas, combínalas: por cada vagón, «num_utis» de la hoja de datos y «cajas» de la tabla.
- Escribe los números de vagón tal cual, con el guion del dígito de control. Las cajas sin espacios.
- Si algo no se lee con seguridad, explícalo en «dudas». No inventes vagones ni cajas."""

ESQUEMA_TREN = {
    "type": "object",
    "properties": {
        "numero_tren": {"type": ["string", "null"]},
        "fecha": {"type": ["string", "null"], "description": "AAAA-MM-DD"},
        "vagones": {"type": "array", "items": {"type": "object", "properties": {
            "orden": {"type": "integer"},
            "vagon": {"type": "string"},
            "num_utis": {"type": ["integer", "null"]},
            "cajas": {"type": "array", "items": {"type": "string"}},
        }, "required": ["orden", "vagon", "cajas"]}},
        "dudas": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["vagones", "dudas"],
}


def _bloque(contenido: bytes, tipo: str) -> dict:
    datos = base64.b64encode(contenido).decode()
    if tipo == "application/pdf":
        return {"type": "document", "source": {"type": "base64", "media_type": tipo, "data": datos}}
    if tipo not in TIPOS_IMAGEN:
        raise LecturaError("Formato no admitido: manda una foto (JPG/PNG) o un PDF.")
    return {"type": "image", "source": {"type": "base64", "media_type": tipo, "data": datos}}


def _preguntar(archivos: list[tuple[bytes, str]], prompt: str, herramienta: str, esquema: dict,
               cliente: httpx.Client | None = None) -> dict:
    clave = os.environ.get("ANTHROPIC_API_KEY", "")
    if not clave:
        raise LecturaError("Falta configurar la lectura automática (ANTHROPIC_API_KEY).")
    cuerpo = {
        "model": os.environ.get("PR_MODELO", MODELO_DEFECTO),
        "max_tokens": 4000,
        "tools": [{"name": herramienta, "description": "Devuelve los datos leídos", "input_schema": esquema}],
        "tool_choice": {"type": "tool", "name": herramienta},
        "messages": [{"role": "user", "content": [*(_bloque(c, t) for c, t in archivos), {"type": "text", "text": prompt}]}],
    }
    cab = {"x-api-key": clave, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    cli = cliente or httpx.Client(timeout=25)
    try:
        r = cli.post(API_URL, json=cuerpo, headers=cab)
    except httpx.HTTPError as e:
        raise LecturaError("No se ha podido leer el documento (sin conexión). Inténtalo de nuevo.") from e
    if r.status_code != 200:
        log.warning("Lectura con Claude: HTTP %s %s", r.status_code, r.text[:300])
        raise LecturaError("No se ha podido leer el documento. Inténtalo de nuevo o haz otra foto.")
    for b in r.json().get("content", []):
        if b.get("type") == "tool_use" and b.get("name") == herramienta:
            return b["input"]
    raise LecturaError("No se ha podido leer el documento. Haz otra foto con más luz.")


def leer_albaran(contenido: bytes, tipo: str, cliente: httpx.Client | None = None) -> dict:
    return _preguntar([(contenido, tipo)], PROMPT_ALBARAN, "datos_albaran", ESQUEMA_ALBARAN, cliente)


def leer_tren(archivos: list[tuple[bytes, str]], cliente: httpx.Client | None = None) -> dict:
    return _preguntar(archivos, PROMPT_TREN, "composicion_tren", ESQUEMA_TREN, cliente)
