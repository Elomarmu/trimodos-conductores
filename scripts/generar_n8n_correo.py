"""Genera n8n/stock-correo.json: flujo de n8n que lee los correos que llegan a agente.burriana@stinsa.com (con el albarán o la
petición de salida del cliente en PDF, Excel o fotos), se los manda a la app (POST /api/stock/correo) y avisa a la oficina por
correo de lo que ha preparado (para revisarlo en la app) o de lo que no ha podido procesar.

Una ejecución por tanda de correos nuevos (el disparador solo ejecuta cuando hay correo). La app es idempotente por id de
correo: si n8n repite el envío no se duplica nada. La app NUNCA cierra nada sola: deja la entrada/salida «por revisar».

Uso:  python scripts/generar_n8n_correo.py     (después, en n8n: Importar desde archivo, conectar las dos credenciales y activar)
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

DESTINO = Path(__file__).resolve().parent.parent / "n8n" / "stock-correo.json"

JS_CONFIG = """// Ajustes del flujo STOCK BURRIANA · CORREO
const cfg = {
  alias: 'agente.burriana@stinsa.com',                                   // solo se procesan los correos dirigidos a este alias
  urlApp: '<<PONER EN N8N: https://TU-APP.vercel.app/api/stock/correo>>',
  claveApp: '<<PONER EN N8N: la misma que STOCK_CORREO_CLAVE en Vercel>>',
  buzonEnvio: 'agente.trafico@transargi.com',                            // buzón de la credencial «Outlook Agente Trafico» (desde donde salen los avisos)
  paraPorDefecto: 'carmelomartinez@stinsa.com',                          // si la app no dice otra cosa (STOCK_CORREO_PARA)
  presupuestoB64: 4000000,                                               // Vercel admite cuerpos de 4,5 MB
};
// Deja pasar el correo (datos y adjuntos) y le añade los ajustes: así «Preparar» recibe el correo y $('Configuración') sigue dando los ajustes.
return $input.all().map(it => ({ json: { ...it.json, ...cfg }, binary: it.binary }));
"""

JS_PREPARAR = """// Un elemento por correo: comprueba que va al alias y prepara el JSON para la app (adjuntos útiles en base64).
const cfg = $('Configuración').first().json;
const alias = cfg.alias.toLowerCase();
const salida = [];
for (let i = 0; i < $input.all().length; i++) {
  const m = $input.all()[i].json, bin = $input.all()[i].binary || {};
  const dirs = [...(m.toRecipients || []), ...(m.ccRecipients || []), ...(m.bccRecipients || [])].map(r => String((r.emailAddress || {}).address || '').toLowerCase());
  if (dirs.length && !dirs.includes(alias)) continue;                    // no es para el alias del almacén
  const texto = (m.body && m.body.content ? String(m.body.content) : String(m.bodyPreview || ''))
    .replace(/<style[\\s\\S]*?<\\/style>/gi, ' ').replace(/<[^>]+>/g, ' ').replace(/&nbsp;/g, ' ').replace(/\\s+/g, ' ').trim().slice(0, 4000);
  const cand = [];
  for (const k of Object.keys(bin)) {
    const b = bin[k], nombre = b.fileName || k, n = nombre.toLowerCase();
    const util = /\\.(pdf|xlsx)$/.test(n) || (/\\.(jpe?g|png)$/.test(n) && Number(b.fileSize ? parseFloat(b.fileSize) * (/mb/i.test(b.fileSize) ? 1e6 : 1e3) : 1e6) >= 60000);   // las firmas (logos pequeños) no cuentan
    if (util) cand.push({ k, nombre, tipo: b.mimeType || '', peso: /\\.(pdf|xlsx)$/.test(n) ? 0 : 1 });
  }
  cand.sort((a, b) => a.peso - b.peso);                                   // primero PDF y Excel, luego fotos
  const adjuntos = [], omitidos = [];
  let usado = 0;
  for (const c of cand) {
    const buf = await this.helpers.getBinaryDataBuffer(i, c.k);
    const b64 = buf.toString('base64');
    if (usado + b64.length > cfg.presupuestoB64 || adjuntos.length >= 8) { omitidos.push(c.nombre); continue; }
    usado += b64.length;
    adjuntos.push({ nombre: c.nombre, tipo: c.tipo, archivo_b64: b64 });
  }
  const de = (m.from && m.from.emailAddress && m.from.emailAddress.address) || '';
  salida.push({ json: { payload: { mensaje_id: m.id || '', de, asunto: m.subject || '', adjuntos,
    cuerpo: texto + (omitidos.length ? ` [Adjuntos no enviados por tamaño: ${omitidos.join(', ')}]` : '') } } });
}
return salida;
"""

JS_AVISO = """// Redacta el aviso a la oficina según lo que contesta la app.
const cfg = $('Configuración').first().json;
const p = $('Preparar').item.json.payload;
const r = $json || {};
const esc = s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const lista = a => (a && a.length) ? '<ul>' + a.map(x => `<li>${esc(x)}</li>`).join('') + '</ul>' : '';
const estilo = 'font:11pt Calibri,Arial';
let asunto, html;
if (r.resultado === 'creada') {
  if (r.repetido) return [];                                              // correo ya tratado antes: sin aviso
  const nom = r.tipo === 'salida' ? 'Salida' : 'Entrada';
  asunto = `Almacén Burriana · ${nom} ${r.id} preparada desde el correo «${p.asunto}»`;
  html = `<p style='${estilo}'>He preparado la <b>${nom.toLowerCase()} ${esc(r.id)}</b> de <b>${esc(r.cliente)}</b> con el correo de ${esc(p.de)}`
    + (r.albaran ? ` (albarán ${esc(r.albaran)})` : '') + (r.pedido ? ` (pedido ${esc(r.pedido)})` : '') + ` · ${esc(r.palets)} palets.</p>`
    + (r.avisos && r.avisos.length ? `<p style='${estilo};color:#C00000'><b>Avisos:</b></p>${lista(r.avisos)}` : `<p style='${estilo}'>Sin conflictos.</p>`)
    + `<p style='${estilo}'>Revísala y confírmala en la app (Stock Burriana › ${r.tipo === 'salida' ? 'Salidas' : 'Entradas'}): hasta entonces no se puede cerrar.</p>`;
} else {
  const motivo = r.resultado === 'no_procesada' ? r.motivo : `La app no ha contestado bien (${esc(r.error || r.message || 'sin respuesta')}).`;
  asunto = `⚠ Almacén Burriana · No he podido procesar el correo «${p.asunto}»`;
  html = `<p style='${estilo}'>No he podido preparar nada con el correo de <b>${esc(p.de)}</b> («${esc(p.asunto)}»).</p>`
    + `<p style='${estilo};color:#C00000'>${esc(motivo)}</p>` + lista(r.avisos)
    + `<p style='${estilo}'>Cárgalo a mano en la app (Stock Burriana) con los adjuntos del correo.</p>`;
}
const para = String(r.para || cfg.paraPorDefecto).split(/[;,]/).map(s => s.trim()).filter(Boolean);
return [{ json: { mensaje: { message: { subject: asunto, body: { contentType: 'HTML', content: html },
  toRecipients: para.map(a => ({ emailAddress: { address: a } })) }, saveToSentItems: true } } }];
