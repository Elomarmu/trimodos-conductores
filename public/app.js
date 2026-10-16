"use strict";
// App de conductores: 4 pantallas (menú, escáner, edición, resumen/resultado). Sin dependencias.

const $ = (id) => document.getElementById(id);
const MAX_KG_WARNING = 44000;
const PENDING = { pending: true };   // ha escrito en Mercancía pero aún no ha elegido de la lista
const state = { data: null, edits: {}, lastReq: null, company: null, pdfUrl: null, pdfBlob: null, health: {} };

// ---------- utilidades ----------
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null) el.append(c.nodeType ? c : document.createTextNode(c));
  return el;
}
const group = (s) => s.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
function fmtNum(n) {
  const [int, dec] = String(Math.round(n * 100) / 100).split(".");
  return group(int) + (dec ? "," + dec : "");
}
function parseNum(text) {
  const t = String(text).trim().replace(/\s/g, "").replace(/\./g, "").replace(",", ".");
  return t === "" || !/^\d*\.?\d+$/.test(t) ? NaN : Number(t);
}

function showError(msg) {
  const e = $("err");
  e.textContent = msg;
  e.hidden = false;
  window.scrollTo({ top: 0 });
}
function clearError() { $("err").hidden = true; }
function busy(msg) { $("busy-msg").textContent = msg || "Un momento…"; $("busy").hidden = !msg; }

// Código de acceso compartido: se pide una vez y se recuerda en este móvil.
const CODE_KEY = "codigo-acceso";
function getCode() { try { return localStorage.getItem(CODE_KEY) || ""; } catch { return ""; } }
function saveCode(v) { try { localStorage.setItem(CODE_KEY, v); } catch { /* sin almacenamiento: lo pedirá cada vez */ } }
const authHeaders = (code = getCode()) => ({ "X-Codigo-Acceso": code });

async function api(path, options = {}) {
  let res;
  try {
    res = await fetch(path, { ...options, headers: { ...(options.headers || {}), ...authHeaders() } });
  } catch {
    throw new Error("No hay conexión. Inténtalo de nuevo.");
  }
  let body = null;
  try { body = await res.json(); } catch { /* PDF u otra cosa */ }
  if (!res.ok) {
    const e = new Error((body && body.error) || "Algo ha fallado. Inténtalo de nuevo.");
    e.code = body && body.codigo;
    if (e.code === "codigo_invalido") askCode("El código de acceso ya no es válido. Pídelo en la oficina.");
    throw e;
  }
  return body;
}

function askCode(help) {
  if (help) $("access-help").textContent = help;
  $("access-err").hidden = true;
  $("access-code").value = "";
  $("access").hidden = false;
  $("access-code").focus();
}
$("access-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const code = $("access-code").value.trim();
  if (!code) return;
  $("access-err").hidden = true;
  try {
    const res = await fetch("/api/login", { method: "POST", headers: authHeaders(code) });
    if (!res.ok) {
      const b = await res.json().catch(() => ({}));
      throw new Error(b.error || "Algo ha fallado. Inténtalo de nuevo.");
    }
    saveCode(code);
    $("access").hidden = true;
    $("btn-scan").disabled = false;
  } catch (e) {
    $("access-err").textContent = e instanceof TypeError ? "No hay conexión. Inténtalo de nuevo." : e.message;
    $("access-err").hidden = false;
  }
});
const post = (path, data) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

// ---------- navegación ----------
// Cada pantalla es una entrada del historial: el botón Atrás de Android vuelve a la pantalla anterior
// en vez de cerrar la app.
let currentScreen = "menu";
function show(name) {
  clearError();
  currentScreen = name;
  for (const s of document.querySelectorAll(".screen")) s.hidden = s.id !== "s-" + name;
  if (name !== "scan") stopScan();
  if (name === "scan") startScan();
  window.scrollTo({ top: 0 });
}
function go(name) {
  if (name === currentScreen) return show(name);
  if (name === "menu") {                      // volver al menú: se limpia la orden y el historial
    state.data = null; state.edits = {}; pending = null;
    history.replaceState({ s: "menu" }, "");
    return show("menu");
  }
  history.pushState({ s: name }, "");
  show(name);
}
window.addEventListener("popstate", (e) => {
  let target = (e.state && e.state.s) || "menu";
  if (!state.data && (target === "edit" || target === "review")) target = "menu";
  if (target === "review" && !pending) target = "edit";
  if (!$("confirm").hidden) $("confirm").hidden = true;
  show(target);
});
history.replaceState({ s: "menu" }, "");
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-go]");
  if (b) go(b.dataset.go);
});

