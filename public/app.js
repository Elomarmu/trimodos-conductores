"use strict";
// App de conductores: 4 pantallas (menú, escáner, edición, resumen/resultado). Sin dependencias.

const $ = (id) => document.getElementById(id);
const LOGOS = { trimodos: "/img/trimodos.png", stinsa: "/img/stinsa.png", transargi: "/img/transargi.png" };
const COMPANY_BY_ID = { 1: "stinsa", 7: "transargi" };
const PENDING = { pending: true };   // ha escrito en Mercancía pero aún no ha elegido de la lista
const state = { data: null, orderChoices: null, edits: {}, vehicleEdits: {}, vehicleRequest: {}, lastReq: null, company: null, pdfUrl: null, pdfBlob: null, pdfLoading: null, health: {} };

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

function showError(msg, retry) {
  const e = $("err");
  e.replaceChildren(document.createTextNode(msg));
  if (retry) {
    e.append(h("button", {
      type: "button", class: "btn primary huge retry-action",
      onclick: () => { clearError(); retry(); },
    }, "Reintentar"));
  }
  e.hidden = false;
  window.scrollTo({ top: 0 });
}
function clearError() { $("err").hidden = true; $("err").replaceChildren(); }
function busy(msg) { $("busy-msg").textContent = msg || "Un momento…"; $("busy").hidden = !msg; }

// Código de acceso compartido: se pide una vez y se recuerda en este móvil.
// Dos apps con el mismo código fuente: la de conductores («/») y la de oficina y almacén («/oficina/», con su propio código).
const APP = document.documentElement.dataset.app === "oficina" ? "oficina" : "conductor";
const aOficina = () => { if (APP === "conductor" && /^#(oficina|almacen)$/.test(location.hash)) location.replace("/oficina/" + location.hash); };
aOficina();   // enlaces antiguos (…/#almacen, …/#oficina) → app de oficina y almacén
window.addEventListener("hashchange", aOficina);
const CODE_KEY = APP === "oficina" ? "codigo-oficina" : "codigo-acceso";
const NO_ES_OFICINA = "Este es el código de los conductores. Para la app de oficina y almacén pide su código en la oficina.";
async function rolDe(res) { try { return (await res.json()).rol || "conductor"; } catch { return "conductor"; } }
function getCode() { try { return localStorage.getItem(CODE_KEY) || ""; } catch { return ""; } }
function saveCode(v) { try { localStorage.setItem(CODE_KEY, v); } catch { /* sin almacenamiento: lo pedirá cada vez */ } }
const authHeaders = (code = getCode()) => ({ "X-Codigo-Acceso": code });

async function api(path, options = {}) {
  if (navigator.onLine === false) {
    const error = new Error("No hay conexión. Busca cobertura y vuelve a intentarlo.");
    error.code = "sin_cobertura";
    throw error;
  }
  let res;
  try {
    res = await fetch(path, { ...options, headers: { ...(options.headers || {}), ...authHeaders() } });
  } catch {
    const error = new Error("No hay conexión. Busca cobertura y vuelve a intentarlo.");
    error.code = "sin_cobertura";
    throw error;
  }
  let body = null;
  try { body = await res.json(); } catch { /* PDF u otra cosa */ }
  if (!res.ok) {
    const e = new Error((body && body.error) || "Algo ha fallado. Inténtalo de nuevo.");
    e.code = body && body.codigo;
    e.fromServer = true;
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
  const errorBox = $("access-err");
  errorBox.hidden = true;
  errorBox.replaceChildren();
  try {
    if (navigator.onLine === false) throw new Error("No hay conexión. Busca cobertura y vuelve a intentarlo.");
    let res;
    try { res = await fetch("/api/login", { method: "POST", headers: authHeaders(code) }); }
    catch { throw new Error("No hay conexión. Busca cobertura y vuelve a intentarlo."); }
    if (!res.ok) {
      const b = await res.json().catch(() => ({}));
      throw new Error(b.error || "Algo ha fallado. Inténtalo de nuevo.");
    }
    if (APP === "oficina" && (await rolDe(res)) !== "oficina") throw new Error(NO_ES_OFICINA);
    saveCode(code);
    $("access").hidden = true;
    $("btn-scan").disabled = false; $("btn-pr").disabled = false;
    window.dispatchEvent(new Event("codigo-ok"));   // p. ej. el almacén entró por su enlace antes de poner el código
  } catch (e) {
    const message = e instanceof TypeError ? "No hay conexión. Busca cobertura y vuelve a intentarlo." : e.message;
    errorBox.append(document.createTextNode(message));
    if (message === "No hay conexión. Busca cobertura y vuelve a intentarlo.") {
      errorBox.append(h("button", {
        type: "button", class: "btn primary huge retry-action",
        onclick: () => $("access-form").requestSubmit(),
      }, "Reintentar"));
    }
    errorBox.hidden = false;
  }
});
const post = (path, data) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

// ---------- navegación ----------
// Cada pantalla es una entrada del historial: el botón Atrás de Android vuelve a la pantalla anterior
// en vez de cerrar la app.
let currentScreen = "menu";
// Marca: el color de acento y el logo de la franja dependen de la empresa de la orden.
function setBrand(company, screen) {
  if (company !== undefined) { if (company) document.body.dataset.company = company; else delete document.body.dataset.company; }
  document.body.dataset.screen = screen;
  const co = document.body.dataset.company;
  const usaEmpresa = co && (screen === "edit" || screen === "review");
  const key = usaEmpresa ? co : "trimodos";
  $("brand-logo").src = LOGOS[key];
  $("brand-logo").alt = { trimodos: "Grupo Trimodos", stinsa: "STINSA", transargi: "TRANSARGI" }[key];
}
function show(name) {
  clearError();
  currentScreen = name;
  setBrand(undefined, name);
  for (const s of document.querySelectorAll(".screen")) s.hidden = s.id !== "s-" + name;
  if (name !== "scan") stopScan();
  if (name === "scan") startScan();
  window.scrollTo({ top: 0 });
}
function go(name) {
  if (name === currentScreen) return show(name);
  if (name === "menu") {                      // volver al menú: se limpia la orden y el historial
    state.data = null; state.orderChoices = null; state.edits = {}; state.vehicleEdits = {}; state.vehicleRequest = {}; pending = null; pendingVehicles = null; state.company = null; state.onConfirm = null; state.pdfName = null;
    for (const x of document.querySelectorAll(".seg-btn")) x.setAttribute("aria-checked", "false");
    setBrand(null, "menu");
    history.replaceState({ s: "menu" }, "");
    return show("menu");
  }
  history.pushState({ s: name }, "");
  show(name);
}
window.addEventListener("popstate", (e) => {
  let target = (e.state && e.state.s) || "menu";
  if (!state.data && (target === "edit" || target === "review")) target = "menu";
  if (target === "choose" && !state.orderChoices) target = "scan";
  if (target === "review" && !pending) target = "edit";
  if (!$("confirm").hidden) closeConfirm();
  if (!$("mma").hidden) $("mma").hidden = true;
  show(target);
});
history.replaceState({ s: "menu" }, "");
setBrand(null, "menu");   // la franja arranca ya con el tamaño del menú
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-go]");
  if (b) go(b.dataset.go);
});

