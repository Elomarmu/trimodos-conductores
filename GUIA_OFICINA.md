# Guía de la app de oficina y almacén

Esta app es para el **personal de oficina y de almacén**. Los conductores usan otra, la de siempre: en ella solo tienen «DECA - Trimodos» y «Carga de PR».

En la pantalla principal tienes:

- **DECA - Trimodos**: corregir una orden de transporte escaneando su QR (igual que los conductores).
- **Oficina Sevilla**: todo lo de los viajes de PR de SAICA (registrar, modificar, pendientes, composición del tren, bobinas).
- **Almacén Sevilla**: cargar las cajas móviles del tren.
- **Oficina Burriana**: entradas y salidas de mercancía del almacén de Burriana, su inventario, las ubicaciones y el control de stock.
- **Cómo se usa (guía)**: esta guía.

## Antes de empezar

- **Dirección:** la de siempre terminada en **/oficina/**. Instálala en el móvil o el ordenador: en **Chrome**, menú ⋮ → **«Instalar aplicación»** (o «Añadir a pantalla de inicio»). En iPhone: **Safari → Compartir → «Añadir a pantalla de inicio»**. Sale como **«Oficina Trimodos»**.
- **Código de acceso:** el de **oficina y almacén**, que no es el de los conductores. Con el de conductor no se puede entrar. Solo se pide la primera vez.
- Todo lo que hace la app queda en **Odoo**, que es donde están los datos. Las Excel y los documentos de SharePoint se rehacen solos a partir de Odoo: **no escribas a mano en esas Excel**.

## Cómo funcionan los viajes de PR

- Cada viaje es **una línea del pedido del mes** («PR NOVIEMBRE», «PR DICIEMBRE»…) del cliente SAICA PAPER, con su orden de transporte (tarea) y su bulto. El pedido del mes **se crea solo** con el primer viaje del mes y llega un correo con el enlace.
- Cada carga necesita **dos documentos para el cliente**: la **foto del albarán** y el **DI de residuos**. Pueden ser fotos o PDF.
- Si un albarán trae un **punto de carga que no está dado de alta**, el viaje no se guarda y llega un correo para darlo de alta.
- **Correo de cada mañana** (lunes a sábado): viajes que llegaron ayer a la nave (el lunes, los del sábado y el domingo), todas las cargas del tren actual (en el orden del tren, lleguen cuando lleguen), los que tienen datos o documentos pendientes y los que quedan en stock. Lleva botones al pedido y a la Excel del tren, y cada albarán enlaza con su orden de transporte en Odoo.

## Foto de documentos: encuadre y escaneo

Cada vez que hagas una foto a un albarán o a un DI:

1. La app marca las **cuatro esquinas del folio** con círculos amarillos. Si alguna no está bien, **arrástrala con el dedo**.
2. Pulsa **«Aceptar»**: endereza el folio, quita el fondo y oscurece el texto.
3. Revisa el resultado y pulsa **«Usar este escaneo»**. También puedes elegir «Volver a encuadrar» o, en el primer paso, «Usar la foto tal cual».

Consejo: el folio entero, sobre una superficie oscura y con buena luz.

## Oficina Sevilla (viajes de PR)

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

### Sacar de stock (cargadas en un tren anterior)

Si en el almacén salen como «en stock» cargas que en realidad ya se fueron en un tren anterior (por ejemplo, viajes metidos a mano en el pedido que se cargaron por el procedimiento de siempre):

1. Pulsa **«Sacar de stock (cargadas en un tren anterior)»**.
2. Marca las cargas una a una, o usa **«Marcar todas con fecha de carga hasta el día…»** y pulsa «Marcar».
3. Pulsa **«Sacar de stock las marcadas»** y confirma.

Dejan de salir en el almacén, en «STOCK EN NAVE» y en «Cargas pendientes», y pasan a «HISTORICO TRENES» como «Tren anterior». **En Odoo no se borra nada.** Si alguna se sacó por error, abajo sale en «Ya sacadas de stock»: pulsa **«Devolver a stock»**.

**Importante:** no borres filas a mano en la Excel de registro: la app la rehace entera desde Odoo cada vez.

### Aviso «No se ha podido actualizar en SharePoint»

Si una Excel (la de registro o la del tren) está **abierta** en algún ordenador cuando la app la quiere cambiar, SharePoint no deja sustituirla. Entonces, al entrar en **Oficina Sevilla** o en **Almacén Sevilla**, sale un aviso amarillo con la Excel y la hora. **Ciérrala en todos los ordenadores** (sobre todo en el Excel de escritorio) y pulsa **«Reintentar»**: la app la rehace desde Odoo y la vuelve a subir. El aviso desaparece solo cuando ya se ha podido actualizar.

Consejo: consultad las Excel en el navegador y cerradlas al acabar.

### Composición del tren

Arriba, en **«Tren cargado ahora»**, se ve el tren que está cargando el almacén con su **nº de convoy de entrada** (el del listado de Captrain).

- El **nº de convoy de salida no es el mismo que el de entrada**. Cuando lo sepas, escríbelo en **«Nº convoy de salida»** y pulsa **«Guardar el convoy de salida»**.
- Se cambia en la Excel del tren, en las cargas ya asignadas y en el correo del tren. Si te equivocas, escríbelo otra vez y guarda.
- Si el tren está **terminado**, sale «TERMINADO» y el botón **«Modificar el tren»** para volver a abrirlo.

Debajo sale la **composición completa** del tren cargado: cada vagón con sus cajas y cuántas cargas lleva.

- Si un vagón **no va a salir** en este tren (por ejemplo, por un descarrilamiento del tren de entrada), pulsa **«Segregar»** en ese vagón y confirma. El vagón sale de la Excel del tren y del almacén (no aparece al asignar cargas). En la hoja «COMPOSICION» de la Excel queda marcado como «SEGREGADO».
- **Sus cargas no se quitan solas:** se quedan en sus cajas. El vagón pasa al apartado **«Vagones segregados (en espera)»**, más abajo en esta misma pantalla, y se queda ahí hasta que alguien haga algo con él:
    - **«Quitar»** (en cada carga): saca esa carga de la caja y la devuelve a stock.
    - **«Agregar al tren N»**: mete el vagón en el tren abierto, con sus cargas. Si es el mismo tren del que salió, vuelve a su sitio; si es otro tren, sus cajas se añaden al final de la composición y los documentos de sus cargas pasan a la carpeta de ese tren. Solo se puede si hay un tren sin terminar.
    - **«Eliminar»**: pide confirmación y borra el vagón de la lista. Si aún lleva cargas, vuelven a stock.
- Los vagones segregados **solo aparecen en Composición del tren**, nunca en la pantalla del almacén.

Para un **tren nuevo**:

- **Solo se puede cargar si el tren actual está terminado.** Si no lo está, la app lo avisa y ofrece **«Ir a terminar el tren»**.
- Si vuelves a subir la composición del **mismo** tren (mismo número), las cargas ya asignadas **se mantienen**. Las que estaban en una caja que ya no aparece vuelven a stock, y la app te dice cuáles.

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

## Almacén Sevilla: cargar las cajas móviles

Arriba pone **«Está cargado el tren N de fecha DD-MM-AAAA»**. Comprueba que es el tren que estáis cargando. Si no lo es, la oficina tiene que cargar su composición.

Se entra desde **«Almacén Sevilla»** (menú principal). La oficina también puede asignar cargas desde **Oficina Sevilla › «Asignar cargas a cajas»**: es la misma pantalla, pero con los botones de terminar y modificar el tren.

Cada cambio (asignar, quitar, mover o corregir) pone al día **la Excel del tren y la de registro en SharePoint** en ese momento. Si alguna vez no cuadra lo de SharePoint con la app, la oficina tiene el botón **«Rehacer la Excel y los documentos del tren en SharePoint»** en «Asignar cargas a cajas».

### Asignar una carga a una caja

1. En **«Caja móvil»** elige la caja. Salen como «caja - vagón», y solo las que están libres.
2. En **«Carga»** elige lo que va dentro: «código Paper - balas - kilos». Si a una carga le falta un documento, lo verás al lado (por ejemplo «· falta DI de residuos»).
    - Si la caja va **vacía**, elige **«Caja vacía»** (la última opción).
3. Si ha habido algún problema, escríbelo en **«Observaciones»**: por ejemplo, se rompe una balanza durante la carga, aparecen cucarachas o hay balas mal apretadas. Si no, déjalo en blanco.
4. Pulsa **«Asignar»**.

Las observaciones salen debajo de la caja en «Ya cargadas» (con ⚠), en la columna **«Observaciones»** de la Excel del tren (antes de las comprobaciones) y en el correo del tren.

### El orden del tren

La lista **«Ya cargadas»** está en el orden del tren: **arriba los vagones pegados a la locomotora, abajo los de cola**. La Excel del tren sale en ese mismo orden.

- Como se carga de atrás hacia delante, **cada vagón que empiezas a cargar sube arriba**. Esto vale también si va vacío.
- Si un vagón lleva dos cajas y cargas la segunda, **se junta con su vagón sin moverlo**.
- Si un vagón no está en su sitio, muévelo con **▲** (hacia la locomotora) o **▼** (hacia la cola).

### Corregir y quitar

- **Modificar**: cambia el nombre de la caja móvil, el número del vagón o las **observaciones** de esa carga. El vagón tiene 12 cifras y la app comprueba su dígito de control. Al cambiar el vagón, cambia en todas sus cajas.
    - Para corregir una caja antes de cargarla, elígela en el desplegable y pulsa **«Modificar la caja elegida»**.
- **Quitar**: la carga vuelve a stock. En una caja marcada como vacía, quita esa marca.

### Terminar el tren (solo la oficina)

Se hace desde **Oficina Sevilla › «Asignar cargas a cajas»**. Cuando esté todo cargado, pulsa **«Terminar el tren y enviar las Excel»**. La app enseña el **nº de convoy de salida** (si todavía no se había puesto, el de entrada):

- Si es correcto, pulsa **«Confirmar y enviar»**.
- Si no lo es, **escribe el bueno** y pulsa «Confirmar y enviar»: se guarda en el tren y en sus cargas antes de enviar.

Al enviar se hacen tres cosas:

- se generan las Excel **CARTON PAJA TREN N** y **REGISTRO VIAJES PR**, que también se pueden descargar;
- se guardan en SharePoint;
- se envía un correo con las Excel y **todas las fotos de albaranes y DI**. Si falta algún documento, el correo lo indica.

Después el tren queda **TERMINADO**: el almacén lo ve, pero ya no puede cambiarlo. Si hay que corregir algo, la oficina pulsa **«Modificar el tren»**, hace el cambio y vuelve a pulsar «Terminar el tren y enviar las Excel». Esto se puede hacer hasta que se cargue la composición del siguiente tren.

## Stock Burriana: entradas, salidas, inventario y ubicaciones

El almacén de Burriana guarda mercancía de clientes (**Raminatrans Castellón**, **Aliplast Iberia**…). La app controla **cuántos palets entran y salen, cuál es cada uno por su código** (el número que va bajo el código de barras de la etiqueta) **y en qué zona del almacén está**. No controla los bultos de cada palet. En las capturas, los **números rojos** señalan lo que se explica en cada paso.

![Menú principal: Oficina Burriana](/guia-img/menu.jpg)

Pantalla principal → **Oficina Burriana**. Dentro hay cuatro grandes botones: **Entrada de mercancía**, **Salidas**, **Inventario** y **Clientes del almacén**.

**Quién hace qué:** la **oficina** da de alta la entrada (albarán) o la salida (petición) y revisa; el **almacén** apunta los palets que descarga o carga. Todo se hace desde el móvil.

### 1. Entrada de mercancía (descarga de un camión)

1. **La oficina** pulsa **Entradas → «Nueva entrada»** y hace foto al albarán, o elige el **PDF y el Excel** que manda el cliente (el Excel trae la lista exacta de palets con su código y su peso). Se pueden elegir hasta 4 archivos (también el CMR y el packing list).
2. La app propone el **cliente** y avisa de conflictos (albarán ya cargado, referencia que no está en su catálogo, fecha rara). Revisa los datos y **el día de entrada en el almacén**: la app propone el del albarán, pero **cámbialo** si el camión se cargó un día y se descarga al siguiente (es el día que cuenta para el estocaje). Pulsa **«Guardar la entrada»**.
3. **El almacén** abre la entrada y va apuntando cada palet.

![Entrada: botones para apuntar palets y ubicación](/guia-img/entrada-arriba.jpg)

- **(1)** **«Foto de la etiqueta del palet»:** la app lee la etiqueta de la foto.
- **(2)** **«Escanear códigos de barras»:** se abre la cámara y se pasan los palets uno tras otro sin cerrarla; cada código leído suena, vibra y se apunta solo (sale como «escaneado»). Es lo más rápido y evita errores al teclear.
- **(3)** **Ubicación en el almacén:** **antes de empezar a escanear, elige la zona** donde vas a dejar los palets: **estanterías E1 a E6** o **suelo S1 a S10**. Cada palet que apuntes queda en esa ubicación. Si a mitad de descarga ya no caben, **cambia la ubicación en el desplegable antes de escanear el siguiente** y sigue.
- **(4)** **«Lo que dice el albarán»:** cuántos palets de cada referencia faltan por verificar (3/6 = 3 apuntados de 6).

   Si ninguna forma funciona, abajo está **«Elegir un palet de la lista o apuntarlo a mano»**: se elige el código en la lista desplegable (solo salen los del albarán que faltan por apuntar) o se escribe a mano (sale como «a mano»).

La app comprueba que cada código está en el albarán y no está repetido. En la lista **«Palets verificados»** cada palet muestra su referencia, lote, kilos, código y su **📍 ubicación**:

![Lista de palets con sus botones](/guia-img/entrada-palets.jpg)

- **(1)** **📝 Corregir:** cambia los datos de un palet mal leído (ver más abajo).
- **(2)** **⚠️ Incidencia:** anota un problema del bulto con fotos (el número indica cuántas lleva ya).
- **(3)** **Quitar:** el palet deja de contar en esta entrada.
- **(4)** **Aviso amarillo:** la app explica por qué ese palet no cuadra (no está en el albarán, está repetido, lote distinto…).

**Palet leído dos veces o fuera de lista:** el primero queda como bueno y el repetido sale como advertencia para depurar (puedes quitarlo con «Quitar»); si no lo quitas, se descarta solo al cerrar y no entra dos veces. Los palets repetidos o que no figuran en la lista no cuentan para decir que «sobran» otros: los palets declarados en el albarán se dan por buenos.

#### Corregir un palet con error

Si un palet sale con aviso porque se leyó mal (referencia, lote, kilos o código), pulsa **📝**. Sus datos pasan al formulario de abajo con un recuadro amarillo; cambia lo que esté mal y pulsa **«Guardar corrección»**. El palet se vuelve a comprobar con el albarán (si ya cuadra, desaparece el aviso) y **conserva su foto, su ubicación y sus incidencias**. «Cancelar» lo deja como estaba.

![Corregir un palet](/guia-img/corregir.jpg)

- **(1)** Recuadro que dice qué palet se está corrigiendo.
- **(2)** Campos con los datos actuales: cámbialos.
- **(3)** **Guardar corrección.**

#### Incidencia de un bulto (palet roto, film abierto, golpe…)

Pulsa **⚠️** en el palet. Escribe qué ha pasado y haz hasta **4 fotos** del bulto, con la cámara o **eligiéndolas de la galería**. Al terminar la entrada, el **correo a la oficina** lleva una sección **«Incidencias por bulto»** con la explicación y las fotos dentro del propio correo. También se guardan en la carpeta de SharePoint y la Excel tiene la columna «Incidencia». Funciona igual en las salidas.

![Incidencia del bulto](/guia-img/incidencia.jpg)

- **(1)** Qué ha pasado.
- **(2)** **Hacer una foto del bulto** con la cámara.
- **(3)** **Elegir fotos de la galería** o de archivos.
- **(4)** **Guardar la incidencia.**

#### Terminar la descarga

Abajo, si hace falta cambia el **día de entrada**, escribe las **incidencias generales de la descarga** (falta un palet, camión sucio…) haz la **foto del albarán firmado** por nosotros como recepción de la mercancía (es **obligatoria**: sin ella la app no deja terminar; puedes hacerla con la cámara o elegir hasta 4 fotos de la galería) y pulsa **«Terminar la descarga»**. Si hay diferencias (palets de más o de menos, un código que no está en el albarán), la app las lista y pide confirmar. Al cerrar se manda **un correo** a la oficina con la Excel de la entrada, las fotos (incluido el albarán firmado) y el resumen por ubicación, y se actualiza la Excel de control en SharePoint.

![Cierre de la entrada](/guia-img/cierre.jpg)

- **(1)** Día de entrada en el almacén.
- **(2)** Incidencias de la descarga (salen en el correo).
- **(3)** **Terminar la descarga.**

**Una entrada no se puede declarar dos veces:** si el albarán ya está cargado para ese cliente, la app da error y dice con qué entrada coincide (ni confirmando se puede repetir). Si una entrada está duplicada o mal hecha, ábrela y pulsa **«Anular esta entrada»**: deja de contar (si ya estaba cerrada, sus palets salen del inventario) y el albarán puede volver a declararse. No se puede anular si algún palet suyo ya está en una salida; en ese caso se corrige primero la salida. El correo que se envió al cerrar no se puede retirar.

**Correos que llegan solos (agente.burriana@stinsa.com):** si el cliente manda el albarán a esa dirección, la app prepara la entrada sola (o la salida, si el correo habla de recoger o retirar mercancía). En la lista aparece con **📧 POR REVISAR**: ábrela, comprueba el cliente, el día de entrada y los palets, y pulsa **«Está bien: confirmar»**. **Hasta que no se confirme no se puede cerrar.** Si la app no ha podido prepararla (cliente que no reconoce, albarán ya cargado, sin adjuntos), la oficina recibe un correo «No he podido procesar…» y hay que cargarla a mano.

**Aviso de llegada al cliente (Aliplast):** si el cliente lo tiene activado, al terminar la descarga la app manda un correo a **traficolevante@stinsa.com y a Carmelo** con el mensaje ya redactado para el cliente (albarán, palets, kilos, lotes, incidencias) y la **documentación del transporte** (CMR, albarán, packing list). Arriba lleva un recuadro con el **contacto del cliente**. Quien lo revise lo **reenvía al cliente** y borra el recuadro. El cliente nunca recibe nada directamente de la app. Si el envío falla, aparece un botón para repetirlo. Para Aliplast la app compara además la lista del packing list con los totales del albarán y avisa si no cuadran.

### 2. Salida de mercancía (carga de un camión)

1. **La oficina** pulsa **Salidas → «Nueva salida»**, elige el cliente y escribe lo que piden (o lee la petición de una foto, un PDF o un Excel con los palets).
2. **El almacén** abre la salida y apunta cada palet que carga: con **«Escanear códigos de barras»**, con la foto de la etiqueta o eligiéndolo en la lista desplegable.

![Salida: dónde está el material pedido](/guia-img/salida.jpg)

- **(1)** **«Está en…»:** debajo de cada referencia pedida, la app dice **en qué ubicaciones hay ese material** y cuántos palets en cada una (por ejemplo «E1 (1) · sin ubicación (1)»). Así se sabe dónde ir a buscarlo.
- **(2)** **Escanear códigos de barras:** cada palet cargado se comprueba con el stock: que está en el almacén, que es del cliente y que es lo que se pidió.

3. En la lista de **«Palets cargados»** cada palet muestra su **📍 ubicación** de donde se ha sacado. En la lista desplegable de palets disponibles también sale la ubicación de cada uno, y los más antiguos primero. **No hay que respetar el orden de entrada (FIFO)**: solo se avisa (⏳) de lo que lleva más de un mes.

![Palets cargados con su ubicación](/guia-img/salida-palets.jpg)

4. En los clientes marcados «hay que localizar el palet concreto» (Aliplast) hay que cargar exactamente los palets de la lista de la petición y la app avisa de los que faltan; en los demás (Raminatrans) basta con la referencia y el número de palets.
5. **Palets sin código:** si la mercancía no tiene código (ver Inventario), en el formulario de abajo hay un desplegable **«Palets sin código del almacén»** para elegir el que se carga.
6. Incidencias de la carga en **«Incidencias de la carga»** (y ⚠️ en cada palet). Al terminar, **«Terminar la carga y expedir la Excel»**: sale un correo con la Excel de lo cargado y las fotos.

### 3. Inventario: lo que hay en el almacén

**Inventario** muestra los palets y kilos que hay ahora, por cliente y referencia, con el lote y las **ubicaciones**. Avisa (⏳) de la mercancía que **lleva más de un mes** en el almacén. El botón **«Rehacer la Excel de control»** vuelve a generar el libro en SharePoint.

![Inventario](/guia-img/inventario.jpg)

- **(1)** **Entrada manual de inventario:** dar de alta mercancía que ya estaba en la nave.
- **(2)** **Control de inventario:** contar lo que hay físicamente y compararlo con el sistema.
- **(3)** **Ubicar / cambiar ubicación:** en cada referencia, para poner o cambiar la ubicación de los palets que ya están en el almacén.

#### Ubicar o cambiar la ubicación de lo que ya está en el almacén

Pulsa **«Ubicar / cambiar ubicación»** en la referencia. Se abre la lista de sus palets, cada uno con su 📍.

![Ubicar palets](/guia-img/ubicar.jpg)

- **(1)** Elige la **ubicación** que quieres asignar.
- **(2)** **«Marcar los que no tienen ubicación»** los selecciona de golpe (también puedes marcar a mano o usar «Marcar todos / ninguno»).
- **(3)** Casilla de cada palet.
- **(4)** **«Asignar ubicación a los marcados».**

Poner ubicación a palets que **no la tenían** es directo. Si entre los marcados hay palets que **ya tienen otra ubicación**, la app avisa de cuáles son y **pide confirmación** antes de cambiarlos. Se guarda la ubicación anterior de cada palet.

#### Entrada manual de inventario

Sirve para dar de alta mercancía que ya estaba en la nave (por ejemplo, las existencias de un cliente nuevo) o que aparece sin albarán. **Inventario → «Entrada manual de inventario»**, eliges cliente y fecha de entrada, y apuntas los palets como en una entrada normal (foto de la etiqueta, escáner o a mano), **eligiendo antes la ubicación**. Al terminar, **«Terminar la entrada de inventario»**: se manda un correo a **traficolevante@stinsa.com y a Carmelo**. No se compara con ningún albarán, no se avisa al cliente y no cuenta como manipulación de entrada para facturar (sí para el estocaje). También tiene los botones 📝 corregir, ⚠️ incidencia y Quitar.

![Entrada manual de inventario con un palet sin código](/guia-img/inventario-manual.jpg)

- **(1)** **Ubicación** donde se deja el palet.
- **(2)** **«Este palet no tiene código»:** para mercancía no normativa sin etiqueta con código. Se acepta **siempre que tenga ubicación**. La app le da un código interno para poder controlarlo y sacarlo después, y en las listas se ve como **«SIN CÓDIGO»**. Solo vale en la entrada manual de inventario; en una entrada con albarán el palet tiene que traer su código.
- **(3)** **Añadir el palet.**

#### Control de inventario

Sirve para comprobar que lo que hay en la nave coincide con el sistema. **Inventario → «Control de inventario»**: eliges el cliente (o todos), escaneas o escribes los palets que hay físicamente y la app te dice los que **faltan** (están en el sistema y no los has contado) y los que **sobran** (los has encontrado y no están en el sistema, o son de otro cliente). Al terminar puedes marcar **«Dar de baja los que faltan»**; se manda un resumen por correo.

![Control de inventario](/guia-img/control.jpg)

- **(1)** **Palets sin código del inventario:** desplegable para elegir el palet sin código que has encontrado (no tiene etiqueta que escanear).
- **(2)** **Escanear códigos de barras** para el resto.

### 4. Clientes del almacén

Toca un cliente para cambiar su nombre, sus otros nombres o su **catálogo de referencias** (una por línea: `referencia;descripción;códigos equivalentes separados por /`, por ejemplo `F05182S;Film;F05182S-MV`). Si llega una referencia que no está en el catálogo, la app avisa; los códigos equivalentes se tratan como la misma referencia. También se configura si en las salidas hay que localizar el palet concreto y el **aviso de llegada** al cliente (contacto y correo).

### 5. Dónde queda en SharePoint (Almacen Burriana)

- **CONTROL ALMACEN BURRIANA.xlsx**: hojas Resumen stock, Stock (con la ubicación de cada palet), Entradas, Salidas, Movimientos y Clientes. Lo rehace la app: **no escribas en ella**, se pierde. Solo se guarda en SharePoint (lleva el stock de todos los clientes, no se reenvía).
- Una carpeta por operación, **«ENTRADA E-2026-0001»**, **«SALIDA S-2026-0001»** o **«ENTRADA MANUAL DE INVENTARIO I-2026-0001»**, con su Excel y las fotos (incluidas las de las incidencias).

### Si algo falla

- **«No hay conexión con el sistema»:** la app no ha podido hablar con Odoo en ese momento. Espera unos segundos y repite; si pasa siempre, avisa a la oficina de Sevilla.
- **«No hay conexión. Inténtalo de nuevo.»:** el móvil no ha podido enviar la foto o la petición (poca cobertura). Repite con mejor señal.
- **Un palet sale con aviso amarillo:** léelo; casi siempre se arregla con **📝 Corregir** (dato mal leído) o **Quitar** (palet repetido).
- **La app no muestra lo último que se ha cambiado:** cierra la app del todo y ábrela otra vez.


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
| «Hay discrepancias entre el albarán y las etiquetas…» | Lee la lista. Si es un error de foto, quita el palet y fotografíalo otra vez; si es real (palet que sobra o falta), confirma y deja la incidencia escrita. |
| «Esta operación llegó por correo y la oficina todavía no la ha revisado» | Abre la entrada o salida y pulsa «Está bien» tras comprobar los datos. |
| «La referencia … no está en el catálogo» | Comprueba que está bien escrita. Si es un producto nuevo, añádela en Clientes del almacén. |
| «Modo de prueba: no se ha guardado nada» | La app está en pruebas. Avisa al responsable de la app. |

## Consejos

- Si un dato del albarán se ha leído mal, corrígelo **antes de guardar**. Después también se puede, desde «Modificar una carga».
- Revisa **«Cargas pendientes»** antes de terminar cada tren, para que el correo a SAICA vaya completo.
- Si la app no muestra lo nuevo después de una actualización, ciérrala del todo y vuelve a abrirla.
