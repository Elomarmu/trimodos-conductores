"use strict";
// Viajes de PR (SAICA): foto del albarán → revisar → guardar en Odoo y descargar la orden de transporte.
// Usa las utilidades de app.js ($, h, post, api, busy, showError, go, state, loadPdf, abrirPdf…).

const pr = { viaje: null, vehiculos: null, puntos: [] };
const MAX_LADO = 2000;   // la foto se reduce en el móvil: se lee igual de bien y sube en un momento

$("btn-pr").addEventListener("click", () => go("pr-foto"));
$("btn-pr-oficina").addEventListener("click", () => {
  $("pr-completar").hidden = true; $("pr-completar-ok").hidden = true; $("pr-buscar").value = "";
  go("pr-oficina");
});
$("pr-otra-foto").addEventListener("click", () => go("pr-foto"));

// ---------- 1. foto ----------
function leerArchivo(file) {
  return new Promise((ok, mal) => {
    const r = new FileReader();
    r.onload = () => ok(String(r.result).split(",")[1]);
    r.onerror = () => mal(new Error("No se ha podido leer el archivo."));
    r.readAsDataURL(file);
  });
}
async function reducir(file) {
  if (file.type === "application/pdf") return { archivo_b64: await leerArchivo(file), tipo: "application/pdf" };
  try {
    const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
    const k = Math.min(1, MAX_LADO / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas");
    c.width = Math.round(bmp.width * k); c.height = Math.round(bmp.height * k);
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    const blob = await new Promise((ok) => c.toBlob(ok, "image/jpeg", 0.85));
    return { archivo_b64: await leerArchivo(blob), tipo: "image/jpeg" };
  } catch {   // formato que el navegador no sabe redibujar: se manda tal cual
    if (file.size > 4_000_000) throw new Error("La foto es demasiado grande. Hazla de nuevo con la cámara de la app.");
    return { archivo_b64: await leerArchivo(file), tipo: file.type || "image/jpeg" };
  }
}
async function alElegir(ev) {
  const file = ev.target.files && ev.target.files[0];
  ev.target.value = "";   // así se puede volver a elegir la misma foto
  if (!file) return;
  clearError();
  busy("Leyendo el albarán…");
  try {
    const r = await post("/api/pr/leer-albaran", await reducir(file));
    pr.viaje = r.viaje; pr.vehiculos = r.vehiculos || null; pr.puntos = r.puntos || [];
    pintarRevision();
    go("pr-revisar");
  } catch (e) { showError(e.message); } finally { busy(null); }
}
$("pr-camara").addEventListener("change", alElegir);
$("pr-archivo").addEventListener("change", alElegir);

// ---------- 2. revisar ----------
// Dígito de control de un contenedor (ISO 6346): 4 letras + 6 cifras + dígito.
function contenedorValido(c) {
  if (!/^[A-Z]{4}\d{7}$/.test(c)) return false;
  const valores = {}; let v = 10;
  for (const l of "ABCDEFGHIJKLMNOPQRSTUVWXYZ") { if (v % 11 === 0) v++; valores[l] = v++; }
  let total = 0;
  for (let i = 0; i < 10; i++) total += (/[A-Z]/.test(c[i]) ? valores[c[i]] : Number(c[i])) * 2 ** i;
  return total % 11 % 10 === Number(c[10]);
}
const val = (id) => $(id).value.trim();
const limpiaPlaca = (t) => t.toUpperCase().replace(/[^A-Z0-9]/g, "");

function pintarRevision() {
  const v = pr.viaje;
  $("pr-punto").replaceChildren(
    h("option", { value: "" }, "— Elige el punto de carga —"),
    ...pr.puntos.map((p) => h("option", { value: p.clave, selected: v.punto && v.punto.clave === p.clave }, p.nombre)));
  $("pr-fecha").value = v.fecha || "";
  $("pr-hora").value = v.hora || "";
  $("pr-albaran").value = v.albaran || "";
  $("pr-tractora").value = v.tractora || "";
  $("pr-remolque").value = v.remolque || "";
  $("pr-bultos").value = v.bultos ?? "";
  $("pr-peso").value = v.peso_kg ? fmtNum(v.peso_kg) : "";
  $("pr-paper").value = v.codigo_paper || "";
  $("pr-contenedor").value = v.contenedor || "";
  pintarAvisos();
}

function pintarAvisos() {
  const v = pr.viaje, caja = $("pr-avisos");
  const lista = [];
  if (!v.punto && !val("pr-punto")) {
    lista.push(h("div", { class: "banner bad" }, "Punto de carga no reconocido" + (v.punto_texto ? ` («${v.punto_texto}»)` : "") +
      ". Si no está en la lista, no se puede guardar: ya se ha avisado a la oficina."));
  }
  const veh = pr.vehiculos;
  for (const clave of ["tractora", "remolque"]) {
    const d = veh && veh[clave];
    if (!d || d.existe || limpiaPlaca(val("pr-" + clave)) !== d.matricula) continue;
    if (d.parecidas.length) {
      lista.push(h("div", { class: "banner warn" }, `La ${clave} ${d.matricula} no está dada de alta. ¿Es alguna de estas?`,
        h("div", { class: "sugg" }, d.parecidas.map((p) => h("button", { type: "button", class: "btn", onclick: () => {
          $("pr-" + clave).value = p; d.matricula = p; d.existe = true; pintarAvisos(); comprobarVehiculos();
        } }, p)))));
    } else {
      lista.push(h("div", { class: "banner warn" }, `La ${clave} ${d.matricula} no está dada de alta. Revísala.`));
    }
  }
  for (const a of v.avisos || []) lista.push(h("div", { class: "banner warn" }, a));
  if ((v.pendientes || []).length) {
    lista.push(h("div", { class: "banner info" }, "Falta en el albarán: " + v.pendientes.join(", ") + ". Se puede guardar igual."));
  }
  caja.replaceChildren(...lista);
  $("pr-colaborador").textContent = veh && veh.colaborador ? "Transportista: " + veh.colaborador : "";
  const punto = val("pr-punto");
  $("pr-cont-campo").hidden = !(punto === "LEOPROEX" || val("pr-contenedor"));
}
$("pr-punto").addEventListener("change", pintarAvisos);

let temporizador = null;
async function comprobarVehiculos() {
  try {
    const q = new URLSearchParams({ tractora: limpiaPlaca(val("pr-tractora")), remolque: limpiaPlaca(val("pr-remolque")) });
    pr.vehiculos = await post("/api/pr/comprobar-vehiculos?" + q, {});
    pintarAvisos();
  } catch { /* sin conexión o en modo de prueba: se comprobará al guardar */ }
}
for (const id of ["pr-tractora", "pr-remolque"]) {
  $(id).addEventListener("input", () => { clearTimeout(temporizador); temporizador = setTimeout(comprobarVehiculos, 700); });
}

function recoger() {
  const punto = val("pr-punto");
  if (!punto) throw new Error("Elige el punto de carga.");
  if (!val("pr-fecha")) throw new Error("Pon la fecha de carga.");
  if (!limpiaPlaca(val("pr-tractora"))) throw new Error("Pon la matrícula de la tractora.");
  const peso = val("pr-peso") ? parseNum(val("pr-peso")) : null;
  if (peso !== null && (Number.isNaN(peso) || peso <= 0)) throw new Error("Revisa el peso.");
  const bultos = val("pr-bultos") ? parseNum(val("pr-bultos")) : null;
  if (bultos !== null && (Number.isNaN(bultos) || !Number.isInteger(bultos))) throw new Error("Revisa las balas.");
  const paper = limpiaPlaca(val("pr-paper"));
  if (paper && !/^S\d{12}$/.test(paper)) throw new Error("El código de carga Paper es una S y 12 cifras (o déjalo en blanco).");
  const cont = limpiaPlaca(val("pr-contenedor"));
  if (cont && !contenedorValido(cont)) throw new Error("El contenedor no es correcto (no cuadra el último dígito). Corrígelo o déjalo en blanco y lo completa la oficina.");
  return { ...pr.viaje, punto: { clave: punto }, fecha: val("pr-fecha"), hora: val("pr-hora"), albaran: val("pr-albaran").replace(/\s/g, ""),
    tractora: limpiaPlaca(val("pr-tractora")), remolque: limpiaPlaca(val("pr-remolque")), bultos, peso_kg: peso,
    codigo_paper: paper, contenedor: limpiaPlaca(val("pr-contenedor")) };
}

$("pr-guardar").addEventListener("click", () => {
  clearError();
  let datos;
  try { datos = recoger(); } catch (e) { return showError(e.message); }
  state.onConfirm = () => guardar(datos);
  $("confirm-txt").textContent = "Se guardará el viaje en el pedido del mes y se generará la orden de transporte.";
  $("confirm").hidden = false;
  $("btn-no").focus();
});

async function guardar(datos) {
  $("confirm").hidden = true;
  busy("Guardando el viaje y generando la orden…");
  try {
    const r = await post("/api/pr/registrar", { viaje: datos });
    pintarHecho(r);
    go("pr-hecho");
  } catch (e) { showError(e.message); } finally { busy(null); state.onConfirm = null; }
}

// ---------- 3. hecho ----------
function pintarHecho(r) {
  const cuerpo = [];
  const mark = h("div", { class: "done-mark" });
  mark.innerHTML = CHECK_SVG;
  if (r.simulado) {
    $("pr-hecho-titulo").textContent = "Prueba sin guardar";
    cuerpo.push(h("div", { class: "banner info" }, "La app está en modo de prueba: no se ha guardado nada."));
  } else if (r.duplicado) {
    $("pr-hecho-titulo").textContent = "Este albarán ya estaba guardado";
    cuerpo.push(h("p", {}, `No se ha creado nada nuevo. Bulto ${r.bulto}.`));
  } else {
    $("pr-hecho-titulo").textContent = "Orden generada";
    cuerpo.push(mark, h("p", {}, `Viaje guardado en el pedido ${r.pedido} (bulto ${r.bulto}).`));
  }
  $("pr-hecho-cuerpo").replaceChildren(...cuerpo);
  const hayPdf = !!(r.pdf && r.pdf.id);
  state.pdfUrl = hayPdf ? `/api/pdf/${r.pdf.id}?token=${encodeURIComponent(r.pdf.token || "")}` : null;
  state.pdfBlob = null; state.pdfLoading = null;
  state.pdfName = `orden-${r.bulto || "PR"}.pdf`;
  if (hayPdf) loadPdf().catch(() => { /* se reintenta al pulsar */ });
  $("pr-abrir").hidden = !hayPdf; $("pr-descargar").hidden = !hayPdf;
  $("pr-compartir").hidden = !(hayPdf && navigator.canShare && navigator.share);
}
$("pr-abrir").addEventListener("click", abrirPdf);
$("pr-descargar").addEventListener("click", descargarPdf);
$("pr-compartir").addEventListener("click", compartirPdf);

// ---------- oficina: completar un viaje ----------
let bultoActual = null;
$("pr-buscar-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  clearError();
  $("pr-completar-ok").hidden = true;
  const q = val("pr-buscar");
  if (!q) return;
  busy("Buscando…");
  try {
    const r = await api("/api/pr/buscar?q=" + encodeURIComponent(q));
    bultoActual = r.bulto;
    const d = r.datos || {};
    $("pr-completar-titulo").replaceChildren(h("span", {}, "Albarán " + (d.albaran || "—")), h("span", { class: "code" }, r.bulto.name));
    $("pr-completar-datos").textContent = [(r.bulto.shipping_origin_id || [])[1], (r.bulto.pickup_date || "").slice(0, 10)].filter(Boolean).join(" · ");
    $("pr-c-paper").value = r.bulto.carrier_tracking_ref || "";
    $("pr-c-bultos").value = r.bulto.number_of_packages || "";
    $("pr-c-peso").value = r.bulto.shipping_weight ? fmtNum(r.bulto.shipping_weight) : "";
    $("pr-c-contenedor").value = "";
    $("pr-completar").hidden = false;
  } catch (e) { $("pr-completar").hidden = true; showError(e.message); } finally { busy(null); }
});
$("pr-completar").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  clearError();
  const b = bultoActual, cambios = {};
  const paper = limpiaPlaca(val("pr-c-paper"));
  if (paper && paper !== (b.carrier_tracking_ref || "")) cambios.codigo_paper = paper;
  const balas = val("pr-c-bultos") ? parseNum(val("pr-c-bultos")) : null;
  if (balas !== null && balas !== b.number_of_packages) cambios.bultos = balas;
  const peso = val("pr-c-peso") ? parseNum(val("pr-c-peso")) : null;
  if (peso !== null && peso !== b.shipping_weight) cambios.peso_kg = peso;
  if (val("pr-c-contenedor")) cambios.contenedor = limpiaPlaca(val("pr-c-contenedor"));
  if (!Object.keys(cambios).length) return showError("No has cambiado nada.");
  busy("Guardando…");
  try {
    const r = await post("/api/pr/actualizar", { bulto_id: b.id, cambios });
    $("pr-completar-ok").textContent = r.simulado ? "Modo de prueba: no se ha guardado nada." :
      `Guardado en ${r.bulto}: ${r.cambiado.join(", ")}.` + (r.pdf ? " La orden de transporte se ha regenerado." : "");
    $("pr-completar-ok").hidden = false;
    Object.assign(b, { carrier_tracking_ref: cambios.codigo_paper ?? b.carrier_tracking_ref,
      number_of_packages: cambios.bultos ?? b.number_of_packages, shipping_weight: cambios.peso_kg ?? b.shipping_weight });
  } catch (e) { showError(e.message); } finally { busy(null); }
});

