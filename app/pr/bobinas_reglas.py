"""Reglas de los viajes de bobinas de SAICA (Lista de Carga → línea del pedido «BOBINAS {MES}»).

Comprobado en Odoo el 27/11/2026 con los pedidos S26100643 (BOBINAS NOVIEMBRE) y S26093468 (BOBINAS OCTUBRE):
- Origen siempre STINSA SEVILLA (12888): las bobinas llegan en tren y se reparten a la fábrica del cliente.
- El producto y el destino dependen del «Lugar de entrega» de la Lista de Carga.
- Cantidad de la línea = toneladas (kg / 1000). El precio lo pone el producto (no se escribe).
- Bulto: mercancía BOBINAS DE PAPEL (93), referencia de seguimiento = Nº de la Lista de Carga.
- Coste de compra: subcontrata = el de su último viaje de ese producto; camión propio = 0.
- Los productos «CON RETORNO» no se detectan en el documento: los pone la oficina a mano.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

CLIENTE_ID = 9228            # (SAICA) S.A. INDUSTRIAS CELULOSA ARAGONESA
EMPRESA_ID = 1               # STINSA
ORIGEN_ID = 12888            # STINSA SEVILLA (terminal)
TIPO_VENTA_ID = 36           # Transporte Nacional
COMERCIAL_ID = 47            # Sandra Venega Sánchez (comercial de los pedidos de bobinas)
GOODS_BOBINAS = 93           # tms.goods «BOBINAS DE PAPEL»

MESES = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE",
         "NOVIEMBRE", "DICIEMBRE"]


@dataclass(frozen=True)
class DestinoBobinas:
    clave: str
    linea: str                # descripción de la línea, como la pone la oficina
    producto_id: int
    destino_id: int           # res.partner de la fábrica de descarga
    palabras: tuple[str, ...]  # cómo aparece en «Lugar de entrega»


DESTINOS: tuple[DestinoBobinas, ...] = (
    DestinoBobinas("SEVILLA", "BOBINAS PAPEL SEVILLA", 41, 7206, ("SAICA PACK SEVILLA", "SEVILLA")),
    DestinoBobinas("CORDOBA", "BOBINAS PAPEL CORDOBA (SIN RETORNO)", 725, 7905, ("RAMBLE", "RAMBALE", "CORDOBA", "LA RAMBLA")),
    DestinoBobinas("HUELVA", "BOBINAS PAPEL HUELVA", 726, 9283, ("SKG", "SMURFIT", "HUELVA")),
    DestinoBobinas("ALMENDRALEJO", "BOBINAS DE PAPEL ALMENDRALEJO (SIN RETORNO)", 788, 13909, ("ONDUPACK", "ALMENDRALEJO")),
    DestinoBobinas("VALENCIA", "BOBINAS PAPEL SEV-VALENCIA", 2385, 28759, ("PATERNA", "VALENCIA")),
)


def sin_tildes(texto: str | None) -> str:
    t = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in t if not unicodedata.combining(c)).upper()


def destino(lugar_entrega: str | None) -> DestinoBobinas | None:
    """Destino por el «Lugar de entrega». Se mira primero lo más concreto (Huelva, Córdoba…) y Sevilla al final,
    porque «SEVILLA» también aparece en el resto de documentos (destino del tren)."""
    t = sin_tildes(lugar_entrega)
    if not t:
        return None
    for d in DESTINOS[1:] + DESTINOS[:1]:
        if any(p in t for p in d.palabras):
            return d
    return None


def referencia_pedido(fecha: str) -> str:
    """«2026-11-24» → «BOBINAS NOVIEMBRE» (como los pedidos que ya hace la oficina)."""
    return f"BOBINAS {MESES[int(fecha[5:7]) - 1]}"


def toneladas(kg: int | None) -> float:
    return round((kg or 0) / 1000, 3)
