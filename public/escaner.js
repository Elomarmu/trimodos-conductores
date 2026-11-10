"use strict";
// Escáner de documentos en el móvil (sin librerías): después de hacer la foto se encuadra el folio (cuatro esquinas que
// se pueden mover con el dedo), se endereza y se «escanea» (fondo blanco y texto más oscuro, sin perder el color de los
// sellos). Lo usan la foto del albarán y la del DI de residuos.
//   const foto = await escanearDocumento(file);   // {archivo_b64, tipo} · null si se cancela
// Los PDF no se tocan. Si algo falla, se usa la foto tal cual (nunca impide hacer el trabajo).

const ESC = { LADO_TRABAJO: 2000, LADO_DETECCION: 360, LADO_SALIDA: 1600, CALIDAD: 0.85, RADIO_TOQUE: 34 };

async function escanearDocumento(file) {
  if (!file || file.type === "application/pdf") return reducir(file);
  let fuente;
  try { fuente = await escCargar(file); } catch { return reducir(file); }
  let esquinas = escDetectar(fuente);
  for (;;) {
    const r = await escEncuadrar(fuente, esquinas);
    if (r.accion === "cancelar") return null;
    if (r.accion === "tal_cual") return reducir(file);
    esquinas = r.esquinas;
    let escaneo;
    try { escaneo = escMejorar(escEnderezar(fuente, esquinas)); } catch { return reducir(file); }
    const r2 = await escRevisar(escaneo);
    if (r2 === "cancelar") return null;
    if (r2 === "usar") {
      const blob = await new Promise((ok) => escaneo.toBlob(ok, "image/jpeg", ESC.CALIDAD));
      return { archivo_b64: await leerArchivo(blob), tipo: "image/jpeg" };
    }
    // «Volver a encuadrar»: se repite con las esquinas que había
  }
}

async function escCargar(file) {
  const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
  const k = Math.min(1, ESC.LADO_TRABAJO / Math.max(bmp.width, bmp.height));
  const c = document.createElement("canvas");
  c.width = Math.round(bmp.width * k); c.height = Math.round(bmp.height * k);
  c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
  return c;
}

// ---- 1. Detectar el folio: la zona clara más grande (el papel) y sus cuatro esquinas -------------------------------
function escDetectar(fuente) {
  const W = fuente.width, H = fuente.height;
  const k = Math.min(1, ESC.LADO_DETECCION / Math.max(W, H));
  const w = Math.max(8, Math.round(W * k)), h = Math.max(8, Math.round(H * k));
  const c = document.createElement("canvas"); c.width = w; c.height = h;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(fuente, 0, 0, w, h);
  const px = ctx.getImageData(0, 0, w, h).data;
  const gris = new Float32Array(w * h);
  for (let i = 0; i < w * h; i++) gris[i] = 0.299 * px[i * 4] + 0.587 * px[i * 4 + 1] + 0.114 * px[i * 4 + 2];
  const suave = escDesenfocar(gris, w, h, 2);
  const umbral = escOtsu(suave);
  const claro = new Uint8Array(w * h);
  for (let i = 0; i < w * h; i++) claro[i] = suave[i] > umbral ? 1 : 0;
  // zona clara conectada más grande
  const visto = new Uint8Array(w * h), cola = new Int32Array(w * h);
  let mejor = null, mejorN = 0;
  for (let s = 0; s < w * h; s++) {
    if (!claro[s] || visto[s]) continue;
    let ini = 0, fin = 0, n = 0;
    const zona = { tl: [0, 0, Infinity], br: [0, 0, -Infinity], tr: [0, 0, -Infinity], bl: [0, 0, Infinity] };
    cola[fin++] = s; visto[s] = 1;
    while (ini < fin) {
      const p = cola[ini++], x = p % w, y = (p / w) | 0;
      n++;
      if (x + y < zona.tl[2]) zona.tl = [x, y, x + y];
      if (x + y > zona.br[2]) zona.br = [x, y, x + y];
      if (x - y > zona.tr[2]) zona.tr = [x, y, x - y];
      if (x - y < zona.bl[2]) zona.bl = [x, y, x - y];
      for (const q of [x > 0 ? p - 1 : -1, x < w - 1 ? p + 1 : -1, y > 0 ? p - w : -1, y < h - 1 ? p + w : -1]) {
        if (q >= 0 && claro[q] && !visto[q]) { visto[q] = 1; cola[fin++] = q; }
      }
    }
    if (n > mejorN) { mejorN = n; mejor = zona; }
  }
  const margen = (f) => [[W * f, H * f], [W * (1 - f), H * f], [W * (1 - f), H * (1 - f)], [W * f, H * (1 - f)]];
  if (!mejor || mejorN < 0.15 * w * h) return margen(0.04);          // no se ve claro el folio: casi toda la foto
  const e = [mejor.tl, mejor.tr, mejor.br, mejor.bl].map(([x, y]) => [(x + 0.5) / k, (y + 0.5) / k]);
  return escArea(e) < 0.15 * W * H ? margen(0.04) : e;
}

