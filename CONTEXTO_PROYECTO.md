# Contexto del proyecto: app de conductores «DECA Trimodos»

> Documento de traspaso para retomar el proyecto en otra conversación de Claude. **No contiene secretos** (contraseñas, claves API, códigos de acceso): esos están en `.env`, `.env.odoo18*` (ignorados por git) y en las variables de Vercel.
> Fecha: 19/10/2026. Detalle técnico completo en `README.md`; guía para conductores en `GUIA_CONDUCTORES.md`.

## 1. Qué es
PWA móvil (HTML/JS sin framework + backend FastAPI) para que los conductores de Grupo Trimodos / TRANSARGI / STINSA **corrijan al cargar la mercancía, elementos (bultos), euro palets y peso** de la orden de transporte y descarguen la **nueva orden en PDF**. Se integra con Odoo por JSON-RPC desde un backend intermedio: **el móvil nunca ve credenciales de Odoo**.

Flujo (4 pantallas): menú → escáner QR (o número de pedido + empresa) → datos actuales y edición → resumen «Antes → Ahora» + «¿Seguro?» → PDF (abrir / descargar / compartir). Todo en español, botones grandes, tema oscuro de alto contraste, instalable en Android e iPhone.

## 2. Estado (qué está hecho)
Fases 1-5 completadas: lectura QR/pedido, pantallas, escritura real + nota interna + impresión del PDF, PWA instalable (funciona sin cobertura), publicación en Vercel. Añadido después: franja de marca por empresa, guía dentro de la app, código de acceso compartido, soporte de Odoo 18, mercancía en blanco, aviso de peso por MMA.
**Calidad:** 91 tests (`pytest`) + prueba de navegador completa con cámara simulada (`scripts/prueba_navegador.py`). Todo subido a GitHub.

## 3. Entornos y datos de referencia
| | |
|---|---|
| Repo (privado) | `https://github.com/Elomarmu/trimodos-conductores` (rama `main`; commits con el correo noreply de GitHub) |
| App publicada | `https://trimodos-conductores-pcsur-elo.vercel.app` (Vercel Hobby, región `cdg1`, `maxDuration` 30 s) |
| Odoo producción (v15) | `https://www.stinsa.com`, base `prod` |
| Odoo pruebas (v18) | `https://stinsa18.tecnativa.net`, base `prod` (copia con datos reales) |
| Empresas | 1 = STINSA, 7 = TRANSARGI S.L.U |
| Pedido de prueba | **S2621473**: tarea 204067, bulto PK2627486 (id 287102), mercancía AZULEJO |
| Usuario técnico v18 | `agente.trafico@transargi.com` (clave API para datos; contraseña web solo para imprimir) |
| **Hoy Vercel apunta a** | **la v18 de pruebas**, con `DRY_RUN=false` (los guardados escriben de verdad ahí) |

Modelo Odoo: `sale.order` → `tasks_ids` → `project.task` → `tms_package_ids` (`tms.package`). Campos editables (solo esos 4): `goods_id`, `number_of_packages`, `euro_pallet_qty`, `shipping_weight`. Informe: `tms_reporting.tms_transportation_order` (id 412). El QR del PDF lleva `/web/content/<adjunto>?access_token=…`; el token se valida contra el adjunto.

## 4. Reglas de negocio acordadas
- **Editable solo si la etapa de la tarea es «Nuevo» o «En desarrollo»** (`EDITABLE_STAGES`). Sustituye a las reglas de cerrada/facturada/bultos.
- Enteros ≥ 0, peso > 0, mercancía existente. **La mercancía se puede dejar en blanco** (borrar el texto); si se escribe algo hay que elegir de la lista.
- **Peso:** no hay límite. Si el peso puesto supera `PESO_AVISO_KG` (27.000 por defecto) sale «Es posible que el conjunto supere la MMA autorizada, confirme que es correcto el peso» y, si confirma, pasa al resumen. 44.000 kg es la MMA del conjunto, **no** el peso de la mercancía (error corregido). El umbral vive solo en el servidor.
- **Pedido escrito a mano:** hay que elegir empresa (el mismo número puede existir en STINSA y TRANSARGI).
- Una tarea cuenta como orden de transporte si tiene `is_transport_order`, **o** una orden ya generada, **o** bultos.
- **Orden sin bultos** (p. ej. contenedor/grupaje): se lee pero no se edita; mensaje «Ponte en contacto con tu responsable de tráfico».
- Antes de escribir se revalida: sesión firmada, bultos de esa tarea, `write_date` sin cambios, etapa.
- Cada guardado deja una **nota interna** en la tarea («Modificado desde la app de conductores», antes → ahora). La última pantalla no muestra avisos de peso.
- Acceso: **código compartido** obligatorio en modo real (pantalla única la primera vez por dispositivo). Pendiente futuro: PIN por conductor.