// ---------- almacén: tren de PR ----------
const tren = { cajas: [], viajes: [], asignado: {}, comp: {} };
const COMPROBACIONES = [["dist_balas", "Balas"], ["lonas", "Lonas"], ["puertas", "Puertas"], ["precinto", "Precinto"]];
const COMP_DEFECTO = { dist_balas: true, lonas: true, puertas: true, precinto: false };   // como en la Excel de siempre

$("btn-pr-tren").addEventListener("click", () => {
  $("tren-paso1").hidden = false; $("tren-paso2").hidden = true; $("tren-hecho").hidden = true;
  go("pr-tren");
});

$("tren-archivo").addEventListener("change", async (ev) => {
  const files = [...(ev.target.files || [])].slice(0, 4);
  ev.target.value = "";
  if (!files.length) return;
  clearError();
  busy("Leyendo el listado del tren…");
  try {
    const archivos = [];
    for (const f of files) archivos.push(await reducir(f));
    const [t, v] = await Promise.all([post("/api/pr/leer-tren", { archivos }), api("/api/pr/viajes")]);
    tren.cajas = t.cajas; tren.viajes = v.viajes.filter((x) => x.estado === "En stock");
    tren.asignado = {}; tren.comp = {};
    $("tren-num").value = v.siguiente_tren;
    $("tren-convoy").value = t.numero_tren || "";
    $("tren-fecha").value = t.fecha || new Date().toISOString().slice(0, 10);
    $("tren-avisos").replaceChildren(...(t.avisos || []).map((a) => h("div", { class: "banner warn" }, a)));
    if (!tren.cajas.length) throw new Error("No se han leído cajas en el listado. Prueba con el PDF o con otra foto.");
    pintarCajas();
    $("tren-paso1").hidden = true; $("tren-paso2").hidden = false;
  } catch (e) { showError(e.message); } finally { busy(null); }
});