function escOtsu(v) {
  const hist = new Float64Array(256);
  for (const x of v) hist[Math.min(255, Math.max(0, x | 0))]++;
  let total = v.length, suma = 0;
  for (let i = 0; i < 256; i++) suma += i * hist[i];
  let sB = 0, wB = 0, mejor = 0, umbral = 128;
  for (let t = 0; t < 256; t++) {
    wB += hist[t]; if (!wB) continue;
    const wF = total - wB; if (!wF) break;
    sB += t * hist[t];
    const mB = sB / wB, mF = (suma - sB) / wF, entre = wB * wF * (mB - mF) ** 2;
    if (entre > mejor) { mejor = entre; umbral = t; }
  }
  return umbral;
}

// desenfoque de caja (dos pasadas horizontal + vertical), r = radio en píxeles
function escDesenfocar(v, w, h, r) {
  const tmp = new Float32Array(w * h), out = new Float32Array(w * h);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let s = 0, n = 0;
      for (let d = -r; d <= r; d++) { const xx = x + d; if (xx >= 0 && xx < w) { s += v[y * w + xx]; n++; } }
      tmp[y * w + x] = s / n;
    }
  }
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let s = 0, n = 0;
      for (let d = -r; d <= r; d++) { const yy = y + d; if (yy >= 0 && yy < h) { s += tmp[yy * w + x]; n++; } }
      out[y * w + x] = s / n;
    }
  }
  return out;
}

function escArea(e) {
  let a = 0;
  for (let i = 0; i < 4; i++) { const [x1, y1] = e[i], [x2, y2] = e[(i + 1) % 4]; a += x1 * y2 - x2 * y1; }
  return Math.abs(a) / 2;
}

