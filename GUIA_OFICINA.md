# Guía de la app de oficina y almacén

Esta app es para el **personal de oficina y de almacén**. Los conductores usan otra, la de siempre: en ella solo tienen «DECA - Trimodos» y «Carga de PR».

En la pantalla principal tienes:

- **DECA - Trimodos**: corregir una orden de transporte escaneando su QR (igual que los conductores).
- **Carga de PR · foto del albarán**: dar de alta un viaje de PR con la foto del albarán y del DI.
- **Oficina · PR**: todo lo de los viajes de PR de SAICA (registrar, modificar, pendientes, composición del tren, bobinas).
- **Almacén · PR**: cargar las cajas móviles del tren.
- **Cómo se usa (guía)**: esta guía.

## Antes de empezar

- **Dirección:** la de siempre terminada en **/oficina/**. Instálala en el móvil o el ordenador: en **Chrome**, menú ⋮ → **«Instalar aplicación»** (o «Añadir a pantalla de inicio»). En iPhone: **Safari → Compartir → «Añadir a pantalla de inicio»**. Sale como **«Oficina Trimodos»**.
- **Código de acceso:** el de **oficina y almacén**, que no es el de los conductores. Con el de conductor no se puede entrar. Solo se pide la primera vez.
- Todo lo que hace la app queda en **Odoo**, que es donde están los datos. Las Excel y los documentos de SharePoint se rehacen solos a partir de Odoo: **no escribas a mano en esas Excel**.

## Cómo funcionan los viajes de PR

- Cada viaje es **una línea del pedido del mes** («PR NOVIEMBRE», «PR DICIEMBRE»…) del cliente SAICA PAPER, con su orden de transporte (tarea) y su bulto. El pedido del mes **se crea solo** con el primer viaje del mes y llega un correo con el enlace.
- Cada carga necesita **dos documentos para el cliente**: la **foto del albarán** y el **DI de residuos**. Pueden ser fotos o PDF.
- Si un albarán trae un **punto de carga que no está dado de alta**, el viaje no se guarda y llega un correo para darlo de alta.
- **Correo de cada mañana** (lunes a sábado): viajes que llegaron ayer a la nave (el lunes, los del sábado y el domingo), los que se cargaron en el tren, los que tienen datos o documentos pendientes y los que quedan en stock. Lleva botones al pedido y a la Excel del tren, y cada albarán enlaza con su orden de transporte en Odoo.

## Foto de documentos: encuadre y escaneo

Cada vez que hagas una foto a un albarán o a un DI:

1. La app marca las **cuatro esquinas del folio** con círculos amarillos. Si alguna no está bien, **arrástrala con el dedo**.
2. Pulsa **«Aceptar»**: endereza el folio, quita el fondo y oscurece el texto.
3. Revisa el resultado y pulsa **«Usar este escaneo»**. También puedes elegir «Volver a encuadrar» o, en el primer paso, «Usar la foto tal cual».

Consejo: el folio entero, sobre una superficie oscura y con buena luz.

## Oficina · PR

### Registrar un viaje (foto del albarán)

Igual que el conductor:

1. Haz la foto del albarán y espera a que se lea.
2. Revisa los datos y añade el **DI de residuos** en «Documentos para el cliente».
3. Pulsa **«Guardar y generar la orden»**.

Si falta el DI, el viaje se guarda igual y queda pendiente.

### Modificar una carga

Úsalo para completar lo que no venía en el albarán (código Paper, peso, balas, contenedor) o para añadir o cambiar documentos.

1. Busca la carga de una de estas dos formas:
    - **Con foto del albarán:** la app lo lee, encuentra el viaje y te propone lo leído junto a lo que hay en Odoo. Si el viaje no existe, ofrece **«Registrarlo ahora»**.
    - **Buscando:** escribe el **nº de albarán, el código Paper o el bulto (PK…)** y pulsa «Buscar».
2. Cambia los datos que haga falta.
3. En **«Documentos para el cliente»** verás si cada uno está **✓ Guardado** o **✗ Falta**. Para añadir o sustituir uno, pulsa «Hacer foto» o «Elegir foto o PDF».
4. Pulsa **«Guardar»**.
    - Si cambian el código Paper, el peso, las balas o el contenedor, se genera una orden de transporte nueva.
    - Si la carga ya está en un tren, sus documentos de SharePoint se renombran solos.

### Cargas con documentos o datos pendientes

Lista de las cargas a las que les falta algo: foto del albarán, DI de residuos, código Paper, peso o balas. En cada una pone **«Falta: …»**.

- Pulsa **«Completar»** y se abre esa carga en «Modificar una carga».
- Revísala a menudo, sobre todo antes de cerrar un tren.

### Composición del tren

Arriba, en **«Tren cargado ahora»**, se ve el tren que está cargando el almacén con su **nº de convoy de entrada** (el del listado de Captrain).

- El **nº de convoy de salida no es el mismo que el de entrada**. Cuando lo sepas, escríbelo en **«Nº convoy de salida»** y pulsa **«Guardar el convoy de salida»**.
- Se cambia en la Excel del tren, en las cargas ya asignadas y en el correo del tren. Si te equivocas, escríbelo otra vez y guarda.

Para un **tren nuevo**:

1. Pulsa **«Subir el listado del tren»**. Puedes subir el PDF de Captrain o fotos de la primera y la última hoja.
2. La app lee los vagones y las cajas móviles. Revisa:
    - el **Nº de tren**: es nuestro número correlativo y la app propone el siguiente;
    - el **Nº convoy de entrada** (el de Captrain) y, si ya lo sabes, el **de salida** (si no, se pone después);
    - la **fecha**;
    - la lista de vagones y cajas.
3. Pulsa **«Guardar la composición del tren»**.
    - Si parece **repetida** (mismo tren, mismo convoy o casi las mismas cajas que otro tren), la app avisa con el motivo. Solo se guarda si confirmas otra vez.
4. En SharePoint › Automatizacion se crea la carpeta **«CARTON PAJA TREN N»** con su Excel. La composición va en la hoja «COMPOSICION».

### Bobinas de un convoy

1. Sube la captura (o una foto) de la pantalla **«SAICA - Detalle convoy»**. Si el convoy no cabe en una pantalla, sube hasta 4 capturas.
2. Revisa el total por clase de bobina y los avisos, por ejemplo una caja que no suma lo que dice su cabecera o una captura que falta.
3. Pulsa **«Enviar la Excel a Sevilla»**. También se puede descargar.

## Almacén · PR: cargar las cajas móviles

Arriba pone **«Está cargado el tren N de fecha DD-MM-AAAA»**. Comprueba que es el tren que estáis cargando. Si no lo es, la oficina tiene que cargar su composición.

### Asignar una carga a una caja

1. En **«Caja móvil»** elige la caja. Salen como «caja - vagón», y solo las que están libres.
2. En **«Carga»** elige lo que va dentro: «código Paper - balas - kilos». Si a una carga le falta un documento, lo verás al lado (por ejemplo «· falta DI de residuos»).
    - Si la caja va **vacía**, elige **«Caja vacía»** (la última opción).
3. Pulsa **«Asignar»**.

### El orden del tren

La lista **«Ya cargadas»** está en el orden del tren: **arriba los vagones pegados a la locomotora, abajo los de cola**. La Excel del tren sale en ese mismo orden.

- Como se carga de atrás hacia delante, **cada vagón que empiezas a cargar sube arriba**. Esto vale también si va vacío.
- Si un vagón lleva dos cajas y cargas la segunda, **se junta con su vagón sin moverlo**.
- Si un vagón no está en su sitio, muévelo con **▲** (hacia la locomotora) o **▼** (hacia la cola).

### Corregir y quitar

- **Modificar**: cambia el nombre de la caja móvil o el número del vagón. El vagón tiene 12 cifras y la app comprueba su dígito de control. Al cambiar el vagón, cambia en todas sus cajas.
    - Para corregir una caja antes de cargarla, elígela en el desplegable y pulsa **«Modificar la caja elegida»**.
- **Quitar**: la carga vuelve a stock. En una caja marcada como vacía, quita esa marca.

### Terminar el tren

Cuando esté todo cargado, pulsa **«Terminar el tren y enviar las Excel»** y confirma. Se hacen tres cosas:

- se generan las Excel **CARTON PAJA TREN N** y **REGISTRO VIAJES PR**, que también se pueden descargar;
- se guardan en SharePoint;
- se envía un correo con las Excel y **todas las fotos de albaranes y DI**. Si falta algún documento, el correo lo indica.

## Dónde queda todo en SharePoint (Automatizacion)

- **Carpeta «CARTON PAJA TREN N»** (una por tren):
    - la Excel del tren con sus hojas «TREN N» y «COMPOSICION»;
    - las fotos de los albaranes, que se llaman **«código Paper - caja móvil»**;
    - los DI, que se llaman **«código Paper - caja móvil - DI»**.
- **REGISTRO VIAJES PR.xlsx**:
    - hoja **«STOCK EN NAVE»**: lo que hay en la nave pendiente de cargar, que es el stock real;
    - hoja **«HISTORICO TRENES»**: lo que ya se cargó, con el último tren arriba.
    - Se pone al día sola cada vez que se da de alta o se cambia una carga.

## Avisos que puedes ver

| Mensaje | Qué hacer |
|---|---|
| «Este es el código de los conductores…» | Estás en la app de oficina con el código de conductor. Pide el código de oficina y almacén. |
| «Esta función es de la app de oficina y almacén» | Se ha intentado usar una función de oficina desde la app de conductores. Abre **…/oficina/**. |
| «Composición repetida» | Lee el motivo. Si de verdad es un tren nuevo o quieres sustituir el anterior, confirma; si no, cancela. |
| «Otra persona acaba de cargar la caja …» | Dos personas han asignado la misma caja a la vez. Elige otra caja. |
| «La caja … ya lleva la carga …» | Esa caja ya está ocupada. Quita antes la carga si te has equivocado. |
| «El vagón … no cuadra con su dígito de control» | Revisa el número del vagón: la última cifra no corresponde. |
| «Falta: DI de residuos» (u otro) | Completa la carga en «Modificar una carga» o desde «Cargas pendientes». |
| «El archivo es demasiado grande» | Haz una foto del documento en vez de subir el PDF. |
| «Modo de prueba: no se ha guardado nada» | La app está en pruebas. Avisa al responsable de la app. |

## Consejos

- Si un dato del albarán se ha leído mal, corrígelo **antes de guardar**. Después también se puede, desde «Modificar una carga».
- Revisa **«Cargas pendientes»** antes de terminar cada tren, para que el correo a SAICA vaya completo.
- Si la app no muestra lo nuevo después de una actualización, ciérrala del todo y vuelve a abrirla.
