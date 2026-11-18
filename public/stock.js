"use strict";
// Stock del almacén de Burriana: entradas (albarán → etiquetas de los palets), salidas (petición → palets cargados),
// stock actual y clientes. Solo en la app de oficina y almacén. Usa las utilidades de app.js y pr.js
// ($, h, post, api, busy, showError, clearError, go, state, fechaEs, reducir, escanearDocumento).

const stk = { clientes: [], entrada: null, salida: null, albaran: null, todas: false, ssTodas: false };

const tipoTxt = { pendiente: "Pendiente de descarga", descargando: "Descargando", cerrada: "Cerrada", pedida: "Pedida", preparando: "Preparando", cargada: "Cargada" };
const n = (x) => (x == null ? "—" : fmtNum(x));
// descargarXlsx({nombre, b64}) viene de pr.js
// Los errores se ven siempre; las advertencias (amarillas) van plegadas tras una flechita para que no ocupen la pantalla.
const avisosHtml = (lista) => {
  const caja = (a) => h("div", { class: "banner " + (a.nivel === "error" ? "bad" : "warn") }, a.texto);
  const errores = lista.filter((a) => a.nivel === "error").map(caja), avisos = lista.filter((a) => a.nivel !== "error");
  if (!avisos.length) return errores;
  return [...errores, h("details", { class: "avisos-plegados" },
    h("summary", {}, `⚠ ${avisos.length} advertencia${avisos.length === 1 ? "" : "s"} (toca para ver)`), ...avisos.map(caja))];
};
async function conCarga(msg, fn) {
  clearError(); busy(msg);
  try { return await fn(); } catch (e) { showError(e.message); return null; } finally { busy(null); }
}
// Pregunta «¿Seguro?» con el texto que se quiera; si dice que sí, ejecuta `fn`.
function confirmar(texto, boton, fn) {
  $("confirm-txt").textContent = texto;
  $("confirm-txt").style.whiteSpace = "pre-line";
  $("btn-yes").textContent = boton;
  state.onConfirm = async () => { $("confirm").hidden = true; state.onConfirm = null; await fn(); };
  $("confirm").hidden = false;
  $("btn-no").focus();
}
$("btn-no").addEventListener("click", () => { $("confirm-txt").style.whiteSpace = ""; $("btn-yes").textContent = "Sí, guardar"; state.onConfirm = null; });
$("btn-yes").addEventListener("click", () => { $("confirm-txt").style.whiteSpace = ""; setTimeout(() => { $("btn-yes").textContent = "Sí, guardar"; }, 0); });

// ---------- menú ----------
$("btn-stock").addEventListener("click", () => go("stock"));
$("st-entradas").addEventListener("click", () => { go("stock-entradas"); cargarEntradas(); });
$("st-salidas").addEventListener("click", () => { go("stock-salidas"); cargarSalidas(); });
$("st-stock").addEventListener("click", () => { go("stock-actual"); cargarStock(); });
$("st-clientes").addEventListener("click", () => { go("stock-clientes"); cargarClientes(); });

// ---------- clientes ----------
async function cargarClientes() {
  const r = await conCarga("Cargando los clientes…", () => api("/api/stock/clientes"));
  if (!r) return;
  stk.clientes = r.clientes;
  stk.cliEdit = null;
  $("sc-lista").replaceChildren(...(r.clientes.length ? r.clientes.map((c) => h("button", { type: "button", class: "btn secondary", onclick: () => editarCliente(c) },
    `${c.nombre} · ${(c.referencias || []).length} referencias`))
    : [h("p", { class: "hint" }, "Todavía no hay clientes: da de alta el primero para poder cargar entradas.")]));
}
function editarCliente(c) {
  stk.cliEdit = c.codigo;
  $("sc-nombre").value = c.nombre; $("sc-alias").value = (c.alias || []).join(", ");
  $("sc-refs").value = (c.referencias || []).map((r) => `${r.ref};${r.descripcion || ""}` + ((r.alias || []).length ? ";" + r.alias.join("/") : "")).join("\n");
  $("sc-porpalet").checked = !!c.salida_por_palet;
  const av = c.aviso_llegada || {};
  $("sc-aviso").checked = !!av.activo; $("sc-contacto").value = av.contacto || ""; $("sc-correo").value = av.correo || "";
  avisoCampos();
  $("sc-guardar").textContent = "Guardar los cambios de " + c.nombre;
  $("sc-nombre").scrollIntoView({ behavior: "smooth" });
}
$("sc-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const nombre = $("sc-nombre").value.trim();
  if (nombre.length < 2) return showError("Escribe el nombre del cliente.");
  const referencias = $("sc-refs").value.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
    const [ref, desc, eq] = l.split(";");   // referencia;descripción;códigos equivalentes (separados por /)
    return { ref: ref.trim(), descripcion: (desc || "").trim(), alias: (eq || "").split("/").map((x) => x.trim()).filter(Boolean) };
  });
  const aviso = { activo: $("sc-aviso").checked, contacto: $("sc-contacto").value.trim(), correo: $("sc-correo").value.trim() };
  const r = await conCarga("Guardando…", () => post("/api/stock/clientes", { codigo: stk.cliEdit || "", nombre, referencias, salida_por_palet: $("sc-porpalet").checked, aviso_llegada: aviso,
    alias: $("sc-alias").value.split(",").map((s) => s.trim()).filter(Boolean) }));
  if (r) {
    for (const i of ["sc-nombre", "sc-alias", "sc-refs", "sc-contacto", "sc-correo"]) $(i).value = "";
    $("sc-porpalet").checked = false; $("sc-aviso").checked = false; avisoCampos();
    $("sc-guardar").textContent = "Guardar el cliente"; cargarClientes();
  }
});
const avisoCampos = () => { $("sc-aviso-campos").hidden = !$("sc-aviso").checked; };
$("sc-aviso").addEventListener("change", avisoCampos);
avisoCampos();
function opcionesClientes(sel, elegido) {
  sel.replaceChildren(h("option", { value: "" }, "— Elige el cliente —"), ...stk.clientes.map((c) => h("option", { value: c.codigo, selected: c.codigo === elegido }, c.nombre)));
}

// ---------- entradas: lista ----------
async function cargarEntradas() {
  const r = await conCarga("Cargando las entradas…", () => api("/api/stock/entradas" + (stk.todas ? "" : "?abiertas=true")));
  if (!r) return;
  $("se-todas").textContent = stk.todas ? "Ver solo las que faltan" : "Ver también las cerradas";
  $("se-lista").replaceChildren(...(r.entradas.length ? r.entradas.map((e) => h("button", { type: "button", class: "btn secondary", onclick: () => abrirEntrada(e.id) },
    (e.tipo === "inventario" ? `📦 Inventario ${e.id} · ${e.cliente.nombre} · ${e.verificados} palets · ${tipoTxt[e.estado]}` : `${e.cliente.nombre} · albarán ${e.albaran || "—"} · ${e.verificados}/${e.esperados} palets · ${tipoTxt[e.estado]}`) + (e.discrepancias ? ` · ⚠ ${e.discrepancias}` : "") + (e.por_revisar ? " · 📧 POR REVISAR" : "")))
    : [h("p", { class: "hint" }, "No hay entradas pendientes.")]));
}
$("se-anular").addEventListener("click", () => {
  const e = stk.entrada;
  confirmar(`¿Anular la entrada ${e.id}${e.albaran ? " (albarán " + e.albaran + ")" : ""}?` + (e.estado === "cerrada" ? "\nYa está cerrada: sus palets dejarán de contar en el inventario. El correo que se envió al cerrarla no se puede retirar." : "\nSus palets no llegarán a entrar en el inventario."),
    "Sí, anularla", async () => {
      const r = await conCarga("Anulando…", () => post(`/api/stock/entradas/${encodeURIComponent(e.id)}/anular`, {}));
      if (r) { go("stock-entradas"); cargarEntradas(); }
    });
});
$("se-todas").addEventListener("click", () => { stk.todas = !stk.todas; cargarEntradas(); });