// ---- 2. Pantalla de encuadre: las esquinas se mueven con el dedo -------------------------------------------------
function escEncuadrar(fuente, inicial) {
  return new Promise((resolver) => {
    const esquinas = inicial.map((p) => p.slice());
    const cv = $("esc-canvas"), caja = cv.parentElement;
    escPreparar("Encuadra el folio", "Mueve las esquinas amarillas hasta las del folio y pulsa Aceptar.",
      [["Aceptar", "primary", () => fin({ accion: "ok", esquinas })],
       ["Usar la foto tal cual", "secondary", () => fin({ accion: "tal_cual" })],
       ["Cancelar", "ghost", () => fin({ accion: "cancelar" })]]);
    let k = 1, arrastrando = -1;
    const pintar = () => {
      const dpr = window.devicePixelRatio || 1;
      const anchoMax = caja.clientWidth, altoMax = Math.max(220, window.innerHeight * 0.58);
      k = Math.min(anchoMax / fuente.width, altoMax / fuente.height);
      cv.style.width = `${Math.round(fuente.width * k)}px`; cv.style.height = `${Math.round(fuente.height * k)}px`;
      cv.width = Math.round(fuente.width * k * dpr); cv.height = Math.round(fuente.height * k * dpr);
      const ctx = cv.getContext("2d");
      ctx.setTransform(dpr * k, 0, 0, dpr * k, 0, 0);
      ctx.drawImage(fuente, 0, 0);
      ctx.fillStyle = "rgba(0,0,0,.45)";                    // fuera del folio, más oscuro
      ctx.beginPath(); ctx.rect(0, 0, fuente.width, fuente.height);
      esquinas.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.closePath();
      ctx.fill("evenodd");
      ctx.strokeStyle = "#fcda00"; ctx.lineWidth = 3 / k;
      ctx.beginPath(); esquinas.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.closePath(); ctx.stroke();
      for (const [x, y] of esquinas) {
        ctx.beginPath(); ctx.arc(x, y, 13 / k, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(252,218,0,.9)"; ctx.fill(); ctx.lineWidth = 2 / k; ctx.strokeStyle = "#000"; ctx.stroke();
      }
    };
    const punto = (ev) => { const r = cv.getBoundingClientRect(); return [(ev.clientX - r.left) / k, (ev.clientY - r.top) / k]; };
    const abajo = (ev) => {
      const [x, y] = punto(ev);
      let i = -1, d = ESC.RADIO_TOQUE / k;
      esquinas.forEach(([ex, ey], j) => { const dd = Math.hypot(ex - x, ey - y); if (dd < d) { d = dd; i = j; } });
      if (i < 0) return;
      arrastrando = i; cv.setPointerCapture(ev.pointerId); ev.preventDefault();
    };
    const mover = (ev) => {
      if (arrastrando < 0) return;
      const [x, y] = punto(ev);
      esquinas[arrastrando] = [Math.min(fuente.width, Math.max(0, x)), Math.min(fuente.height, Math.max(0, y))];
      pintar(); ev.preventDefault();
    };
    const arriba = () => { arrastrando = -1; };
    cv.addEventListener("pointerdown", abajo); cv.addEventListener("pointermove", mover);
    cv.addEventListener("pointerup", arriba); cv.addEventListener("pointercancel", arriba);
    window.addEventListener("resize", pintar);
    const fin = (r) => {
      cv.removeEventListener("pointerdown", abajo); cv.removeEventListener("pointermove", mover);
      cv.removeEventListener("pointerup", arriba); cv.removeEventListener("pointercancel", arriba);
      window.removeEventListener("resize", pintar);
      if (r.accion === "ok") r.esquinas = escOrdenar(esquinas);
      else $("escaner").hidden = true;
      resolver(r);
    };
    $("escaner").hidden = false;
    requestAnimationFrame(pintar);
  });
}

// la pantalla de después: el escaneo para ver si se lee bien
function escRevisar(escaneo) {
  return new Promise((resolver) => {
    const cv = $("esc-canvas"), caja = cv.parentElement;
    const fin = (r) => { if (r !== "volver") $("escaner").hidden = true; resolver(r); };
    escPreparar("¿Se lee bien?", "Así se guardará el documento.",
      [["Usar este escaneo", "primary", () => fin("usar")],
       ["Volver a encuadrar", "secondary", () => fin("volver")],
       ["Cancelar", "ghost", () => fin("cancelar")]]);
    const k = Math.min(caja.clientWidth / escaneo.width, Math.max(220, window.innerHeight * 0.58) / escaneo.height);
    const dpr = window.devicePixelRatio || 1;
    cv.style.width = `${Math.round(escaneo.width * k)}px`; cv.style.height = `${Math.round(escaneo.height * k)}px`;
    cv.width = Math.round(escaneo.width * k * dpr); cv.height = Math.round(escaneo.height * k * dpr);
    cv.getContext("2d").drawImage(escaneo, 0, 0, cv.width, cv.height);
  });
}

function escPreparar(titulo, ayuda, botones) {
  $("esc-q").textContent = titulo;
  $("esc-hint").textContent = ayuda;
  $("esc-botones").replaceChildren(...botones.map(([texto, clase, accion]) =>
    h("button", { type: "button", class: `btn ${clase}`, onclick: accion }, texto)));
}

// esquinas en orden: arriba-izquierda, arriba-derecha, abajo-derecha, abajo-izquierda (aunque se hayan cruzado)
function escOrdenar(e) {
  const cx = e.reduce((a, p) => a + p[0], 0) / 4, cy = e.reduce((a, p) => a + p[1], 0) / 4;
  const o = e.slice().sort((a, b) => Math.atan2(a[1] - cy, a[0] - cx) - Math.atan2(b[1] - cy, b[0] - cx));
  const i = o.reduce((m, p, j) => (p[0] + p[1] < o[m][0] + o[m][1] ? j : m), 0);
  return [...o.slice(i), ...o.slice(0, i)];
}

// ---- 3. Enderezar: transformación de perspectiva del cuadrilátero a un rectángulo -------------------------------
function escEnderezar(fuente, [tl, tr, br, bl]) {
  const d = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
  let W = Math.max(d(tl, tr), d(bl, br)), H = Math.max(d(tl, bl), d(tr, br));
  const k = Math.min(1, ESC.LADO_SALIDA / Math.max(W, H));
  W = Math.max(50, Math.round(W * k)); H = Math.max(50, Math.round(H * k));
  const M = escHomografia([[0, 0], [W, 0], [W, H], [0, H]], [tl, tr, br, bl]);   // destino → foto
  const sw = fuente.width, sh = fuente.height;
  const src = fuente.getContext("2d", { willReadFrequently: true }).getImageData(0, 0, sw, sh).data;
  const out = document.createElement("canvas"); out.width = W; out.height = H;
  const octx = out.getContext("2d"), img = octx.createImageData(W, H), o = img.data;
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const z = M[6] * x + M[7] * y + 1;
      let sx = (M[0] * x + M[1] * y + M[2]) / z, sy = (M[3] * x + M[4] * y + M[5]) / z;
      sx = Math.min(sw - 1.001, Math.max(0, sx)); sy = Math.min(sh - 1.001, Math.max(0, sy));
      const x0 = sx | 0, y0 = sy | 0, fx = sx - x0, fy = sy - y0;
      const a = (y0 * sw + x0) * 4, b = a + 4, c = a + sw * 4, e = c + 4, t = (y * W + x) * 4;
      for (let ch = 0; ch < 3; ch++) {
        o[t + ch] = (src[a + ch] * (1 - fx) + src[b + ch] * fx) * (1 - fy) + (src[c + ch] * (1 - fx) + src[e + ch] * fx) * fy;
      }
      o[t + 3] = 255;
    }
  }
  octx.putImageData(img, 0, 0);
  return out;
}