// ---------- pantalla 2: escáner ----------
// Un solo escáner para todos los móviles: la cámara se pide en alta resolución (sin pedirla, iPhone da 640x480 y un
// QR pequeño no se lee). Lee el lector del sistema (BarcodeDetector: Android) o, si no hay o falla (iPhone), jsQR
// sobre el centro de la imagen, que es lo que se ve en pantalla, sin reducirla.
let stream = null, scanTimer = null, scanGen = 0;
const SCAN_EVERY_MS = 200;
const JSQR_MAX_SIDE = 1200;   // lado máximo (px) del recorte que analiza jsQR
const DETECTOR_MAX_FAILS = 8; // fallos seguidos del lector del sistema antes de pasar a jsQR

async function systemDetector() {
  if (!("BarcodeDetector" in window)) return null;
  try {
    if (!(await BarcodeDetector.getSupportedFormats()).includes("qr_code")) return null;
    return new BarcodeDetector({ formats: ["qr_code"] });
  } catch { return null; }   // existe pero no funciona (pasa en algunos iPhone con la opción experimental activada)
}

function readWithJsQR(v, canvas, ctx) {
  const side = Math.min(v.videoWidth, v.videoHeight);
  const n = Math.round(side * Math.min(1, JSQR_MAX_SIDE / side));
  if (canvas.width !== n) { canvas.width = n; canvas.height = n; }
  ctx.drawImage(v, (v.videoWidth - side) / 2, (v.videoHeight - side) / 2, side, side, 0, 0, n, n);
  const code = jsQR(ctx.getImageData(0, 0, n, n).data, n, n, { inversionAttempts: "dontInvert" });
  return code ? code.data : null;
}

async function startScan() {
  const gen = ++scanGen;
  $("cam-msg").textContent = "Apunta al código QR de la orden.";
  if (!window.isSecureContext || !navigator.mediaDevices) {
    $("cam-msg").textContent = "La cámara necesita una conexión segura (https). Escribe el número de pedido.";
    return;
  }
  try {
    const s = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: false });
    if (gen !== scanGen) { s.getTracks().forEach((t) => t.stop()); return; } // ya se salió de la pantalla
    stream = s;
    const v = $("video");
    v.srcObject = s;
    await v.play();
    // Cámara frontal (la del ordenador): se ve como en un espejo, que es lo natural al acercarle el papel.
    const facing = s.getVideoTracks()[0]?.getSettings?.().facingMode;
    $("cam").classList.toggle("mirror", facing === "user" || (!facing && matchMedia("(pointer: fine)").matches));
    $("cam").classList.add("live");
    let detector = await systemDetector();
    if (!detector) await loadScript("/vendor/jsQR.js");
    if (gen !== scanGen) return;
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    let fails = 0;
    const tick = async () => {
      if (gen !== scanGen) return;
      let text = null;
      if (v.videoWidth) {
        if (detector) {
          try {
            const codes = await detector.detect(v);
            if (codes.length) text = codes[0].rawValue;
            fails = 0;
          } catch {
            if (++fails >= DETECTOR_MAX_FAILS) { await loadScript("/vendor/jsQR.js"); detector = null; }
          }
        } else {
          text = readWithJsQR(v, canvas, ctx);
        }
      }
      if (gen !== scanGen) return;
      if (text) return onQr(text);
      scanTimer = setTimeout(() => tick().catch(scanFailed(gen)), SCAN_EVERY_MS);
    };
    await tick();
  } catch (e) {
    scanFailed(gen)(e);
  }
}
const scanFailed = (gen) => () => {
  if (gen === scanGen) $("cam-msg").textContent = "No se puede usar la cámara. Revisa el permiso o escribe el número de pedido.";
};
function loadScript(src) {
  return new Promise((ok, fail) => {
    if (window.jsQR) return ok();
    document.head.append(h("script", { src, onload: ok, onerror: fail }));
  });
}
function stopScan() {
  scanGen++;
  clearTimeout(scanTimer); scanTimer = null;
  if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
  $("video").srcObject = null;
  $("cam").classList.remove("live");
}
let scanning = false;
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
    setBrand(state.company, currentScreen);   // el botón principal toma el color de la empresa elegida
    for (const x of document.querySelectorAll(".seg-btn")) x.setAttribute("aria-checked", String(x === b));
  });
}
// Es un formulario: «Intro» en el ordenador o «Buscar» en el teclado del móvil también buscan.
$("order-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  clearError();
  if (!state.company) return showError("Elige la empresa: TRANSARGI o STINSA.");
  const n = $("order").value.trim();
  if (!n) return showError("Escribe el número de pedido. Ejemplo: S2621473");
  stopScan();
  load({ order_name: n, company: state.company });
});
$("btn-test-qr").addEventListener("click", () => { stopScan(); load({ qr_text: state.health.qr_prueba }); });