"""

NOTA = """FLUJO «STOCK BURRIANA · CORREO»
Lee los correos que llegan a agente.burriana@stinsa.com con el albarán (PDF/Excel/fotos) o una petición de salida.
La app prepara la entrada o la salida «POR REVISAR» y la oficina recibe un aviso. Nunca se cierra nada solo.

PUESTA EN MARCHA
1. Credencial del disparador «Correo nuevo»: la del BUZÓN que recibe el alias agente.burriana@stinsa.com.
2. Credencial de «Avisar a la oficina»: «Outlook Agente Trafico» (la misma del flujo stock-burriana).
3. Nodo «Configuración»: pegar la URL de la app y la clave (= STOCK_CORREO_CLAVE en Vercel).
4. Activar el flujo y mandar un correo de prueba al alias."""


def nodo(nombre, tipo, version, params, pos, **extra):
    return {"parameters": params, "id": str(uuid.uuid4()), "name": nombre, "type": tipo, "typeVersion": version, "position": pos, **extra}


def main() -> None:
    cred_graph = {"microsoftOutlookOAuth2Api": {"id": "RKtfxCpWlTpVxiJw", "name": "Outlook Agente Trafico"}}
    nodos = [
        nodo("Correo nuevo", "n8n-nodes-base.microsoftOutlookTrigger", 1,
             {"pollTimes": {"item": [{"mode": "everyX", "value": 2, "unit": "minutes"}]}, "output": "raw",
              "filters": {"hasAttachments": True}, "options": {"downloadAttachments": True}}, [0, 0],
             credentials={"microsoftOutlookOAuth2Api": {"id": "", "name": "Outlook buzón agente.burriana"}}),
        nodo("Configuración", "n8n-nodes-base.code", 2, {"jsCode": JS_CONFIG}, [240, 0]),
        nodo("Preparar", "n8n-nodes-base.code", 2, {"jsCode": JS_PREPARAR}, [480, 0]),
        nodo("Enviar a la app", "n8n-nodes-base.httpRequest", 4.2,
             {"method": "POST", "url": "={{ $('Configuración').first().json.urlApp }}", "sendHeaders": True,
              "headerParameters": {"parameters": [{"name": "X-Clave", "value": "={{ $('Configuración').first().json.claveApp }}"}]},
              "sendBody": True, "specifyBody": "json", "jsonBody": "={{ JSON.stringify($json.payload) }}",
              "options": {"timeout": 150000, "response": {"response": {"neverError": True}}}}, [720, 0],
             retryOnFail=True, maxTries=2, waitBetweenTries=5000, onError="continueRegularOutput"),
        nodo("Redactar aviso", "n8n-nodes-base.code", 2, {"jsCode": JS_AVISO}, [960, 0]),
        nodo("Avisar a la oficina", "n8n-nodes-base.httpRequest", 4.2,
             {"method": "POST", "url": "=https://graph.microsoft.com/v1.0/users/{{ $('Configuración').first().json.buzonEnvio }}/sendMail",
              "authentication": "predefinedCredentialType", "nodeCredentialType": "microsoftOutlookOAuth2Api", "options": {},
              "sendBody": True, "specifyBody": "json", "jsonBody": "={{ JSON.stringify($json.mensaje) }}"}, [1200, 0],
             credentials=cred_graph, retryOnFail=True, maxTries=3, waitBetweenTries=3000),
        nodo("Instrucciones", "n8n-nodes-base.stickyNote", 1, {"content": NOTA, "height": 300, "width": 460}, [0, 240]),
    ]
    orden = ["Correo nuevo", "Configuración", "Preparar", "Enviar a la app", "Redactar aviso", "Avisar a la oficina"]
    conexiones = {a: {"main": [[{"node": b, "type": "main", "index": 0}]]} for a, b in zip(orden, orden[1:])}
    flujo = {"name": "STOCK BURRIANA · CORREO", "nodes": nodos, "connections": conexiones, "pinData": {}, "active": False,
             "settings": {"executionOrder": "v1", "timezone": "Europe/Madrid"}, "meta": {"templateCredsSetupCompleted": False}, "tags": []}
    DESTINO.write_text(json.dumps(flujo, ensure_ascii=False, indent=2), encoding="utf-8")
    print("escrito", DESTINO)


if __name__ == "__main__":
    main()
