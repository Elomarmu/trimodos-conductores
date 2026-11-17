"use strict";
// Stock del almacén de Burriana: entradas (albarán → etiquetas de los palets), salidas (petición → palets cargados),
// stock actual y clientes. Solo en la app de oficina y almacén. Usa las utilidades de app.js y pr.js
// ($, h, post, api, busy, showError, clearError, go, state, fechaEs, reducir, escanearDocumento).

const stk = { clientes: [], entrada: null, salida: null, albaran: null, todas: false, ssTodas: false };

const tipoTxt = { pendiente: "Pendiente de descarga", descargando: "Descargando", cerrada: "Cerrada", pedida: "Pedida", preparando: "Preparando", cargada: "Cargada" };
const n = (x) => (x == null ? "—" : fmtNum(x));
// descargarXlsx({nombre, b64}) viene de pr.js
const avisosHtml = (lista) => lista.map((a) => h("div", { class: "banner " + (a.nivel === "error" ? "bad" : "warn") }, a.texto));
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
  $("sc-refs").value = (c.referencias || []).map((r) => `${r.ref};${r.descripcion || ""}`).join("\n");
  $("sc-guardar").textContent = "Guardar los cambios de " + c.nombre;
  $("sc-nombre").scrollIntoView({ behavior: "smooth" });
}
$("sc-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const nombre = $("sc-nombre").value.trim();
  if (nombre.length < 2) return showError("Escribe el nombre del cliente.");
  const referencias = $("sc-refs").value.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => { const [ref, ...d] = l.split(";"); return { ref: ref.trim(), descripcion: d.join(";").trim() }; });
  const r = await conCarga("Guardando…", () => post("/api/stock/clientes", { codigo: stk.cliEdit || "", nombre, referencias,
    alias: $("sc-alias").value.split(",").map((s) => s.trim()).filter(Boolean) }));
  if (r) { $("sc-nombre").value = ""; $("sc-alias").value = ""; $("sc-refs").value = ""; $("sc-guardar").textContent = "Guardar el cliente"; cargarClientes(); }
});
function opcionesClientes(sel, elegido) {
  sel.replaceChildren(h("option", { value: "" }, "— Elige el cliente —"), ...stk.clientes.map((c) => h("option", { value: c.codigo, selected: c.codigo === elegido }, c.nombre)));
}