async function load(req, draft = null) {
  if (req.task_id == null) state.orderChoices = null;
  clearError();
  busy("Buscando la orden…");
  try {
    const result = await post("/api/resolve-qr", req);
    state.lastReq = req;
    if (result.seleccion_requerida) {
      state.data = null;
      state.orderChoices = result;
      renderTaskChoices();
      go("choose");
      return true;
    }
    state.data = result;
    state.edits = {};
    state.vehicleEdits = {};
    state.vehicleRequest = {};
    renderEdit();
    go("edit");
    if (draft) restoreDraft(draft);
    return true;
  } catch (e) {
    if (draft && state.data) go("edit");
    else go(state.orderChoices ? "choose" : "scan");
    showError(e.message, () => load(req, draft));
    return false;
  } finally {
    busy(null);
  }
}

function captureDraft() {
  const packages = state.data.bultos.map((p) => ({
    id: p.id,
    goodsText: $(`g-${p.id}`).value,
    goodsEdit: state.edits[p.id] && state.edits[p.id].goods,
    elements: $(`elementos-${p.id}`).value,
    weight: $(`peso-${p.id}`).value,
  }));
  const vehicles = Object.fromEntries(["tractora", "remolque"].map((role) => [
    role,
    { value: $(`vehicle-${role}`).value, edit: state.vehicleEdits[role] ? { ...state.vehicleEdits[role] } : null },
  ]));
  return { packages, vehicles };
}

function restoreDraft(draft) {
  for (const saved of draft.packages) {
    const goodsInput = $(`g-${saved.id}`);
    const elementsInput = $(`elementos-${saved.id}`);
    const weightInput = $(`peso-${saved.id}`);
    if (!goodsInput || !elementsInput || !weightInput) continue;
    goodsInput.value = saved.goodsText;
    if (saved.goodsEdit !== undefined) {
      (state.edits[saved.id] ||= {}).goods = saved.goodsEdit;
      if (saved.goodsEdit === PENDING) goodsInput.dispatchEvent(new Event("input", { bubbles: true }));
    }
    elementsInput.value = saved.elements;
    setEdit(state.data.bultos.find((p) => p.id === saved.id), "elementos", parseNum(saved.elements));
    weightInput.value = saved.weight;
    setEdit(state.data.bultos.find((p) => p.id === saved.id), "peso", parseNum(saved.weight));
  }
  for (const role of ["tractora", "remolque"]) {
    const saved = draft.vehicles[role];
    if (!saved?.edit) continue;
    const input = $(`vehicle-${role}`);
    input.value = saved.value;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.blur();
  }
  markChanged();
}

function retrySaveAfterUncertainOutcome() {
  return load(state.lastReq, captureDraft());
}

function taskDateLabel(value) {
  if (!value) return "Fecha de carga sin indicar";
  const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}:\d{2}))?/);
  return match ? `Carga: ${match[3]}/${match[2]}/${match[1]}${match[4] ? ` ${match[4]}` : ""}` : `Carga: ${value}`;
}

function renderTaskChoices() {
  const result = state.orderChoices;
  $("choose-order").textContent = `Pedido ${result.pedido}. Tiene varias órdenes de transporte; elige la correcta.`;
  $("task-choices").replaceChildren(...result.ordenes.map((order) => {
    const button = h("button", {
      type: "button",
      class: `btn huge task-choice${order.editable ? "" : " no-editable"}`,
      onclick: () => load({ ...state.lastReq, task_id: order.id }),
    },
    h("span", { class: "task-choice-title" }, order.nombre),
    h("span", { class: "task-choice-detail" }, taskDateLabel(order.fecha_carga)),
    h("span", { class: "task-choice-detail" }, `${order.origen || "Origen sin indicar"} → ${order.destino || "destino sin indicar"}`),
    h("span", { class: "task-choice-detail" }, `Tractora: ${order.tractora || "(en blanco)"} · Remolque: ${order.remolque || "(en blanco)"}`),
    h("span", { class: "task-choice-detail" }, `Etapa: ${order.etapa || "sin etapa"}`),
    !order.editable ? h("span", { class: "task-choice-reason" }, order.motivo_no_editable) : null);
    return button;
  }));
}

$("btn-choose-back").addEventListener("click", () => {
  state.orderChoices = null;
  state.data = null;
  go("scan");
});