// homografía que lleva los 4 puntos «de» a los 4 puntos «a» (sistema de 8 ecuaciones)
function escHomografia(de, a) {
  const A = [], B = [];
  for (let i = 0; i < 4; i++) {
    const [x, y] = de[i], [u, v] = a[i];
    A.push([x, y, 1, 0, 0, 0, -u * x, -u * y]); B.push(u);
    A.push([0, 0, 0, x, y, 1, -v * x, -v * y]); B.push(v);
  }
  for (let c = 0; c < 8; c++) {                     // Gauss con pivote
    let p = c;
    for (let r = c + 1; r < 8; r++) if (Math.abs(A[r][c]) > Math.abs(A[p][c])) p = r;
    [A[c], A[p]] = [A[p], A[c]]; [B[c], B[p]] = [B[p], B[c]];
    for (let r = c + 1; r < 8; r++) {
      const f = A[r][c] / A[c][c];
      for (let k = c; k < 8; k++) A[r][k] -= f * A[c][k];
      B[r] -= f * B[c];
    }
  }
  const s = new Array(8).fill(0);
  for (let r = 7; r >= 0; r--) {
    let t = B[r];
    for (let k = r + 1; k < 8; k++) t -= A[r][k] * s[k];
    s[r] = t / A[r][r];
  }
  return s;
}

// ---- 4. «Escanear»: se quita la sombra (se divide por el fondo) y se sube el contraste ----------------------------
function escMejorar(cv) {
  const W = cv.width, H = cv.height, ctx = cv.getContext("2d", { willReadFrequently: true });
  const img = ctx.getImageData(0, 0, W, H), p = img.data;
  // fondo (la iluminación del papel): imagen pequeña muy desenfocada
  const f = Math.max(1, Math.round(Math.max(W, H) / 120)), w = Math.ceil(W / f), h = Math.ceil(H / f);
  const peq = new Float32Array(w * h);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      // el fondo es lo más claro de cada bloque (el texto oscuro no cuenta)
      let m = 0;
      for (let yy = y * f; yy < Math.min(H, (y + 1) * f); yy += 2) {
        for (let xx = x * f; xx < Math.min(W, (x + 1) * f); xx += 2) {
          const i = (yy * W + xx) * 4, l = 0.299 * p[i] + 0.587 * p[i + 1] + 0.114 * p[i + 2];
          if (l > m) m = l;
        }
      }
      peq[y * w + x] = m;
    }
  }
  const fondo = escDesenfocar(peq, w, h, 3);
  for (let y = 0; y < H; y++) {
    const fy = Math.min(h - 1.001, Math.max(0, y / f - 0.5)), y0 = fy | 0, ty = fy - y0;
    for (let x = 0; x < W; x++) {
      const fx = Math.min(w - 1.001, Math.max(0, x / f - 0.5)), x0 = fx | 0, tx = fx - x0;
      const g = (fondo[y0 * w + x0] * (1 - tx) + fondo[y0 * w + x0 + 1] * tx) * (1 - ty)
              + (fondo[(y0 + 1) * w + x0] * (1 - tx) + fondo[(y0 + 1) * w + x0 + 1] * tx) * ty;
      const div = 255 / Math.max(40, g), i = (y * W + x) * 4;
      for (let ch = 0; ch < 3; ch++) {
        let t = Math.min(1, (p[i + ch] * div) / 255);          // papel → blanco
        t = t >= 0.92 ? 1 : Math.pow(t / 0.92, 1.7);              // el texto, más oscuro y nítido
        p[i + ch] = t * 255;
      }
    }
  }
  ctx.putImageData(img, 0, 0);
  return cv;
}
