"""Prepara los logos para la web: recorta el margen en blanco, reduce el tamaño y muestra el color corporativo.

Uso:  .\\.venv\\Scripts\\python.exe -m scripts.preparar_logos trimodos=ruta.png stinsa=ruta.png transargi=ruta.png
Salida: public/img/<nombre>.png (alto 96 px, con transparencia en el fondo blanco). Requiere Pillow.
"""
import sys
from collections import Counter
from pathlib import Path

from PIL import Image

OUT = Path(__file__).resolve().parent.parent / "public" / "img"
ALTO = 96  # píxeles; se ve nítido incluso a 2x en el móvil


def preparar(nombre: str, origen: str) -> str:
    img = Image.open(origen).convert("RGBA")
    # el fondo blanco (o casi) pasa a transparente
    px = img.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = px[x, y]
            if a and r > 245 and g > 245 and b > 245:
                px[x, y] = (255, 255, 255, 0)
    img = img.crop(img.getchannel("A").getbbox())          # recorta el margen vacío
    ancho = round(img.width * ALTO / img.height)
    img = img.resize((ancho, ALTO), Image.LANCZOS)
    OUT.mkdir(parents=True, exist_ok=True)
    img.save(OUT / f"{nombre}.png", optimize=True)
    # color predominante que no sea casi negro ni transparente
    cuenta = Counter(p[:3] for p in img.getdata() if p[3] > 250 and max(p[:3]) > 60)
    r, g, b = cuenta.most_common(1)[0][0]
    return f"#{r:02x}{g:02x}{b:02x}"


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        nombre, ruta = arg.split("=", 1)
        print(f"{nombre}: {preparar(nombre, ruta)}  ->  public/img/{nombre}.png")