// ---------- entradas: lista ----------
async function cargarEntradas() {
  const r = await conCarga("Cargando las entradas…", () => api("/api/stock/entradas" + (stk.todas ? "" : "?abiertas=true")));
  if (!r) return;
  $("se-todas").textContent = stk.todas ? "Ver solo las que faltan" : "Ver también las cerradas";
  $("se-lista").replaceChildren(...(r.entradas.length ? r.entradas.map((e) => h("button", { type: "button", class: "btn secondary", onclick: () => abrirEntrada(e.id) },
    `${e.cliente.nombre} · albarán ${e.albaran || "—"} · ${e.verificados}/${e.esperados} palets · ${tipoTxt[e.estado]}` + (e.discrepancias ? ` · ⚠ ${e.discrepancias}` : "") + (e.por_revisar ? " · 📧 POR REVISAR" : "")))
    : [h("p", { class: "hint" }, "No hay entradas pendientes.")]));
}
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
  $("se-titulo").textContent = `Entrada ${e.id}`;
  $("se-cabecera").textContent = `${e.cliente.nombre}${e.cliente_final ? " (para " + e.cliente_final + ")" : ""} · albarán ${e.albaran || "—"} del ${fechaEs(e.fecha)} · entrada en almacén ${fechaEs(e.fecha_entrada)} · ${e.verificados} de ${e.esperados} palets verificados` + (cerrada ? " · CERRADA" : "");
  document.querySelector('label[for="se-etq-camara"]').hidden = cerrada;
  $("se-barras").hidden = cerrada;
  pintarListaEntrada(e);
  $("se-cierre").hidden = cerrada;
  $("se-revisar").hidden = !e.por_revisar;
  $("se-revisar-avisos").replaceChildren(...avisosHtml(e.avisos || []));
  $("se-fentrada").value = e.fecha_entrada || ""; $("se-obs").value = e.observaciones || "";
  $("se-manual").closest("details").hidden = cerrada;
  $("se-lineas").replaceChildren(...e.lineas.map((l) => h("div", { class: "fila" },
    h("span", {}, h("b", {}, l.ref || l.descripcion), l.ref && l.descripcion ? " · " + l.descripcion : "", h("br"),
      h("span", { class: "hint" }, `Albarán ${l.esperados} palets${l.kg_albaran ? " · " + n(l.kg_albaran) + " kg" : ""}`)),
    h("span", { class: l.verificados === l.esperados ? "code" : "nota" }, `${l.verificados}/${l.esperados}`))));
  $("se-palets-titulo").hidden = !e.palets.length;
  $("se-palets").replaceChildren(...e.palets.map((p) => h("div", { class: "fila" },
    h("span", {}, h("b", {}, p.ref || "¿?"), p.lote ? ` · lote ${p.lote}` : "", p.kg ? ` · ${n(p.kg)} kg` : "", h("br"),
      h("span", { class: "hint" }, p.id + (p.sscc ? " · " + p.sscc : "") + (p.origen === "barras" ? " · escaneado" : p.manual ? " · a mano" : "")),
      p.estado === "discrepancia" ? h("br") : null, p.estado === "discrepancia" ? h("span", { class: "nota" }, "⚠ " + p.motivo) : null),
    cerrada ? null : h("button", { type: "button", class: "btn ghost quitar", onclick: () => quitarPaletEntrada(p) }, "Quitar"))));
  $("se-descargas").replaceChildren();
}
$("se-etq-camara").addEventListener("change", async (ev) => {
  const file = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  const r = await conCarga("Leyendo la etiqueta…", async () => post(`/api/stock/entradas/${encodeURIComponent(stk.entrada.id)}/etiqueta`, { foto: await reducir(file) }));
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
$("se-barras").addEventListener("click", () => abrirLectorBarras({
  titulo: `Entrada ${stk.entrada.id}: escanear palets`,
  yaEsta: (c) => stk.entrada.palets.some((p) => p.sscc === c),
  alLeer: async (codigo) => {
    const r = await post(`/api/stock/entradas/${encodeURIComponent(stk.entrada.id)}/palet`, { sscc: codigo, origen: "barras" });
    pintarEntrada(r.entrada);
    const p = r.palet;
    return p.estado === "ok" ? { ok: true, texto: `✓ ${p.ref} · ${codigo}${p.kg ? " · " + n(p.kg) + " kg" : ""}: coincide con el albarán.` } : { ok: false, texto: `⚠ ${codigo}: ${p.motivo}` };
  },
}));
$("se-manual").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const kg = parseNum($("se-m-kg").value);
  const r = await conCarga("Añadiendo…", () => post(`/api/stock/entradas/${encodeURIComponent(stk.entrada.id)}/palet`,
    { ref: $("se-m-ref").value.trim(), lote: $("se-m-lote").value.trim(), kg: isNaN(kg) ? null : kg, sscc: $("se-m-sscc").value.trim() }));
  if (r) { $("se-manual").reset(); pintarEntrada(r.entrada); }
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
async function cerrarEntrada(confirmado) {
  clearError(); busy("Terminando la descarga y mandando la documentación…");
  try {
    const r = await post(`/api/stock/entradas/${encodeURIComponent(stk.entrada.id)}/cerrar`, { confirmado, nota: $("se-nota").value.trim(), observaciones: $("se-obs").value.trim() });
    busy(null);
    pintarEntrada(r.entrada);
    $("se-descargas").replaceChildren(
      h("div", { class: "banner info" }, r.enviado ? `Entrada cerrada. Documentación enviada a ${r.para}.` : "Entrada cerrada. El envío automático no está configurado o ha fallado: descarga la Excel aquí."),
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
  $("ss-cabecera").textContent = `${s.cliente.nombre}${s.datos.referencia ? " · pedido " + s.datos.referencia : ""}${s.datos.destino ? " · destino " + s.datos.destino : ""} · ${s.cargados} de ${s.pedidos} palets` + (cargada ? " · CARGADA" : "");
  document.querySelector('label[for="ss-etq-camara"]').hidden = cargada;
  $("ss-barras").hidden = cargada;
  pintarListaSalida(s);
  $("ss-cierre").hidden = cargada;
  $("ss-obs").value = s.observaciones || "";
  $("ss-revisar").hidden = !s.por_revisar;
  $("ss-revisar-avisos").replaceChildren(...avisosHtml(s.avisos || []));
  $("ss-manual").closest("details").hidden = cargada;
  $("ss-lineas").replaceChildren(...s.lineas.map((l) => h("div", { class: "fila" },
    h("span", {}, h("b", {}, l.ref || l.descripcion), l.ref && l.descripcion ? " · " + l.descripcion : ""),
    h("span", { class: l.cargados === l.pedidos ? "code" : "nota" }, `${l.cargados}/${l.pedidos}`))));
  $("ss-palets-titulo").hidden = !s.palets.length;
  $("ss-palets").replaceChildren(...s.palets.map((p) => h("div", { class: "fila" },
    h("span", {}, h("b", {}, p.ref || "¿?"), p.lote ? ` · lote ${p.lote}` : "", p.kg ? ` · ${n(p.kg)} kg` : "", h("br"),
      h("span", { class: "hint" }, (p.palet_id || "sin stock") + (p.sscc ? " · " + p.sscc : "") + (p.origen === "barras" ? " · escaneado" : p.manual ? " · a mano" : "")),
      p.estado === "discrepancia" ? h("br") : null, p.estado === "discrepancia" ? h("span", { class: "nota" }, "⚠ " + p.motivo) : null,
      p.aviso ? h("br") : null, p.aviso ? h("span", { class: "hint" }, "ℹ " + p.aviso) : null),
    cargada ? null : h("button", { type: "button", class: "btn ghost quitar", onclick: () => quitarPaletSalida(p) }, "Quitar"))));
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
    ...stk.disponibles.map((x) => h("option", { value: x.codigo }, `${x.codigo} · ${x.ref || "¿?"}${x.lote ? " · lote " + x.lote : ""}${x.kg ? " · " + n(x.kg) + " kg" : ""} · entró ${fechaEs(x.fecha_entrada)}`)));
}
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
      h("div", { class: "banner info" }, r.enviado ? `Salida cargada. Documentación enviada a ${r.para}.` : "Salida cargada. El envío automático no está configurado o ha fallado: descarga la Excel aquí."),
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
  $("sa-lista").replaceChildren(...(r.resumen.length ? r.resumen.map((g) => h("div", { class: "fila" },
    h("span", {}, h("b", {}, g.cliente_nombre), h("br"), `${g.ref || "—"}${g.descripcion ? " · " + g.descripcion : ""}`, h("br"),
      h("span", { class: "hint" }, `${n(g.kg)} kg · desde hace ${g.dias} días`)),
    h("span", { class: "code" }, `${g.palets} palets`))) : [h("p", { class: "hint" }, "El almacén está vacío (todavía no hay entradas cerradas).")]));
  $("sa-descargas").replaceChildren();
}
$("sa-libro").addEventListener("click", async () => {
  const r = await conCarga("Rehaciendo la Excel de control…", () => post("/api/stock/libro", {}));
  if (r) $("sa-descargas").replaceChildren(
    h("div", { class: "banner info" }, r.enviado ? "Excel de control actualizada en SharePoint." : "No se ha podido subir a SharePoint: descárgala aquí."),
    h("button", { class: "btn secondary", onclick: () => descargarXlsx(r.archivo) }, "Descargar " + r.archivo.nombre));
});