const etiquetaViaje = (v) => [v.fecha.slice(8, 10) + "/" + v.fecha.slice(5, 7), v.lugar, v.albaran, v.tractora,
  v.peso_kg ? fmtNum(v.peso_kg) + " kg" : "sin peso"].filter(Boolean).join(" · ");

function pintarCajas() {
  const usados = new Set(Object.values(tren.asignado).filter(Boolean).map(String));
  $("tren-cajas").replaceChildren(...tren.cajas.map((c, i) => {
    const elegido = tren.asignado[i] ? String(tren.asignado[i]) : "";
    const sel = h("select", { "aria-label": `Viaje para la caja ${c.caja_movil}`, onchange: (e) => {
      tren.asignado[i] = e.target.value ? Number(e.target.value) : null;
      if (tren.asignado[i] && !tren.comp[i]) tren.comp[i] = { ...COMP_DEFECTO };
      pintarCajas();
    } },
      h("option", { value: "" }, "— Caja vacía —"),
      ...tren.viajes.map((v) => h("option", { value: v.bulto_id, selected: elegido === String(v.bulto_id),
        disabled: usados.has(String(v.bulto_id)) && elegido !== String(v.bulto_id) }, etiquetaViaje(v))));
    const comp = tren.comp[i];
    return h("div", { class: "pkg caja" + (elegido ? " llena" : "") },
      h("h2", {}, h("span", {}, c.caja_movil), h("span", { class: "code" }, `Vagón ${c.orden} · ${c.vagon}` + (c.tara_kg ? ` · tara ${fmtNum(c.tara_kg)}` : ""))),
      sel,
      comp ? h("div", { class: "checks" }, COMPROBACIONES.map(([k, t]) => h("button", { type: "button",
        class: "btn chk " + (comp[k] ? "si" : "no"), "aria-pressed": comp[k] ? "true" : "false",
        onclick: () => { comp[k] = !comp[k]; pintarCajas(); } }, `${t} ${comp[k] ? "OK" : "NO"}`))) : null);
  }));
  const n = Object.values(tren.asignado).filter(Boolean).length;
  const quedan = tren.viajes.length - n;
  $("tren-cuenta").textContent = `${n} de ${tren.cajas.length} cajas cargadas · ` + (quedan === 1 ? "1 viaje queda" : `${quedan} viajes quedan`) +
    " en stock para el siguiente tren.";
}