// ---------- pantalla 2: escáner ----------
let stream = null, scanTimer = null, html5 = null, html5Ready = null, scanGen = 0;

async function startScan() {
  const gen = ++scanGen;
  $("cam-msg").textContent = "Apunta al código QR de la orden.";
  if (!window.isSecureContext || !navigator.mediaDevices) {
    $("cam-msg").textContent = "La cámara necesita una conexión segura (https). Escribe el número de pedido.";
    return;
  }
  try {
    if ("BarcodeDetector" in window) {
      const detector = new BarcodeDetector({ formats: ["qr_code"] });
      const s = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false });
      if (gen !== scanGen) { s.getTracks().forEach((t) => t.stop()); return; } // ya se salió de la pantalla
      stream = s;
      const v = $("video");
      v.srcObject = s;
      await v.play();
      scanTimer = setInterval(async () => {
        try {
          const codes = await detector.detect(v);
          if (codes.length) onQr(codes[0].rawValue);
        } catch { /* fotograma no válido: se ignora */ }
      }, 250);
    } else {
      await loadScript("/vendor/html5-qrcode.min.js");
      if (gen !== scanGen) return;
      const c = new Html5Qrcode("reader");
      html5 = c;
      html5Ready = c.start({ facingMode: "environment" }, { fps: 10, qrbox: (w, h) => { const s = Math.floor(Math.min(w, h) * 0.85); return { width: s, height: s }; } }, (text) => onQr(text), () => {});
      await html5Ready;
    }
  } catch {
    if (gen === scanGen) $("cam-msg").textContent = "No se puede usar la cámara. Revisa el permiso o escribe el número de pedido.";
  }
}
function loadScript(src) {
  return new Promise((ok, fail) => {
    if (window.Html5Qrcode) return ok();
    document.head.append(h("script", { src, onload: ok, onerror: fail }));
  });
}
function stopScan() {
  scanGen++;
  clearInterval(scanTimer); scanTimer = null;
  if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
  $("video").srcObject = null;
  if (html5) {
    const c = html5, ready = html5Ready;
    html5 = null; html5Ready = null;
    // esperar a que termine de arrancar antes de pararla; cualquier fallo aquí es irrelevante
    Promise.resolve(ready).then(() => c.stop()).then(() => c.clear()).catch(() => {});
  }
}let scanning = false;
function onQr(text) {
  if (scanning) return;
  scanning = true;
  stopScan();
  load({ qr_text: text }).finally(() => { scanning = false; });
}

// entrada manual
for (const b of document.querySelectorAll(".seg-btn")) {
  b.addEventListener("click", () => {
    state.company = b.dataset.company;
    for (const x of document.querySelectorAll(".seg-btn")) x.setAttribute("aria-checked", String(x === b));
  });
}
$("btn-order").addEventListener("click", () => {
  clearError();
  if (!state.company) return showError("Elige la empresa: TRANSARGI o STINSA.");
  const n = $("order").value.trim();
  if (!n) return showError("Escribe el número de pedido. Ejemplo: S2621473");
  stopScan();
  load({ order_name: n, company: state.company });
});
$("btn-test-qr").addEventListener("click", () => { stopScan(); load({ qr_text: state.health.qr_prueba }); });

async function load(req) {
  clearError();
  busy("Buscando la orden…");
  try {
    state.data = await post("/api/resolve-qr", req);
    state.lastReq = req;
    state.edits = {};
    renderEdit();
    go("edit");
  } catch (e) {
    go("scan");
    showError(e.message);
  } finally {
    busy(null);
  }
}

// ---------- pantalla 3: edición ----------
const FIELDS = [
  { key: "mercancia", label: "Mercancía" },
  { key: "elementos", label: "Elementos (bultos)" },
  { key: "euro_palets", label: "Euro palets" },
  { key: "peso", label: "Peso (kg)" },
];

function current(p) {
  const e = state.edits[p.id] || {};
  return {
    goods: "goods" in e ? e.goods : p.mercancia, // null = en blanco; PENDING = ha escrito sin elegir de la lista
    elementos: e.elementos ?? p.elementos,
    euro_palets: e.euro_palets ?? p.euro_palets,
    peso: e.peso ?? p.peso,
  };
}
function setEdit(p, key, value) { (state.edits[p.id] ||= {})[key] = value; }

