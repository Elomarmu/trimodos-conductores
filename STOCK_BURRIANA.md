# Stock Burriana — diseño y puesta en marcha

Sección «Stock Burriana» de la app de oficina y almacén (`/oficina/`). Sigue la estructura de Viajes PR para poder integrarse después con Odoo.

## Qué hace

**Entrada de mercancía**
1. Oficina fotografía el albarán del cliente → la app lo lee (Claude visión), **recomienda cliente** y avisa de conflictos (albarán repetido, cliente dudoso).
2. Almacén fotografía la etiqueta de cada palet → la app la compara con el albarán (sobra, no figura, lote distinto, SSCC repetido, cliente distinto).
3. Al terminar, si hay discrepancias hay que confirmarlas (se aceptan los palets físicos). Si todo cuadra, se cierra.
4. Al cerrar: Excel de la entrada + fotos a SharePoint, se actualiza el libro de control y se envía **un** correo.

**Salida de mercancía**
1. Oficina carga la petición del cliente (foto o a mano).
2. Almacén fotografía los palets que carga; la app muestra lo pedido y va completando (FIFO como sugerencia, avisa de palets reservados en otras salidas, de otro cliente, exceso o faltas).
3. Cargada y sin discrepancias → se expide la Excel de lo cargado y se envía el correo.

El stock se **calcula** (entradas cerradas − salidas cargadas); no se guarda aparte.

## Excel «CONTROL ALMACEN BURRIANA.xlsx»
Lo genera la app (no hay que mantenerlo a mano; lo escrito a mano se pierde en la siguiente regeneración).
Hojas: RESUMEN STOCK, STOCK, ENTRADAS, SALIDAS, MOVIMIENTOS, CLIENTES. Imagen corporativa STINSA.
Se guarda solo en SharePoint (`correo:false`) porque contiene el stock de todos los clientes. Cada operación crea además su carpeta `ENTRADA E-AAAA-NNNN` / `SALIDA S-AAAA-NNNN` con su Excel y fotos.

## Piezas del código
- `app/stock/stock_reglas.py` reglas puras · `stock_lector.py` lectura con Claude · `stock_store.py` estado (adjuntos de Odoo en `res.company` 1, o memoria en pruebas) · `stock_excel.py` libros · `stock_facturacion.py` movimientos y estocaje (solo preparación) · `stock_api.py` rutas `/api/stock`.
- `public/stock.js` + pantallas `s-stock-*` en `public/index.html` (el de `/oficina/` se regenera con `scripts/generar_oficina.py`).
- `scripts/generar_n8n_stock.py` → `n8n/stock-burriana.json`.
- `tests/test_stock.py` (15 pruebas).

## Puesta en marcha (paso a paso)
1. **Vercel → Settings → Environment Variables**: `STOCK_AVISOS_WEBHOOK` (URL de producción del webhook de n8n, ruta `stock-burriana`), `STOCK_AVISOS_CLAVE` (una clave inventada, larga), `STOCK_CORREO_PARA` (por defecto carmelomartinez@stinsa.com; luego añadir traficolevante@stinsa.com separado por coma). Redesplegar.
2. **n8n**: Importar `n8n/stock-burriana.json`. En el nodo «Configuración» pegar la misma clave. Reconectar credenciales «Microsoft Excel (SharePoint RRHH)» y «Outlook Agente Trafico». Activar el flujo.
3. En la app: **Stock Burriana → Clientes**, dar de alta los clientes (nombre y alias) antes de la primera entrada.
4. Probar con una entrada y una salida; comprobar carpeta en SharePoint y correo.

## Facturación futura
Cada palet verificado/cargado es un movimiento. `stock_facturacion.lineas_facturables` ya calcula manipulación de entrada/salida y estocaje (palets·día) con tarifas por cliente. Los clientes tienen `odoo_partner_id` previsto. Nada se envía a Odoo todavía.

