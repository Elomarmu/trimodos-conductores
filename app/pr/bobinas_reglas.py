"""Reglas de negocio para viajes de bobinas SAICA.

Mapeo destino → producto, limpieza de datos, constantes Odoo.
Los viajes de bobinas llegan en el mismo tren que PR pero son un tipo diferente.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

MADRID = __import__("zoneinfo").ZoneInfo("Europe/Madrid")

# Datos fijos en Odoo (empresa 1 = STINSA). Validados para bobinas.
CLIENTE_ID = 9228            # S.A. INDUSTRIAS CELULOSA ARAGONESA (SAICA PAPER)
EMPRESA_ID = 1               # SERVICIOS DE TRÁFICOS INTERMODALES (STINSA)
DESTINO_ID = 12888           # STINSA SEVILLA (La Negrilla): destino default
TIPO_VENTA_ID = 36           # Transporte Nacional
COMERCIAL_ID = 48            # Olga Venega Sánchez


@dataclass(frozen=True)
class DestinoBobinas:
    """Mapeo de destino → producto de bobinas."""
    clave: str                # "SEVILLA", "CORDOBA", etc.
    nombre: str               # Nombre línea: "BOBINAS SEVILLA"
    producto_id: int          # ID del producto en Odoo
    origen_id: int | None = None  # Punto de origen (si varía)
    precio_t: float | None = None  # €/Tn (si no, usa el del producto)


# Mapeo de destinos a productos. Completar con IDs reales de Odoo.
DESTINOS_BOBINAS: dict[str, DestinoBobinas] = {
    "SEVILLA": DestinoBobinas("SEVILLA", "BOBINAS SEVILLA", 0),  # TODO: buscar producto_id
    # Agregar más destinos según Odoo
}


def sin_tildes(texto: str | None) -> str:
    """Quita acentos y pasa a mayúsculas."""
    t = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in t if not unicodedata.combining(c)).upper()


def mapear_destino(destino_texto: str | None) -> DestinoBobinas | None:
    """Busca el destino en la tabla DESTINOS_BOBINAS.

    Busca por palabra clave (ej: 'Sevilla' → 'SEVILLA', 'Córdoba' → 'CORDOBA').
    """
    if not destino_texto:
        return None

    t = sin_tildes(destino_texto).strip()

    # Búsqueda exacta primero
    if t in DESTINOS_BOBINAS:
        return DESTINOS_BOBINAS[t]

    # Búsqueda parcial: si la clave está contenida
    for clave, destino in DESTINOS_BOBINAS.items():
        if clave in t or t in clave:
            return destino

    return None


def matricula(texto: str | None) -> str:
    """Limpia matrícula: quita espacios, caracteres especiales, pasa a mayúsculas."""
    return re.sub(r"[^A-Z0-9]", "", sin_tildes(texto))


def kilos(valor) -> int | None:
    """«24.800» (punto de miles), «24800», 24800, 24.8 (toneladas) → 24800 kg."""
    if valor in (None, ""):
        return None
    if isinstance(valor, (int, float)):
        n = float(valor)
    else:
        t = str(valor).strip().upper().replace("KG", "").replace(" ", "")
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", t):
            t = t.replace(".", "")
        t = t.replace(",", ".")
        try:
            n = float(t)
        except ValueError:
            return None
    if 0 < n < 100:  # vino en toneladas
        n *= 1000
    return int(round(n)) if n > 0 else None


def referencia_pedido(mes: str) -> str:
    """Referencia del pedido mensual: BOBINAS NOVIEMBRE 2026, etc.

    Formato: 'BOBINAS {MES}'.
    """
    return f"BOBINAS {mes.upper()}"