// ---------- entradas: foto del albarán (oficina) ----------
async function alElegirAlbaran(ev) {
  const files = [...(ev.target.files || [])].slice(0, 4);
  ev.target.value = "";
  if (!files.length) return;
  clearError();
  const fotos = [];
  try {
    for (const f of files) {
      const foto = files.length === 1 ? await escanearDocumento(f) : await reducir(f);
      if (!foto) return;   // ha cancelado el encuadre
      fotos.push(foto);
    }
  } catch (e) { return showError(e.message); }
  const r = await conCarga("Leyendo el albarán…", () => post("/api/stock/entradas/leer", { archivos: fotos }));
  if (!r) return;
  stk.albaran = { datos: r.albaran, fotos, esperados: r.esperados || [], fechaEntrada: r.fecha_entrada };
  stk.clientes = r.clientes;
  pintarAlbaran(r);
  go("stock-entrada-nueva");
}
$("se-camara").addEventListener("change", alElegirAlbaran);
$("se-archivo").addEventListener("change", alElegirAlbaran);

function filaLinea(l, campos, borrar) {
  // Cada línea es una cuadrícula de dos columnas: referencia y descripción a todo el ancho; el resto (palets, kg) a medias.
  const inp = ([k, ph, ancho, modo]) => h("label", { class: "hint", style: ancho === "full" ? "grid-column:1/-1" : "" }, ph,
    h("input", { type: "text", "data-k": k, value: l[k] ?? "", inputmode: modo || null, autocomplete: "off" }));
  const medias = campos.filter((c) => c[2] !== "full").length;
  return h("div", { class: "fila", style: "display:grid;grid-template-columns:1fr 1fr;gap:6px;align-items:center" }, ...campos.map(inp),
    h("button", { type: "button", class: "btn ghost quitar", style: medias % 2 ? "" : "grid-column:1/-1",
      onclick: (ev) => { ev.target.closest(".fila").remove(); borrar && borrar(); } }, "Quitar línea"));
}
const leerFilas = (cont, numericos) => [...$(cont).children].map((f) => {
  const o = {};
  for (const i of f.querySelectorAll("input")) { const k = i.dataset.k; const v = i.value.trim(); o[k] = numericos.includes(k) ? (v === "" ? null : Math.round(parseNum(v))) : v; }
  return o;
}).filter((o) => Object.values(o).some((v) => v !== "" && v != null));

const CAMPOS_ALBARAN = [["ref", "Referencia", "full"], ["descripcion", "Descripción", "full"], ["palets", "Palets", "half", "numeric"], ["kg", "Kg", "half", "numeric"]];
function pintarAlbaran(r) {
  const a = r.albaran;
  $("sen-avisos").replaceChildren(...(r.avisos.length ? avisosHtml(r.avisos) : [h("div", { class: "banner info" }, "Sin conflictos: el albarán parece correcto.")]));
  opcionesClientes($("sen-cliente"), r.cliente_sugerido);
  $("sen-final").value = [a.cliente_final, a.codigo_cliente_final].filter(Boolean).join(" · ");
  $("sen-fentrada").value = stk.albaran.fechaEntrada || a.fecha;
  $("sen-numero").value = a.numero; $("sen-fecha").value = a.fecha; $("sen-matricula").value = a.matricula; $("sen-transportista").value = a.transportista;
  $("sen-lineas").replaceChildren(...a.lineas.map((l) => filaLinea(l, CAMPOS_ALBARAN, totalAlbaran)));
  totalAlbaran();
}
function totalAlbaran() {
  const ls = leerFilas("sen-lineas", ["palets", "kg"]);
  $("sen-total").textContent = `${ls.reduce((s, l) => s + (l.palets || 0), 0)} palets en ${ls.length} líneas.`;
}
$("sen-mas").addEventListener("click", () => $("sen-lineas").append(filaLinea({}, CAMPOS_ALBARAN, totalAlbaran)));
$("sen-lineas").addEventListener("input", totalAlbaran);
$("sen-form").addEventListener("submit", (ev) => { ev.preventDefault(); guardarEntrada(false); });
async function guardarEntrada(confirmado) {
  const cliente = $("sen-cliente").value;
  if (!cliente) return showError("Elige el cliente de la lista (si es nuevo, dalo de alta en «Clientes del almacén»).");
  const base = stk.albaran.datos;
  const albaran = { ...base, cliente_final: $("sen-final").value.trim(), numero: $("sen-numero").value.trim(), fecha: $("sen-fecha").value, matricula: $("sen-matricula").value.trim(),
    transportista: $("sen-transportista").value.trim(), lineas: leerFilas("sen-lineas", ["palets", "kg", "bultos"]).map((l) => ({ lote: "", bultos: null, ...l })) };
  clearError(); busy("Guardando la entrada…");
  try {
    const e = await post("/api/stock/entradas", { cliente, albaran, fotos: stk.albaran.fotos, esperados: stk.albaran.esperados || [], fecha_entrada: $("sen-fentrada").value, confirmado });
    busy(null);
    stk.entrada = e;
    pintarEntrada(e);
    go("stock-entrada");
  } catch (err) {
    busy(null);
    if (err.code === "entrada_conflicto") confirmar(err.message, "Sí, guardarla", () => guardarEntrada(true));
    else showError(err.message);
  }
}

// Botones de cada palet, pequeños y juntos: 📝 corregir (solo entradas), ⚠️ incidencia (con su número si ya hay) y Quitar.
function accionesPalet(p, f) {
  const n = (p.incidencias || []).length;
  return h("span", { class: "acciones" },
    f.corregir ? h("button", { type: "button", class: "btn ghost mini", title: "Corregir los datos del palet", "aria-label": "Corregir los datos del palet", onclick: f.corregir }, "📝") : null,
    h("button", { type: "button", class: "btn ghost mini", title: "Incidencia del bulto", "aria-label": "Incidencia del bulto" + (n ? ` (${n})` : ""), onclick: f.incidencia }, n ? `⚠️${n}` : "⚠️"),
    h("button", { type: "button", class: "btn ghost quitar", onclick: f.quitar }, "Quitar"));
}