function renderEdit() {
  const d = state.data;
  $("edit-title").textContent = "Pedido " + (d.tarea.pedido || d.tarea.nombre);
  const banners = $("edit-banners");
  banners.replaceChildren();
  if (d.version_antigua) banners.append(h("div", { class: "banner warn" }, "Esta orden tiene una versión más nueva. Estos son los datos actuales."));
  if (!d.editable) banners.append(h("div", { class: "banner bad" }, d.motivo_no_editable || "Esta orden no se puede modificar."));
  else if (!d.verificado_por_qr) banners.append(h("div", { class: "banner info" }, "Orden abierta por número de pedido (sin QR)."));
  $("btn-review").hidden = !d.editable;

  const box = $("packages");
  box.replaceChildren();
  d.bultos.forEach((p, i) => box.append(packageCard(p, i)));
}

function packageCard(p, index) {
  const ro = !state.data.editable;
  const route = [p.origen?.name, p.destino?.name].filter(Boolean).join(" → ");
  const card = h("section", { class: "pkg" },
    h("h2", {}, `Lugar ${index + 1} · ${p.name}`),
    h("div", { class: "route" }, route),
  );

  // mercancía con sugerencias
  const goodsInput = h("input", { type: "text", id: `g-${p.id}`, value: current(p).goods?.name || "", autocomplete: "off", disabled: ro });
  const sugg = h("div", { class: "sugg" });
  let timer = null;
  goodsInput.addEventListener("input", () => {
    clearTimeout(timer);
    // texto vacío = mercancía en blanco (vale); con texto, no vale hasta que elija de la lista
    setEdit(p, "goods", goodsInput.value.trim() === "" ? null : PENDING);
    markChanged();
    timer = setTimeout(async () => {
      const q = goodsInput.value.trim();
      if (q.length < 2) return sugg.replaceChildren();
      try {
        const r = await api("/api/goods?q=" + encodeURIComponent(q));
        sugg.replaceChildren(...r.resultados.map((g) => h("button", { type: "button", class: "btn", onclick: () => {
          setEdit(p, "goods", { id: g.id, name: g.name });
          goodsInput.value = g.name;
          sugg.replaceChildren();
          markChanged();
        } }, g.name)));
        if (!r.resultados.length) sugg.replaceChildren(h("p", { class: "hint" }, "No hay mercancías con ese nombre."));
      } catch (e) { showError(e.message); }
    }, 300);
  });
  const goodsField = h("div", { class: "field", "data-key": "mercancia" }, h("label", { for: `g-${p.id}` }, "Mercancía"), goodsInput, sugg, h("div", { class: "was" }, "Ahora en Odoo: " + (p.mercancia?.name || "(en blanco)")));

  const numField = (key, label, value, dec) => {
    const input = h("input", { type: "text", inputmode: dec ? "decimal" : "numeric", pattern: "[0-9.,]*", id: `${key}-${p.id}`, value: fmtNum(value), disabled: ro, autocomplete: "off" });
    input.addEventListener("input", () => { setEdit(p, key, parseNum(input.value)); markChanged(); });
    input.addEventListener("blur", () => { const n = parseNum(input.value); if (!Number.isNaN(n)) input.value = fmtNum(n); });
    input.addEventListener("focus", () => input.select());
    return h("div", { class: "field", "data-key": key }, h("label", { for: input.id }, label), input, h("div", { class: "was" }, "Ahora en Odoo: " + fmtNum(p[key])));
  };
  const c = current(p);
  card.append(goodsField,
    h("div", { class: "row2" }, numField("elementos", "Elementos", c.elementos, false), numField("euro_palets", "Euro palets", c.euro_palets, false)),
    numField("peso", "Peso (kg)", c.peso, true));
  card.dataset.pkg = p.id;
  return card;
}

function markChanged() {
  for (const card of document.querySelectorAll(".pkg")) {
    const p = state.data.bultos.find((x) => String(x.id) === card.dataset.pkg);
    const c = current(p);
    const changed = {
      mercancia: c.goods === PENDING || (c.goods ? c.goods.id : null) !== (p.mercancia ? p.mercancia.id : null),
      elementos: c.elementos !== p.elementos, euro_palets: c.euro_palets !== p.euro_palets, peso: c.peso !== p.peso,
    };
    for (const f of card.querySelectorAll(".field")) f.classList.toggle("changed", !!changed[f.dataset.key]);
  }
}

