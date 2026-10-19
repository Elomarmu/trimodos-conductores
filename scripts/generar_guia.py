"""Genera public/guia.html (la guía dentro de la app) a partir de GUIA_CONDUCTORES.md.

La fuente es SIEMPRE el .md: edítalo y vuelve a ejecutar
    .\\.venv\\Scripts\\python.exe -X utf8 -m scripts.generar_guia
Un test comprueba que guia.html no se ha quedado desfasado respecto al .md. Requiere el paquete "markdown".
"""
from pathlib import Path

import markdown

RAIZ = Path(__file__).resolve().parent.parent
ORIGEN = RAIZ / "GUIA_CONDUCTORES.md"
DESTINO = RAIZ / "public" / "guia.html"

PLANTILLA = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#000000">
<meta name="robots" content="noindex, nofollow">
<title>Guía de uso - DECA Trimodos</title>
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" type="image/png" href="/favicon.png">
<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">
<link rel="stylesheet" href="/styles.css">
</head>
<body data-screen="guia">
<!-- Archivo GENERADO por scripts/generar_guia.py desde GUIA_CONDUCTORES.md: no lo edites a mano. -->
<header class="brandbar"><a href="/"><img src="/img/trimodos.png" alt="Grupo Trimodos"></a></header>
<main class="screen guia">
<a class="btn primary" href="/">Volver a la app</a>
{contenido}
<a class="btn primary" href="/">Volver a la app</a>
</main>
</body>
</html>
"""


def generar() -> str:
    cuerpo = markdown.markdown(ORIGEN.read_text(encoding="utf-8"), extensions=["tables", "sane_lists"])
    return PLANTILLA.format(contenido=cuerpo)


if __name__ == "__main__":
    DESTINO.write_text(generar(), encoding="utf-8", newline="\n")
    print("generado", DESTINO, f"({DESTINO.stat().st_size} bytes)")