// ---------- entradas: descarga con etiquetas (almacén) ----------
async function abrirEntrada(id) {
  const e = await conCarga("Cargando la entrada…", () => api("/api/stock/entradas/" + encodeURIComponent(id)));
  if (!e) return;
  pintarEntrada(e);
  go("stock-entrada");
}
function pintarEntrada(e) {
  stk.entrada = e;
  const cerrada = e.estado === "cerrada";
  const inv = e.tipo === "inventario";
  $("se-titulo").textContent = inv ? `Entrada manual de inventario ${e.id}` : `Entrada ${e.id}`;
  $("se-m-sincod-campo").hidden = !inv || cerrada;
  $("se-lineas-titulo").textContent = inv ? "Referencias apuntadas" : "Lo que dice el albarán";
  $("se-cerrar").textContent = inv ? "Terminar la entrada de inventario" : "Terminar la descarga";
  $("se-barras").textContent = inv ? "Leer el código de barras de un palet" : "Escanear códigos de barras";
  $("se-cabecera").textContent = inv ? `${e.cliente.nombre} · en el almacén desde ${fechaEs(e.fecha_entrada)} · ${e.verificados} palets apuntados` + (cerrada ? " · CERRADA" : "")
    : `${e.cliente.nombre}${e.cliente_final ? " (para " + e.cliente_final + ")" : ""} · albarán ${e.albaran || "—"} del ${fechaEs(e.fecha)} · entrada en almacén ${fechaEs(e.fecha_entrada)} · ${e.verificados} de ${e.esperados} palets verificados` + (cerrada ? " · CERRADA" : "");
  document.querySelector('label[for="se-etq-camara"]').hidden = cerrada;
  $("se-barras").hidden = cerrada;
  $("se-ubic-campo").hidden = cerrada;
  if (stk.ubicEntrada !== e.id) {
    $("se-ubic").replaceChildren(h("option", { value: "" }, "— Sin ubicación —"),
      ...(e.ubicaciones_validas || []).map((g) => h("optgroup", { label: g.grupo }, ...g.zonas.map((z) => h("option", { value: z }, z)))));
    $("se-ubic").value = e.ubicacion || ""; stk.ubicEntrada = e.id;
  }   // la que se escribe se conserva mientras se sigue en la misma entrada
  pintarListaEntrada(e);
  $("se-cierre").hidden = cerrada;
  $("se-revisar").hidden = !e.por_revisar;
  $("se-revisar-avisos").replaceChildren(...avisosHtml(e.avisos || []));
  $("se-fentrada").value = e.fecha_entrada || ""; $("se-obs").value = e.observaciones || "";
  $("se-manual").closest("details").hidden = cerrada;
  $("se-lineas").replaceChildren(...e.lineas.map((l) => h("div", { class: "fila" },
    h("span", {}, h("b", {}, l.ref || l.descripcion), l.ref && l.descripcion ? " · " + l.descripcion : "", h("br"),
      h("span", { class: "hint" }, inv ? `${l.verificados} palets${l.kg_etiquetas ? " · " + n(l.kg_etiquetas) + " kg" : ""}` : `Albarán ${l.esperados} palets${l.kg_albaran ? " · " + n(l.kg_albaran) + " kg" : ""}`)),
    inv ? null : h("span", { class: l.verificados === l.esperados ? "code" : "nota" }, `${l.verificados}/${l.esperados}`))));
  $("se-palets-titulo").hidden = !e.palets.length;
  $("se-palets").replaceChildren(...e.palets.map((p) => h("div", { class: "fila" },
    h("span", {}, h("b", {}, p.ref || "¿?"), p.lote ? ` · lote ${p.lote}` : "", p.kg ? ` · ${n(p.kg)} kg` : "", p.fecha_prod ? ` · prod. ${fechaEs(p.fecha_prod)}` : "", h("br"),
      h("span", { class: "hint" }, p.id + (p.sin_codigo ? " · SIN CÓDIGO" : p.sscc ? " · " + p.sscc : "") + (p.origen === "barras" ? " · escaneado" : p.manual ? " · a mano" : "")),
      h("br"), h("b", {}, p.ubicacion ? "📍 " + p.ubicacion : "📍 sin ubicación"),
      p.estado === "discrepancia" ? h("br") : null, p.estado === "discrepancia" ? h("span", { class: "nota" }, "⚠ " + p.motivo) : null),
    cerrada ? null : accionesPalet(p, { corregir: () => corregirPaletEntrada(p), incidencia: () => abrirIncidencia("entrada", p.id, p), quitar: () => quitarPaletEntrada(p) }))));
  $("se-descargas").replaceChildren();
}
$("se-etq-camara").addEventListener("change", async (ev) => {
  const file = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  const r = await conCarga("Leyendo la etiqueta…", async () => post(`/api/stock/entradas/${encodeURIComponent(stk.entrada.id)}/etiqueta`, { foto: await reducir(file), ubicacion: $("se-ubic").value.trim() }));
  if (!r) return;
  pintarEntrada(r.entrada);
  const p = r.palet;
  $("se-aviso-palet").replaceChildren(h("div", { class: "banner " + (p.estado === "ok" ? "info" : "warn") },
    p.estado === "ok" ? `✓ ${p.ref} · lote ${p.lote || "—"} · ${n(p.kg)} kg: coincide con el albarán.` : "⚠ " + p.motivo));
});
// Lista desplegable: los palets de la lista del albarán que todavía no se han apuntado. Al elegir uno se rellena el formulario.
function pintarListaEntrada(e) {
  const pend = e.pendientes || [];
  $("se-lista-campo").hidden = !pend.length || e.estado === "cerrada";
  $("se-m-lista").replaceChildren(h("option", { value: "" }, `— Elegir (${pend.length} sin apuntar) —`),
    ...pend.map((x) => h("option", { value: x.codigo }, `${x.codigo} · ${x.ref || "¿?"}${x.kg ? " · " + n(x.kg) + " kg" : ""}`)));
}
$("se-m-lista").addEventListener("change", () => {
  const x = (stk.entrada.pendientes || []).find((y) => y.codigo === $("se-m-lista").value);
  if (!x) return;
  $("se-m-sscc").value = x.codigo; $("se-m-ref").value = x.ref || ""; $("se-m-lote").value = x.lote || ""; $("se-m-kg").value = x.kg ? String(x.kg) : "";
});
$("se-barras").addEventListener("click", () => stk.entrada.tipo === "inventario" ? abrirLectorBarras({
  titulo: `Inventario ${stk.entrada.id}: leer el código del palet`,
  yaEsta: (c) => stk.entrada.palets.some((p) => p.sscc === c),
  alLeer: async (codigo) => {   // en el inventario el código solo no dice qué producto es: se pasa al formulario para poner la referencia, el lote y los kg
    $("se-m-sscc").value = codigo; $("se-manual").closest("details").open = true; $("se-m-ref").focus();
    return { ok: true, texto: `✓ Código ${codigo} leído. Cierra el escáner y escribe la referencia, el lote y los kg (o haz la foto de la etiqueta, que lo lee todo).` };
  },
}) : abrirLectorBarras({
  titulo: `Entrada ${stk.entrada.id}: escanear palets`,
  yaEsta: (c) => stk.entrada.palets.some((p) => p.sscc === c),
  alLeer: async (codigo) => {
    const r = await post(`/api/stock/entradas/${encodeURIComponent(stk.entrada.id)}/palet`, { sscc: codigo, origen: "barras", ubicacion: $("se-ubic").value.trim() });
    pintarEntrada(r.entrada);
    const p = r.palet;
    return p.estado === "ok" ? { ok: true, texto: `✓ ${p.ref} · ${codigo}${p.kg ? " · " + n(p.kg) + " kg" : ""}: coincide con el albarán.` } : { ok: false, texto: `⚠ ${codigo}: ${p.motivo}` };
  },
}));
// Corregir un palet ya apuntado: sus datos pasan al formulario de «apuntar a mano»; al guardar se sustituyen y se vuelve a comprobar con el albarán.
function corregirPaletEntrada(p) {
  stk.corrigiendo = p.id;
  const det = $("se-manual").closest("details");
  det.hidden = false; det.open = true;
  $("se-m-ref").value = p.ref || ""; $("se-m-lote").value = p.lote || ""; $("se-m-kg").value = p.kg ? String(p.kg) : ""; $("se-m-sscc").value = p.sin_codigo ? "" : p.sscc || "";
  $("se-m-sincod").checked = !!p.sin_codigo; $("se-m-sscc").disabled = !!p.sin_codigo;
  $("se-m-corrigiendo").hidden = false;
  $("se-m-corrigiendo-txt").textContent = `Corrigiendo el palet ${p.id}. Cambia lo que esté mal y pulsa «Guardar corrección».`;
  $("se-m-guardar").textContent = "Guardar corrección";
  det.scrollIntoView({ behavior: "smooth", block: "center" });
}
$("se-m-sincod").addEventListener("change", () => {
  $("se-m-sscc").disabled = $("se-m-sincod").checked;
  if ($("se-m-sincod").checked) $("se-m-sscc").value = "";
});
function terminarCorreccion() {
  stk.corrigiendo = null;
  $("se-m-corrigiendo").hidden = true;
  $("se-m-guardar").textContent = "Añadir el palet";
  $("se-manual").reset();
  $("se-m-sscc").disabled = false;
}
$("se-m-cancelar").addEventListener("click", terminarCorreccion);
$("se-manual").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const kg = parseNum($("se-m-kg").value);
  const datos = { ref: $("se-m-ref").value.trim(), lote: $("se-m-lote").value.trim(), kg: isNaN(kg) ? null : kg, sscc: $("se-m-sscc").value.trim(), sin_codigo: $("se-m-sincod").checked };
  const corr = stk.corrigiendo;
  const r = corr
    ? await conCarga("Guardando la corrección…", () => post(`/api/stock/entradas/${encodeURIComponent(stk.entrada.id)}/corregir`, { ...datos, palet: corr, ubicacion: $("se-ubic").value.trim() }))
    : await conCarga("Añadiendo…", () => post(`/api/stock/entradas/${encodeURIComponent(stk.entrada.id)}/palet`, { ...datos, ubicacion: $("se-ubic").value.trim() }));
  if (!r) return;
  if (corr) terminarCorreccion(); else { $("se-manual").reset(); $("se-m-sscc").disabled = false; }
  pintarEntrada(r.entrada);
  if (corr) $("se-aviso-palet").replaceChildren(h("div", { class: "banner " + (r.palet.estado === "ok" ? "info" : "warn") },
    r.palet.estado === "ok" ? `✓ Palet ${r.palet.id} corregido: ahora coincide con el albarán.` : "⚠ Corregido, pero sigue con aviso: " + r.palet.motivo));
});
async function quitarPaletEntrada(p) {
  confirmar(`El palet ${p.id} (${p.ref || "sin referencia"}) deja de contar en esta entrada.`, "Sí, quitarlo", async () => {
    const e = await conCarga("Quitando…", () => post(`/api/stock/entradas/${encodeURIComponent(stk.entrada.id)}/quitar`, { palet: p.id }));
    if (e) pintarEntrada(e);
  });
}
$("se-fentrada").addEventListener("change", async () => {
  const e = await conCarga("Guardando la fecha…", () => post(`/api/stock/entradas/${encodeURIComponent(stk.entrada.id)}/fecha`, { fecha: $("se-fentrada").value }));
  if (e) pintarEntrada(e);
});
$("se-obs").addEventListener("change", () => post(`/api/stock/entradas/${encodeURIComponent(stk.entrada.id)}/observaciones`, { texto: $("se-obs").value }).catch((e) => showError(e.message)));
$("se-revisar-btn").addEventListener("click", async () => {
  const e = await conCarga("Confirmando…", () => post(`/api/stock/entradas/${encodeURIComponent(stk.entrada.id)}/revisar`, {}));
  if (e) pintarEntrada(e);
});
$("se-recargar").addEventListener("click", () => abrirEntrada(stk.entrada.id));
$("se-cerrar").addEventListener("click", () => cerrarEntrada(false));
// Aviso de llegada al cliente: borrador en traficolevante@ (nunca se envía solo). Solo se dice algo si el cliente lo tiene activado.
function avisoClienteHtml(a) {
  if (!a || !a.activo) return [];
  if (a.creado) return [h("div", { class: "banner info" }, `📧 Aviso para el cliente enviado a ${a.para}${a.adjuntos ? " con la documentación adjunta" : ""}. Reenvíalo al contacto ${a.contacto || "del cliente"}.`)];
  return [h("div", { class: "banner warn" }, "⚠ " + (a.motivo || "No se ha enviado el aviso para el cliente.")),
    h("button", { class: "btn secondary", onclick: async () => {
      const r = await conCarga("Enviando el aviso…", () => post(`/api/stock/entradas/${encodeURIComponent(stk.entrada.id)}/aviso`, {}));
      if (r) $("se-descargas").prepend(...avisoClienteHtml(r.aviso_cliente));
    } }, "Volver a enviar el aviso")];
}
async function cerrarEntrada(confirmado) {
  clearError(); busy("Terminando la descarga y mandando la documentación…");
  try {
    const r = await post(`/api/stock/entradas/${encodeURIComponent(stk.entrada.id)}/cerrar`, { confirmado, nota: $("se-nota").value.trim(), observaciones: $("se-obs").value.trim() });
    busy(null);
    pintarEntrada(r.entrada);
    $("se-descargas").replaceChildren(
      h("div", { class: "banner info", style: "overflow-wrap:anywhere" }, r.enviado ? `Entrada cerrada. Documentación enviada a ${String(r.para||'').split(/[,;]\s*/).join(', ')}.` : "Entrada cerrada. El envío automático no está configurado o ha fallado: descarga la Excel aquí."),
      ...avisoClienteHtml(r.aviso_cliente),
      ...r.archivos.map((a) => h("button", { class: "btn secondary", onclick: () => descargarXlsx(a) }, "Descargar " + a.nombre)));
  } catch (err) {
    busy(null);
    if (err.code === "discrepancias") confirmar(err.message, "Sí, cerrarla", () => cerrarEntrada(true));
    else showError(err.message);
  }
}

