"""Genera los iconos de la PWA en public/icons (solo hace falta si quieres cambiar el diseño). Requiere Pillow."""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "public" / "icons"
YELLOW, BLACK = (255, 212, 0), (0, 0, 0)


def draw(size: int, maskable: bool) -> Image.Image:
    img = Image.new("RGB", (size, size), BLACK if maskable else YELLOW)
    d = ImageDraw.Draw(img)
    # zona segura: los iconos "maskable" se recortan, así que el dibujo ocupa el 60% central
    pad = size * (0.22 if maskable else 0.14)
    box = (pad, pad, size - pad, size - pad)
    if maskable:
        d.rounded_rectangle(box, radius=size * 0.1, fill=YELLOW)
    inner = size - 2 * pad
    m = inner * 0.12                      # margen interior
    cell = (inner - 2 * m) / 7            # cuadrícula 7x7 tipo QR
    x0, y0 = pad + m, pad + m

    def block(cx, cy, w, h, color=BLACK):
        d.rectangle((x0 + cx * cell, y0 + cy * cell, x0 + (cx + w) * cell - 1, y0 + (cy + h) * cell - 1), fill=color)

    for cx, cy in ((0, 0), (4, 0), (0, 4)):  # tres esquinas de localización
        block(cx, cy, 3, 3)
        block(cx + 0.75, cy + 0.75, 1.5, 1.5, YELLOW)
        block(cx + 1, cy + 1, 1, 1)
    for cx, cy, w, h in ((4, 4, 1, 1), (6, 4, 1, 1), (5, 5, 1, 1), (4, 6, 1, 1), (6, 6, 1, 1), (3, 3, 1, 1)):
        block(cx, cy, w, h)
    return img


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size, mask in (("icon-192.png", 192, False), ("icon-512.png", 512, False), ("maskable-512.png", 512, True), ("apple-touch-icon.png", 180, False)):
        draw(size, mask).save(OUT / name)
        print("creado", name)