## 5. Lecciones importantes (no redescubrir)
- **Una clave API NO sirve para imprimir el PDF**: el informe exige sesión web y Odoo no admite claves API (ni Basic ni Bearer) ahí. Hace falta contraseña (`ODOO_WEB_PASSWORD`) y el usuario **sin verificación en dos pasos** (con 2FA devuelve `{uid: null}`). Mejora pendiente: pedir a Tecnativa un método público que genere la orden.
- **El informe lee tractor y remolque** (`fleet.vehicle`): el usuario técnico necesita *Fleet / Administrator*; con solo *Officer* da **403**. Pedir a Tecnativa que lo lea con `sudo`.
- **Odoo 18**: `project.task` no tiene `invoice_count` (ya no se usa); `message_post` escapa el HTML por RPC → se envía `body_is_html=True` solo en ≥ 18. Empresas y campos TMS iguales.
- **Vercel**: no usar `rewrites` a `/api/index` (FastAPI se autodetecta y recibía la ruta reescrita → 404); desactivar *Vercel Authentication* (Settings → Deployment Protection); en Hobby solo despliega commits **cuyo autor está vinculado a la cuenta de GitHub**. Hobby es solo uso personal/no comercial → para conductores, Pro u otro alojamiento.
- Guardar + imprimir tarda **4-6 s**. Cada guardado real crea un PDF y una nota que no se borran solos.
- El usuario técnico tiene **permisos de más** (borrar tareas/bultos, etc.). La app no puede hacerlo (lista blanca en `odoo_client.py`, con tests), pero conviene recortarlos.

## 6. Cómo trabajamos (preferencias del usuario)
- Producción v15 **solo con el pedido S2621473 y con permiso expreso cada vez**; la v18 es entorno de pruebas (aun así, pedir permiso antes de escribir).
- `DRY_RUN=true` hace todo menos escribir/publicar/imprimir.
- El usuario **no tiene experiencia con servidores**: instrucciones paso a paso y en español.
- **Preguntar «¿lo subo?» antes de `git push`** (el push despliega solo en Vercel); el usuario responde «sube».
- Todo texto de la app en español, mensajes claros y no técnicos; un solo botón grande en el menú.
- Secretos: nunca en el chat ni en git; el usuario los escribe en los `.env`.
- Windows/PowerShell: usar los `.cmd` para arrancar (los `.ps1` están bloqueados); `ENV_FILE` elige el entorno y `load_dotenv` **no pisa** variables ya cargadas (ejecutar cada usuario en un proceso aparte); ojo con `\n` literal en cadenas y con filtros `-notmatch 'INFO'` (también cazan «Informe»).

## 7. Comandos clave
```powershell
.\iniciar-demo.cmd                 # simulado (no toca Odoo)
.\iniciar.cmd                      # modo del .env
.\iniciar-v18.cmd [archivo]        # Odoo 18 (.env.odoo18 o .env.odoo18-tecnico)
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -X utf8 -m scripts.prueba_navegador
.\.venv\Scripts\python.exe -X utf8 -m scripts.comprobar_compatibilidad     # con ENV_FILE, solo lectura
.\.venv\Scripts\python.exe -X utf8 -m scripts.comprobar_permisos
.\.venv\Scripts\python.exe -X utf8 -m scripts.generar_guia                  # tras editar GUIA_CONDUCTORES.md
```

## 8. Pendiente / siguientes pasos
1. Decidir si Vercel pasa a `DRY_RUN=true` (hoy escribe de verdad en la v18 de pruebas).
2. Con Tecnativa: método público de impresión; informe de vehículos con `sudo`; recortar permisos del usuario técnico.
3. Migración a v18 en producción: solo cambiar variables de Vercel (`ODOO_URL`, `ODOO_USER`, `ODOO_PASSWORD`, `ODOO_WEB_PASSWORD`) y redesplegar.
4. Alojamiento definitivo para conductores (Vercel Pro, servidor propio o Tecnativa).
5. PIN por conductor (identificación y trazabilidad).
6. Opcional: nombre del usuario técnico más claro en las notas («Agente Claude» hoy); ampliar a órdenes sin `tms.package` si hiciera falta.
