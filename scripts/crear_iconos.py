"""Genera los iconos de la app (acceso directo del móvil y pestaña del navegador). Requiere Pillow.

Diseño: el símbolo de Grupo Trimodos en amarillo sobre fondo grafito, con «DECA» discreto debajo.
El símbolo se dibuja en vectorial con las medidas de public/img/trimodos.png (coincide en un 98 %), así queda nítido
a cualquier tamaño. Ejecutar solo si se cambia el diseño:
    .venv/bin/python -m scripts.crear_iconos          (Windows: .\\.venv\\Scripts\\python.exe -m scripts.crear_iconos)
Después, subir la versión de caché en public/sw.js.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "public" / "icons"
FONT = ROOT / "public" / "fonts" / "atkinson-hyperlegible-next-latin-wght-normal.woff2"
YELLOW = (252, 218, 0)                          # amarillo Grupo Trimodos (el de la app)
GRAPHITE_IN, GRAPHITE_OUT = (42, 42, 42), (9, 9, 9)
TEXT = (168, 168, 168)                          # «DECA» en gris: se lee, pero no compite con el símbolo
MASTER = 2048                                   # se dibuja grande y se reduce: bordes suaves


def symbol(width: int) -> Image.Image:
    """Máscara del símbolo. Unidades del logo original: 94x96, trazo 5."""
    k = width / 94
    w, h = round(94 * k), round(96 * k)
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    P = lambda x, y: (x * k, y * k)
    t = round(5 * k)
    d.rectangle([0, 0, w - 1, h - 1], outline=255, width=t)               # marco
    d.line([P(-5, 31), P(99, 31)], fill=255, width=t)                   # horizontal a un tercio
    d.line([P(47, -5), P(47, 101)], fill=255, width=t)                  # vertical central
    for lado in (-1, 1):                                                # la «A»: del centro de arriba a las esquinas
        d.line([P(47 + lado * 0.49 * -12.5, -10), P(47 + lado * 0.49 * 103.5, 106)], fill=255, width=t)
    r = 53.5 + 2.5                                                      # arco (el trazo se dibuja hacia dentro)
    d.arc([P(47 - r, 25 - r), P(47 + r, 25 + r)], 0, 180, fill=255, width=t)
    return m                                                            # lo que sobresale del marco queda fuera


def word(text: str, size: int) -> Image.Image:
    """Texto con la letra de la app, espaciado amplio."""
    f = ImageFont.truetype(str(FONT), size)
    f.set_variation_by_axes([620])
    track = 0.32 * size
    total = sum(f.getlength(c) for c in text) + track * (len(text) - 1)
    asc, desc = f.getmetrics()
    m = Image.new("L", (int(total) + 8, asc + desc), 0)
    d = ImageDraw.Draw(m)
    x = 0.0
    for c in text:
        d.text((x, 0), c, font=f, fill=255)
        x += f.getlength(c) + track
    return m.crop(m.getbbox())


def background(n: int) -> Image.Image:
    """Grafito con una luz suave detrás del símbolo."""
    g = 256
    cx, cy = 0.5, 0.44
    pts = [min(255, int(255 * min(1.0, (((x / g - cx) ** 2 + (y / g - cy) ** 2) ** 0.5) / 0.72) ** 1.4)) for y in range(g) for x in range(g)]
    mask = Image.new("L", (g, g)); mask.putdata(pts)
    mask = mask.resize((n, n), Image.BICUBIC)
    return Image.composite(Image.new("RGB", (n, n), GRAPHITE_OUT), Image.new("RGB", (n, n), GRAPHITE_IN), mask)


def icon(scale: float, with_text: bool) -> Image.Image:
    """scale: tamaño del dibujo (1 = normal; menos en el icono «maskable», que Android recorta en círculo)."""
    n = MASTER
    img = background(n)
    sym = symbol(round(n * (0.50 if with_text else 0.60) * scale))
    parts = [sym]
    gap = 0
    if with_text:
        txt = word("DECA", round(n * 0.085 * scale))
        gap = round(n * 0.075 * scale)
        parts.append(txt)
    top = (n - sum(p.height for p in parts) - gap) // 2
    img.paste(Image.new("RGB", sym.size, YELLOW), ((n - sym.width) // 2, top), sym)
    if with_text:
        img.paste(Image.new("RGB", txt.size, TEXT), ((n - txt.width) // 2, top + sym.height + gap), txt)
    return img


def rounded(img: Image.Image) -> Image.Image:
    """Pestaña del navegador: esquinas redondeadas y transparentes."""
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=img.width * 0.22, fill=255)
    out = img.convert("RGBA")
    out.putalpha(m)
    return out


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    normal, maskable, solo_simbolo = icon(1.0, True), icon(0.84, True), icon(1.0, False)
    for name, src, size in (("icon-192.png", normal, 192), ("icon-512.png", normal, 512),
                            ("maskable-512.png", maskable, 512), ("apple-touch-icon.png", normal, 180)):
        src.resize((size, size), Image.LANCZOS).save(OUT / name, optimize=True)
        print("creado", name)
    rounded(solo_simbolo).resize((192, 192), Image.LANCZOS).save(OUT.parent / "favicon.png", optimize=True)   # a 16-32 px el texto no se leería
    print("creado favicon.png")