$("tren-guardar").addEventListener("click", () => {
  clearError();
  const num = parseNum(val("tren-num"));
  if (!Number.isInteger(num) || num <= 0) return showError("Pon el número de tren.");
  if (!val("tren-fecha")) return showError("Pon la fecha del tren.");
  if (!Object.values(tren.asignado).some(Boolean)) return showError("No has asignado ningún viaje a ninguna caja.");
  state.onConfirm = () => guardarTren(num);
  $("confirm-txt").textContent = `Se guardará el tren ${num} en Odoo y se generarán y enviarán las dos Excel.`;
  $("confirm").hidden = false;
  $("btn-no").focus();
});

async function guardarTren(num) {
  $("confirm").hidden = true;
  busy("Guardando el tren y preparando las Excel…");
  try {
    const cajas = tren.cajas.map((c, i) => ({ ...c, bulto_id: tren.asignado[i] || null, comprobaciones: tren.comp[i] || {} }));
    const r = await post("/api/pr/tren/guardar", { tren: num, convoy: val("tren-convoy"), fecha: val("tren-fecha"), cajas });
    $("tren-paso2").hidden = true; $("tren-hecho").hidden = false;
    const mark = h("div", { class: "done-mark" });
    mark.innerHTML = CHECK_SVG;
    $("tren-hecho-cuerpo").replaceChildren(
      r.simulado ? h("div", { class: "banner info" }, "Modo de prueba: no se ha guardado nada en Odoo.") : mark,
      h("p", {}, `Tren ${num}: ${r.bultos.length} viajes cargados. Quedan ${r.en_stock} en stock.`),
      h("p", { class: "hint" }, r.enviado ? "Las Excel se han guardado en SharePoint y se han enviado por correo." :
        "Descarga las Excel aquí (el envío automático por correo no está configurado o ha fallado)."));
    $("tren-descargas").replaceChildren(...r.archivos.map((a) => h("button", { class: "btn secondary", onclick: () => {
      const bytes = Uint8Array.from(atob(a.b64), (ch) => ch.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }));
      const link = h("a", { href: url, download: a.nombre });
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } }, "Descargar " + a.nombre)));
  } catch (e) { showError(e.message); } finally { busy(null); state.onConfirm = null; }
}
