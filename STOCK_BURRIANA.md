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
