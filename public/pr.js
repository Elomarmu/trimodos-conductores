"use strict";
// Viajes de PR (SAICA): foto del albarán → revisar → guardar en Odoo y descargar la orden de transporte.
// Usa las utilidades de app.js ($, h, post, api, busy, showError, go, state, loadPdf, abrirPdf…).

const pr = { viaje: null, vehiculos: null, puntos: [], oficina: false };   // oficina: entra desde «Oficina · PR»
const MAX_LADO = 2000;   // la foto se reduce en el móvil: se lee igual de bien y sube en un momento

$("btn-pr").addEventListener("click", () => { pr.oficina = false; go("pr-foto"); });
$("of-registrar").addEventListener("click", () => { pr.oficina = true; go("pr-foto"); });
document.addEventListener("click", (e) => { if (e.target.closest(".pr-volver")) go(pr.oficina ? "pr-oficina" : "menu"); });
$("btn-pr-oficina").addEventListener("click", () => go("pr-oficina"));
$("btn-pr-almacen").addEventListener("click", () => abrirAlmacen(false));
$("of-tren").addEventListener("click", () => {
  $("tren-paso1").hidden = false; $("tren-paso2").hidden = true; $("tren-hecho").hidden = true;
  go("pr-tren");
});
$("of-asignar").addEventListener("click", () => abrirAlmacen(true));
$("of-completar").addEventListener("click", () => {
  pr.oficina = true;
  $("pr-completar").hidden = true; $("pr-completar-ok").hidden = true; $("pr-buscar").value = "";
  $("pr-m-avisos").replaceChildren();
  go("pr-completar");
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
  $("pr-m-avisos").replaceChildren();
  const q = val("pr-buscar");
  if (!q) return;
  busy("Buscando…");
  try { mostrarCarga(await api("/api/pr/buscar?q=" + encodeURIComponent(q))); }
  catch (e) { $("pr-completar").hidden = true; showError(e.message); } finally { busy(null); }
});

// Modificar con la foto: se lee el albarán, se busca su viaje y se proponen los datos leídos (con lo que hay ahora en Odoo al lado).
async function modificarConFoto(ev) {
  const file = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  clearError();
  $("pr-completar-ok").hidden = true;
  $("pr-m-avisos").replaceChildren();
  busy("Leyendo el albarán…");
  try {
    const leido = await post("/api/pr/leer-albaran", await reducir(file));
    const v = leido.viaje;
    let r = null;
    for (const q of [v.albaran, v.codigo_paper].filter(Boolean)) {
      try { r = await api("/api/pr/buscar?q=" + encodeURIComponent(q)); break; } catch (e) { if (e.code !== "no_encontrado") throw e; }
    }
    if (!r) {
      $("pr-completar").hidden = true;
      $("pr-m-avisos").replaceChildren(h("div", { class: "banner warn" },
        `El albarán ${v.albaran || "(sin número)"} no está registrado todavía.`,
        h("div", { class: "sugg" }, h("button", { type: "button", class: "btn", onclick: () => {
          pr.viaje = v; pr.vehiculos = leido.vehiculos || null; pr.puntos = leido.puntos || []; pr.oficina = true;
          pintarRevision(); go("pr-revisar");
        } }, "Registrarlo ahora"))));
      return;
    }
    mostrarCarga(r);
    $("pr-buscar").value = v.albaran || "";
    // lo leído en la foto sustituye a lo que hay en Odoo (se ve al lado para revisarlo antes de guardar)
    const propuesta = [["pr-c-paper", v.codigo_paper, r.bulto.carrier_tracking_ref], ["pr-c-bultos", v.bultos, r.bulto.number_of_packages],
      ["pr-c-peso", v.peso_kg, r.bulto.shipping_weight], ["pr-c-contenedor", v.contenedor, ""]];
    const cambios = [];
    for (const [id, leidoV, actual] of propuesta) {
      if (leidoV == null || leidoV === "" || String(leidoV) === String(actual || "")) continue;
      $(id).value = id === "pr-c-peso" ? fmtNum(leidoV) : leidoV;
      cambios.push(`${$(id).labels[0].textContent}: ${actual ? (id === "pr-c-peso" ? fmtNum(actual) : actual) : "vacío"} → ${$(id).value}`);
    }
    $("pr-m-avisos").replaceChildren(
      h("div", { class: "banner info" }, cambios.length ? "Leído en el albarán (revisa y pulsa Guardar): " + cambios.join(" · ") :
        "Lo leído en el albarán coincide con lo que hay en Odoo."),
      ...(v.avisos || []).map((a) => h("div", { class: "banner warn" }, a)));
  } catch (e) { showError(e.message); } finally { busy(null); }
}
$("pr-m-camara").addEventListener("change", modificarConFoto);
$("pr-m-archivo").addEventListener("change", modificarConFoto);