// ---------- salidas ----------
async function cargarSalidas() {
  const r = await conCarga("Cargando las salidas…", () => api("/api/stock/salidas" + (stk.ssTodas ? "" : "?abiertas=true")));
  if (!r) return;
  $("ss-todas").textContent = stk.ssTodas ? "Ver solo las que faltan" : "Ver también las cargadas";
  $("ss-lista").replaceChildren(...(r.salidas.length ? r.salidas.map((s) => h("button", { type: "button", class: "btn secondary", onclick: () => abrirSalida(s.id) },
    `${s.cliente.nombre}${s.referencia ? " · pedido " + s.referencia : ""} · ${s.cargados}/${s.pedidos} palets · ${tipoTxt[s.estado]}` + (s.por_revisar ? " · 📧 POR REVISAR" : "")))
    : [h("p", { class: "hint" }, "No hay salidas pendientes.")]));
}
$("ss-todas").addEventListener("click", () => { stk.ssTodas = !stk.ssTodas; cargarSalidas(); });

const CAMPOS_PEDIDO = [["ref", "Referencia", "full"], ["descripcion", "Descripción", "full"], ["palets", "Palets", "half", "numeric"]];
$("ss-nueva").addEventListener("click", async () => {
  const r = await conCarga("Cargando…", () => api("/api/stock/clientes"));
  if (!r) return;
  stk.clientes = r.clientes;
  opcionesClientes($("ssn-cliente"), null);
  $("ssn-form").reset();
  $("ssn-avisos").replaceChildren();
  $("ssn-lineas").replaceChildren(filaLinea({}, CAMPOS_PEDIDO));
  stk.peticionFoto = null; stk.peticionEsperados = [];
  go("stock-salida-nueva");
});
$("ssn-mas").addEventListener("click", () => $("ssn-lineas").append(filaLinea({}, CAMPOS_PEDIDO)));
$("ss-leer").addEventListener("change", async (ev) => {
  const file = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  const r = await conCarga("Leyendo la petición…", async () => { const f = await reducir(file); stk.peticionFoto = f; return post("/api/stock/salidas/leer", { archivos: [f] }); });
  if (!r) return;
  const p = r.peticion;
  stk.peticionEsperados = r.esperados || [];
  opcionesClientes($("ssn-cliente"), r.cliente_sugerido);
  $("ssn-referencia").value = p.referencia; $("ssn-destino").value = p.destino;
  $("ssn-lineas").replaceChildren(...(p.lineas.length ? p.lineas : [{}]).map((l) => filaLinea(l, CAMPOS_PEDIDO)));
  $("ssn-avisos").replaceChildren(...p.dudas.map((d) => h("div", { class: "banner warn" }, "Duda al leer: " + d)));
});
$("ssn-form").addEventListener("submit", (ev) => { ev.preventDefault(); guardarSalida(false); });
async function guardarSalida(confirmado) {
  const cliente = $("ssn-cliente").value;
  if (!cliente) return showError("Elige el cliente.");
  const lineas = leerFilas("ssn-lineas", ["palets"]);
  if (!lineas.length) return showError("Añade al menos un tipo de palet.");
  clearError(); busy("Cargando la petición…");
  try {
    const s = await post("/api/stock/salidas", { cliente, referencia: $("ssn-referencia").value.trim(), destino: $("ssn-destino").value.trim(),
      transportista: $("ssn-transportista").value.trim(), lineas, esperados: stk.peticionEsperados || [], foto: stk.peticionFoto || null, confirmado });
    busy(null);
    pintarSalida(s);
    go("stock-salida");
  } catch (err) {
    busy(null);
    if (err.code === "salida_conflicto") confirmar(err.message, "Sí, cargarla", () => guardarSalida(true));
    else showError(err.message);
  }
}

