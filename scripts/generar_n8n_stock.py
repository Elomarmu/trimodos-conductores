"""Genera n8n/stock-burriana.json (flujo de n8n del almacén de Burriana) a partir de n8n/pr-saica.json.

Es un flujo APARTE del de PR (el de PR está en producción y no se toca): mismas credenciales («Microsoft Excel (SharePoint RRHH)»
y «Outlook Agente Trafico»), mismo patrón (la app manda un POST con la cabecera X-Clave; n8n sube a SharePoint y manda el correo
como borrador + adjuntos de uno en uno + enviar, con las esperas que arreglaron el error 429 de Outlook).

Una sola ejecución por entrada o salida cerrada (el usuario quiere el mínimo de ejecuciones): sube a SharePoint › Almacen Burriana
la Excel de la operación y sus fotos (carpeta «ENTRADA E-2026-0001») y el libro de control, y manda UN correo con la Excel y las fotos.

Uso:  python scripts/generar_n8n_stock.py        (después, en n8n: Importar desde archivo y reconectar las credenciales)
"""
from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ORIGEN, DESTINO = RAIZ / "n8n" / "pr-saica.json", RAIZ / "n8n" / "stock-burriana.json"
CARPETA_ALMACEN = "https://grupotrimodos.sharepoint.com/:f:/s/GestionStinsa2/IgC4a5RrKT63TqFhZy7CSgyYAV-VAfwlPPwR1dj_xGwfNjE?e=aJu7Sj"

JS_CONFIG = f"""// Ajustes del flujo STOCK BURRIANA (app de oficina y almacén → SharePoint y correo)
const carpeta = '{CARPETA_ALMACEN}';  // SharePoint › GestionStinsa2 › Almacen Burriana
return [{{ json: {{
  claveWebhook: '<<PONER EN N8N: la misma que STOCK_AVISOS_CLAVE en Vercel>>',
  buzonEnvio: 'agente.trafico@transargi.com',            // buzón de la credencial «Outlook Agente Trafico»
  paraPorDefecto: 'carmelomartinez@stinsa.com',          // pruebas; luego traficolevante@stinsa.com (lo decide la app: STOCK_CORREO_PARA)
  carpetaGraph: 'u!' + Buffer.from(carpeta).toString('base64').replace(/=+$/, '').replace(/\\//g, '_').replace(/\\+/g, '-'),
}}}}];
"""

JS_DATOS = """// Comprueba la clave y toma lo que manda la app.
const cfg = $('Configuración').first().json;
const c = $('Buscar la carpeta').first().json;   // carpeta «Almacen Burriana»: unidad (drive) e id
const w = $('Desde la app').first().json;
const clave = (w.headers || {})['x-clave'] || '';
if (!cfg.claveWebhook || clave !== cfg.claveWebhook) throw new Error('Aviso de stock con clave incorrecta: revisa STOCK_AVISOS_CLAVE en Vercel y «claveWebhook» en Configuración');
const d = w.body;
d.base = `https://graph.microsoft.com/v1.0/drives/${c.parentReference.driveId}/items/${c.id}`;
d.urlCrearCarpeta = `${d.base}/children`;
d.llevaCarpeta = !!d.carpeta;
return [{ json: d }];
"""

JS_PREPARAR = """// Un elemento por archivo o foto que subir a SharePoint y uno para el correo (si lo lleva).
const cfg = $('Configuración').first().json;
const d = $('Datos').first().json;
const ruta = (...partes) => partes.filter(Boolean).map(encodeURIComponent).join('/');
const tipoDe = (n, t) => t || (/\\.xlsx$/i.test(n) ? 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
  : /\\.pdf$/i.test(n) ? 'application/pdf' : /\\.png$/i.test(n) ? 'image/png' : 'image/jpeg');
// Los archivos con sharepoint:false (p. ej. la documentación del aviso a un cliente) solo van al correo.
const subir = [...(d.archivos || []), ...(d.fotos || [])].filter(a => a.sharepoint !== false).map(a => ({
  json: { tipo: 'sharepoint', nombre: a.nombre, url: `${d.base}:/${ruta(a.carpeta, a.nombre)}:/content?@microsoft.graph.conflictBehavior=replace` },
  binary: { archivo: { data: a.b64, mimeType: tipoDe(a.nombre, a.tipo), fileName: a.nombre } } }));
const para = String(d.para || cfg.paraPorDefecto).split(/[;,]/).map(s => s.trim()).filter(Boolean);
const salida = [...subir];
// buzon: la app puede pedir otro buzón (p. ej. traficolevante@stinsa.com para los avisos a clientes); si no, el de la credencial.
if (d.correo !== false) salida.push({ json: { tipo: 'correo', buzon: d.buzon || cfg.buzonEnvio,
  mensaje: { subject: d.asunto || 'Almacén Burriana', body: { contentType: 'HTML', content: String(d.html || '') },
             toRecipients: para.map(a => ({ emailAddress: { address: a } })) } } });
return salida;
"""