function mostrarCarga(r) {
  {
    bultoActual = r.bulto;
    const d = r.datos || {};
    $("pr-completar-titulo").replaceChildren(h("span", {}, "Albarán " + (d.albaran || "—")), h("span", { class: "code" }, r.bulto.name));
    $("pr-completar-datos").textContent = [(r.bulto.shipping_origin_id || [])[1], (r.bulto.pickup_date || "").slice(0, 10)].filter(Boolean).join(" · ");
    $("pr-c-paper").value = r.bulto.carrier_tracking_ref || "";
    $("pr-c-bultos").value = r.bulto.number_of_packages || "";
    $("pr-c-peso").value = r.bulto.shipping_weight ? fmtNum(r.bulto.shipping_weight) : "";
    $("pr-c-contenedor").value = "";
    $("pr-completar").hidden = false;
  }
}
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

// ---------- oficina: composición del tren ----------
let composicion = null;
$("tren-otra").addEventListener("click", () => { $("tren-paso1").hidden = false; $("tren-paso2").hidden = true; });
$("tren-archivo").addEventListener("change", async (ev) => {
  const files = [...(ev.target.files || [])].slice(0, 4);
  ev.target.value = "";
  if (!files.length) return;
  clearError();
  busy("Leyendo el listado del tren…");
  try {
    const archivos = [];
    for (const f of files) archivos.push(await reducir(f));
    const [t, v] = await Promise.all([post("/api/pr/leer-tren", { archivos }), api("/api/pr/viajes").catch(() => ({}))]);
    if (!t.cajas.length) throw new Error("No se han leído cajas en el listado. Prueba con el PDF o con otra foto.");
    composicion = t;
    $("tren-num").value = v.siguiente_tren || "";
    $("tren-convoy").value = t.numero_tren || "";
    $("tren-fecha").value = t.fecha || new Date().toISOString().slice(0, 10);
    $("tren-avisos").replaceChildren(...(t.avisos || []).map((a) => h("div", { class: "banner warn" }, a)));
    $("tren-cajas").replaceChildren(...t.cajas.map((c) => h("div", { class: "fila" },
      h("span", { class: "code" }, c.segunda ? "“" : `Vagón ${c.orden} · ${c.vagon}`),
      h("b", {}, c.caja_movil), h("span", { class: "code" }, c.tara_kg ? `tara ${fmtNum(c.tara_kg)}` : "tara ¿?"))));
    $("tren-paso1").hidden = true; $("tren-paso2").hidden = false;
  } catch (e) { showError(e.message); } finally { busy(null); }
});
$("tren-guardar").addEventListener("click", () => {
  clearError();
  const num = parseNum(val("tren-num"));
  if (!Number.isInteger(num) || num <= 0) return showError("Pon el número de tren.");
  if (!val("tren-fecha")) return showError("Pon la fecha del tren.");
  state.onConfirm = () => guardarComposicion(num);
  $("confirm-txt").textContent = `Se guardará el tren ${num} con ${composicion.cajas.length} cajas para que el almacén asigne las cargas.`;
  $("confirm").hidden = false;
  $("btn-no").focus();
});
async function guardarComposicion(num) {
  $("confirm").hidden = true;
  busy("Guardando el tren…");
  try {
    const r = await post("/api/pr/tren/composicion", { tren: num, convoy: val("tren-convoy"), fecha: val("tren-fecha"), cajas: composicion.cajas });
    const mark = h("div", { class: "done-mark" });
    mark.innerHTML = CHECK_SVG;
    $("tren-hecho-cuerpo").replaceChildren(r.simulado ? h("div", { class: "banner info" }, "Modo de prueba: no se ha guardado nada.") : mark,
      h("p", {}, `Tren ${num} guardado (${composicion.cajas.length} cajas). El almacén ya puede asignar las cargas.`),
      r.simulado ? null : h("p", { class: "hint" }, r.enviado ? `Guardada en SharePoint en ${r.excel} (hoja COMPOSICION).` :
        "No se ha podido guardar la Excel en SharePoint (el envío automático no está configurado o ha fallado)."));
    $("tren-paso2").hidden = true; $("tren-hecho").hidden = false;
  } catch (e) { showError(e.message); } finally { busy(null); state.onConfirm = null; }
}
$("tren-ir-asignar").addEventListener("click", () => abrirAlmacen(true));

// ---------- asignar cargas a cajas (almacén; también la oficina) ----------
const alm = { estado: null, oficina: false };
const fechaEs = (f) => (f || "").split("-").reverse().join("-");
const textoCaja = (c) => `${c.caja_movil} - ${c.vagon}`;
const textoCarga = (g) => [g.codigo_paper || `sin código (albarán ${g.albaran || "?"})`, g.bultos != null ? g.bultos : "¿?",
  g.peso_kg ? fmtNum(g.peso_kg) + " kg" : "¿? kg"].join(" - ");

async function abrirAlmacen(desdeOficina) {
  alm.oficina = desdeOficina;
  $("alm-enviar").hidden = !desdeOficina;
  $("alm-descargas").replaceChildren();
  go("pr-almacen");
  await cargarAlmacen();
}
$("alm-volver").addEventListener("click", () => go(alm.oficina ? "pr-oficina" : "menu"));
$("alm-recargar").addEventListener("click", cargarAlmacen);

