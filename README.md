# App de conductores · Grupo Trimodos / TRANSARGI

PWA para móvil que permite al conductor corregir, al cargar, la **mercancía, los elementos (bultos), los euro palets y el peso** de la orden de transporte, y descargar la nueva orden en PDF. Se integra con **Odoo 15** (JSON-RPC) a través de un pequeño backend (FastAPI): el móvil nunca ve credenciales de Odoo.

> Guía de una página para los conductores: [GUIA_CONDUCTORES.md](GUIA_CONDUCTORES.md)

**Estado:** prototipo. Antes de ponerlo en manos de conductores, lee [Seguridad](#7-seguridad) y [Alojamiento](#6-alojamiento-y-despliegue).

---

## 1. Arrancar en local (Windows y Mac)

Funciona igual en los dos: los archivos **`.cmd` son para Windows** y los **`.sh` para Mac**. Todo se ejecuta en la terminal de VS Code, dentro de la carpeta del proyecto.

### Una sola vez

**Windows** (PowerShell):
1. **Instalar Python y Git:**
   ```powershell
   winget install -e --id Python.Python.3.12
   winget install -e --id Git.Git
   ```
   Cierra y vuelve a abrir VS Code y comprueba: `python --version` y `git --version`.
2. **Preparar el proyecto:**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
   ```

**Mac** (Terminal):
1. **Python 3.10 o más nuevo.** El que trae el Mac es antiguo (3.9). Si no tienes otro: `brew install python@3.12` (o descárgalo de [python.org](https://www.python.org/downloads/)).
2. **Preparar el proyecto:** `./preparar.sh` (busca un Python que valga, crea `.venv` e instala lo necesario).

**En los dos:** crea el `.env` copiando `.env.example` como `.env` y rellénalo (ver [Configuración](#2-configuración-env)). **Nunca lo subas a git** (ya está en `.gitignore`).

### Cada vez que quieras usarla
| Quiero… | Windows | Mac |
|---|---|---|
| Probar **sin tocar Odoo** (datos simulados) | `.\iniciar-demo.cmd` | `./iniciar-demo.sh` |
| Usar el modo del `.env` (normalmente `real`) | `.\iniciar.cmd` | `./iniciar.sh` |
| Usar el Odoo 18 de pruebas (`.env.odoo18`) | `.\iniciar-v18.cmd [archivo]` | `./iniciar-v18.sh [archivo]` |

Después abre **http://localhost:8000** en el navegador. La cámara web funciona porque `localhost` se considera seguro.

- **¿Funciona?** En `http://localhost:8000/api/health` debe salir `{"ok":true,"modo":"mock"...}` (o `"real"`). En modo simulado la pantalla inicial muestra «Modo de prueba» y la pantalla del escáner tiene un botón «Usar QR de prueba».
- **Parar el servidor:** `Ctrl+C` en la terminal donde lo arrancaste.
- Si Windows dice que *«la ejecución de scripts está deshabilitada»*, usa los `.cmd` (no los `.ps1`).
- Si el Mac dice *«permission denied»* al lanzar un `.sh`, ejecútalo con `bash`, por ejemplo `bash iniciar-demo.sh`.
- Las variables que ya estén puestas en la terminal **mandan sobre el `.env`** (`load_dotenv` no las pisa): arranca cada modo en una terminal limpia.

**Comandos de Python:** en este README salen con la ruta de Windows (`.\.venv\Scripts\python.exe`). En el Mac es `.venv/bin/python`, y las variables se ponen delante del comando: `ENV_FILE=.env.odoo18 .venv/bin/python …` en lugar de `$env:ENV_FILE=".env.odoo18"; …`.

## 2. Configuración (`.env`)

| Variable | Qué es |
|---|---|
| `ODOO_MODE` | `mock` (datos simulados) o `real` (Odoo de verdad). |
| `DRY_RUN` | `true`: hace todo **menos escribir/publicar/imprimir** en Odoo (devuelve la orden actual como PDF). `false`: guarda de verdad. **Déjalo en `true` salvo en pruebas autorizadas.** |
| `ODOO_URL`, `ODOO_DB` | Producción: `https://www.stinsa.com`, base `prod`. |
| `ODOO_USER`, `ODOO_PASSWORD` | Usuario y **contraseña** (o API key donde Odoo la admita; ver abajo). |
| `ODOO_COMPANY_IDS` | `1,7` (1 = STINSA, 7 = TRANSARGI S.L.U). |
| `SECRET_KEY` | Clave larga aleatoria para firmar las sesiones (obligatoria en `real`). |
| `APP_ACCESS_CODE` | **Código de acceso compartido** (mínimo 8 caracteres). En modo `real` es **obligatorio**: sin él, la API se niega a funcionar (error 503). Los conductores lo escriben una vez en el móvil y se queda guardado. En modo `mock`, si está vacío, no se pide. |
| `QR_ALLOWED_HOSTS` | Dominios extra aceptados dentro del QR (p. ej. la demo de Tecnativa). |

**Cómo averiguar la base de datos:** Odoo suele tener desactivado el listado. Con la sesión iniciada, abre la consola del navegador (F12) y escribe `odoo.session_info.db`. Para producción es `prod`.

**Contraseña o API key:** la API key (Mis preferencias → Seguridad de la cuenta → Nueva clave API) se acepta como contraseña en `/jsonrpc`. Para imprimir el PDF la app abre una **sesión web** (`/web/session/authenticate`); con el usuario actual se ha verificado que funciona con la **contraseña**. Si quieres usar API key, comprueba antes que también sirve para esa sesión.

## 3. Pruebas

```powershell
.\.venv\Scripts\python.exe -m pytest -q                                   # lógica del backend (QR, validaciones, tokens, DRY_RUN, cliente Odoo simulado)
.\.venv\Scripts\python.exe -X utf8 -m scripts.prueba_navegador            # navegador real + cámara simulada con un QR + PWA + sin conexión
.\.venv\Scripts\python.exe -X utf8 -m scripts.diagnostico S2621473        # SOLO LECTURA contra Odoo (.env en real)
.\.venv\Scripts\python.exe -X utf8 -m scripts.comprobar_permisos          # permisos del usuario de Odoo, sin escribir nada
```
En el Mac, los mismos con `.venv/bin/python` (por ejemplo `.venv/bin/python -m pytest -q`). La prueba de navegador usa Edge en Windows y Chrome en Mac (se puede forzar con `NAVEGADOR=chrome`, `msedge` o `chromium`).

**Producción solo con el pedido de prueba S2621473** (tarea 204067, bulto PK2627486, id 287102) y con permiso expreso: cada guardado real crea un PDF y una nota nuevos en la tarea, que no se borran solos.

## 4. Probar en el móvil antes de publicar (opcional)

La cámara del móvil exige **HTTPS**; `localhost` solo vale en el propio ordenador. Un túnel temporal da una dirección `https://` pública mientras esté abierto.

**Cloudflare (sin cuenta):**
```powershell
winget install -e --id Cloudflare.cloudflared          # en el Mac: brew install cloudflared
# 1) arranca la app:   .\iniciar-demo.cmd              (Mac: ./iniciar-demo.sh; recomendado: datos simulados)
# 2) en otra terminal: cloudflared tunnel --url http://localhost:8000
```
Copia la dirección `https://….trycloudflare.com` que aparece y ábrela en el Android (Chrome → ⋮ → *Instalar aplicación*). Cierra el túnel con `Ctrl+C`.

**ngrok (alternativa):** instala ngrok, crea cuenta gratuita, `ngrok config add-authtoken <token>` y `ngrok http 8000`.

> ⚠️ Mientras el túnel esté abierto, **cualquiera con la dirección puede usar la app**. Úsalo con `iniciar-demo.cmd` / `iniciar-demo.sh`; con datos reales, solo el tiempo imprescindible.

## 5. Usuario técnico en Odoo (sustituir a tu usuario administrador)

El cambio es **solo tocar el `.env`** (`ODOO_USER`, `ODOO_PASSWORD`). Aun con tu usuario, el código solo puede hacer las operaciones de [Seguridad](#seguridad).

1. Crear un usuario interno (p. ej. `app.conductores@…`) **sin grupos de administración**, con acceso a las compañías **STINSA (1) y TRANSARGI (7)**, idioma español, y contraseña larga aleatoria. (Sin doble factor, o la sesión web fallará.)
2. Darle solo estos permisos:

| Modelo | Permisos | Para qué |
|---|---|---|
| `project.task` | leer **y escribir** | leer la orden; Odoo exige *escritura* para publicar la nota (`message_post`) |
| `tms.package` | leer **y escribir** (sin crear ni borrar) | editar los 4 campos |
| `tms.goods` | solo leer | autocompletar mercancía |
| `ir.attachment` | leer (y lo que Odoo necesite para generar el adjunto al imprimir) | comprobar el token del QR y el PDF |
| `sale.order` | solo leer | localizar la tarea por número de pedido |
| Informe 412 (`tms_reporting.tms_transportation_order`) | poder imprimirlo | generar la nueva orden |

3. **Comprobarlo sin escribir nada** (con el `.env` apuntando al usuario nuevo):
   ```powershell
   .\.venv\Scripts\python.exe -X utf8 -m scripts.comprobar_permisos     # Mac: .venv/bin/python -X utf8 -m scripts.comprobar_permisos
   ```
   Debe terminar en «Todo correcto». Con un administrador salen varios «FALLA»: es lo esperado. Esta comprobación solo ve los permisos por modelo; **las reglas de registro (record rules) y la impresión real conviene validarlas con Tecnativa** y con una prueba autorizada sobre S2621473.
4. Rotar la contraseña si alguna vez se ha pegado en un chat o se ha compartido.

## 6. Alojamiento y despliegue

El proyecto ya está preparado para **Vercel**: FastAPI como función Python (`api/index.py`), frontend estático en `public/`, y `vercel.json`. No usa archivos locales ni procesos permanentes, y el mismo código corre en local.

### Pasos
1. Cuenta en **GitHub** y repositorio **privado**; subir el proyecto (`.env` no se sube): `git remote add origin <url>` y `git push -u origin main`.
2. Cuenta en **Vercel** → *Add New Project* → importar el repositorio (preset **Other**, sin comandos de build).
3. En *Settings → Environment Variables* añadir las del `.env` (`ODOO_MODE=real`, `DRY_RUN`, `ODOO_URL`, `ODOO_DB`, `ODOO_USER`, `ODOO_PASSWORD`, `ODOO_COMPANY_IDS`, `SECRET_KEY`, `APP_ACCESS_CODE`), marcando como **Sensitive** las secretas. **Nunca en el código.** Los cambios de variables solo se aplican con un **nuevo despliegue** (*Redeploy*).
4. Desplegar y abrir `https://…vercel.app/api/health`: debe devolver `"ok":true`.
5. Desplegar siempre con `DRY_RUN=true`; ponerlo en `false` solo para pruebas autorizadas y volver a `true`.

### Lecciones del primer despliegue (por si se repite)
- **«Protected by Vercel Authentication»:** Vercel protege los despliegues por defecto y pide iniciar sesión en Vercel. Hay que desactivarlo en *Settings → Deployment Protection* (la app se protege con su propio código de acceso).
- **No poner `rewrites` hacia `/api/index` en `vercel.json`:** Vercel detecta FastAPI solo y entrega a la app la ruta *reescrita*, y todas las rutas `/api/...` daban 404.
- **«Blocked»:** en el plan Hobby Vercel solo despliega commits cuyo **autor está vinculado a la cuenta de GitHub** propietaria. Configurar en el proyecto `git config user.name` / `user.email` con el correo de GitHub (p. ej. `ID+usuario@users.noreply.github.com`).
- **Región:** `vercel.json` fija `"regions": ["cdg1"]` (París) para estar cerca de Odoo. En el plan Hobby solo se permite una.
- **Tiempo medido en producción:** guardar + imprimir ≈ **4-5 s** desde París (≈1 s por lectura). `maxDuration` está a 30 s.
### ⚠️ El plan gratuito (Hobby) es solo para uso personal y no comercial
Si la usan los conductores de la empresa, hay que pasar a **Vercel Pro** u otro alojamiento.

### Alternativas a Vercel para uso real con conductores
- **Servidor de la empresa:** el mismo código con `uvicorn` (o Docker) detrás de un proxy con HTTPS. Control total, mantenimiento propio.
- **Servidor en la nube** (VPS, Azure, AWS, Railway/Render…): una máquina pequeña basta; HTTPS con Caddy o similar.
- **Que lo aloje Tecnativa junto a Odoo:** menos latencia y un único sitio donde mantener todo; lo más cómodo si ellos ya gestionan el servidor.

## 7. Seguridad

- **Sin credenciales en el móvil.** Todo pasa por el backend.
- **Lista blanca en el cliente Odoo** ([app/odoo_client.py](app/odoo_client.py)): no existe ninguna llamada genérica. Solo se puede leer tareas, bultos, mercancías, adjuntos y pedidos; escribir **únicamente** `goods_id`, `number_of_packages`, `euro_pallet_qty` y `shipping_weight` de `tms.package`; publicar nota **solo** en `project.task`; imprimir **solo** el informe de la orden de transporte. Hay tests que lo garantizan.
- **El token del QR se valida en el servidor** (comparación en tiempo constante contra el `access_token` del adjunto) y la sesión firmada (30 min) se vuelve a validar al guardar. El servidor comprueba también que los bultos sean de esa tarea y que no hayan cambiado (`write_date`).
- **Reglas de negocio en el servidor:** enteros ≥ 0, peso > 0 (si el peso que se pone supera el umbral **`PESO_AVISO_KG`**, por defecto 27.000 kg, la app pide confirmarlo antes del resumen; el servidor es el único que tiene el número y se lo manda a la pantalla: «Es posible que el conjunto supere la MMA autorizada…»; no es un límite, el máximo depende del conjunto), mercancía existente y **etapa de la tarea «Nuevo» o «En desarrollo»** (configurable con `EDITABLE_STAGES`; se compara sin importar mayúsculas ni acentos). En cualquier otra etapa la orden se puede consultar pero no modificar.
- **Trazabilidad:** cada guardado deja una **nota interna** en la tarea («Modificado desde la app de conductores», fecha/hora y valores antes → ahora) y un log en el servidor sin datos sensibles (las URL con token no se registran).
- **Cabeceras:** `nosniff`, `no-referrer`, sin marcos, cámara solo para la propia app, y la API nunca se guarda en caché.

### Código de acceso compartido
Abrir una orden **escribiendo un número de pedido** (sin QR) no requiere ningún secreto, y los números de pedido no lo son. Por eso **todas las rutas de la API, salvo `/api/health`, exigen el código** (`APP_ACCESS_CODE`, cabecera `X-Codigo-Acceso`):

- Se compara en tiempo constante, tras un fallo se espera 1 s y se registra el intento **sin guardar lo escrito**.
- En modo `real` sin código (o de menos de 8 caracteres) la API responde 503: falla cerrada, nunca abierta.
- Un test recorre las rutas de la API y falla si alguna nueva se olvida de protegerla.
- **Cambiar el código** (p. ej. si un conductor deja la empresa o se filtra): cámbialo en el `.env` o en las variables de Vercel y vuelve a desplegar. Los móviles lo volverán a pedir.
- Es un código **compartido**: no distingue quién hizo cada cambio ni se puede quitar a una sola persona. Usa un código largo y no lo reutilices. Para eso está la fase 2.

### Fase 2 propuesta: identificar al conductor
Cada conductor tiene un **PIN de 4 dígitos** (lista en una variable de entorno o tabla pequeña). Se pide una sola vez y el móvil lo recuerda; el nombre queda en la nota de la tarea. Una opción aún más simple: un selector con su nombre. Sustituiría al código compartido y permitiría dar de baja a una sola persona.

## 8. Probar contra otra versión de Odoo (p. ej. Odoo 18)

La app puede apuntar a otro Odoo **sin tocar el `.env` de producción**: crea un archivo `.env.odoo18` (git lo ignora) con `ODOO_URL`, `ODOO_DB`, `ODOO_USER`, `ODOO_PASSWORD` y déjalo en `DRY_RUN=true`.

```powershell
$env:ENV_FILE=".env.odoo18"; .\.venv\Scripts\python.exe -X utf8 -m scripts.comprobar_compatibilidad   # SOLO LECTURA
.\iniciar-v18.cmd                                                                                  # arranca la app contra ese Odoo
# Mac:  ENV_FILE=.env.odoo18 .venv/bin/python -X utf8 -m scripts.comprobar_compatibilidad   y   ./iniciar-v18.sh
```

Lo que se ha comprobado con la plataforma de pruebas de Odoo 18 (`stinsa18.tecnativa.net`, base `prod`):
- **JSON-RPC con clave API:** funciona (la clave API se pone en `ODOO_PASSWORD`). Los modelos y campos del módulo TMS son los mismos y las empresas siguen siendo 1 (STINSA) y 7 (TRANSARGI).
- **Regla de edición:** solo depende de la etapa de la tarea («Nuevo» o «En desarrollo»), igual en la v15 y la v18; ya no se consultan facturas ni el estado de los bultos.
- **Notas en la tarea:** Odoo 18 escapa el cuerpo recibido por RPC (se vería el HTML escrito). La app envía `body_is_html=True` solo en Odoo ≥ 18; en la v15 no existe ese parámetro. Comprobado en la plataforma de pruebas.
- **Guardado completo en la v18 (probado):** escribir los 4 campos, dejar la mercancía en blanco, nota interna e imprimir la orden funcionan; tarda 4-5 s.
- **Imprimir el PDF NO funciona solo con clave API.** Imprimir exige una sesión web (`/web/session/authenticate`) y Odoo no admite claves API ahí (tampoco por cabeceras Basic/Bearer). Opciones:
  1. (Verificado: funciona) Usuario técnico **sin verificación en dos pasos (2FA)** y con contraseña, puesta en `ODOO_WEB_PASSWORD` (la clave API sigue en `ODOO_PASSWORD`). Con 2FA activado, Odoo responde `{uid: null}` y no se puede imprimir.
  2. Pedir a Tecnativa un **método público** en la tarea que genere la orden y devuelva el adjunto (sin sesión web, solo con clave API).

- **Guía dentro de la app:** el botón «Cómo se usa (guía)» del menú abre `public/guia.html`, que se **genera** desde `GUIA_CONDUCTORES.md` (una sola fuente). Tras editar el `.md`, ejecuta `.\.venv\Scripts\python.exe -X utf8 -m scripts.generar_guia` (Mac: `.venv/bin/python -X utf8 -m scripts.generar_guia`; un test falla si `guia.html` se queda desfasada). Funciona sin conexión.
- Tema oscuro y de alto contraste pensado para la cabina (poca luz, guantes). Todos los colores están en variables al inicio de `public/styles.css`. Todos los textos superan 4,5:1 de contraste.
- **Tipografía:** Atkinson Hyperlegible Next (Braille Institute, licencia SIL OFL en `public/fonts/OFL.txt`), elegida porque distingue bien 1/l/I y 0/O en números de pedido y pesos. Va dentro de la app (`public/fonts/`), así que funciona sin conexión.
- **Tamaños:** letra base de 18 px en el móvil, botones de 60 px (68 px los de «Sí/No») y campos de 56 px; bordes finos y el color de la empresa solo en lo importante (franja, botón principal, lo cambiado, visor). El visor de cuatro esquinas del escáner se repite en el botón grande del menú.
- **Versión de ordenador:** es la misma dirección; la pantalla se adapta sola. Con ratón la letra y los botones son más contenidos (base 17 px, botones de 50 px); a partir de 900 px de ancho el escáner pone la cámara y el pedido escrito lado a lado, los lugares se ven en dos columnas y el resumen como tabla (dato, antes, ahora). «Intro» busca el pedido y «Esc» responde «No» en los avisos. La cámara del ordenador se ve en espejo.
- **Marca:** franja blanca con el logo y color de acento por empresa: Grupo Trimodos **#fcda00**, STINSA **#009f92**, TRANSARGI **#f17e49** (texto negro sobre los botones: contraste 6,5-7,9:1). El acento cambia según la empresa de la orden (o la elegida al escribir el pedido). Mientras no hay empresa (menú, escáner, guía), la raya bajo el logo lleva los tres colores, como `public/img/compartir.png`. Para elegir empresa al escribir el pedido se usan sus logos de `public/img/`.
- **Icono de la app** (acceso directo del móvil y pestaña): el símbolo de Grupo Trimodos en amarillo sobre fondo grafito, con «DECA» en gris debajo (la pestaña lleva solo el símbolo). Lo genera `scripts/crear_iconos.py`, que dibuja el símbolo en vectorial con las medidas de `public/img/trimodos.png`. En el iPhone, el icono se guarda al añadir el acceso directo: para ver uno nuevo hay que borrar el acceso y volver a añadirlo.
- Los logos se preparan con `scripts/preparar_logos.py` (recorta márgenes y los deja en `public/img/`). Al cambiar archivos de la carcasa, **sube la versión de caché** en `public/sw.js` (`conductores-v2` → `v3`).

## 10. Datos de Odoo: particularidades detectadas

- Una tarea cuenta como **orden de transporte** si tiene `is_transport_order`, **o** una orden de transporte ya generada, **o** bultos (`tms_package_ids`): hay tareas reales (p. ej. las del pedido S2621553) con la marca a `False`, con su PDF o todavía sin imprimir. Que se pueda **modificar** lo decide solo la etapa («Nuevo» o «En desarrollo»).
- Si una tarea se recrea en Odoo, el QR del PDF antiguo apunta a la tarea anterior y deja de encontrarse; hay que escanear el de la orden nueva o escribir el número de pedido.
- Un bulto puede venir **vacío** (sin mercancía, 0 elementos, 0 euro palets, 0 kg). Se puede dejar la **mercancía en blanco** (`goods_id` no es obligatorio en `tms.package`). Al guardar solo se validan los campos que cambian.
- El mismo número de pedido puede existir en STINSA y en TRANSARGI: por eso al escribirlo a mano se elige la empresa.

## 11. Estructura

```
api/index.py            entrada de Vercel (importa app.main:app)
app/main.py             API FastAPI y servidor de estáticos en local
app/service.py          lógica de negocio: resolver, validar, guardar, nota, imprimir
app/odoo_client.py      cliente Odoo (lista blanca)      app/mock_odoo.py  Odoo simulado
app/qr.py, tokens.py    lectura del QR y sesión firmada
public/                 PWA: index.html, app.js, styles.css, sw.js, manifest, iconos, vendor/
scripts/                diagnóstico, permisos, prueba de navegador, iconos
tests/                  pruebas del backend
```

**Librería de terceros incluida:** `public/vendor/jsQR.js` (jsQR 1.4.0, Apache-2.0): lee el QR en los móviles sin lector del sistema (`BarcodeDetector`), como el iPhone. La cámara se pide en alta resolución (1920x1080) y se analiza el centro de la imagen sin reducirla: con la resolución por defecto del iPhone (640x480) un QR pequeño no se leía. Se sirve desde la propia app para que funcione sin depender de un CDN.

**API:** `POST /api/resolve-qr` · `GET /api/goods?q=` · `POST /api/update` · `POST /api/reprint` · `GET /api/pdf/{id}?token=` · `GET /api/health`.