async function abrirSalida(id) {
  const s = await conCarga("Cargando la salida…", () => api("/api/stock/salidas/" + encodeURIComponent(id)));
  if (!s) return;
  pintarSalida(s);
  go("stock-salida");
}
function pintarSalida(s) {
  stk.salida = s;
  const cargada = s.estado === "cargada";
  $("ss-titulo").textContent = `Salida ${s.id}`;
  $("ss-cabecera").textContent = `${s.cliente.nombre}${s.datos.referencia ? " · pedido " + s.datos.referencia : ""}${s.datos.destino ? " · destino " + s.datos.destino : ""} · ${s.cargados} de ${s.pedidos} palets` + (s.por_palet ? " · hay que cargar los palets concretos pedidos (por su código)" : "") + (cargada ? " · CARGADA" : "");
  document.querySelector('label[for="ss-etq-camara"]').hidden = cargada;
  $("ss-barras").hidden = cargada;
  pintarListaSalida(s);
  $("ss-cierre").hidden = cargada;
  $("ss-obs").value = s.observaciones || "";
  $("ss-revisar").hidden = !s.por_revisar;
  $("ss-revisar-avisos").replaceChildren(...avisosHtml(s.avisos || []));
  $("ss-manual").closest("details").hidden = cargada;
  $("ss-lineas").replaceChildren(...s.lineas.map((l) => h("div", { class: "fila" },
    h("span", {}, h("b", {}, l.ref || l.descripcion), l.ref && l.descripcion ? " · " + l.descripcion : "", cargada ? null : h("br"),
      cargada ? null : h("span", { class: "hint", id: "ss-ubic-" + l.linea }, "")),
    h("span", { class: l.cargados === l.pedidos ? "code" : "nota" }, `${l.cargados}/${l.pedidos}`))));
  $("ss-palets-titulo").hidden = !s.palets.length;
  $("ss-palets").replaceChildren(...s.palets.map((p) => h("div", { class: "fila" },
    h("span", {}, h("b", {}, p.ref || "¿?"), p.lote ? ` · lote ${p.lote}` : "", p.kg ? ` · ${n(p.kg)} kg` : "", h("br"),
      h("span", { class: "hint" }, (p.palet_id || "sin stock") + (p.sscc ? " · " + p.sscc : "") + (p.origen === "barras" ? " · escaneado" : p.manual ? " · a mano" : "")),
      p.palet_id ? h("br") : null, p.palet_id ? h("b", {}, p.ubicacion ? "📍 " + p.ubicacion : "📍 sin ubicación") : null,
      p.estado === "discrepancia" ? h("br") : null, p.estado === "discrepancia" ? h("span", { class: "nota" }, "⚠ " + p.motivo) : null,
      p.aviso ? h("br") : null, p.aviso ? h("span", { class: "hint" }, "ℹ " + p.aviso) : null),
    cargada ? null : accionesPalet(p, { incidencia: () => abrirIncidencia("salida", String(p.n), p), quitar: () => quitarPaletSalida(p) }))));
  $("ss-descargas").replaceChildren();
}
$("ss-etq-camara").addEventListener("change", async (ev) => {
  const file = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  const r = await conCarga("Leyendo la etiqueta…", async () => post(`/api/stock/salidas/${encodeURIComponent(stk.salida.id)}/etiqueta`, { foto: await reducir(file) }));
  if (!r) return;
  pintarSalida(r.salida);
  const p = r.palet;
  $("ss-aviso-palet").replaceChildren(h("div", { class: "banner " + (p.estado === "ok" ? "info" : "warn") },
    p.estado === "ok" ? `✓ ${p.ref} · ${p.palet_id}: es lo que piden.` + (p.aviso ? " " + p.aviso : "") : "⚠ " + p.motivo));
});
// Lista desplegable: palets del almacén que se pueden cargar en esta salida (los más antiguos primero).
async function pintarListaSalida(s) {
  const campo = $("ss-lista-campo"), sel = $("ss-m-lista");
  stk.disponibles = [];
  campo.hidden = true;
  if (s.estado === "cargada") return;
  try { stk.disponibles = (await api(`/api/stock/salidas/${encodeURIComponent(s.id)}/disponibles`)).palets; } catch { return; }
  if (!stk.salida || stk.salida.id !== s.id) return;   // se ha cambiado de salida mientras tanto
  campo.hidden = !stk.disponibles.length;
  sel.replaceChildren(h("option", { value: "" }, `— Elegir (${stk.disponibles.length} disponibles) —`),
    ...stk.disponibles.map((x) => h("option", { value: x.codigo }, `${x.sin_codigo ? "SIN CÓDIGO (" + x.palet + ")" : x.codigo} · ${x.ref || "¿?"}${x.lote ? " · lote " + x.lote : ""}${x.kg ? " · " + n(x.kg) + " kg" : ""} · 📍 ${x.ubicacion || "sin ubicación"} · entró ${fechaEs(x.fecha_entrada)}`)));
  const sc = stk.disponibles.filter((x) => x.sin_codigo);
  $("ss-sincod-campo").hidden = !sc.length;
  $("ss-m-sincod").replaceChildren(h("option", { value: "" }, `— Elegir (${sc.length} sin código) —`),
    ...sc.map((x) => h("option", { value: x.codigo }, `${x.palet} · ${x.ref || "¿?"}${x.lote ? " · lote " + x.lote : ""} · 📍 ${x.ubicacion || "sin ubicación"}`)));
  // Dónde está el material pedido: por cada línea, las ubicaciones con los palets que quedan por cargar
  for (const l of s.lineas) {
    const el = $("ss-ubic-" + l.linea);
    if (!el) continue;
    const cuenta = {};
    for (const x of stk.disponibles.filter((y) => y.linea === l.linea)) cuenta[x.ubicacion || "sin ubicación"] = (cuenta[x.ubicacion || "sin ubicación"] || 0) + 1;
    const txt = Object.entries(cuenta).map(([u, k]) => `${u} (${k})`).join(" · ");
    el.textContent = txt ? "📍 Está en: " + txt : "No hay palets de esta referencia en el almacén.";
  }
}
$("ss-m-sincod").addEventListener("change", () => {
  const x = (stk.disponibles || []).find((y) => y.codigo === $("ss-m-sincod").value);
  if (!x) return;
  $("ss-m-sscc").value = x.codigo; $("ss-m-ref").value = x.ref || ""; $("ss-m-lote").value = x.lote || "";
});
$("ss-m-lista").addEventListener("change", () => {
  const x = (stk.disponibles || []).find((y) => y.codigo === $("ss-m-lista").value);
  if (!x) return;
  $("ss-m-sscc").value = x.codigo; $("ss-m-ref").value = x.ref || ""; $("ss-m-lote").value = x.lote || "";
});
$("ss-barras").addEventListener("click", () => abrirLectorBarras({
  titulo: `Salida ${stk.salida.id}: escanear palets`,
  yaEsta: (c) => stk.salida.palets.some((p) => p.sscc === c),
  alLeer: async (codigo) => {
    const r = await post(`/api/stock/salidas/${encodeURIComponent(stk.salida.id)}/palet`, { sscc: codigo, origen: "barras" });
    pintarSalida(r.salida);
    const p = r.palet;
    return p.estado === "ok" ? { ok: true, texto: `✓ ${p.ref} · ${codigo}: es lo que piden.` + (p.aviso ? " " + p.aviso : "") } : { ok: false, texto: `⚠ ${codigo}: ${p.motivo}` };
  },
}));
$("ss-manual").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const r = await conCarga("Añadiendo…", () => post(`/api/stock/salidas/${encodeURIComponent(stk.salida.id)}/palet`,
    { sscc: $("ss-m-sscc").value.trim(), ref: $("ss-m-ref").value.trim(), lote: $("ss-m-lote").value.trim() }));
  if (r) { $("ss-manual").reset(); pintarSalida(r.salida); }
});
function quitarPaletSalida(p) {
  confirmar(`El palet ${p.palet_id || p.ref || ""} deja de contar en esta salida.`, "Sí, quitarlo", async () => {
    const s = await conCarga("Quitando…", () => post(`/api/stock/salidas/${encodeURIComponent(stk.salida.id)}/quitar`, { palet: String(p.n) }));
    if (s) pintarSalida(s);
  });
}
$("ss-obs").addEventListener("change", () => post(`/api/stock/salidas/${encodeURIComponent(stk.salida.id)}/observaciones`, { texto: $("ss-obs").value }).catch((e) => showError(e.message)));
$("ss-revisar-btn").addEventListener("click", async () => {
  const s = await conCarga("Confirmando…", () => post(`/api/stock/salidas/${encodeURIComponent(stk.salida.id)}/revisar`, {}));
  if (s) pintarSalida(s);
});
$("ss-recargar").addEventListener("click", () => abrirSalida(stk.salida.id));
$("ss-cerrar").addEventListener("click", () => cerrarSalida(false));
async function cerrarSalida(confirmado) {
  clearError(); busy("Terminando la carga y expidiendo la Excel…");
  try {
    const r = await post(`/api/stock/salidas/${encodeURIComponent(stk.salida.id)}/cerrar`, { confirmado, nota: $("ss-nota").value.trim(), observaciones: $("ss-obs").value.trim() });
    busy(null);
    pintarSalida(r.salida);
    $("ss-descargas").replaceChildren(
      h("div", { class: "banner info", style: "overflow-wrap:anywhere" }, r.enviado ? `Salida cargada. Documentación enviada a ${String(r.para||'').split(/[,;]\s*/).join(', ')}.` : "Salida cargada. El envío automático no está configurado o ha fallado: descarga la Excel aquí."),
      ...r.archivos.map((a) => h("button", { class: "btn secondary", onclick: () => descargarXlsx(a) }, "Descargar " + a.nombre)));
  } catch (err) {
    busy(null);
    if (err.code === "discrepancias") confirmar(err.message, "Sí, darla por cargada", () => cerrarSalida(true));
    else showError(err.message);
  }
}