async function cargarAlmacen() {
  clearError();
  busy("Cargando el tren…");
  try { pintarAlmacen(await api("/api/pr/tren/actual")); }
  catch (e) { alm.estado = null; $("alm-tren").textContent = e.message; $("alm-form").hidden = true; $("alm-hechas").replaceChildren(); }
  finally { busy(null); }
}

function pintarAlmacen(e) {
  alm.estado = e;
  $("alm-form").hidden = false;
  $("alm-tren").textContent = `Está cargado el tren ${e.tren} de fecha ${fechaEs(e.fecha)}` + (e.convoy ? ` (convoy ${e.convoy})` : "") + ".";
  const libres = e.cajas.filter((c) => !c.carga), llenas = e.cajas.filter((c) => c.carga);
  $("alm-caja").replaceChildren(h("option", { value: "" }, libres.length ? "— Elige la caja —" : "— No quedan cajas libres —"),
    ...libres.map((c) => h("option", { value: c.caja_movil }, textoCaja(c))));
  $("alm-carga").replaceChildren(h("option", { value: "" }, e.stock.length ? "— Elige la carga —" : "— No hay cargas en stock —"),
    ...e.stock.map((g) => h("option", { value: g.bulto_id }, textoCarga(g))));
  $("alm-cuenta").textContent = `${llenas.length} de ${e.cajas.length} cajas cargadas · ${libres.length} libres · ` +
    (e.stock.length === 1 ? "1 carga en stock." : `${e.stock.length} cargas en stock.`);
  $("alm-hechas-titulo").hidden = !llenas.length;
  $("alm-hechas").replaceChildren(...llenas.map((c) => h("div", { class: "fila" },
    h("span", {}, h("b", {}, textoCaja(c)), h("br"), h("span", { class: "code" }, textoCarga(c.carga))),
    h("button", { type: "button", class: "btn ghost quitar", onclick: () => quitarCarga(c) }, "Quitar"))));
}

$("alm-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  clearError();
  const caja = $("alm-caja").value, carga = Number($("alm-carga").value);
  if (!caja) return showError("Elige la caja móvil.");
  if (!carga) return showError("Elige la carga.");
  busy("Guardando…");
  try {
    const r = await post("/api/pr/tren/asignar", { bulto_id: carga, caja_movil: caja });
    if (r.simulado) showError("Modo de prueba: no se ha guardado nada."); else pintarAlmacen(r);
  } catch (e) { showError(e.message); await cargarAlmacen(); } finally { busy(null); }
});

async function quitarCarga(c) {
  state.onConfirm = async () => {
    $("confirm").hidden = true;
    busy("Quitando…");
    try { const r = await post("/api/pr/tren/quitar", { bulto_id: c.carga.bulto_id, caja_movil: c.caja_movil }); if (!r.simulado) pintarAlmacen(r); }
    catch (e) { showError(e.message); } finally { busy(null); state.onConfirm = null; }
  };
  $("confirm-txt").textContent = `La carga ${textoCarga(c.carga)} sale de la caja ${c.caja_movil} y vuelve a stock.`;
  $("confirm").hidden = false;
  $("btn-no").focus();
}

$("alm-enviar").addEventListener("click", () => {
  clearError();
  state.onConfirm = enviarTren;
  $("confirm-txt").textContent = `Se generarán las dos Excel del tren ${alm.estado ? alm.estado.tren : ""}, se guardarán en SharePoint y se enviarán por correo.`;
  $("confirm").hidden = false;
  $("btn-no").focus();
});
async function enviarTren() {
  $("confirm").hidden = true;
  busy("Preparando las Excel…");
  try {
    const r = await post("/api/pr/tren/enviar", {});
    $("alm-descargas").replaceChildren(
      h("div", { class: "banner info" }, `Tren ${r.tren}: ${r.cargas} cargas, ${r.en_stock} en stock. ` +
        (r.enviado ? "Excel guardadas en SharePoint y enviadas por correo." : "Descárgalas aquí (el envío automático no está configurado o ha fallado).")),
      ...r.archivos.map((a) => h("button", { class: "btn secondary", onclick: () => {
        const bytes = Uint8Array.from(atob(a.b64), (ch) => ch.charCodeAt(0));
        const url = URL.createObjectURL(new Blob([bytes], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }));
        const link = h("a", { href: url, download: a.nombre });
        document.body.append(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 60000);
      } }, "Descargar " + a.nombre)));
  } catch (e) { showError(e.message); } finally { busy(null); state.onConfirm = null; }
}

// Accesos directos: …/#almacen abre la asignación de cajas; …/#oficina el menú de oficina.
function accesoDirecto() {
  if (location.hash === "#almacen") abrirAlmacen(false);
  else if (location.hash === "#oficina") go("pr-oficina");
}
window.addEventListener("load", accesoDirecto);
window.addEventListener("hashchange", accesoDirecto);