// ---------- pantalla 3: edición ----------
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
  setBrand(COMPANY_BY_ID[d.tarea.compania && d.tarea.compania.id] || null, "edit");
  $("edit-title").textContent = "Pedido " + (d.tarea.pedido || d.tarea.nombre);
  $("edit-task-name").textContent = `Tarea: ${d.tarea.nombre}`;
  const banners = $("edit-banners");
  banners.replaceChildren();
  if (d.version_antigua) banners.append(h("div", { class: "banner warn" }, "Esta orden tiene una versión más nueva. Estos son los datos actuales."));
  if (!d.editable) banners.append(h("div", { class: "banner bad" }, d.motivo_no_editable || "Esta orden no se puede modificar."));
  else if (!d.verificado_por_qr) banners.append(h("div", { class: "banner info" }, "Orden abierta por número de pedido (sin QR)."));
  if (!d.bultos.length && d.editable) banners.append(h("div", { class: "banner bad" }, "Esta orden todavía no tiene creados los lugares de carga y descarga (bultos). Ponte en contacto con tu responsable de tráfico."));
  $("btn-review").hidden = !d.editable || !d.bultos.length;

  const box = $("packages");
  box.replaceChildren();
  renderVehicles();
  d.bultos.forEach((p, i) => box.append(packageCard(p, i)));
}

function baselineVehicle(role) { return state.data.vehiculos[role] || null; }
function selectedVehicle(role) {
  const edit = state.vehicleEdits[role];
  return edit ? edit.vehicle : baselineVehicle(role);
}
function vehiclePlate(role) {
  const edit = state.vehicleEdits[role];
  return edit ? edit.plate : baselineVehicle(role)?.matricula || "";
}

function renderVehicles() {
  const box = $("vehicles");
  const card = h("section", { class: "pkg vehicle-card" }, h("h2", {}, "Vehículos de la orden"));
  for (const [role, label, type] of [["tractora", "Tractora", "tractor"], ["remolque", "Remolque", "trailer"]]) {
    const vehicle = baselineVehicle(role);
    const input = h("input", {
      type: "text", id: `vehicle-${role}`, value: vehicle?.matricula || "",
      autocapitalize: "characters", autocomplete: "off", spellcheck: "false",
      inputmode: "text", disabled: !state.data.editable,
      "aria-label": label,
    });
    const status = h("p", { class: "vehicle-status", "aria-live": "polite" },
      vehicle ? `✓ Encontrada en Odoo: ${vehicle.matricula}${vehicle.modelo ? ` · ${vehicle.modelo}` : ""}` :
        role === "tractora" ? "Obligatoria" : "Opcional: puede dejarse en blanco.");
    const sugg = h("div", { class: "sugg" });
    input.addEventListener("input", () => {
      state.vehicleRequest[role] = (state.vehicleRequest[role] || 0) + 1;
      state.vehicleEdits[role] = { plate: input.value, vehicle: null, confirmedMismatch: false, status: "pending" };
      status.textContent = "";
      status.className = "vehicle-status";
      sugg.replaceChildren();
      markChanged();
    });
    input.addEventListener("blur", () => checkVehicle(role, type, input, status, sugg));
    card.append(h("div", { class: "field", "data-key": role },
      h("label", { for: input.id }, label), input, status, sugg));
  }
  box.replaceChildren(card);
}

async function checkVehicle(role, type, input, status, sugg) {
  const plate = input.value.trim();
  const seq = (state.vehicleRequest[role] || 0) + 1;
  state.vehicleRequest[role] = seq;
  sugg.replaceChildren();
  if (!plate && role === "remolque") {
    state.vehicleEdits[role] = { plate: "", vehicle: null, confirmedMismatch: false, status: "empty" };
    status.textContent = "Opcional: se guardará sin remolque.";
    status.className = "vehicle-status";
    markChanged();
    return;
  }
  if (!plate && role === "tractora") {
    state.vehicleEdits[role] = { plate: "", vehicle: null, confirmedMismatch: false, status: "not-found" };
    status.textContent = "La tractora es obligatoria.";
    status.className = "vehicle-status bad";
    markChanged();
    return;
  }
  status.textContent = "Comprobando matrícula…";
  status.className = "vehicle-status";
  try {
    const q = new URLSearchParams({
      task_id: String(state.data.tarea.id), sesion: state.data.sesion,
      tipo: type, matricula: plate,
    });
    const result = await api(`/api/vehicles?${q}`);
    if (seq !== state.vehicleRequest[role]) return;
    if (!result.encontrada) {
      state.vehicleEdits[role] = { plate, vehicle: null, confirmedMismatch: false, status: "not-found" };
      status.textContent = `No encontramos la matrícula ${result.matricula_normalizada}. Revísala o avisa a tu responsable de tráfico.`;
      status.className = "vehicle-status bad";
      markChanged();
      return;
    }
    if (result.resultados.length > 1) {
      status.textContent = "Hay varias coincidencias. Elige matrícula y modelo:";
      status.className = "vehicle-status warn";
      sugg.replaceChildren(...result.resultados.map((candidate) =>
        h("button", { type: "button", class: "btn", onclick: () => chooseVehicle(role, type, plate, candidate, status, sugg) },
          `${candidate.matricula}${candidate.modelo ? ` · ${candidate.modelo}` : ""}`)));
      return;
    }
    chooseVehicle(role, type, plate, result.resultados[0], status, sugg);
  } catch (e) {
    if (seq === state.vehicleRequest[role]) {
      state.vehicleEdits[role] = { plate, vehicle: null, confirmedMismatch: false, status: "error" };
      status.textContent = e.message;
      status.className = "vehicle-status bad";
      showError(e.message, () => checkVehicle(role, type, input, status, sugg));
    }
  }
}

