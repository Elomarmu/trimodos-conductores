"use strict";
// Lector de códigos de barras con la cámara (palets de Stock Burriana). Lee varios palets seguidos sin cerrar la cámara.
//   abrirLectorBarras({ titulo, yaEsta(codigo) → bool, alLeer(codigo) → Promise<{ok, texto}> })
// Usa el lector del propio móvil (BarcodeDetector) si lo tiene; si no (iPhone, ordenadores), ZXing (/vendor/zxing.min.js,
// se carga solo la primera vez). Si la cámara no se puede abrir, el código se sigue pudiendo escribir o elegir de la lista.

const BAR = { INTERVALO_MS: 200, FORMATOS: ["code_128", "code_39", "code_93", "ean_13", "ean_8", "itf", "codabar", "upc_a", "upc_e"] };

// Igual que sscc() del servidor: sin letras, solo cifras (un SSCC de GS1-128 llega como «00» + 18 cifras); con letras
// (p. ej. «LP26-0236823»), en mayúsculas y sin guiones ni espacios. Una dirección web (el QR de la etiqueta ambiental) no es un código.
function barNormalizar(texto) {
  let t = String(texto || "").trim();
  if (/^(https?:\/\/|www\.)/i.test(t)) return "";
  if (/[A-Za-z]/.test(t)) return t.toUpperCase().replace(/[^A-Z0-9]/g, "");
  t = t.replace(/\D/g, "");
  if (t.length >= 20 && t.startsWith("00")) t = t.slice(2, 20);
  return t;
}

let barZXingPromesa = null;
function barCargarZXing() {
  if (window.ZXing) return Promise.resolve();
  if (!barZXingPromesa) {
    barZXingPromesa = new Promise((ok, mal) => {
      const s = document.createElement("script");
      s.src = "/vendor/zxing.min.js";
      s.onload = () => ok();
      s.onerror = () => { barZXingPromesa = null; mal(new Error("No se ha podido cargar el lector de códigos de barras (¿sin cobertura?).")); };
      document.head.appendChild(s);
    });
  }
  return barZXingPromesa;
}

// Devuelve una función async (video) → texto del código o null
async function barMotor() {
  if ("BarcodeDetector" in window) {
    try {
      const soportados = await window.BarcodeDetector.getSupportedFormats();
      const formatos = BAR.FORMATOS.filter((f) => soportados.includes(f));
      if (formatos.length) {
        const d = new window.BarcodeDetector({ formats: formatos });
        return async (video) => { const r = await d.detect(video); return r.length ? r[0].rawValue : null; };
      }
    } catch { /* se usa ZXing */ }
  }
  await barCargarZXing();
  return barMotorZXing();
}

function barMotorZXing() {
  const Z = window.ZXing;
  const hints = new Map();
  hints.set(Z.DecodeHintType.POSSIBLE_FORMATS, [Z.BarcodeFormat.CODE_128, Z.BarcodeFormat.CODE_39, Z.BarcodeFormat.CODE_93, Z.BarcodeFormat.EAN_13,
    Z.BarcodeFormat.EAN_8, Z.BarcodeFormat.ITF, Z.BarcodeFormat.CODABAR, Z.BarcodeFormat.UPC_A, Z.BarcodeFormat.UPC_E]);
  hints.set(Z.DecodeHintType.TRY_HARDER, true);
  const lector = new Z.MultiFormatReader();
  lector.setHints(hints);
  const lienzo = document.createElement("canvas");
  const ctx = lienzo.getContext("2d", { willReadFrequently: true });
  return async (video) => {
    const w = video.videoWidth, hh = video.videoHeight;
    if (!w || !hh) return null;
    const k = Math.min(1, 1280 / Math.max(w, hh));
    lienzo.width = Math.round(w * k); lienzo.height = Math.round(hh * k);
    ctx.drawImage(video, 0, 0, lienzo.width, lienzo.height);
    try {
      const mapa = new Z.BinaryBitmap(new Z.HybridBinarizer(new Z.HTMLCanvasElementLuminanceSource(lienzo)));
      return lector.decodeWithState(mapa).getText();
    } catch { return null; }   // no hay código en este fotograma
  };
}

function barPitido(bien) {
  try { if (navigator.vibrate) navigator.vibrate(bien ? 80 : [120, 80, 120]); } catch { /* sin vibración */ }
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    const ac = new Ctx(), o = ac.createOscillator(), g = ac.createGain();
    o.frequency.value = bien ? 1100 : 320; g.gain.value = 0.15;
    o.connect(g); g.connect(ac.destination); o.start(); o.stop(ac.currentTime + (bien ? 0.12 : 0.3));
    setTimeout(() => ac.close(), 500);
  } catch { /* sin sonido */ }
}

async function abrirLectorBarras({ titulo, yaEsta, alLeer }) {
  const caja = document.getElementById("barras"), video = document.getElementById("bar-video");
  const ultimo = document.getElementById("bar-ultimo"), contador = document.getElementById("bar-contador");
  document.getElementById("bar-titulo").textContent = titulo || "Escanear códigos de barras";
  ultimo.replaceChildren(); contador.textContent = "";
  let flujo = null, activo = true, leidos = 0;
  const cerrar = () => {
    activo = false;
    if (flujo) flujo.getTracks().forEach((t) => t.stop());
    video.srcObject = null; caja.hidden = true;
    document.getElementById("bar-cerrar").onclick = null;
  };
  let motor;
  try {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) throw new Error("Este navegador no deja usar la cámara aquí.");
    flujo = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 } }, audio: false });
    motor = await barMotor();
  } catch (e) {
    if (flujo) flujo.getTracks().forEach((t) => t.stop());
    showError(e && e.name === "NotAllowedError" ? "No hay permiso para la cámara. Actívalo en los ajustes del navegador o escribe el código a mano." : (e.message || "No se puede abrir la cámara."));
    return;
  }
  document.getElementById("bar-cerrar").onclick = cerrar;
  video.srcObject = flujo; caja.hidden = false;
  try { await video.play(); } catch { /* el atributo autoplay lo arranca */ }
  const enviados = new Set();
  let ocupado = false;
  const mensaje = (clase, texto) => ultimo.replaceChildren(h("div", { class: "banner " + clase }, texto));
  const vuelta = async () => {
    if (!activo) return;
    if (!ocupado && video.readyState >= 2) {
      let codigo = "";
      try {
        const crudo = await motor(video);
        codigo = crudo ? barNormalizar(crudo) : "";
        if (codigo.length >= 8 && !enviados.has(codigo)) {
          enviados.add(codigo);
          if (yaEsta && yaEsta(codigo)) { barPitido(false); mensaje("warn", `⚠ El palet ${codigo} ya está apuntado.`); }
          else {
            ocupado = true;
            const r = await alLeer(codigo);
            ocupado = false;
            if (r.ok) leidos++;
            barPitido(r.ok);
            mensaje(r.ok ? "info" : "warn", r.texto);
            contador.textContent = `${leidos} palet${leidos === 1 ? "" : "s"} leído${leidos === 1 ? "" : "s"} bien en esta sesión.`;
          }
        }
      } catch (e) { ocupado = false; if (codigo) enviados.delete(codigo); mensaje("warn", "⚠ " + (e.message || "No se ha podido apuntar el palet.")); }
    }
    if (activo) setTimeout(vuelta, BAR.INTERVALO_MS);
  };
  vuelta();
}