// ---------- stock actual ----------
async function cargarStock() {
  const r = await conCarga("Calculando el stock…", () => api("/api/stock/stock"));
  if (!r) return;
  $("sa-total").textContent = `${r.total_palets} palets · ${n(r.total_kg)} kg en el almacén.`;
  // Aviso (solo informativo, no hay FIFO): mercancía que lleva más de un mes en el almacén
  stk.stockPalets = r.palets; stk.ubicValidas = r.ubicaciones_validas || [];
  const viejos = r.palets.filter((p) => p.antiguo);
  $("sa-antiguos").replaceChildren(...(viejos.length ? [h("div", { class: "banner warn" },
    `⏳ ${viejos.length} palet${viejos.length === 1 ? "" : "s"} lleva${viejos.length === 1 ? "" : "n"} más de un mes en el almacén: ` +
    r.resumen.filter((g) => g.antiguos).map((g) => `${g.cliente_nombre} · ${g.ref || "—"} (${g.antiguos})`).join(" · "))] : []));
  const lotes = (g) => [...new Set(r.palets.filter((p) => p.cliente === g.cliente && p.ref === g.ref && p.lote).map((p) => p.lote))].join(", ");
  $("sa-lista").replaceChildren(...(r.resumen.length ? r.resumen.map((g) => h("div", { class: "fila" },
    h("span", {}, h("b", {}, g.cliente_nombre), h("br"), `${g.ref || "—"}${g.descripcion ? " · " + g.descripcion : ""}`, h("br"),
      h("span", { class: "hint" }, `${n(g.kg)} kg · desde hace ${g.dias} días` + (lotes(g) ? " · lote " + lotes(g) : "")),
      h("br"), h("span", { class: "hint" }, "📍 " + (g.ubicaciones || []).map((u) => `${u.ubicacion} (${u.palets})`).join(" · ")),
      h("br"), h("button", { type: "button", class: "btn ghost", onclick: () => abrirUbicar(g) }, "Ubicar / cambiar ubicación"),
      g.antiguos ? h("br") : null, g.antiguos ? h("span", { class: "nota" }, `⏳ ${g.antiguos} con más de un mes`) : null),
    h("span", { class: "code" }, `${g.palets} palets`))) : [h("p", { class: "hint" }, "El almacén está vacío (todavía no hay entradas cerradas).")]));
  $("sa-descargas").replaceChildren();
}
// ---------- ubicar palets que ya están en el almacén ----------
function abrirUbicar(g) {
  stk.ubicarGrupo = { cliente: g.cliente, ref: g.ref, nombre: g.cliente_nombre };
  $("su-ubic").replaceChildren(h("option", { value: "" }, "— Sin ubicación —"),
    ...(stk.ubicValidas || []).map((gr) => h("optgroup", { label: gr.grupo }, ...gr.zonas.map((z) => h("option", { value: z }, z)))));
  pintarUbicar();
  go("stock-ubicar");
}
function paletsUbicar() {
  const g = stk.ubicarGrupo;
  return (stk.stockPalets || []).filter((p) => p.cliente === g.cliente && p.ref === g.ref);
}
function pintarUbicar(marcados) {
  const g = stk.ubicarGrupo, ps = paletsUbicar(), sin = ps.filter((p) => !p.ubicacion).length;
  $("su-cabecera").textContent = `${g.nombre} · ${g.ref || "—"} · ${ps.length} palets (${sin} sin ubicación)`;
  $("su-lista").replaceChildren(...ps.map((p) => h("label", { class: "fila" },
    h("input", { type: "checkbox", value: p.id, checked: !!(marcados && marcados.has(p.id)) }),
    h("span", {}, h("b", {}, p.sin_codigo ? "SIN CÓDIGO" : p.sscc || p.id), p.lote ? ` · lote ${p.lote}` : "", p.kg ? ` · ${n(p.kg)} kg` : "", h("br"),
      h("span", { class: "hint" }, p.id + " · entró " + fechaEs(p.fecha_entrada)), h("br"), h("b", {}, p.ubicacion ? "📍 " + p.ubicacion : "📍 sin ubicación")))));
}
const marcadosUbicar = () => [...$("su-lista").querySelectorAll("input:checked")].map((i) => i.value);
$("su-sin").addEventListener("click", () => pintarUbicar(new Set(paletsUbicar().filter((p) => !p.ubicacion).map((p) => p.id))));
$("su-todos").addEventListener("click", () => pintarUbicar(marcadosUbicar().length ? new Set() : new Set(paletsUbicar().map((p) => p.id))));
$("su-volver").addEventListener("click", () => { go("stock-actual"); cargarStock(); });
async function guardarUbicar(confirmado) {
  const palets = marcadosUbicar();
  if (!palets.length) return showError("Marca al menos un palet.");
  clearError(); busy("Guardando la ubicación…");
  try {
    await post("/api/stock/stock/ubicacion", { palets, ubicacion: $("su-ubic").value, confirmado });
    busy(null);
    const r = await api("/api/stock/stock");
    stk.stockPalets = r.palets;
    pintarUbicar();
  } catch (err) {
    busy(null);
    if (err.code === "cambio_ubicacion") confirmar(err.message, "Sí, cambiar la ubicación", () => guardarUbicar(true));
    else showError(err.message);
  }
}
$("su-guardar").addEventListener("click", () => guardarUbicar(false));
$("sa-libro").addEventListener("click", async () => {
  const r = await conCarga("Rehaciendo la Excel de control…", () => post("/api/stock/libro", {}));
  if (r) $("sa-descargas").replaceChildren(
    h("div", { class: "banner info", style: "overflow-wrap:anywhere" }, r.enviado ? "Excel de control actualizada en SharePoint." : "No se ha podido subir a SharePoint: descárgala aquí."),
    h("button", { class: "btn secondary", onclick: () => descargarXlsx(r.archivo) }, "Descargar " + r.archivo.nombre));
});

