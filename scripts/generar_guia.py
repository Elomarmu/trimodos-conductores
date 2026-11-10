"""Genera las guías dentro de las apps a partir de sus .md:
- public/guia.html          ← GUIA_CONDUCTORES.md (app de conductores, «/»)
- public/oficina/guia.html  ← GUIA_OFICINA.md     (app de oficina y almacén, «/oficina/»)

La fuente es SIEMPRE el .md: edítalo y vuelve a ejecutar
    .\\.venv\\Scripts\\python.exe -X utf8 -m scripts.generar_guia
Un test comprueba que guia.html no se ha quedado desfasado respecto al .md. Requiere el paquete "markdown".
"""
from pathlib import Path

import markdown

RAIZ = Path(__file__).resolve().parent.parent
GUIAS = {  # nombre → (origen .md, destino .html, inicio de la app, manifiesto, título)
    "conductores": (RAIZ / "GUIA_CONDUCTORES.md", RAIZ / "public" / "guia.html", "/", "/manifest.webmanifest",
                    "Guía de uso - DECA Trimodos"),
    "oficina": (RAIZ / "GUIA_OFICINA.md", RAIZ / "public" / "oficina" / "guia.html", "/oficina/",
                "/oficina/manifest.webmanifest", "Guía de uso - Oficina y almacén Trimodos"),
}
ORIGEN, DESTINO = GUIAS["conductores"][:2]   # compatibilidad

PLANTILLA = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#000000">
<meta name="robots" content="noindex, nofollow">
<title>{titulo}</title>
<link rel="manifest" href="{manifiesto}">
<link rel="icon" type="image/png" href="/favicon.png">
<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">
<link rel="stylesheet" href="/styles.css">
</head>
<body data-screen="guia">
<!-- Archivo GENERADO por scripts/generar_guia.py desde {fuente}: no lo edites a mano. -->
<header class="brandbar"><a href="{inicio}"><img src="/img/trimodos.png" alt="Grupo Trimodos"></a></header>
<main class="screen guia">
<a class="btn primary" href="{inicio}">Volver a la app</a>
{contenido}
<a class="btn primary" href="{inicio}">Volver a la app</a>
</main>
</body>
</html>
"""


def generar(nombre: str = "conductores") -> str:
    origen, _, inicio, manifiesto, titulo = GUIAS[nombre]
    cuerpo = markdown.markdown(origen.read_text(encoding="utf-8"), extensions=["tables", "sane_lists"])
    return PLANTILLA.format(contenido=cuerpo, inicio=inicio, manifiesto=manifiesto, titulo=titulo, fuente=origen.name)


if __name__ == "__main__":
    for nombre, (_, destino, *_) in GUIAS.items():
        destino.write_text(generar(nombre), encoding="utf-8", newline="\n")
        print("generado", destino, f"({destino.stat().st_size} bytes)")
