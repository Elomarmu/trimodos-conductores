"""Prueba de extremo a extremo en un navegador real (Edge/Chrome) con una cámara simulada que muestra un QR.

Uso (con el entorno de desarrollo instalado: pip install -r requirements-dev.txt):
    .\\.venv\\Scripts\\python.exe -X utf8 -m scripts.prueba_navegador

Arranca su propio servidor en modo SIMULADO (puerto 8765): no toca Odoo.
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import qrcode
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8765
BASE = f"http://localhost:{PORT}"
QR_TEXT = "https://www.stinsa.com/web/content/900002?access_token=11111111-aaaa-bbbb-cccc-000000000001&download=true"
SALIDA = Path(os.environ.get("PRUEBA_CAPTURAS", tempfile.gettempdir()))


def crear_video_qr(destino: Path, texto: str) -> None:
    """Vídeo y4m de 640x480 con el QR centrado (formato que Chromium acepta como cámara falsa)."""
    w, h = 640, 480
    qr = qrcode.make(texto, box_size=8, border=4).convert("L")
    lienzo = Image.new("L", (w, h), 255)
    lienzo.paste(qr.resize((400, 400)), ((w - 400) // 2, (h - 400) // 2))
    y = lienzo.tobytes()
    uv = bytes([128]) * (w * h // 4)
    with open(destino, "wb") as f:
        f.write(f"YUV4MPEG2 W{w} H{h} F30:1 Ip A1:1 C420jpeg\n".encode())
        for _ in range(60):
            f.write(b"FRAME\n" + y + uv + uv)


def esperar_servidor():
    for _ in range(40):
        try:
            if httpx.get(f"{BASE}/api/health", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.25)
    raise RuntimeError("El servidor no arranca")


def comprobar(cond, texto):
    print(("  OK   " if cond else "  FALLA ") + texto)
    if not cond:
        comprobar.fallos += 1


comprobar.fallos = 0


def main():
    video = Path(tempfile.gettempdir()) / "qr_camara.y4m"
    crear_video_qr(video, QR_TEXT)
    env = {**os.environ, "ODOO_MODE": "mock", "DRY_RUN": "false"}
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT)], cwd=ROOT, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        esperar_servidor()
        with sync_playwright() as p:
            navegador = p.chromium.launch(channel="msedge", headless=True, args=[
                "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", f"--use-file-for-fake-video-capture={video}"])
            ctx = navegador.new_context(viewport={"width": 390, "height": 844}, permissions=["camera"], accept_downloads=True)
            pg = ctx.new_page()
            errores = []
            pg.on("pageerror", lambda e: errores.append(str(e)))
            pg.on("console", lambda m: errores.append(m.text) if m.type == "error" else None)

            print("Escáner con cámara simulada")
            pg.goto(BASE)
            pg.wait_for_selector("#modo:not([hidden])")
            pg.click("#btn-scan")
            pg.wait_for_selector("#s-edit:not([hidden])", timeout=20000)
            comprobar("S2621473" in pg.inner_text("#edit-title"), "el QR se lee con la cámara y abre el pedido S2621473")
            comprobar(pg.is_hidden("#edit-banners .banner.info"), "QR verificado: sin aviso de «sin QR»")
            pg.screenshot(path=str(SALIDA / "p4_edicion.png"))

            print("Botón Atrás")
            pg.go_back()
            pg.wait_for_selector("#s-scan:not([hidden])", timeout=5000)
            comprobar(True, "Atrás desde edición vuelve al escáner")
            pg.go_back()
            pg.wait_for_selector("#s-menu:not([hidden])", timeout=5000)
            comprobar(True, "Atrás desde el escáner vuelve al menú")

            print("Flujo completo")
            pg.click("#btn-scan")
            pg.wait_for_selector("#s-edit:not([hidden])", timeout=20000)
            pg.fill("#elementos-287102", "20")
            pg.click("#btn-review")
            pg.wait_for_selector("#s-review:not([hidden])")
            pg.click("#btn-save")
            comprobar(pg.is_visible("#confirm"), "aparece el segundo aviso «¿Seguro?»")
            pg.click("#btn-no")
            comprobar(pg.is_hidden("#confirm") and pg.is_visible("#btn-save"), "«No, volver» no guarda y deja en el resumen")
            pg.click("#btn-save"); pg.click("#btn-yes")
            pg.wait_for_selector("#done-actions:not([hidden])")
            with pg.expect_download() as d:
                pg.click("#btn-download")
            comprobar(open(d.value.path(), "rb").read(5) == b"%PDF-", "se descarga un PDF válido")
            pg.screenshot(path=str(SALIDA / "p4_resultado.png"))
            pg.click("#s-review [data-go=menu]")
            comprobar(pg.is_visible("#s-menu"), "«Volver al menú» limpia y vuelve al inicio")

            print("PWA")
            pg.goto(BASE)
            pg.wait_for_selector("#modo:not([hidden])")
            m = httpx.get(f"{BASE}/manifest.webmanifest")
            comprobar(m.status_code == 200 and "manifest+json" in m.headers["content-type"], "manifest servido con su tipo correcto")
            datos = m.json()
            comprobar(datos["display"] == "standalone" and datos["lang"] == "es", "manifest: standalone y en español")
            for icono in datos["icons"]:
                comprobar(httpx.get(BASE + icono["src"]).status_code == 200, f"icono {icono['src']}")
            comprobar(httpx.get(f"{BASE}/api/health").headers.get("cache-control") == "no-store", "la API no se guarda en caché")
            comprobar(httpx.get(f"{BASE}/sw.js").headers.get("cache-control") == "no-cache", "sw.js siempre se revalida")
            pg.wait_for_function("navigator.serviceWorker.ready.then(() => true)")
            pg.reload()
            pg.wait_for_function("!!navigator.serviceWorker.controller")
            comprobar(True, "service worker instalado y controlando la página")
            ctx.set_offline(True)
            pg.reload()
            pg.wait_for_selector("#btn-scan")
            comprobar(pg.is_visible("#btn-scan"), "sin conexión la app abre igualmente")
            pg.click("#btn-scan"); pg.fill("#order", "S2621473"); pg.click("[data-company=transargi]"); pg.click("#btn-order")
            pg.wait_for_selector("#err:not([hidden])")
            comprobar("No hay conexión" in pg.inner_text("#err"), "sin conexión avisa con un mensaje claro: «" + pg.inner_text("#err") + "»")
            ctx.set_offline(False)
            pg.goto(BASE)

            # Se ignoran: fallos de red del paso "sin conexión" (esperados) y el aviso benigno de la librería del escáner al apagar la cámara.
            ruido = ("ERR_INTERNET_DISCONNECTED", "play() request was interrupted")
            reales = [e for e in errores if not any(r in e for r in ruido)]
            comprobar(not reales, f"sin errores de JavaScript {reales or ''}")
            navegador.close()
    finally:
        srv.terminate()
    print("\nTODO CORRECTO" if not comprobar.fallos else f"\n{comprobar.fallos} COMPROBACIONES FALLAN")
    sys.exit(1 if comprobar.fallos else 0)


if __name__ == "__main__":
    main()