function chooseVehicle(role, expectedType, plate, vehicle, status, sugg) {
  const wrongType = vehicle.tipo && vehicle.tipo !== expectedType;
  if (wrongType) {
    const actual = vehicle.tipo === "trailer" ? "un remolque" : "una tractora";
    state.vehicleEdits[role] = { plate, vehicle: null, candidate: vehicle, confirmedMismatch: false, status: "mismatch" };
    status.textContent = `Aviso: ${vehicle.matricula} es ${actual}, no ${role === "tractora" ? "una tractora" : "un remolque"}.`;
    status.className = "vehicle-status warn";
    sugg.replaceChildren(
      h("p", { class: "hint" }, "¿Quieres continuar de todos modos?"),
      h("div", { class: "vehicle-confirm" },
        h("button", { type: "button", class: "btn ok", onclick: () => acceptVehicle(role, plate, vehicle, true, status, sugg) }, "Sí, continuar"),
        h("button", { type: "button", class: "btn danger", onclick: () => {
          state.vehicleEdits[role] = { plate, vehicle: null, confirmedMismatch: false, status: "not-found" };
          status.textContent = "Escribe otra matrícula.";
          status.className = "vehicle-status bad";
          sugg.replaceChildren();
          markChanged();
        } }, "No, corregir")));
    return;
  }
  acceptVehicle(role, plate, vehicle, false, status, sugg);
}

function acceptVehicle(role, plate, vehicle, confirmedMismatch, status, sugg) {
  state.vehicleEdits[role] = { plate, vehicle, confirmedMismatch, status: "found" };
  status.textContent = `✓ Encontrada en Odoo: ${vehicle.matricula}${vehicle.modelo ? ` · ${vehicle.modelo}` : ""}`;
  status.className = "vehicle-status found";
  sugg.replaceChildren();
  markChanged();
}

function packageCard(p, index) {
  const ro = !state.data.editable;
  const route = [p.origen?.name, p.destino?.name].filter(Boolean).join(" → ");
  const card = h("section", { class: "pkg" },
    h("h2", {}, `Lugar ${index + 1}`, h("span", { class: "code" }, p.name)),
    h("div", { class: "route" }, route),
  );

  // mercancía con sugerencias
  const goodsInput = h("input", { type: "text", id: `g-${p.id}`, value: current(p).goods?.name || "", autocomplete: "off", disabled: ro });
  const sugg = h("div", { class: "sugg" });
  let timer = null;
  const searchGoods = async () => {
    const q = goodsInput.value.trim();
    if (q.length < 2) return sugg.replaceChildren();
    try {
      const r = await api("/api/goods?q=" + encodeURIComponent(q));
      const options = [];
      if (r.resultados.length) options.push(h("p", { class: "hint" }, "¿Es alguna de estas?"));
      options.push(...r.resultados.map((g) => h("button", { type: "button", class: "btn", onclick: () => selectGoods(p, goodsInput, sugg, g) }, g.name)));
      if (r.crear_nombre) options.push(h("button", {
        type: "button", class: "btn secondary",
        onclick: () => confirmNewGoods(p, goodsInput, sugg, r.crear_nombre, r.resultados),
      }, `➕ Crear mercancía nueva: "${r.crear_nombre}"`));
      if (!options.length) options.push(h("p", { class: "hint" }, "No hay mercancías parecidas."));
      sugg.replaceChildren(...options);
    } catch (e) { showError(e.message, searchGoods); }
  };
  goodsInput.addEventListener("input", () => {
    clearTimeout(timer);
    // texto vacío = mercancía en blanco (vale); con texto, no vale hasta que elija de la lista
    setEdit(p, "goods", goodsInput.value.trim() === "" ? null : PENDING);
    markChanged();
    timer = setTimeout(searchGoods, 300);
  });
  const goodsField = h("div", { class: "field", "data-key": "mercancia" }, h("label", { for: `g-${p.id}` }, "Mercancía"), goodsInput, sugg, h("div", { class: "was" }, "Antes: " + (p.mercancia?.name || "(en blanco)")));

  const numField = (key, label, value, dec) => {
    const input = h("input", { type: "text", inputmode: dec ? "decimal" : "numeric", pattern: "[0-9.,]*", id: `${key}-${p.id}`, value: fmtNum(value), disabled: ro, autocomplete: "off" });
    input.addEventListener("input", () => { setEdit(p, key, parseNum(input.value)); markChanged(); });
    input.addEventListener("blur", () => { const n = parseNum(input.value); if (!Number.isNaN(n)) input.value = fmtNum(n); });
    input.addEventListener("focus", () => input.select());
    return h("div", { class: "field", "data-key": key }, h("label", { for: input.id }, label), input, h("div", { class: "was" }, "Antes: " + fmtNum(p[key])));
  };
  const readOnlyNumField = (label, value) => h("div", { class: "field" },
    h("label", {}, label),
    h("input", { type: "text", value: fmtNum(value), readonly: true, "aria-readonly": "true", tabindex: "-1" }));
  const c = current(p);
  card.append(goodsField,
    h("div", { class: "row2" }, numField("elementos", "Bultos", c.elementos, false), readOnlyNumField("Euro palets · solo lectura", p.euro_palets)),
    numField("peso", "Kilos (kg)", c.peso, true));
  card.dataset.pkg = p.id;
  return card;
}

function selectGoods(p, input, sugg, goods) {
  setEdit(p, "goods", { id: goods.id, name: goods.name });
  input.value = goods.name;
  sugg.replaceChildren();
  markChanged();
}