JS_ADJUNTOS = """// La Excel de la operación y las fotos se añaden de una en una al borrador (así no hay límite de tamaño del correo).
// El libro de control (correo: false) solo va a SharePoint: lleva el stock de todos los clientes y no se reenvía.
const d = $('Datos').first().json;
const borrador = $('Crear borrador').first().json;
const buzon = d.buzon || $('Configuración').first().json.buzonEnvio;
const url = `https://graph.microsoft.com/v1.0/users/${buzon}/messages/${borrador.id}/attachments`;
const todos = [...(d.archivos || []).filter(a => a.correo !== false).map(a => ({ name: a.nombre, contentBytes: a.b64, contentType: a.tipo })),
               ...(d.fotos || []).map(f => ({ name: f.nombre, contentBytes: f.b64, contentType: f.tipo }))];
if (!todos.length) return [{ json: { sinAdjuntos: true } }];
return todos.map(a => ({ json: { sinAdjuntos: false, url, adjunto: { '@odata.type': '#microsoft.graph.fileAttachment', ...a } } }));
"""

NOTA = """## STOCK BURRIANA · app de oficina y almacén → SharePoint y correo
**Desde la app** (POST con cabecera X-Clave, una llamada por operación):
- entrada_cerrada / salida_cargada: crea la carpeta «ENTRADA E-AAAA-NNNN» / «SALIDA S-AAAA-NNNN» en Almacen Burriana, sube la Excel y las fotos, actualiza CONTROL ALMACEN BURRIANA.xlsx y manda UN correo con la Excel y las fotos.
- inventario_cerrado / control_cerrado: igual que arriba (el control solo actualiza el libro y manda el resumen por correo).
- aviso_llegada: NO se envía. Deja un BORRADOR en el buzón indicado por la app (traficolevante@stinsa.com) dirigido al contacto del cliente, con la documentación del transporte adjunta (no se sube a SharePoint). Una persona lo revisa y lo manda. Requiere que la credencial «Outlook Agente Trafico» tenga acceso a ese buzón.
- libro: solo rehace el libro de control en SharePoint (sin correo).

Los correos se crean como borrador, se les añaden los adjuntos uno a uno y se envían (sin límite de tamaño) desde agente.trafico@.
En «Configuración»: la clave del webhook (la misma que STOCK_AVISOS_CLAVE en Vercel). Destinatarios: los decide la app (STOCK_CORREO_PARA).
Flujo APARTE del de PR: no lo toques desde allí."""