// ---------- inventario: entrada manual ----------
$("sa-nueva-inv").addEventListener("click", async () => {
  const r = await conCarga("Cargando…", () => api("/api/stock/clientes"));
  if (!r) return;
  stk.clientes = r.clientes;
  opcionesClientes($("sin-cliente"), null);
  $("sin-form").reset();
  go("stock-inv-nueva");
});
$("sin-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const cliente = $("sin-cliente").value;
  if (!cliente) return showError("Elige el cliente de la lista.");
  const e = await conCarga("Creando la entrada de inventario…", () => post("/api/stock/inventario", { cliente, fecha_entrada: $("sin-fecha").value, nota: $("sin-nota").value.trim() }));
  if (!e) return;
  pintarEntrada(e);
  $("se-aviso-palet").replaceChildren();
  go("stock-entrada");
});

// ---------- inventario: control (contar lo que hay y compararlo con el sistema) ----------
$("sa-control").addEventListener("click", async () => {
  const [c, r] = await conCarga("Cargando…", () => Promise.all([api("/api/stock/clientes"), api("/api/stock/controles")])) || [];
  if (!c) return;
  stk.clientes = c.clientes;
  $("sco-cliente").replaceChildren(h("option", { value: "" }, "Todos los clientes"), ...c.clientes.map((x) => h("option", { value: x.codigo }, x.nombre)));
  $("sco-lista").replaceChildren(...(r.controles.length ? r.controles.map((k) => h("button", { type: "button", class: "btn secondary", onclick: () => abrirControl(k.id) },
    `${k.id} · ${k.cliente.nombre} · ${k.contados} palets contados · ${k.estado === "cerrado" ? "Cerrado" : "Abierto"}`)) : [h("p", { class: "hint" }, "Todavía no se ha hecho ningún control.")]));
  go("stock-controles");
});
$("sco-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const k = await conCarga("Abriendo el control…", () => post("/api/stock/controles", { cliente: $("sco-cliente").value }));
  if (k) { pintarControl(k); $("sc2-aviso").replaceChildren(); $("sc2-descargas").replaceChildren(); go("stock-control"); }
});
async function abrirControl(id) {
  const k = await conCarga("Cargando el control…", () => api("/api/stock/controles/" + encodeURIComponent(id)));
  if (k) { pintarControl(k); $("sc2-aviso").replaceChildren(); $("sc2-descargas").replaceChildren(); go("stock-control"); }
}
function pintarControl(k) {
  stk.control = k;
  const cerrado = k.estado === "cerrado", r = k.resultado;
  $("sc2-titulo").textContent = `Control de inventario ${k.id}`;
  $("sc2-cabecera").textContent = `${k.cliente.nombre} · en el sistema ${r.esperados} palets · contados ${r.contados} · correctos ${r.bien} · faltan ${r.faltan.length} · sobran ${r.sobran.length}` + (cerrado ? " · CERRADO" : "");
  for (const i of ["sc2-barras", "sc2-form", "sc2-cierre"]) $(i).hidden = cerrado;
  if (!cerrado) cargarSinCodigoControl(k);
  const caja = (clase, texto) => h("div", { class: "fila" }, h("span", { class: clase }, texto));
  $("sc2-resultado").replaceChildren(
    ...(r.sobran.length ? [h("h2", {}, `Sobran (${r.sobran.length}): encontrados y no están en el sistema`)] : []),
    ...r.sobran.map((x) => caja("nota", `${x.sscc} · ${x.motivo}`)),
    ...(r.faltan.length ? [h("h2", {}, `Faltan (${r.faltan.length}): en el sistema y sin contar`)] : []),
    ...r.faltan.map((x) => caja("", `${x.sscc} · ${x.ref || "—"}${x.lote ? " · lote " + x.lote : ""}${x.kg ? " · " + n(x.kg) + " kg" : ""}${k.cliente.codigo ? "" : " · " + x.cliente}`)),
    ...(!r.faltan.length && !r.sobran.length ? [h("p", { class: "hint" }, r.contados ? "De momento todo lo contado coincide con el sistema." : "Escanea el primer palet.")] : []),
    ...(cerrado && k.bajas.length ? [h("div", { class: "banner warn" }, `Se dieron de baja ${k.bajas.length} palets del sistema.`)] : []));
}
// Palets sin código del inventario del cliente del control: se eligen de la lista (no tienen etiqueta que escanear)
async function cargarSinCodigoControl(k) {
  if (stk.ctlSinId === k.id) return;
  stk.ctlSinId = k.id;
  $("sc2-sincod-campo").hidden = true;
  let r;
  try { r = await api("/api/stock/stock" + (k.cliente.codigo ? "?cliente=" + encodeURIComponent(k.cliente.codigo) : "")); } catch { stk.ctlSinId = null; return; }
  const sc = r.palets.filter((p) => p.sin_codigo);
  $("sc2-sincod-campo").hidden = !sc.length;
  $("sc2-sincod").replaceChildren(h("option", { value: "" }, `— Elegir (${sc.length} sin código) —`),
    ...sc.map((p) => h("option", { value: p.sscc }, `${p.id} · ${p.ref || "¿?"}${p.lote ? " · lote " + p.lote : ""} · 📍 ${p.ubicacion || "sin ubicación"}`)));
}
$("sc2-sincod").addEventListener("change", () => { if ($("sc2-sincod").value) $("sc2-codigo").value = $("sc2-sincod").value; });
const contarPalet = async (codigo, origen) => {
  const r = await post(`/api/stock/controles/${encodeURIComponent(stk.control.id)}/palet`, { sscc: codigo, origen });
  pintarControl(r.control);
  return r;
};
$("sc2-barras").addEventListener("click", () => abrirLectorBarras({
  titulo: `Control ${stk.control.id}: escanear palets`,
  yaEsta: () => false,   // un repetido lo avisa el servidor
  alLeer: async (codigo) => {
    const r = await contarPalet(codigo, "barras");
    if (r.repetido) return { ok: false, texto: `⚠ ${codigo} ya estaba contado.` };
    return r.palet.estado === "ok" ? { ok: true, texto: `✓ ${r.palet.ref || ""} · ${codigo}` } : { ok: false, texto: `⚠ ${codigo}: ${r.palet.motivo}` };
  },
}));
$("sc2-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const codigo = $("sc2-codigo").value.trim();
  if (!codigo) return;
  const r = await conCarga("Contando…", () => contarPalet(codigo, ""));
  if (!r) return;
  $("sc2-codigo").value = ""; $("sc2-sincod").value = "";
  $("sc2-aviso").replaceChildren(h("div", { class: "banner " + (!r.repetido && r.palet.estado === "ok" ? "info" : "warn") },
    r.repetido ? `⚠ ${codigo} ya estaba contado.` : r.palet.estado === "ok" ? `✓ ${r.palet.ref || ""} · ${codigo}` : `⚠ ${codigo}: ${r.palet.motivo}`));
});
$("sc2-cerrar").addEventListener("click", () => {
  const r = stk.control.resultado, baja = $("sc2-baja").checked;
  confirmar(`Contados ${r.contados} palets; faltan ${r.faltan.length} y sobran ${r.sobran.length}.\n` + (baja && r.faltan.length ? `Se darán de baja del sistema los ${r.faltan.length} que faltan.` : "No se dará de baja ningún palet.") + "\nSe avisará por correo.", "Sí, terminar el control", async () => {
    const x = await conCarga("Cerrando el control…", () => post(`/api/stock/controles/${encodeURIComponent(stk.control.id)}/cerrar`, { baja_faltantes: baja, nota: $("sc2-nota").value.trim() }));
    if (!x) return;
    pintarControl(x.control);
    $("sc2-descargas").replaceChildren(h("div", { class: "banner info" }, x.enviado ? `Control cerrado. Resumen enviado a ${x.para}.` : "Control cerrado. El envío del resumen no está configurado o ha fallado."));
  });
});

