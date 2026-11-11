# Contexto del proyecto: app de conductores «DECA Trimodos»

> Documento de traspaso para retomar el proyecto en otra conversación de Claude. **No contiene secretos** (contraseñas, claves API, códigos de acceso): esos están en `.env`, `.env.odoo18*` (ignorados por git) y en las variables de Vercel.
> Fecha: 19/10/2026. Detalle técnico completo en `README.md`; guía para conductores en `GUIA_CONDUCTORES.md`.

## 1. Qué es
PWA móvil (HTML/JS sin framework + backend FastAPI) para que los conductores de Grupo Trimodos / TRANSARGI / STINSA corrijan **mercancía, bultos y kilos por lugar**, y **tractora/remolque por orden**, y descarguen la **nueva orden en PDF**. Los euro palets se muestran en solo lectura. Se integra con Odoo por JSON-RPC desde un backend intermedio: **el móvil nunca ve credenciales de Odoo**.

Flujo: menú → escáner QR (o número de pedido + empresa) → (si hay varias) «Elige tu orden» → datos actuales y edición → resumen «Antes → Ahora» + «¿Seguro?» → PDF (abrir / descargar / compartir). Todo en español, botones grandes, tema oscuro de alto contraste, instalable en Android e iPhone.

## 2. Estado (qué está hecho)
Fases 1-5 completadas: lectura QR/pedido, pantallas, escritura + nota interna + impresión del PDF, PWA instalable (funciona sin cobertura), publicación en Vercel. Añadido después: franja de marca por empresa, guía dentro de la app, código de acceso compartido, soporte de Odoo 18, mercancía en blanco, aviso de peso por MMA, validación de tractora/remolque y creación diferida de mercancías.
**Calidad en `deca-tractora-remolque` (11/11/2026):** `pytest -q`: 187 pasados, 36 omitidos; `scripts.prueba_navegador`: correcto, incluida la selección entre varias órdenes.

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

Modelo Odoo: `sale.order` → `tasks_ids` → `project.task` → `tms_package_ids` (`tms.package`). Campos editables por bulto: `goods_id`, `number_of_packages`, `shipping_weight`; `euro_pallet_qty` es solo lectura. La tarea guarda los vehículos en `tractor_id` y `trailer_id` (`fleet.vehicle`); la matrícula está en `license_plate`. En las bases v15 y v18 probadas, el informe 412 (`tms_reporting.tms_transportation_order`) usa esos dos campos para la cabecera «Tractora / Remolque». El QR del PDF lleva `/web/content/<adjunto>?access_token=…`; el token se valida contra el adjunto.

## 4. Reglas de negocio acordadas
- **Editable solo si la etapa de la tarea es «Nuevo» o «En desarrollo»** (`EDITABLE_STAGES`). Sustituye a las reglas de cerrada/facturada/bultos.
- Bultos entero ≥ 0, peso > 0. Euro palets se muestran sin poder modificarse. **La mercancía se puede dejar en blanco**; si se elige una mercancía existente se selecciona de la lista. La nueva se normaliza a mayúsculas, se confirma y solo se crea al guardar; `tms.goods.name` es el único campo obligatorio conocido.
- La tractora es obligatoria y el remolque opcional. Se normalizan las matrículas ignorando todo lo que no sea letra o número, se buscan en Odoo y se revalidan en servidor; no se crean vehículos. Se admiten vehículos sin compañía o de la compañía de la tarea. Las matrículas duplicadas requieren selección; el tipo de vehículo se advierte y se puede confirmar.
- Investigación de flota: `fleet.vehicle.license_plate` guarda la matrícula; `vehicle_type` distingue tractor/remolque (también se ve reflejado en el nombre del modelo). Hay vehículos sin compañía y otros asignados a STINSA o TRANSARGI; la app solo ofrece los de la compañía de la tarea o sin compañía.
- En las comprobaciones de solo lectura v15 y v18 existen los campos, funciona `name_search`, el informe 412 está instalado y las credenciales de cada entorno abren sesión web. En v18 el usuario técnico tiene los permisos nuevos necesarios (escritura de tarea, creación de mercancía y lectura de flota). Aún conserva permisos sobrantes: `project.task.unlink`, `tms.package.create/unlink`, `tms.goods.write` y `sale.order.write`; solicitar a Tecnativa que los retire.
- `tms.goods.name` es el único campo obligatorio por metadatos; `adr_id` es opcional. No se imprimió un PDF real porque Odoo crea un adjunto nuevo; el PDF de muestra simulado valida tractora y remolque.
- **Peso:** no hay límite. Si el peso puesto supera `PESO_AVISO_KG` (27.000 por defecto) sale «Es posible que el conjunto supere la MMA autorizada, confirme que es correcto el peso» y, si confirma, pasa al resumen. 44.000 kg es la MMA del conjunto, **no** el peso de la mercancía (error corregido). El umbral vive solo en el servidor.
- **Pedido escrito a mano:** hay que elegir empresa (el mismo número puede existir en STINSA y TRANSARGI). Si hay varias órdenes de transporte, se muestra una lista por fecha de carga con nombre de tarea, ruta, vehículos y etapa; las no editables se ven en gris con el motivo. El servidor comprueba que la tarea elegida siga vinculada al pedido y a la empresa. El nombre de la tarea aparece en edición, resumen y nota interna.
- Una tarea cuenta como orden de transporte si tiene `is_transport_order`, **o** una orden ya generada, **o** bultos.
- **Orden sin bultos** (p. ej. contenedor/grupaje): se lee pero no se edita; mensaje «Ponte en contacto con tu responsable de tráfico».
- Antes de escribir se revalida: sesión firmada, bultos de esa tarea, `write_date` de tarea y bultos, etapa y matrículas actuales en Odoo.
- Cada guardado deja una **nota interna** en la tarea («Modificado desde la app de conductores», antes → ahora, incluidos vehículos y mercancía nueva). Se conserva esa cabecera para n8n; «Preparar aviso» usa el cuerpo completo de la nota y no requiere cambio.
- El usuario técnico necesita lectura de `fleet.vehicle`, escritura de `tractor_id`/`trailer_id` en `project.task` y creación de `tms.goods` con `name`. No imprimir un PDF real para validar el cambio sin permiso: imprimir crea un adjunto nuevo.
- La app de oficina y almacén vive en `/oficina/`; `scripts/generar_oficina.py` la genera desde `public/index.html` y debe ejecutarse tras cambiar la carcasa. Usa `APP_OFICINA_CODE` si está configurado (mínimo 8 caracteres); si no, comparte `APP_ACCESS_CODE`. No modificar ni añadir a git el JSON local de n8n sin permiso.
- Acceso: **código compartido** obligatorio en modo real (pantalla única la primera vez por dispositivo). Pendiente futuro: PIN por conductor.