function confirmNewGoods(p, input, sugg, name, similar) {
  const alternatives = $("confirm-similar");
  alternatives.replaceChildren();
  if (similar.length) {
    alternatives.append(h("p", { class: "hint" }, "¿Es alguna de estas?"));
    alternatives.append(...similar.map((goods) => h("button", {
      type: "button", class: "btn",
      onclick: () => {
        selectGoods(p, input, sugg, goods);
        closeConfirm();
      },
    }, goods.name)));
  }
  $("confirm-q").textContent = "Confirmar mercancía nueva";
  $("confirm-txt").textContent = `Se creará la mercancía "${name}" en el sistema. ¿Es correcto?`;
  $("btn-yes").textContent = "Sí, es correcto";
  state.onConfirm = () => {
    setEdit(p, "goods", { id: null, name, nueva: true });
    input.value = name;
    sugg.replaceChildren();
    markChanged();
    closeConfirm();
  };
  $("confirm").hidden = false;
  $("btn-no").focus();
}

function closeConfirm() {
  $("confirm").hidden = true;
  $("confirm-q").textContent = "¿Seguro?";
  $("confirm-txt").textContent = "Se generará una nueva orden de transporte.";
  $("btn-yes").textContent = "Sí, guardar";
  $("confirm-similar").replaceChildren();
  state.onConfirm = null;
}

function markChanged() {
  for (const card of document.querySelectorAll("#packages .pkg")) {
    const p = state.data.bultos.find((x) => String(x.id) === card.dataset.pkg);
    const c = current(p);
    const changed = {
      mercancia: c.goods === PENDING || (c.goods ? c.goods.id : null) !== (p.mercancia ? p.mercancia.id : null),
      elementos: c.elementos !== p.elementos, peso: c.peso !== p.peso,
    };
    for (const f of card.querySelectorAll(".field")) f.classList.toggle("changed", !!changed[f.dataset.key]);
  }
  for (const role of ["tractora", "remolque"]) {
    const changed = (selectedVehicle(role)?.id || null) !== (baselineVehicle(role)?.id || null);
    document.querySelector(`#vehicles [data-key="${role}"]`)?.classList.toggle("changed", changed);
  }
}

// ---------- pantalla 4: resumen, confirmación, resultado ----------
function collectChanges() {
  const changes = [];
  for (const p of state.data.bultos) {
    const c = current(p);
    const place = `Lugar ${state.data.bultos.indexOf(p) + 1}`;
    const name = `${place} · ${p.name}`;   // en los avisos; en pantalla, lugar y bulto van por separado
    const changed = {
      goods: c.goods === PENDING || (c.goods ? c.goods.id : null) !== (p.mercancia ? p.mercancia.id : null),
      elementos: c.elementos !== p.elementos, peso: c.peso !== p.peso,
    };
    if (!Object.values(changed).some(Boolean)) continue;
    // Bulto modificado: tiene que quedar completo y correcto.
    if (c.goods === PENDING) throw new Error(`${name}: elige una mercancía, crea una nueva con confirmación o borra el texto para dejarla en blanco.`);
    if (!Number.isInteger(c.elementos) || c.elementos < 0) {
      throw new Error(`${name}: Bultos tiene que ser un número entero, 0 o más.`);
    }
    if (!(c.peso > 0)) throw new Error(`${name}: el peso tiene que ser mayor que 0.`);
    const rows = [
      {
        label: "Mercancía", before: p.mercancia?.name || "(en blanco)",
        after: c.goods ? c.goods.name : "(en blanco)", changed: changed.goods,
        nueva: !!c.goods?.nueva,
      },
      { label: "Bultos", before: fmtNum(p.elementos), after: fmtNum(c.elementos), changed: changed.elementos },
      { label: "Euro palets", before: fmtNum(p.euro_palets), after: fmtNum(p.euro_palets), changed: false },
      { label: "Kilos", before: fmtNum(p.peso) + " kg", after: fmtNum(c.peso) + " kg", changed: changed.peso },
    ];
    const payload = { package_id: p.id, number_of_packages: c.elementos, shipping_weight: c.peso };
    if (c.goods?.nueva) payload.new_goods_name = c.goods.name;
    else payload.goods_id = c.goods ? c.goods.id : 0;
    changes.push({ pkg: p, name, place, rows, payload, heavy: changed.peso && c.peso > state.data.umbral_peso_kg });
  }
  return changes;
}

function clientNormalizePlate(value) { return String(value || "").toUpperCase().replace(/[^A-Z0-9]/g, ""); }
function vehicleLabel(vehicle) {
  return vehicle ? `${vehicle.matricula}${vehicle.modelo ? ` (${vehicle.modelo})` : ""}` : "(en blanco)";
}

function collectVehicleChanges() {
  const rows = [];
  const payload = {};
  let changed = false;
  for (const role of ["tractora", "remolque"]) {
    const edit = state.vehicleEdits[role];
    const vehicle = selectedVehicle(role);
    const plate = edit ? edit.plate.trim() : vehicle?.matricula || "";
    if (role === "tractora" && !vehicle) {
      throw new Error("La tractora es obligatoria. Escribe la matrícula y espera a que aparezca «Encontrada».");
    }
    if (plate && !vehicle) {
      throw new Error(`No encontramos la matrícula ${clientNormalizePlate(plate)}. Revísala o avisa a tu responsable de tráfico.`);
    }
    if (vehicle && clientNormalizePlate(plate) !== clientNormalizePlate(vehicle.matricula)) {
      throw new Error(`${role === "tractora" ? "Tractora" : "Remolque"}: vuelve a comprobar la matrícula antes de continuar.`);
    }
    const initial = baselineVehicle(role);
    const differs = (vehicle?.id || null) !== (initial?.id || null);
    changed ||= differs;
    rows.push({
      label: role === "tractora" ? "Tractora" : "Remolque",
      before: vehicleLabel(initial), after: vehicleLabel(vehicle), changed: differs,
    });
    payload[role] = {
      vehicle_id: vehicle?.id || null,
      matricula: plate,
      confirmar_tipo_incorrecto: !!edit?.confirmedMismatch,
    };
  }
  return { rows, payload, changed };
}

