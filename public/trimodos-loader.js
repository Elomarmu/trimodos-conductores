/*
 * Loader de Grupo Trimodos — cuadrado animado del logotipo.
 *
 * El marco se dibuja en negro fino; el tren (verde STINSA), la carretera (naranja TRANSARGI)
 * y el ancla (azul) forman el símbolo y pasan al amarillo de marca mientras el marco engorda.
 * Se queda quieto ~1,5 s y se deshace en espejo. Ciclo de ~7 s.
 *
 * Uso:
 *   <script src="trimodos-loader.js" data-wait="ready"></script>   (lo antes posible en <body>)
 *   TrimodosLoader.ready()                 → cierra la pantalla de inicio (si se puso data-wait="ready")
 *   TrimodosLoader.show("Cargando…")       → capa de carga a pantalla completa (aparece si tarda > 250 ms)
 *   TrimodosLoader.hide()                  → la quita (lleva la cuenta: un hide por cada show)
 *   TrimodosLoader.track(promesa, "Msg")   → show + hide automáticos alrededor de una promesa
 *   TrimodosLoader.html(48)                → HTML del icono en línea (para meter en innerHTML)
 *   TrimodosLoader.block("Cargando…")      → icono centrado con texto (sustituye esqueletos de carga)
 *   <div data-trimodos-loader></div>       → se rellena solo con el icono
 *
 * Colores ajustables desde el CSS de cada app:
 *   --tl-ink        color inicial del marco (negro en apps claras, blanco en oscuras)
 *   --tl-splash-bg  fondo de la pantalla de inicio
 *   --tl-bg         fondo de la capa de carga
 *   --tl-fg         color del texto
 * Opciones del <script>: data-splash="off" (sin pantalla de inicio), data-wait="ready".
 */
