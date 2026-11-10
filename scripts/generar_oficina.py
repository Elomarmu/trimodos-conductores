"""Genera public/oficina/index.html (app de oficina y almacén) a partir de public/index.html (app de conductores).

Las dos apps son el mismo código: la de oficina lleva data-app="oficina", su manifiesto (se instala aparte, con su
nombre) y su título. Ejecuta este script cada vez que cambies public/index.html (lo comprueba un test):
    python scripts/generar_oficina.py
"""
from __future__ import annotations

from pathlib import Path

PUBLIC = Path(__file__).resolve().parent.parent / "public"
CAMBIOS = [
    ('<html lang="es">', '<html lang="es" data-app="oficina">'),
    ('<link rel="manifest" href="/manifest.webmanifest">', '<link rel="manifest" href="/oficina/manifest.webmanifest">'),
    ('<meta name="apple-mobile-web-app-title" content="DECA Trimodos">', '<meta name="apple-mobile-web-app-title" content="Oficina Trimodos">'),
    ("<title>Modificación DECA - Trimodos</title>", "<title>Oficina y almacén - Trimodos</title>"),
    ('<meta property="og:title" content="Modificación DECA - Trimodos">', '<meta property="og:title" content="Oficina y almacén - Trimodos">'),
    ('<meta name="twitter:title" content="Modificación DECA - Trimodos">', '<meta name="twitter:title" content="Oficina y almacén - Trimodos">'),
    ('<h1 id="menu-titulo">Conductores</h1>', '<h1 id="menu-titulo">Oficina y almacén</h1>'),
    ('<a id="btn-guia" class="btn ghost" href="/guia.html">', '<a id="btn-guia" class="btn ghost" href="/oficina/guia.html">'),
    ("<p id=\"access-help\">Pídelo en la oficina. Solo hace falta la primera vez.</p>",
     "<p id=\"access-help\">Es el código de oficina y almacén (no el de los conductores). Solo hace falta la primera vez.</p>"),
]


def generar() -> str:
    html = (PUBLIC / "index.html").read_text(encoding="utf-8")
    for viejo, nuevo in CAMBIOS:
        if html.count(viejo) != 1:
            raise SystemExit(f"No encuentro en index.html: {viejo}")
        html = html.replace(viejo, nuevo)
    aviso = "<!-- GENERADO con scripts/generar_oficina.py a partir de public/index.html: no lo edites a mano. -->"
    return html.replace("<!doctype html>\n", "<!doctype html>\n" + aviso + "\n", 1)


if __name__ == "__main__":
    (PUBLIC / "oficina" / "index.html").write_text(generar(), encoding="utf-8")
    print("public/oficina/index.html generado")