## 5. Lecciones importantes (no redescubrir)
- **Una clave API NO sirve para imprimir el PDF**: el informe exige sesión web y Odoo no admite claves API (ni Basic ni Bearer) ahí. Hace falta contraseña (`ODOO_WEB_PASSWORD`) y el usuario **sin verificación en dos pasos** (con 2FA devuelve `{uid: null}`). Mejora pendiente: pedir a Tecnativa un método público que genere la orden.
- **El informe lee tractor y remolque** (`fleet.vehicle`): necesita acceso de Fleet. En una consulta de solo lectura a Odoo v18 (11/11/2026), el usuario técnico pertenece a *Fleet / Administrator*, tiene permiso de lectura en `fleet.vehicle`, puede abrir sesión web y el informe está instalado. El error de PDF posterior a una prueba no tiene causa confirmada: los logs de runtime de Vercel no estaban disponibles desde este entorno. No pedir cambios a Tecnativa sin evidencia; si el log indica 403 pese al grupo Administrador, pedir que el informe `tms_reporting.tms_transportation_order` lea los vehículos con `sudo`.
- **Odoo 18**: `project.task` no tiene `invoice_count` (ya no se usa); `message_post` escapa el HTML por RPC → se envía `body_is_html=True` solo en ≥ 18. Empresas y campos TMS iguales.
- **Vercel**: no usar `rewrites` a `/api/index` (FastAPI se autodetecta y recibía la ruta reescrita → 404); desactivar *Vercel Authentication* (Settings → Deployment Protection); en Hobby solo despliega commits **cuyo autor está vinculado a la cuenta de GitHub**. Hobby es solo uso personal/no comercial → para conductores, Pro u otro alojamiento.
- Guardar + imprimir tarda **4-6 s**. Cada guardado real crea un PDF y una nota que no se borran solos.
- El usuario técnico tiene **permisos de más** (borrar tareas/bultos, etc.). La app no puede hacerlo (lista blanca en `odoo_client.py`, con tests), pero conviene recortarlos.

## 6. Cómo trabajamos (preferencias del usuario)
- Producción v15 **solo con el pedido S2621473 y con permiso expreso cada vez**; la v18 es entorno de pruebas (aun así, pedir permiso antes de escribir o imprimir).
- `DRY_RUN=true` hace todo menos escribir/publicar/imprimir.
- El usuario **no tiene experiencia con servidores**: instrucciones paso a paso y en español.
- **Preguntar «¿lo subo?» antes de `git push`** (el push despliega solo en Vercel); el usuario responde «sube».
- Todo texto de la app en español, mensajes claros y no técnicos; un solo botón grande en el menú.
- Secretos: nunca en el chat ni en git; el usuario los escribe en los `.env`.
- Windows/PowerShell: usar los `.cmd` para arrancar (los `.ps1` están bloqueados); `ENV_FILE` elige el entorno y `load_dotenv` **no pisa** variables ya cargadas (ejecutar cada usuario en un proceso aparte); ojo con `\n` literal en cadenas y con filtros `-notmatch 'INFO'` (también cazan «Informe»).
- Mac: mismos arranques en `.sh` (`./preparar.sh` la primera vez: el `python3` del Mac es 3.9 y la app necesita ≥ 3.10). `.gitattributes` mantiene los `.sh` con saltos de línea de Unix. La prueba de navegador usa Edge en Windows y Chrome en Mac (`NAVEGADOR` para forzarlo).

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
```bash
# Mac
./preparar.sh                      # solo la primera vez: crea .venv con un Python ≥ 3.10
./iniciar-demo.sh | ./iniciar.sh | ./iniciar-v18.sh [archivo]
.venv/bin/python -m pytest -q
.venv/bin/python -X utf8 -m scripts.prueba_navegador
ENV_FILE=.env.odoo18 .venv/bin/python -X utf8 -m scripts.comprobar_compatibilidad
```

## 8. Pendiente / siguientes pasos
1. Decidir si Vercel pasa a `DRY_RUN=true` (hoy escribe de verdad en la v18 de pruebas).
2. Con Tecnativa: método público de impresión; informe de vehículos con `sudo`; recortar permisos del usuario técnico.
3. Migración a v18 en producción: solo cambiar variables de Vercel (`ODOO_URL`, `ODOO_USER`, `ODOO_PASSWORD`, `ODOO_WEB_PASSWORD`) y redesplegar.
4. Alojamiento definitivo para conductores (Vercel Pro, servidor propio o Tecnativa).
5. PIN por conductor (identificación y trazabilidad).
6. Opcional: nombre del usuario técnico más claro en las notas («Agente Claude» hoy); ampliar a órdenes sin `tms.package` si hiciera falta.
