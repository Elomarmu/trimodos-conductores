"""Crea la imagen de vista previa (1200x630) que se ve al compartir el enlace por WhatsApp, Teams, correo...

Uso:  .\\.venv\\Scripts\\python.exe -m scripts.crear_imagen_compartir ruta_logo_trimodos.png "Modificación DECA"
Salida: public/img/compartir.png. Requiere Pillow y la fuente Segoe UI de Windows.
"""
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "public" / "img" / "compartir.png"
W, H = 1200, 630
AMARILLO, VERDE, NARANJA, TEXTO = (252, 218, 0), (0, 159, 146), (241, 126, 73), (17, 17, 17)


def main(logo_path: str, titulo: str):
    logo = Image.open(logo_path).convert("RGBA")
    fondo = Image.new("RGBA", logo.size, (255, 255, 255, 255))
    logo = logo.crop(ImageChops.difference(Image.alpha_composite(fondo, logo).convert("RGB"), Image.new("RGB", logo.size, (255, 255, 255))).getbbox())
    ancho = 820
    logo = logo.resize((ancho, round(logo.height * ancho / logo.width)), Image.LANCZOS)

    img = Image.new("RGB", (W, H), (255, 255, 255))
    img.paste(logo, ((W - ancho) // 2, 120), logo)
    d = ImageDraw.Draw(img)
    fuente = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 66)
    caja = d.textbbox((0, 0), titulo, font=fuente)
    d.text(((W - (caja[2] - caja[0])) // 2, 400), titulo, font=fuente, fill=TEXTO)
    # franja con los tres colores del grupo
    for i, color in enumerate((AMARILLO, VERDE, NARANJA)):
        d.rectangle((i * W // 3, H - 28, (i + 1) * W // 3, H), fill=color)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, optimize=True)
    print("creada", OUT, img.size, OUT.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "Modificación DECA")