// ---------- incidencia de un bulto (entrada o salida) ----------
function abrirIncidencia(tipo, clave, p) {
  stk.inc = { tipo, clave, fotos: [], vuelta: tipo === "entrada" ? "stock-entrada" : "stock-salida" };
  $("sin2-cabecera").textContent = `${tipo === "entrada" ? "Entrada " + stk.entrada.id : "Salida " + stk.salida.id} · ${p.ref || "palet"}${p.sscc ? " · " + p.sscc : ""}`
    + ((p.incidencias || []).length ? ` · ya tiene ${p.incidencias.length} incidencia(s) anotada(s)` : "");
  $("sin2-texto").value = ""; $("sin2-fotos").textContent = "";
  go("stock-incidencia");
}
async function alElegirFotosIncidencia(ev) {
  const libres = 4 - stk.inc.fotos.length;
  const files = [...(ev.target.files || [])].slice(0, Math.max(libres, 0));
  const sobran = (ev.target.files || []).length > files.length;
  ev.target.value = "";
  try { for (const f of files) stk.inc.fotos.push(await reducir(f)); } catch (e) { return showError(e.message); }
  const k = stk.inc.fotos.length;
  $("sin2-fotos").textContent = k ? `📷 ${k} foto${k === 1 ? "" : "s"} lista${k === 1 ? "" : "s"} (máximo 4)` + (sobran ? " · las demás no caben" : "") : "";
}
$("sin2-foto").addEventListener("change", alElegirFotosIncidencia);      // cámara
$("sin2-galeria").addEventListener("change", alElegirFotosIncidencia);   // galería / archivos
$("sin2-volver").addEventListener("click", () => go(stk.inc.vuelta));
$("sin2-guardar").addEventListener("click", async () => {
  const { tipo, clave, fotos, vuelta } = stk.inc, texto = $("sin2-texto").value.trim();
  if (!texto && !fotos.length) return showError("Escribe qué ha pasado o haz una foto del bulto.");
  const id = tipo === "entrada" ? stk.entrada.id : stk.salida.id;
  const r = await conCarga("Guardando la incidencia…", () => post(`/api/stock/${tipo === "entrada" ? "entradas" : "salidas"}/${encodeURIComponent(id)}/incidencia`, { palet: clave, texto, fotos }));
  if (!r) return;
  if (tipo === "entrada") pintarEntrada(r); else pintarSalida(r);
  go(vuelta);
});