## Supuestos a confirmar
- Destinatarios: «primero Carmelo y después traficolevante» se interpreta como lista configurable por `STOCK_CORREO_PARA`.
- Mismo código de acceso de oficina para oficina y almacén.
- Última escritura gana si dos personas editan a la vez.
- Fotos guardadas como adjuntos de Odoo (estado) y en SharePoint (documentación).
- Creación de carpetas por Graph: verificar en la primera prueba de n8n.
- Columnas y orden de la Excel abiertos a tu revisión.

## Primer cliente: Raminatrans Castellón, S.L. (2026-11-17)
- **Cliente del almacén** = Raminatrans (el operador que emite el albarán y la etiqueta, «Grupo Raminatrans»); viene dado de alta con su catálogo de 7 referencias. **BECRISA 2011 S.L.U. (03783)** es el cliente de su cliente: se guarda como «Cliente final» (informativo, sale en Excel y correo, no interviene en la facturación).
- El cliente manda **PDF + Excel** (una fila por palet con su código de 10 cifras y su peso). Botón «Elegir PDF, Excel o fotos»: el Excel se lee sin IA (lista exacta de palets y kg); el PDF aporta número, fecha, cliente final y la «Entrada» (ECS26-xxxx) de cada palet.
- **Se controla solo el nº de palets y sus códigos individuales** (no los bultos/unidades). Cada etiqueta se casa por código: palet que no está en el albarán, repetido, de otra referencia o de otra «Entrada» → discrepancia. Al cerrar, se listan los códigos que faltan por etiquetar.
- **Peso**: sale del albarán por palet (la etiqueta no lo trae); se compara el total con el del albarán (tolerancia 2 %).
- **Lote = código de «Entrada»** (ECS26-xxxx) de la etiqueta.
- **Fecha de entrada en almacén**: propuesta por el albarán, editable al cargar la entrada y hasta que se cierra (camión cargado un día y descargado al siguiente). Es la que cuenta para el estocaje y la antigüedad (FIFO).
- **Incidencias**: campo de observaciones en la descarga y en la carga; si se rellena, el correo sale con «⚠» y un bloque rojo de incidencias, y aparece en las Excel.
- **Catálogo de referencias** por cliente (pantalla Clientes → tocar el cliente): avisa si llega una referencia que no está.
- **Salidas**: la petición puede venir con Excel de palets; la app comprueba que están en stock, que se cargan exactamente esos códigos y lista los que faltan por cargar.

## Buzón agente.burriana@stinsa.com (correo → app)
- Segundo flujo de n8n: `n8n/stock-correo.json` (generado con `scripts/generar_n8n_correo.py`). Una ejecución por tanda de correos nuevos.
- Lee el correo y sus adjuntos (PDF, Excel y fotos de más de 60 kB; las firmas se ignoran) y los manda a `POST /api/stock/correo` (cabecera `X-Clave` = `STOCK_CORREO_CLAVE`).
- La app prepara la **entrada** (o la **salida**, si el asunto/cuerpo habla de salida, recoger, recogida, retirar, retirada) en estado **POR REVISAR**: la oficina la ve en la lista con 📧, comprueba cliente, fecha de entrada y palets y pulsa «Está bien». Hasta entonces no se puede cerrar.
- n8n manda a la oficina un correo «preparada» (con los avisos) o «⚠ no he podido procesar» (cliente no reconocido, albarán ya cargado, sin adjuntos…). Nunca se cierra nada solo.
- Idempotente por id de correo (un reintento no duplica). Límite: ~4 MB de adjuntos por correo (Vercel); lo que no cabe se avisa en el cuerpo.
- Puesta en marcha: en n8n importar el flujo, conectar la credencial del buzón que recibe el alias (disparador) y «Outlook Agente Trafico» (avisos), pegar URL y clave en «Configuración», activar. En Vercel: `STOCK_CORREO_CLAVE`.

