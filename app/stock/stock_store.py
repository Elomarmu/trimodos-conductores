"""Dónde se guarda el estado del almacén de Burriana (entradas, salidas, clientes y fotos).

Vercel no guarda nada entre llamadas, así que el estado vive fuera. Igual que la composición del tren en los viajes de PR
(adjunto JSON en Odoo), aquí cada documento es un adjunto JSON de Odoo colgado de la empresa STINSA:

    STOCK-entrada-E-2026-0001.json · STOCK-salida-S-2026-0001.json · STOCK-config-clientes.json · STOCK-foto-<doc>--<nombre>

Un adjunto por entrada/salida (no uno gordo para todo el stock): dos personas trabajando a la vez en operaciones distintas
no se pisan. El stock se calcula con las entradas y salidas (`stock_reglas.stock`), no se guarda.

Interfaz (la que habrá que cumplir si algún día se cambia por modelos propios de Odoo, ver `stock_facturacion`):
    listar(tipo) · leer(tipo, id) · escribir(tipo, id, datos) · guardar_foto(doc, nombre, bytes, tipo) · leer_fotos(doc)
"""
from __future__ import annotations

import base64
import json
import threading
from typing import Protocol

RES_MODEL = "res.company"   # los adjuntos cuelgan de la empresa (STINSA = 1)
PREFIJO = "STOCK-"


class Store(Protocol):
    def listar(self, tipo: str) -> list[dict]: ...
    def leer(self, tipo: str, ident: str) -> dict | None: ...
    def escribir(self, tipo: str, ident: str, datos: dict) -> None: ...
    def guardar_foto(self, doc: str, nombre: str, datos: bytes, tipo: str) -> None: ...
    def leer_fotos(self, doc: str) -> list[dict]: ...


class MemoriaStore:
    """Para pruebas y para el modo de demostración (no sobrevive a un reinicio)."""

    def __init__(self):
        self.docs: dict[tuple[str, str], dict] = {}
        self.fotos: dict[str, dict[str, dict]] = {}
        self._cerrojo = threading.Lock()

    def listar(self, tipo):
        return [json.loads(json.dumps(v)) for (t, _), v in sorted(self.docs.items()) if t == tipo]

    def leer(self, tipo, ident):
        v = self.docs.get((tipo, ident))
        return json.loads(json.dumps(v)) if v is not None else None

    def escribir(self, tipo, ident, datos):
        with self._cerrojo:
            self.docs[(tipo, ident)] = json.loads(json.dumps(datos))

    def guardar_foto(self, doc, nombre, datos, tipo):
        self.fotos.setdefault(doc, {})[nombre] = {"nombre": nombre, "b64": base64.b64encode(datos).decode(), "tipo": tipo}

    def leer_fotos(self, doc):
        return list(self.fotos.get(doc, {}).values())


class OdooAdjuntosStore:
    """Adjuntos de Odoo. `odoo` es el cliente con lista blanca de la app de PR (`OdooPR`): solo usa `ir.attachment`
    (search_read, create, write), que ya están permitidos."""

    def __init__(self, odoo, empresa_id: int = 1):
        self.odoo, self.empresa_id = odoo, empresa_id

    @staticmethod
    def _nombre(tipo, ident):
        return f"{PREFIJO}{tipo}-{ident}.json"

    def _buscar(self, nombre: str, campos=("id",)):
        r = self.odoo("ir.attachment", "search_read", [[["res_model", "=", RES_MODEL], ["name", "=", nombre]]], fields=list(campos), limit=1)
        return r[0] if r else None

    def listar(self, tipo):
        filas = self.odoo("ir.attachment", "search_read", [[["res_model", "=", RES_MODEL], ["name", "=like", f"{PREFIJO}{tipo}-%.json"]]],
                          fields=["name", "datas"], order="name asc")
        return [json.loads(base64.b64decode(f["datas"]).decode()) for f in filas if f.get("datas")]

    def leer(self, tipo, ident):
        a = self._buscar(self._nombre(tipo, ident), ("datas",))
        return json.loads(base64.b64decode(a["datas"]).decode()) if a and a.get("datas") else None

    def escribir(self, tipo, ident, datos):
        contenido = base64.b64encode(json.dumps(datos, ensure_ascii=False).encode()).decode()
        previo = self._buscar(self._nombre(tipo, ident))
        if previo:
            self.odoo("ir.attachment", "write", [[previo["id"]], {"datas": contenido}])
        else:
            self.odoo("ir.attachment", "create", [{"name": self._nombre(tipo, ident), "res_model": RES_MODEL, "res_id": self.empresa_id,
                                                   "datas": contenido, "mimetype": "application/json", "type": "binary"}])

    def guardar_foto(self, doc, nombre, datos, tipo):
        clave = f"{PREFIJO}foto-{doc}--{nombre}"
        contenido = base64.b64encode(datos).decode()
        previo = self._buscar(clave)
        if previo:
            self.odoo("ir.attachment", "write", [[previo["id"]], {"datas": contenido, "mimetype": tipo}])
        else:
            self.odoo("ir.attachment", "create", [{"name": clave, "res_model": RES_MODEL, "res_id": self.empresa_id, "datas": contenido,
                                                   "mimetype": tipo, "type": "binary"}])

    def leer_fotos(self, doc):
        filas = self.odoo("ir.attachment", "search_read", [[["res_model", "=", RES_MODEL], ["name", "=like", f"{PREFIJO}foto-{doc}--%"]]],
                          fields=["name", "datas", "mimetype"], order="id asc")
        corte = len(f"{PREFIJO}foto-{doc}--")
        return [{"nombre": f["name"][corte:], "b64": f["datas"], "tipo": f.get("mimetype") or "image/jpeg"} for f in filas if f.get("datas")]