let pending = null;
let pendingVehicles = null;
$("btn-review").addEventListener("click", () => {
  clearError();
  try {
    pending = collectChanges();
    pendingVehicles = collectVehicleChanges();
    if (!pending.length && !pendingVehicles.changed) throw new Error("No has cambiado ningún dato.");
  } catch (e) { return showError(e.message); }
  const pesados = pending.filter((c) => c.heavy);
  if (!pesados.length) return mostrarResumen();
  // peso alto: antes del resumen se pide confirmarlo
  $("mma-list").replaceChildren(...pesados.map((c) => h("li", {}, `${c.pkg.name}: ${fmtNum(c.payload.shipping_weight)} kg`)));
  $("mma").hidden = false;
  $("btn-mma-no").focus();
});
$("btn-mma-si").addEventListener("click", () => { $("mma").hidden = true; mostrarResumen(); });
$("btn-mma-no").addEventListener("click", () => { $("mma").hidden = true; });

function mostrarResumen() {
  $("review-title").textContent = "Revisa los cambios";
  $("review-actions").hidden = false;
  $("done-actions").hidden = true;
  $("review-body").className = "";
  const vehicleDiff = h("section", { class: "diff" }, h("h2", {}, "Vehículos de la orden"),
    pendingVehicles.rows.map((r) => r.changed
      ? h("div", { class: "line changed" }, h("span", { class: "lbl" }, r.label), h("span", { class: "before" }, "Antes: " + r.before), h("span", { class: "after" }, "Ahora: " + r.after))
      : h("div", { class: "line same" }, h("span", { class: "lbl" }, r.label), h("span", { class: "val" }, r.after, h("small", {}, " (sin cambios)")))));
  const packageDiffs = pending.map((c) =>
    h("section", { class: "diff" }, h("h2", {}, c.place, h("span", { class: "code" }, c.pkg.name)),
      c.rows.map((r) => r.changed
        ? h("div", { class: "line changed" }, h("span", { class: "lbl" }, r.label), h("span", { class: "before" }, "Antes: " + r.before), h("span", { class: "after" }, "Ahora: " + r.after, r.nueva ? h("small", {}, " (mercancía nueva creada desde la app)") : null))
        : h("div", { class: "line same" }, h("span", { class: "lbl" }, r.label), h("span", { class: "val" }, r.after, h("small", {}, " (sin cambios)"))))));
  $("review-body").replaceChildren(
    h("p", { class: "hint task-review-name" }, `Tarea: ${state.data.tarea.nombre}`),
    h("div", { class: "diffs" }, ...(pendingVehicles.changed ? [vehicleDiff] : []), ...packageDiffs),
  );
  go("review");
}
$("btn-back-edit").addEventListener("click", () => go("edit"));
$("btn-save").addEventListener("click", () => {
  closeConfirm();
  $("confirm-txt").textContent = "Se generará una nueva orden de transporte.";
  $("confirm").hidden = false; $("btn-no").focus();
});
$("btn-no").addEventListener("click", closeConfirm);
// En el ordenador, «Esc» responde «No» en los avisos (nunca guarda nada).
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("confirm").hidden) $("btn-no").click();
  else if (!$("mma").hidden) $("btn-mma-no").click();
});
$("btn-yes").addEventListener("click", () => {
  const action = state.onConfirm || save;
  closeConfirm();
  action();
});

async function save() {
  $("confirm").hidden = true;
  busy("Guardando y generando la orden…");
  try {
    const r = await post("/api/update", {
      task_id: state.data.tarea.id, sesion: state.data.sesion,
      cambios: pending.map((c) => c.payload), ...pendingVehicles.payload,
    });
    renderDone(r);
  } catch (e) {
    if (e.code === "datos_cambiados") {
      const refreshed = await load(state.lastReq, captureDraft());
      if (refreshed) showError(e.message);
    } else if (e.code === "pdf_fallo") {
      renderPdfFailed(e.message);   // los datos ya están guardados: solo falta el PDF
    } else if (["odoo_mantenimiento", "odoo_no_responde", "sin_cobertura"].includes(e.code)) {
      showError(e.message, retrySaveAfterUncertainOutcome);
    } else if (e.code === "odoo_acceso") {
      showError(e.message, () => save());
    } else showError(e.message);
  } finally { busy(null); }
}

function renderPdfFailed(msg) {
  $("review-title").textContent = "Cambios guardados";
  $("review-body").className = "";
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
    $("btn-retry").hidden = true;
    renderDone({ ...r, bultos_modificados: pending.length, vehiculos_modificados: pendingVehicles.changed ? 1 : 0 });
  } catch (e) { showError(e.message, () => $("btn-retry").click()); } finally { busy(null); }
});

const CHECK_SVG = '<svg viewBox="0 0 48 48" aria-hidden="true"><path d="M11 25 l9 9 l18 -19" fill="none" stroke="currentColor" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/></svg>';