// ---------- pantalla 4: resumen, confirmación, resultado ----------
function collectChanges() {
  const changes = [];
  for (const p of state.data.bultos) {
    const c = current(p);
    const name = `Lugar ${state.data.bultos.indexOf(p) + 1} · ${p.name}`;
    const changed = {
      goods: c.goods === PENDING || (c.goods ? c.goods.id : null) !== (p.mercancia ? p.mercancia.id : null),
      elementos: c.elementos !== p.elementos, euro_palets: c.euro_palets !== p.euro_palets, peso: c.peso !== p.peso,
    };
    if (!Object.values(changed).some(Boolean) && !Number.isNaN(c.elementos + c.euro_palets + c.peso)) continue; // bulto sin tocar: no se valida
    // Bulto modificado: tiene que quedar completo y correcto.
    if (c.goods === PENDING) throw new Error(`${name}: elige la mercancía de la lista o borra el texto para dejarla en blanco.`);
    for (const k of ["elementos", "euro_palets"]) {
      if (!Number.isInteger(c[k]) || c[k] < 0) throw new Error(`${name}: ${k === "elementos" ? "Elementos" : "Euro palets"} tiene que ser un número entero, 0 o más.`);
    }
    if (!(c.peso > 0)) throw new Error(`${name}: el peso tiene que ser mayor que 0.`);
    const rows = [
      { label: "Mercancía", before: p.mercancia?.name || "(en blanco)", after: c.goods ? c.goods.name : "(en blanco)", changed: changed.goods },
      { label: "Elementos", before: fmtNum(p.elementos), after: fmtNum(c.elementos), changed: changed.elementos },
      { label: "Euro palets", before: fmtNum(p.euro_palets), after: fmtNum(c.euro_palets), changed: changed.euro_palets },
      { label: "Peso", before: fmtNum(p.peso) + " kg", after: fmtNum(c.peso) + " kg", changed: changed.peso },
    ];
    changes.push({ pkg: p, name, rows, payload: { package_id: p.id, goods_id: c.goods ? c.goods.id : 0, number_of_packages: c.elementos, euro_pallet_qty: c.euro_palets, shipping_weight: c.peso }, heavy: c.peso > MAX_KG_WARNING });
  }
  if (!changes.length) throw new Error("No has cambiado ningún dato.");
  return changes;
}
let pending = null;
$("btn-review").addEventListener("click", () => {
  clearError();
  try { pending = collectChanges(); } catch (e) { return showError(e.message); }
  $("review-title").textContent = "Revisa los cambios";
  $("review-actions").hidden = false;
  $("done-actions").hidden = true;
  const body = $("review-body");
  body.replaceChildren();
  if (pending.some((c) => c.heavy)) body.append(h("div", { class: "banner warn" }, "Atención: el peso supera 44.000 kg. Puedes continuar."));
  for (const c of pending) {
    body.append(h("section", { class: "diff" }, h("h2", {}, c.name),
      c.rows.map((r) => r.changed
        ? h("div", { class: "line changed" }, h("div", { class: "lbl" }, r.label), h("div", { class: "before" }, "Antes: " + r.before), h("div", { class: "after" }, "Ahora: " + r.after))
        : h("div", { class: "line same" }, `${r.label}: ${r.after} (sin cambios)`))));
  }
  go("review");
});
$("btn-back-edit").addEventListener("click", () => go("edit"));
$("btn-save").addEventListener("click", () => { $("confirm").hidden = false; $("btn-no").focus(); });
$("btn-no").addEventListener("click", () => { $("confirm").hidden = true; });
$("btn-yes").addEventListener("click", save);

async function save() {
  $("confirm").hidden = true;
  busy("Guardando y generando la orden…");
  try {
    const r = await post("/api/update", { task_id: state.data.tarea.id, sesion: state.data.sesion, cambios: pending.map((c) => c.payload) });
    state.pdfUrl = r.pdf_url;
    state.pdfBlob = null;
    renderDone(r);
  } catch (e) {
    if (e.code === "datos_cambiados") {
      await load(state.lastReq);
      showError(e.message);
    } else if (e.code === "pdf_fallo") {
      renderPdfFailed(e.message);   // los datos ya están guardados: solo falta el PDF
    } else showError(e.message);
  } finally { busy(null); }
}