(function () {
  "use strict";
  if (window.TrimodosLoader) return;

  var Y = "#FDDB00", G = "#009F92", O = "#F27C49", B = "#1E6BB8", INK = "var(--tl-ink,#141413)";
  var CSS = [
    ".tl-svg{display:block;width:100%;height:100%;overflow:visible}",
    ".tl-t,.tl-tr,.tl-mk,.tl-an{animation-delay:var(--tl-off,0s);animation-duration:3.5s;animation-iteration-count:infinite;animation-direction:alternate;animation-fill-mode:both}",
    ".tl-t{fill:none;stroke:" + Y + ";stroke-width:4.8}",
    ".tl-tr{fill:none;stroke:" + G + ";stroke-width:3.2;transform-box:view-box;transform-origin:50px 5px}",
    ".tl-mk{fill:none;stroke:" + O + ";stroke-width:3.6;transform-box:view-box;transform-origin:50px 5px}",
    ".tl-an{color:" + B + "}",
    ".tl-af{fill:currentColor}.tl-ar{fill:none;stroke:currentColor;stroke-width:4}.tl-aw{fill:none;stroke:currentColor;stroke-width:6;stroke-linecap:round}",
    ".tl-marco{stroke-linecap:square;stroke-linejoin:miter;animation-name:tl-marco;stroke-dasharray:360 720;stroke-dashoffset:360}",
    ".tl-rielI{animation-name:tl-rielI;stroke-dasharray:100.62 201.24;stroke-dashoffset:100.62}",
    ".tl-rielC{animation-name:tl-rielC;stroke-dasharray:90 180;stroke-dashoffset:90}",
    ".tl-borde{animation-name:tl-borde;stroke-dasharray:100.62 201.24;stroke-dashoffset:100.62}",
    ".tl-trav0{animation-name:tl-trav0}.tl-trav1{animation-name:tl-trav1}.tl-trav2{animation-name:tl-trav2}.tl-trav3{animation-name:tl-trav3}.tl-trav4{animation-name:tl-trav4}",
    ".tl-marca0{animation-name:tl-marca0}.tl-marca1{animation-name:tl-marca1}.tl-marca2{animation-name:tl-marca2}.tl-marca3{animation-name:tl-marca3}",
    ".tl-anclaN{animation-name:tl-anclaN,tl-anclaColor}.tl-anclaB1{animation-name:tl-anclaB1,tl-anclaColor}.tl-anclaB2{animation-name:tl-anclaB2,tl-anclaColor}.tl-anclaB3{animation-name:tl-anclaB3,tl-anclaColor}.tl-lineas{animation-name:tl-lineas}",
    "@keyframes tl-marco{0%,4%{stroke-dashoffset:360;opacity:0;stroke:" + INK + ";stroke-width:1.4}4.2%{stroke-dashoffset:360;opacity:1;stroke:" + INK + ";stroke-width:1.4;animation-timing-function:cubic-bezier(.65,0,.35,1)}18%,50%{stroke-dashoffset:0;opacity:1;stroke:" + INK + ";stroke-width:1.4;animation-timing-function:cubic-bezier(.45,0,.25,1)}74%,100%{stroke-dashoffset:0;opacity:1;stroke:" + Y + ";stroke-width:4.8}}",
    "@keyframes tl-rielI{0%,11.2%{stroke-dashoffset:100.62;opacity:0;stroke:" + G + "}11.4%{stroke-dashoffset:100.62;opacity:1;stroke:" + G + ";animation-timing-function:linear}27.2%,32%{stroke-dashoffset:0;opacity:1;stroke:" + G + ";animation-timing-function:ease-in-out}44%,100%{stroke-dashoffset:0;opacity:1;stroke:" + Y + "}}",
    "@keyframes tl-rielC{0%,11.2%{stroke-dashoffset:90;opacity:0;stroke:" + G + "}11.4%{stroke-dashoffset:90;opacity:1;stroke:" + G + ";animation-timing-function:linear}27.2%,32%{stroke-dashoffset:0;opacity:1;stroke:" + G + ";animation-timing-function:ease-in-out}44%,100%{stroke-dashoffset:0;opacity:1;stroke:" + Y + "}}",
    "@keyframes tl-borde{0%,22%{stroke-dashoffset:100.62;opacity:0;stroke:" + O + "}22.2%{stroke-dashoffset:100.62;opacity:1;stroke:" + O + ";animation-timing-function:cubic-bezier(.2,.6,.4,1)}40%,44%{stroke-dashoffset:0;opacity:1;stroke:" + O + ";animation-timing-function:ease-in-out}56%,100%{stroke-dashoffset:0;opacity:1;stroke:" + Y + "}}",
    "@keyframes tl-trav0{0%,14.4%{transform:scale(0);opacity:0}16%{transform:scale(.09);opacity:1;animation-timing-function:cubic-bezier(.55,0,.95,.55)}30.4%,100%{transform:scale(1.2);opacity:1}}",
    "@keyframes tl-trav1{0%,18%{transform:scale(0);opacity:0}19.6%{transform:scale(.09);opacity:1;animation-timing-function:cubic-bezier(.55,0,.95,.55)}34%,100%{transform:scale(1.2);opacity:1}}",
    "@keyframes tl-trav2{0%,21.6%{transform:scale(0);opacity:0}23.2%{transform:scale(.09);opacity:1;animation-timing-function:cubic-bezier(.55,0,.95,.55)}37.6%,100%{transform:scale(1.2);opacity:1}}",
    "@keyframes tl-trav3{0%,25.2%{transform:scale(0);opacity:0}26.8%{transform:scale(.09);opacity:1;animation-timing-function:cubic-bezier(.55,0,.95,.55)}41.2%,100%{transform:scale(1.2);opacity:1}}",
    "@keyframes tl-trav4{0%,28.8%{transform:scale(0);opacity:0}30.4%{transform:scale(.09);opacity:1;animation-timing-function:cubic-bezier(.55,0,.95,.55)}44.8%,100%{transform:scale(1.2);opacity:1}}",
    "@keyframes tl-marca0{0%,22%{transform:scale(1.2);opacity:0}22.4%{transform:scale(1.2);opacity:1;animation-timing-function:cubic-bezier(.2,.5,.4,1)}38%{transform:scale(.2);opacity:1;animation-timing-function:linear}42%,100%{transform:scale(.05);opacity:0}}",
    "@keyframes tl-marca1{0%,26%{transform:scale(1.2);opacity:0}26.4%{transform:scale(1.2);opacity:1;animation-timing-function:cubic-bezier(.2,.5,.4,1)}42%{transform:scale(.2);opacity:1;animation-timing-function:linear}46%,100%{transform:scale(.05);opacity:0}}",
    "@keyframes tl-marca2{0%,30%{transform:scale(1.2);opacity:0}30.4%{transform:scale(1.2);opacity:1;animation-timing-function:cubic-bezier(.2,.5,.4,1)}46%{transform:scale(.2);opacity:1;animation-timing-function:linear}50%,100%{transform:scale(.05);opacity:0}}",
    "@keyframes tl-marca3{0%,34%{transform:scale(1.2);opacity:0}34.4%{transform:scale(1.2);opacity:1;animation-timing-function:cubic-bezier(.2,.5,.4,1)}50%{transform:scale(.2);opacity:1;animation-timing-function:linear}54%,100%{transform:scale(.05);opacity:0}}",
    "@keyframes tl-anclaN{0%,36%{opacity:0;animation-timing-function:cubic-bezier(.33,0,.2,1)}46%,52%{opacity:1;animation-timing-function:ease-in-out}60%,100%{opacity:0}}",
    "@keyframes tl-anclaB1{0%,48%{opacity:0;animation-timing-function:ease-in-out}52%,58%{opacity:1;animation-timing-function:ease-in-out}64%,100%{opacity:0}}",
    "@keyframes tl-anclaB2{0%,54%{opacity:0;animation-timing-function:ease-in-out}58%,62%{opacity:1;animation-timing-function:ease-in-out}68%,100%{opacity:0}}",
    "@keyframes tl-anclaB3{0%,58%{opacity:0;animation-timing-function:ease-in-out}62%,64%{opacity:.9;animation-timing-function:cubic-bezier(.4,0,.6,1)}74%,100%{opacity:0}}",
    "@keyframes tl-anclaColor{0%,46%{color:" + B + ";animation-timing-function:ease-in-out}72%,100%{color:" + Y + "}}",
    "@keyframes tl-lineas{0%,56%{opacity:0;stroke:" + B + ";animation-timing-function:ease-in-out}72%{opacity:1;animation-timing-function:ease-in-out}76%,100%{opacity:1;stroke:" + Y + "}}",
    "@media (prefers-reduced-motion: reduce){.tl-t,.tl-tr,.tl-mk,.tl-an{animation:none!important}.tl-t{stroke-dashoffset:0!important;opacity:1!important}.tl-tr,.tl-mk,.tl-anclaN,.tl-anclaB1,.tl-anclaB2,.tl-anclaB3{opacity:0!important}.tl-lineas{opacity:1!important}}",
    // Capas
    ".tl-layer{position:fixed;inset:0;z-index:2147483000;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:20px;padding:24px;box-sizing:border-box;opacity:0;transition:opacity .3s ease;-webkit-tap-highlight-color:transparent}",
    ".tl-layer.tl-on{opacity:1}",
    ".tl-busy{background:var(--tl-bg,rgba(255,255,255,.9))}",
    ".tl-splash{background:var(--tl-splash-bg,#fff);opacity:1;transition:opacity .45s ease}",
    ".tl-splash.tl-out{opacity:0}",
    ".tl-box{width:var(--tl-size,112px);height:var(--tl-size,112px);flex:none}",
    ".tl-splash .tl-box{--tl-size:min(42vw,180px)}",
    ".tl-msg{margin:0;max-width:min(80vw,420px);text-align:center;font-size:17px;font-weight:500;line-height:1.4;font-family:inherit;color:var(--tl-fg,#141413)}",
    ".tl-inline{display:inline-block;vertical-align:middle;width:var(--tl-size,48px);height:var(--tl-size,48px)}",
    ".tl-block{display:flex;flex-direction:column;align-items:center;gap:12px;padding:24px 0}",
    ".tl-note{margin:0;text-align:center;font-size:14px;color:var(--tl-note,inherit);max-width:36ch}"
  ].join("\n");

  var ANCLA = '<circle class="tl-ar" cx="50" cy="15" r="6"/><path class="tl-aw" d="M28 33.3 L72 33.3 M50 21 L50 80"/>' +
    '<circle class="tl-af" cx="27" cy="33.3" r="3.6"/><circle class="tl-af" cx="73" cy="33.3" r="3.6"/>' +
    '<path class="tl-aw" d="M12 66.2 A60 60 0 0 0 88 66.2"/>' +
    '<path class="tl-af" d="M5 60.5 L8.8 70.1 L15.2 62.4 Z M95 60.5 L84.8 62.4 L91.2 70.1 Z M45 76 L50 85 L55 76 Z"/>';
  var TRAV = "", MARCAS = "", i;
  for (i = 0; i < 5; i++) TRAV += '<path class="tl-tr tl-trav' + i + '" d="M5 95 L50 95"/>';
  for (i = 0; i < 4; i++) MARCAS += '<path class="tl-mk tl-marca' + i + '" d="M72.5 95 L67.5 75"/>';

  var seq = 0;
  // Cada icono lleva sus propios ids: si se quita uno (p. ej. la pantalla de inicio) los demás siguen bien.
  function svg(label) {
    var k = "tl" + (++seq), f = "";
    [1, 2.2, 3.8].forEach(function (d, j) {
      f += '<filter id="' + k + "f" + (j + 1) + '" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="' + d + '"/></filter>';
    });
    var a = function (cls, filt) {
      return '<g class="tl-an ' + cls + '"' + (filt ? ' filter="url(#' + k + filt + ')"' : "") + "><use href=\"#" + k + 'a" xlink:href="#' + k + 'a"/></g>';
    };
    return '<svg class="tl-svg" xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 100 100" ' +
      (label ? 'role="img" aria-label="' + label + '"' : 'aria-hidden="true" focusable="false"') + ">" +
      '<defs><clipPath id="' + k + 'c"><rect x="5" y="5" width="90" height="90"/></clipPath>' + f + '<g id="' + k + 'a">' + ANCLA + "</g></defs>" +
      '<g clip-path="url(#' + k + 'c)">' + TRAV + MARCAS +
      '<path class="tl-t tl-rielI" d="M50 5 L5 95"/><path class="tl-t tl-rielC" d="M50 5 L50 95"/><path class="tl-t tl-borde" d="M95 95 L50 5"/>' +
      a("tl-anclaB3", "f3") + a("tl-anclaB2", "f2") + a("tl-anclaB1", "f1") + a("tl-anclaN", "") +
      '<g class="tl-t tl-an tl-lineas"><path d="M5 33.3 L95 33.3"/><path d="M5 59.5 A60 60 0 0 0 95 59.5"/></g></g>' +
      '<path class="tl-t tl-marco" d="M5 95 L5 5 L95 5 L95 95 Z"/></svg>';
  }

  function css() {
    if (document.getElementById("tl-style")) return;
    var s = document.createElement("style");
    s.id = "tl-style";
    s.textContent = CSS;
    (document.head || document.documentElement).appendChild(s);
  }

  function esc(t) { return String(t).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  // Si la app vuelve a pintar la pantalla mientras carga, el icono sigue por donde iba en vez de empezar de cero.
  var epoch = 0, lastHtml = 0;
  function html(size, label) {
    css();
    var now = Date.now();
    if (now - lastHtml > 1500) epoch = now;   // una carga nueva empieza desde el principio
    lastHtml = now;
    var off = -(((now - epoch) / 1000) % 7);
    return '<span class="tl-inline" style="--tl-size:' + (size || 48) + "px;--tl-off:" + off.toFixed(2) + 's">' + svg(label || "") + "</span>";
  }
  // Bloque centrado con icono y texto, para sustituir esqueletos de carga.
  function block(msg, size) {
    return '<div class="tl-block" role="status">' + html(size || 72) + (msg ? '<p class="tl-note">' + esc(msg) + "</p>" : "") + "</div>";
  }

  function mountAll(root) {
    css();
    var els = (root || document).querySelectorAll("[data-trimodos-loader]:not([data-tl-done])");
    for (var j = 0; j < els.length; j++) {
      els[j].setAttribute("data-tl-done", "");
      els[j].innerHTML = svg(els[j].getAttribute("aria-label") || "");
    }
  }

  // ---------- Capa de carga ----------
  var count = 0, timer = null, layer = null, msgEl = null;
  function show(msg) {
    css();
    count++;
    if (layer) { if (msg) msgEl.textContent = msg; return; }
    clearTimeout(timer);
    timer = setTimeout(function () {
      if (count <= 0 || layer) return;
      layer = document.createElement("div");
      layer.className = "tl-layer tl-busy";
      layer.setAttribute("role", "status");
      layer.setAttribute("aria-live", "polite");
      layer.innerHTML = '<div class="tl-box">' + svg() + '</div><p class="tl-msg">' + esc(msg || "Un momento…") + "</p>";
      msgEl = layer.querySelector(".tl-msg");
      document.body.appendChild(layer);
      requestAnimationFrame(function () { if (layer) layer.classList.add("tl-on"); });
    }, 250);   // las operaciones rápidas no llegan a enseñar nada (sin parpadeos)
  }
  function hide() {
    count = Math.max(0, count - 1);
    if (count) return;
    clearTimeout(timer);
    if (!layer) return;
    var l = layer; layer = null; msgEl = null;
    l.classList.remove("tl-on");
    setTimeout(function () { if (l.parentNode) l.parentNode.removeChild(l); }, 320);
  }
  function track(p, msg) {
    show(msg);
    return Promise.resolve(p).then(function (v) { hide(); return v; }, function (e) { hide(); throw e; });
  }

  // ---------- Pantalla de inicio ----------
  var script = document.currentScript;
  var opt = function (n) { return script ? script.getAttribute("data-" + n) : null; };
  var splash = null, isReady = false, minDone = false;
  function closeSplash() {
    if (!splash || !isReady || !minDone) return;
    var s = splash; splash = null;
    s.classList.add("tl-out");
    setTimeout(function () { if (s.parentNode) s.parentNode.removeChild(s); }, 500);
  }
  function ready() { isReady = true; closeSplash(); }

  if (opt("splash") !== "off") {
    css();
    // La primera vez de cada sesión se deja ver el logotipo entero (~2,7 s); después, solo lo que tarde en cargar.
    var first = true;
    try { first = !sessionStorage.getItem("tl-visto"); sessionStorage.setItem("tl-visto", "1"); } catch (e) { /* sin almacenamiento */ }
    var reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
    splash = document.createElement("div");
    splash.className = "tl-layer tl-splash tl-on";
    splash.setAttribute("role", "status");
    splash.setAttribute("aria-label", "Cargando");
    splash.innerHTML = '<div class="tl-box">' + svg("Grupo Trimodos") + "</div>";
    splash.addEventListener("click", function () { minDone = true; isReady = true; closeSplash(); });   // tocar = saltar
    (document.body || document.documentElement).appendChild(splash);
    setTimeout(function () { minDone = true; closeSplash(); }, reduce ? 300 : first ? 2700 : 500);
    setTimeout(ready, 15000);   // por seguridad: nunca se queda bloqueada
    if (opt("wait") !== "ready") {
      if (document.readyState === "complete") ready(); else window.addEventListener("load", ready);
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { mountAll(); });
  else mountAll();

  window.TrimodosLoader = { show: show, hide: hide, track: track, html: html, block: block, mount: mountAll, ready: ready, svg: svg };
})();
