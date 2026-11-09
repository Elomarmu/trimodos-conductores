"""Reglas de negocio de los viajes de PR (papel recuperado) de SAICA PAPER.

Aquí no hay llamadas a Odoo ni a Claude: solo tablas fijas y funciones puras (fáciles de probar).
- Tabla de puntos de carga → producto, nombre de la línea, origen en Odoo, precio por tonelada.
- Limpieza y comprobación de lo leído en el albarán (matrículas, código de carga Paper, contenedor ISO 6346, pesos).
- Texto de las observaciones de descarga del bulto (lo que no tiene campo propio en Odoo).
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")

# Datos fijos en Odoo (empresa 1 = STINSA). Comprobados en la v18 de pruebas el 09/11/2026.
CLIENTE_ID = 9228            # S.A. INDUSTRIAS CELULOSA ARAGONESA (SAICA PAPER), NIF A50002567
EMPRESA_ID = 1               # SERVICIOS DE TRÁFICOS INTERMODALES (STINSA)
DESTINO_ID = 12888           # STINSA SEVILLA (La Negrilla): destino de todos los viajes de PR
TIPO_VENTA_ID = 36           # Transporte Nacional
COMERCIAL_ID = 48            # Olga Venega Sánchez (comercial del pedido mensual)


@dataclass(frozen=True)
class PuntoCarga:
    clave: str
    nombre: str               # como se escribe en la Excel («Lugar de carga»)
    reconoce: tuple[str, ...]  # palabras que lo identifican en el albarán (sin tildes, en mayúsculas)
    producto_id: int
    linea: str                # nombre de la línea del pedido
    origen_id: int            # res.partner del origen del envío
    precio_t: float           # € por tonelada
    toneladas_fijas: float | None = None  # se factura una cantidad fija (Mercadona Huévar: 21 t)


PUNTOS_CARGA: tuple[PuntoCarga, ...] = (
    PuntoCarga("JEREZ", "SAICA NATUR - JEREZ", ("JEREZ",), 719, "PAPEL RECUPERADO JEREZ", 7274, 8.82),
    PuntoCarga("SEVILLA", "SAICA NATUR - SEVILLA", ("ALCALA DE GUADAIRA", "POL.IND. LA RED", "LA RED 11"),
               712, "PAPEL RECUPERADO SEVILLA", 8101, 4.72),
    PuntoCarga("LEOPROEX", "LEOPROEX", ("LEOPROEX", "MARTINEZ CANO"), 712, "PAPEL RECUPERADO SEVILLA", 15296, 4.72),
    PuntoCarga("MERCADONA", "MERCADONA - HUEVAR", ("HUEVAR",), 2664, "PR MERCADONA HUEVAR", 8151, 8.70, 21.0),
    PuntoCarga("PERSAN", "PERSAN", ("PERSAN",), 712, "PAPEL RECUPERADO SEVILLA", 34049, 4.72),
    PuntoCarga("VILELLA", "RAMON VILELLA", ("VILELLA",), 712, "PAPEL RECUPERADO SEVILLA", 25239, 4.72),
    PuntoCarga("ALTERNATIVA", "ALTERNATIVA ECOLOGICA", ("ALTERNATIVA ECOLOGICA",), 712, "PAPEL RECUPERADO SEVILLA", 16163, 4.72),
    PuntoCarga("ALCAMPO", "ALCAMPO SEVILLA", ("ALCAMPO",), 712, "PAPEL RECUPERADO SEVILLA", 8057, 4.72),
    PuntoCarga("PUSAMA", "PUSAMA", ("PUSAMA",), 719, "PAPEL RECUPERADO JEREZ", 18814, 8.82),
    PuntoCarga("ILUNION", "ILUNION", ("ILUNION",), 719, "PAPEL RECUPERADO JEREZ", 49152, 8.82),
)


def sin_tildes(texto: str | None) -> str:
    t = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in t if not unicodedata.combining(c)).upper()


def punto_de_carga(*textos: str | None) -> PuntoCarga | None:
    """Busca el punto de carga en el nombre/dirección del punto de carga (y, si hace falta, en el emisor del albarán).
    Se mira primero el texto del punto de carga: un albarán de Saica Natur cargado en Mercadona es Mercadona."""
    for texto in textos:
        t = re.sub(r"\s+", " ", sin_tildes(texto))
        for p in PUNTOS_CARGA:
            if any(r in t for r in p.reconoce):
                return p
    return None


# ---- limpieza de lo leído ----------------------------------------------------------------------------------------

RE_TRACTORA = re.compile(r"^\d{4}[A-Z]{3}$")          # 8734KDW
RE_REMOLQUE = re.compile(r"^R\d{4}[A-Z]{3}$")         # R8896BDF
RE_PAPER = re.compile(r"^S\d{12}$")                   # S261104001499
RE_CONTENEDOR = re.compile(r"^[A-Z]{4}\d{7}$")        # NPWU4957554


def matricula(texto: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", sin_tildes(texto))


def separar_matriculas(tractora: str | None, remolque: str | None) -> tuple[str, str]:
    """El albarán de Saica pone «R8896BDF 8734KDW» en un solo campo: se separan y se colocan en su sitio."""
    trozos = [matricula(x) for x in re.split(r"[\s/,;]+", f"{tractora or ''} {remolque or ''}") if matricula(x)]
    tr = next((x for x in trozos if RE_TRACTORA.match(x)), "")
    re_ = next((x for x in trozos if RE_REMOLQUE.match(x)), "")
    if not tr:
        tr = matricula(tractora)
    if not re_:
        re_ = matricula(remolque)
    return tr, re_


def codigo_paper(texto: str | None) -> str:
    t = matricula(texto)
    return t if RE_PAPER.match(t) else ""


def digito_iso6346(codigo10: str) -> int:
    """Dígito de control de un contenedor (ISO 6346) a partir de las 4 letras y 6 cifras."""
    valores, v = {}, 10
    for letra in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if v % 11 == 0:
            v += 1
        valores[letra] = v
        v += 1
    total = sum((valores[c] if c.isalpha() else int(c)) * (2 ** i) for i, c in enumerate(codigo10))
    return total % 11 % 10


def contenedor(texto: str | None) -> tuple[str, bool]:
    """(matrícula limpia, ¿dígito de control correcto?). «NPWU 495755-4» → («NPWU4957554», True)."""
    t = matricula(texto)
    if not RE_CONTENEDOR.match(t):
        return t, False
    return t, digito_iso6346(t[:10]) == int(t[10])


def kilos(valor) -> int | None:
    """«24.800» (punto de miles), «24800», 24800, 24.8 (toneladas) → 24800."""
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


def tipo_calidad(texto: str | None) -> str:
    """«110104 CARTON PAJA 100% 1.05.00» o «1.05.01 CARTON PAJA» → «1.05» (columna Tipo de la Excel)."""
    m = re.search(r"\b(\d\.\d{2})(?:\.\d{2})?\b", texto or "")
    return m.group(1) if m else ""


def fecha_hora_utc(fecha: str | None, hora: str | None) -> str | None:
    """Fecha y hora del albarán (hora de Madrid) → texto UTC para Odoo. Sin hora: 10:00 de Madrid."""
    if not fecha:
        return None
    try:
        d = dt.date.fromisoformat(fecha)
    except ValueError:
        return None
    h, m = 10, 0
    if hora and re.fullmatch(r"\d{1,2}:\d{2}", hora.strip()):
        h, m = (int(x) for x in hora.strip().split(":"))
    local = dt.datetime(d.year, d.month, d.day, h, m, tzinfo=MADRID)
    return local.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def toneladas(punto: PuntoCarga | None, peso_kg: int | None) -> float | None:
    if punto and punto.toneladas_fijas:
        return punto.toneladas_fijas
    return round(peso_kg / 1000, 2) if peso_kg else None


MESES = ("ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE",
         "NOVIEMBRE", "DICIEMBRE")


def referencia_pedido(fecha: str) -> str:
    """Referencia del pedido mensual: «PR NOVIEMBRE» (como los pedidos que ya existen: «PR OCTUBRE»)."""
    return f"PR {MESES[dt.date.fromisoformat(fecha).month - 1]}"


# ---- viaje: lo leído, ya limpio y comprobado --------------------------------------------------------------------

CAMPOS_OBS = (  # orden de las líneas en las observaciones de descarga del bulto
    ("albaran", "Albarán salida"), ("codigo_paper", "Código carga Paper"), ("codigo_natur", "Código carga Natur"),
    ("ler", "LER"), ("calidad", "Calidad"), ("bruto_kg", "Peso bruto kg"), ("tara_kg", "Tara kg"),
    ("tren", "Tren"), ("vagon", "Vagón"), ("caja_movil", "Caja móvil"), ("comprobaciones", "Comprobaciones"),
)
MARCA_OBS = "[PR SAICA]"


@dataclass
class Viaje:
    tipo_albaran: str = ""
    albaran: str = ""
    fecha: str = ""            # AAAA-MM-DD
    hora: str = ""             # HH:MM
    punto_texto: str = ""
    punto: PuntoCarga | None = None
    tractora: str = ""
    remolque: str = ""
    calidad: str = ""
    tipo: str = ""             # 1.05
    ler: str = ""
    peso_kg: int | None = None
    bultos: int | None = None
    bruto_kg: int | None = None
    tara_kg: int | None = None
    codigo_paper: str = ""
    codigo_natur: str = ""
    contenedor: str = ""
    avisos: list[str] = field(default_factory=list)   # lo que el conductor debe revisar
    pendientes: list[str] = field(default_factory=list)  # datos que faltan y se completarán después

    def a_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "punto"}
        d["punto"] = None if not self.punto else {
            "clave": self.punto.clave, "nombre": self.punto.nombre, "linea": self.punto.linea}
        d["toneladas"] = toneladas(self.punto, self.peso_kg)
        return d


def limpiar(leido: dict, hoy: dt.date | None = None) -> Viaje:
    """De lo que devuelve la lectura del albarán (Claude) a un Viaje limpio, con avisos y datos pendientes."""
    v = Viaje()
    v.tipo_albaran = (leido.get("tipo_albaran") or "").strip()
    v.albaran = re.sub(r"\s+", "", str(leido.get("numero_albaran") or ""))
    v.fecha = (leido.get("fecha") or "").strip()
    v.hora = (leido.get("hora") or "").strip()
    v.punto_texto = " ".join(x for x in (leido.get("punto_carga_nombre"), leido.get("punto_carga_direccion")) if x)
    v.punto = punto_de_carga(v.punto_texto, leido.get("emisor_albaran"))
    v.tractora, v.remolque = separar_matriculas(leido.get("matricula_tractora"), leido.get("matricula_remolque"))
    v.calidad = (leido.get("producto") or "").strip()
    v.tipo = tipo_calidad(v.calidad)
    v.ler = matricula(leido.get("ler"))
    v.peso_kg = kilos(leido.get("peso_neto_kg"))
    v.bruto_kg, v.tara_kg = kilos(leido.get("peso_bruto_kg")), kilos(leido.get("tara_kg"))
    b = leido.get("bultos")
    v.bultos = int(b) if isinstance(b, (int, float)) or (isinstance(b, str) and b.strip().isdigit()) else None
    v.codigo_paper = codigo_paper(leido.get("codigo_carga_paper"))
    v.codigo_natur = matricula(leido.get("codigo_carga_natur"))
    cont, ok = contenedor(leido.get("contenedor"))
    v.contenedor = cont

    if not v.albaran:
        v.avisos.append("No se lee el número de albarán.")
    try:
        f = dt.date.fromisoformat(v.fecha)
        if abs((f - (hoy or dt.date.today())).days) > 10:
            v.avisos.append(f"Revisa la fecha: {f:%d/%m/%Y}.")
    except ValueError:
        v.avisos.append("No se lee la fecha de carga.")
    if not v.punto:
        v.avisos.append("Punto de carga nuevo o no reconocido: se avisará a la oficina.")
    if not RE_TRACTORA.match(v.tractora):
        v.avisos.append("Revisa la matrícula de la tractora.")
    if not RE_REMOLQUE.match(v.remolque):
        v.avisos.append("Revisa la matrícula del remolque.")
    if v.peso_kg and v.bruto_kg and v.tara_kg and abs(v.bruto_kg - v.tara_kg - v.peso_kg) > 20:
        v.avisos.append("El peso neto no cuadra con bruto menos tara: revísalo.")
    if v.peso_kg and not 5000 <= v.peso_kg <= 30000:
        v.avisos.append("Revisa el peso: es raro para un viaje de PR.")
    if v.contenedor and not ok:
        v.avisos.append("Revisa el número de contenedor (no cuadra el dígito de control).")
    if v.punto and v.punto.clave == "LEOPROEX" and not v.contenedor:
        v.avisos.append("Falta el número de contenedor (en LEOPROEX se carga de un contenedor).")
    if not v.peso_kg:
        v.pendientes.append("peso")
    if v.bultos is None:
        v.pendientes.append("balas")
    if not v.codigo_paper:
        v.pendientes.append("código carga Paper")
    return v


def observaciones(datos: dict, anteriores: str | None = None) -> str:
    """Observaciones de descarga del bulto: «Clave: valor» por línea, tras la marca [PR SAICA].
    Si ya había observaciones con la marca, se actualizan esos valores y se respeta lo demás que hubiera escrito."""
    previos = leer_observaciones(anteriores)
    previos.update({k: v for k, v in datos.items() if v not in (None, "")})
    otras = [l for l in (anteriores or "").splitlines()
             if l.strip() and l.strip() != MARCA_OBS and not any(l.startswith(f"{t}:") for _, t in CAMPOS_OBS)]
    lineas = [MARCA_OBS] + [f"{t}: {previos[k]}" for k, t in CAMPOS_OBS if previos.get(k) not in (None, "")]
    return "\n".join(lineas + otras)


def leer_observaciones(texto: str | None) -> dict:
    por_titulo = {t: k for k, t in CAMPOS_OBS}
    res = {}
    for l in (texto or "").splitlines():
        if ":" in l:
            t, valor = l.split(":", 1)
            if t.strip() in por_titulo and valor.strip():
                res[por_titulo[t.strip()]] = valor.strip()
    return res


# ---- tren: composición (vagones y cajas móviles) -------------------------------------------------------------------

# Tara de la caja móvil (kg) según el principio de su matrícula. Se completa con lo que se vaya viendo en la Excel.
TARAS_CAJA = (("ICR102", 5900), ("ICR111", 5100), ("ICR091", 5600), ("MXMU", 5600), ("CRPU", 5600), ("CTEA", 4400))


def tara_caja(caja: str) -> int | None:
    return next((t for pref, t in TARAS_CAJA if caja.startswith(pref)), None)


def digito_uic(once: str) -> int:
    """Dígito de control de un vagón (UIC): pesos 2,1,2,1… sobre las 11 cifras, sumando las cifras de cada producto."""
    total = sum(sum(divmod(int(c) * (2 if i % 2 == 0 else 1), 10)) for i, c in enumerate(once))
    return (10 - total % 10) % 10


def vagon_excel(vagon: str) -> tuple[str, bool]:
    """«43714378668-3» → («437143786683», True). En la Excel CARTON PAJA TREN el vagón va con sus 12 cifras, sin guion.
    El segundo valor dice si el dígito de control cuadra (si no, seguramente se ha leído mal alguna cifra)."""
    cifras = re.sub(r"\D", "", vagon or "")
    if len(cifras) != 12:
        return cifras, False
    return cifras, digito_uic(cifras[:11]) == int(cifras[11])


def limpiar_tren(leido: dict) -> dict:
    """Lista de cajas del tren (una fila por caja, como la Excel) y avisos si algo no cuadra."""
    filas, avisos = [], list(leido.get("dudas") or [])
    for v in sorted(leido.get("vagones") or [], key=lambda x: x.get("orden") or 0):
        cajas = [matricula(c) for c in v.get("cajas") or [] if matricula(c)]
        if v.get("num_utis") is not None and v["num_utis"] != len(cajas):
            avisos.append(f"Vagón {v.get('orden')}: la hoja dice {v['num_utis']} cajas y se han leído {len(cajas)}.")
        vagon, vagon_ok = vagon_excel(v.get("vagon", ""))
        if not vagon_ok:
            avisos.append(f"Vagón {v.get('orden')} ({v.get('vagon')}): revisa el número, no cuadra el dígito de control.")
        for i, c in enumerate(cajas):
            filas.append({"orden": v.get("orden"), "vagon": vagon, "segunda": i > 0,
                          "caja_movil": c, "tara_kg": tara_caja(c)})
            if tara_caja(c) is None:
                avisos.append(f"Caja {c}: tara desconocida, complétala en la Excel.")
    return {"numero_tren": leido.get("numero_tren") or "", "fecha": leido.get("fecha") or "", "cajas": filas, "avisos": avisos}