def generar() -> dict:
    flujo = json.loads(ORIGEN.read_text(encoding="utf-8"))
    nodos = {n["name"]: n for n in flujo["nodes"]}
    quedan = ["Desde la app", "Configuración", "Buscar la carpeta", "Datos (app o resumen)", "Crear carpeta del tren", "Preparar SharePoint y correo",
              "¿SharePoint o correo?", "Subir Excel a SharePoint", "Crear borrador", "Adjuntos", "¿Sin adjuntos?", "Añadir adjunto", "Enviar correo", "Instrucciones"]
    res = [copy.deepcopy(nodos[n]) for n in quedan]
    por_nombre = {n["name"]: n for n in res}
    flujo["name"] = "STOCK BURRIANA · fotos, Excel en SharePoint y correos de entradas y salidas (pruebas)"
    por_nombre["Desde la app"]["parameters"]["path"] = "stock-burriana"
    por_nombre["Desde la app"]["webhookId"] = str(uuid.uuid4())
    por_nombre["Configuración"]["parameters"]["jsCode"] = JS_CONFIG
    datos = por_nombre["Datos (app o resumen)"]
    datos["name"] = "Datos"
    datos["parameters"]["jsCode"] = JS_DATOS
    por_nombre["Preparar SharePoint y correo"]["parameters"]["jsCode"] = JS_PREPARAR
    por_nombre["Adjuntos"]["parameters"]["jsCode"] = JS_ADJUNTOS
    por_nombre["Instrucciones"]["parameters"]["content"] = NOTA
    por_nombre["Subir Excel a SharePoint"]["name"] = "Subir a SharePoint"
    carpeta = por_nombre["Crear carpeta del tren"]
    carpeta["name"] = "Crear carpeta"
    carpeta["onError"] = "continueRegularOutput"   # si ya existe, sigue
    carpeta["executeOnce"] = True
    carpeta["parameters"]["jsonBody"] = "={{ JSON.stringify({ name: $json.carpeta, folder: {}, '@microsoft.graph.conflictBehavior': 'fail' }) }}"
    # ¿Lleva carpeta? (las operaciones sí; «libro» no): reutiliza el nodo «¿Sin adjuntos?» como modelo de condición booleana
    lleva = copy.deepcopy(nodos["¿Sin adjuntos?"])
    lleva.update(name="¿Lleva carpeta?", id=str(uuid.uuid4()), position=[1120, 200])
    lleva["parameters"]["conditions"]["conditions"][0]["leftValue"] = "={{ $json.llevaCarpeta }}"
    res.insert(res.index(carpeta), lleva)
    # ¿Se envía? (enviar:false = solo deja el borrador, p. ej. el aviso de llegada al cliente)
    envia = copy.deepcopy(nodos["¿Sin adjuntos?"])
    envia.update(name="¿Se envía?", id=str(uuid.uuid4()), position=[2660, 300], executeOnce=True)
    envia["parameters"]["conditions"]["conditions"][0]["leftValue"] = "={{ $('Datos').first().json.enviar !== false }}"
    res.insert(res.index(por_nombre["Enviar correo"]), envia)
    por_nombre["Enviar correo"]["parameters"]["url"] = ("=https://graph.microsoft.com/v1.0/users/{{ $('Datos').first().json.buzon || $('Configuración').first().json.buzonEnvio }}"
                                                       "/messages/{{ $('Crear borrador').first().json.id }}/send")
    por_nombre["Enviar correo"]["position"] = [2880, 300]
    flujo["nodes"] = res
    flujo["connections"] = {
        "Desde la app": {"main": [[{"node": "Configuración", "type": "main", "index": 0}]]},
        "Configuración": {"main": [[{"node": "Buscar la carpeta", "type": "main", "index": 0}]]},
        "Buscar la carpeta": {"main": [[{"node": "Datos", "type": "main", "index": 0}]]},
        "Datos": {"main": [[{"node": "¿Lleva carpeta?", "type": "main", "index": 0}]]},
        "¿Lleva carpeta?": {"main": [[{"node": "Crear carpeta", "type": "main", "index": 0}], [{"node": "Preparar SharePoint y correo", "type": "main", "index": 0}]]},
        "Crear carpeta": {"main": [[{"node": "Preparar SharePoint y correo", "type": "main", "index": 0}]]},
        "Preparar SharePoint y correo": {"main": [[{"node": "¿SharePoint o correo?", "type": "main", "index": 0}]]},
        "¿SharePoint o correo?": {"main": [[{"node": "Subir a SharePoint", "type": "main", "index": 0}], [{"node": "Crear borrador", "type": "main", "index": 0}]]},
        "Crear borrador": {"main": [[{"node": "Adjuntos", "type": "main", "index": 0}]]},
        "Adjuntos": {"main": [[{"node": "¿Sin adjuntos?", "type": "main", "index": 0}]]},
        "¿Sin adjuntos?": {"main": [[{"node": "¿Se envía?", "type": "main", "index": 0}], [{"node": "Añadir adjunto", "type": "main", "index": 0}]]},
        "Añadir adjunto": {"main": [[{"node": "¿Se envía?", "type": "main", "index": 0}]]},
        "¿Se envía?": {"main": [[{"node": "Enviar correo", "type": "main", "index": 0}]]},
    }
    flujo["active"] = False
    return flujo


if __name__ == "__main__":
    DESTINO.write_text(json.dumps(generar(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{DESTINO.relative_to(RAIZ)} generado")