## Segundo cliente: Aliplast Iberia, S.L.U. (2026-11-18)
Bobinas de film de Aliplast S.p.A. (Italia), que llegan a Burriana y se reparten a fábricas cercanas (p. ej. Peronda). Viene dado de alta con su catálogo de 6 referencias (F05182S con alias F05182S-MV, F02891S/10064527, F05199FP, F04208SP/10070922, F04168S/10070920, F03932S).
- **Cliente = Aliplast Iberia** (el que emite el albarán de salida «M-2026/154»), nunca STINSA (destino de la entrada) ni Peronda (destino/cliente final de la salida, informativo).
- **Entrada**: el documento es un PDF escaneado con DDT + packing list + CMR; el lector (visión) saca el nº de DDT como «código de entrada», una fila por palet con su código `LP26-xxxxxxx` (Code128, alfanumérico), lote de producción, **kg netos** y fecha de producción. La app compara nº de palets y suma de kg de la lista con los totales del albarán (40 palets, 15.166 kg en el ejemplo) y avisa. Una bobina por palet normalmente; las unidades no se controlan.
- **Lote** = lote de producción (P03-015457…); la fecha de producción se guarda y se enseña como informativa.
- **Salida por palet concreto** (`salida_por_palet`): en Aliplast hay que localizar y cargar los palets de la lista (LP) de la petición; en Raminatrans solo cuenta referencia y nº de palets. Es un ajuste por cliente.
- **No hay FIFO** para ningún cliente; solo se avisa (⏳, en Inventario y en los correos) de la mercancía con más de 30 días.
- **Aviso de llegada**: al cerrar la entrada, correo a traficolevante@stinsa.com y carmelomartinez@stinsa.com (variable `STOCK_AVISO_PARA`) con el mensaje redactado, el contacto del cliente arriba y la documentación del transporte adjunta (evento `aviso_llegada`, sin subir a SharePoint), para **reenviarlo al cliente**. No se crean borradores en otro buzón (agente.trafico es un buzón compartido y no se puede dar acceso a traficolevante). **No hay recordatorio de CMR**: es obligación del transportista.
- **Ubicación**: desplegable bajo los botones de escanear en cualquier entrada (también la manual de inventario) con E1-E6 (estanterías) y S1-S10 (suelo), validado también en la API (`R.UBICACIONES`); se guarda en cada palet (`ubicacion`) y se mantiene hasta que se cambia. Sale en Inventario, Excel (STOCK, palets de entrada y salida), correo de entrada (resumen por ubicación), líneas y lista de disponibles de la salida y palets cargados. El correo de la entrada manual de inventario y de los controles va a traficolevante@ y Carmelo (`STOCK_INVENTARIO_PARA`).
- **Ubicar el stock existente**: Inventario → «Ubicar / cambiar ubicación» por referencia; `POST /api/stock/stock/ubicacion {palets, ubicacion, confirmado}`: asignar a palets sin ubicación es directo; cambiar una ya puesta devuelve 409 `cambio_ubicacion` hasta que llega `confirmado`. Se guarda el historial en `ubicaciones_previas` del palet.
- **Palet SIN CODIGO** (solo entrada manual de inventario): se marca la casilla «sin código» (`sin_codigo` en la API; también vale escribir «SIN CODIGO»); en control y salida hay un desplegable con los sin código del stock; se acepta si tiene ubicación (si no, discrepancia). Guarda `sin_codigo=True` y un código interno `SC`+id (`R.codigo_interno`) para controlarlo y sacarlo. En entradas con albarán da 400 `sin_codigo_no_vale`.
- **Inventario**: «Stock actual» pasa a llamarse **Inventario**, con **Entrada manual de inventario** (ids `I-AAAA-NNNN`; es una entrada sin albarán) y **Control de inventario** (ids `C-AAAA-NNNN`, faltan/sobran y bajas opcionales). Falta cargar el stock actual de Aliplast (los 8 palets del ejemplo de salida son del lote P03-014037, que no está en la entrada de muestra).
- **Contacto del aviso**: nombre y correo se escriben en la ficha del cliente (no se consulta Odoo).
- **n8n** (`n8n/stock-burriana.json`, regenerado; hay que **volver a importarlo**): buzón elegido por la app (`buzon`), nodo «¿Se envía?» (si `enviar:false` solo deja el borrador), archivos con `sharepoint:false` no se suben. 