function renderPdfFailed(msg) {
  $("review-title").textContent = "Cambios guardados";
  $("review-body").replaceChildren(h("div", { class: "banner warn" }, msg));
  $("review-actions").hidden = true;
  $("done-actions").hidden = false;
  for (const id of ["btn-open", "btn-download", "btn-share"]) $(id).hidden = true;
  $("btn-retry").hidden = false;
  window.scrollTo({ top: 0 });
}
$("btn-retry").addEventListener("click", async () => {
  clearError();
  busy("Generando la orden…");
  try {
    const r = await post("/api/reprint", { task_id: state.data.tarea.id, sesion: state.data.sesion });
    state.pdfUrl = r.pdf_url; state.pdfBlob = null;
    $("btn-retry").hidden = true;
    renderDone({ ...r, bultos_modificados: pending.length, avisos: [] });
  } catch (e) { showError(e.message); } finally { busy(null); }
});

function renderDone(r) {
  $("review-title").textContent = "Orden generada";
  const body = $("review-body");
  body.replaceChildren(...[
    h("div", { class: "done-mark" }, "✓"),
    h("p", {}, `Se ha guardado y se ha generado la nueva orden de transporte (${r.bultos_modificados} ${r.bultos_modificados === 1 ? "lugar modificado" : "lugares modificados"}).`),
    r.modo_aviso ? h("div", { class: "banner info" }, r.modo_aviso) : null,
    ...r.avisos.map((a) => h("div", { class: "banner warn" }, a)),
  ].filter(Boolean));
  $("review-actions").hidden = true;
  $("done-actions").hidden = false;
  $("btn-open").hidden = false; $("btn-download").hidden = false;
  $("btn-retry").hidden = true;
  $("btn-share").hidden = !(navigator.canShare && navigator.share);
  window.scrollTo({ top: 0 });
}

async function getPdf() {
  if (state.pdfBlob) return state.pdfBlob;
  busy("Preparando el PDF…");
  try {
    let res;
    try { res = await fetch(state.pdfUrl, { headers: authHeaders() }); } catch { throw new Error("No hay conexión. Inténtalo de nuevo."); }
    if (!res.ok) throw new Error("No se ha podido descargar el PDF. Inténtalo de nuevo.");
    state.pdfBlob = await res.blob();
    return state.pdfBlob;
  } finally { busy(null); }
}
const pdfName = () => `orden-${(state.data.tarea.pedido || state.data.tarea.id)}.pdf`;
async function pdfAction(fn) { clearError(); try { await fn(await getPdf()); } catch (e) { showError(e.message); } }

$("btn-open").addEventListener("click", () => pdfAction((b) => {
  const w = window.open(URL.createObjectURL(b), "_blank");
  if (!w) showError("El navegador ha bloqueado la ventana. Usa «Descargar PDF».");
}));
$("btn-download").addEventListener("click", () => pdfAction((b) => {
  const a = h("a", { href: URL.createObjectURL(b), download: pdfName() });
  document.body.append(a); a.click(); a.remove();
}));
$("btn-share").addEventListener("click", () => pdfAction(async (b) => {
  const file = new File([b], pdfName(), { type: "application/pdf" });
  if (!navigator.canShare({ files: [file] })) throw new Error("Este móvil no permite compartir el PDF. Usa «Descargar PDF».");
  try { await navigator.share({ files: [file], title: "Orden de transporte" }); } catch (e) { if (e.name !== "AbortError") throw new Error("No se ha podido compartir. Inténtalo de nuevo."); }
}));

// ---------- arranque ----------
if ("serviceWorker" in navigator && window.isSecureContext) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}
$("btn-scan").addEventListener("click", () => go("scan"));
(async () => {
  try {
    state.health = await api("/api/health");
    if (state.health.requiere_codigo) {
      $("btn-scan").disabled = true;           // hasta comprobar el código guardado
      const guardado = getCode();
      let ok = false, sinRed = false;
      if (guardado) {
        try { ok = (await fetch("/api/login", { method: "POST", headers: authHeaders(guardado) })).ok; } catch { sinRed = true; }
      }
      if (ok || sinRed) $("btn-scan").disabled = false;   // sin red no se puede comprobar: ya avisará al buscar
      else askCode();
    }
    if (state.health.modo !== "real") {
      $("modo").hidden = false;
      $("modo").textContent = "Modo de prueba (datos simulados)";
      $("btn-test-qr").hidden = false;
    }
  } catch { /* sin conexión: se avisará al buscar */ }
})();