function renderDone(r) {
  state.pdfUrl = r.pdf_url; state.pdfBlob = null; state.pdfLoading = null;
  loadPdf().catch(() => { /* se reintenta al pulsar un botón del PDF */ });
  $("review-title").textContent = "Orden generada";
  const mark = h("div", { class: "done-mark" });
  mark.innerHTML = CHECK_SVG;
  $("review-body").className = "result";
  $("review-body").replaceChildren(...[
    mark,
    h("p", {}, `Se ha guardado y se ha generado la nueva orden de transporte. Bultos modificados: ${r.bultos_modificados || 0}. Vehículos modificados: ${r.vehiculos_modificados || 0}.`),
    r.modo_aviso ? h("div", { class: "banner info" }, r.modo_aviso) : null,
  ].filter(Boolean));
  $("review-actions").hidden = true;
  $("done-actions").hidden = false;
  $("btn-open").hidden = false; $("btn-download").hidden = false;
  $("btn-retry").hidden = true;
  $("btn-share").hidden = !(navigator.canShare && navigator.share);
  window.scrollTo({ top: 0 });
}

// El PDF se descarga en segundo plano nada más generarse. Así, al pulsar «Abrir» o «Compartir», se abre en el mismo
// toque: el iPhone solo deja abrir ventanas y compartir justo al pulsar, no después de esperar a la red.
function loadPdf() {
  if (!state.pdfLoading) {
    const url = state.pdfUrl;
    state.pdfLoading = (async () => {
      if (navigator.onLine === false) {
        const error = new Error("No hay conexión. Busca cobertura y vuelve a intentarlo.");
        error.code = "sin_cobertura";
        throw error;
      }
      let res;
      try { res = await fetch(url, { headers: authHeaders() }); }
      catch {
        const error = new Error("No hay conexión. Busca cobertura y vuelve a intentarlo.");
        error.code = "sin_cobertura";
        throw error;
      }
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        const error = new Error(body?.error || "No se ha podido descargar el PDF. Inténtalo de nuevo.");
        error.code = body?.codigo;
        error.fromServer = true;
        throw error;
      }
      return res.blob();
    })()
      .then((b) => { if (state.pdfUrl === url) state.pdfBlob = b; return b; })
      .catch((e) => { if (state.pdfUrl === url) state.pdfLoading = null; throw e; });
  }
  return state.pdfLoading;
}
const pdfName = () => state.pdfName || `orden-${(state.data.tarea.pedido || state.data.tarea.id)}.pdf`;
function pdfAction(fn) {
  clearError();
  const run = (b) => {   // se llama sin esperas intermedias: abrir y compartir ocurren dentro del toque
    try { const r = fn(b); if (r && r.catch) r.catch((e) => showError(e.message, () => pdfAction(fn))); } catch (e) { showError(e.message); }
  };
  if (state.pdfBlob) { run(state.pdfBlob); return; }   // ya descargado: se usa dentro del mismo toque
  busy("Preparando el PDF…");
  loadPdf().finally(() => busy(null)).then(run, (e) => showError(e.message, () => pdfAction(fn)));
}
function pdfObjectUrl(b) {
  const u = URL.createObjectURL(b);
  setTimeout(() => URL.revokeObjectURL(u), 60000);
  return u;
}

const abrirPdf = () => pdfAction((b) => {
  const w = window.open(pdfObjectUrl(b), "_blank");
  if (!w) showError("El navegador ha bloqueado la ventana. Usa «Descargar PDF».");
});
const descargarPdf = () => pdfAction((b) => {
  const a = h("a", { href: pdfObjectUrl(b), download: pdfName() });
  document.body.append(a); a.click(); a.remove();
});
const compartirPdf = () => pdfAction(async (b) => {
  const file = new File([b], pdfName(), { type: "application/pdf" });
  if (!navigator.canShare({ files: [file] })) throw new Error("Este móvil no permite compartir el PDF. Usa «Descargar PDF».");
  try { await navigator.share({ files: [file], title: "Orden de transporte" }); } catch (e) { if (e.name !== "AbortError") throw new Error("No se ha podido compartir. Inténtalo de nuevo."); }
});
$("btn-open").addEventListener("click", abrirPdf);
$("btn-download").addEventListener("click", descargarPdf);
$("btn-share").addEventListener("click", compartirPdf);

// ---------- arranque ----------
if ("serviceWorker" in navigator && window.isSecureContext) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}
$("btn-scan").addEventListener("click", () => go("scan"));
document.querySelectorAll(".solo-oficina").forEach((b) => { b.hidden = APP !== "oficina"; });
document.querySelectorAll(".solo-conductor").forEach((b) => { b.hidden = APP === "oficina"; });   // en oficina va dentro de «Oficina · PR»
(async () => {
  try {
    state.health = await api("/api/health");
    if (state.health.requiere_codigo) {
      $("btn-scan").disabled = true; $("btn-pr").disabled = true;   // hasta comprobar el código guardado
      const guardado = getCode();
      let ok = false, sinRed = false;
      if (guardado) {
        try {
          const res = await fetch("/api/login", { method: "POST", headers: authHeaders(guardado) });
          ok = res.ok && (APP !== "oficina" || (await rolDe(res)) === "oficina");
        } catch { sinRed = true; }
      }
      if (ok || sinRed) { $("btn-scan").disabled = false; $("btn-pr").disabled = false; }   // sin red no se puede comprobar: ya avisará al buscar
      else askCode();
    }
    if (state.health.modo !== "real") {
      $("modo").hidden = false;
      $("modo").textContent = "Modo de prueba (datos simulados)";
      $("btn-test-qr").hidden = false;
    }
  } catch (e) { showError(e.message, () => window.location.reload()); }
})();
