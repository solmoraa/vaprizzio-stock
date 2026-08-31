
## Formato obligatorio al agregar productos

Cuando el script devuelva `"ok": true` para `agregar-producto`, mostrar los datos así:

Producto agregado correctamente:

Marca: Elfbar
Sabor: Menta
Stock: 35
Costo: $9.000
Precio de venta: $19.000
Ganancia por unidad: $10.000

Usar los valores reales devueltos por el script.

No escribir la respuesta en una sola oración.
No usar paréntesis.
No mostrar el JSON crudo.
Aplicar separador de miles y signo `$` a costo, precio y ganancia.

## Formato obligatorio al registrar una venta

Cuando el script devuelva `"ok": true` para `registrar-venta`, responder con este formato:

Venta registrada correctamente:

Orden: {orden}
Cliente: {cliente}
Marca: {marca}
Sabor: {sabor}
Cantidad: {cantidad}
Estado: {estado}
Precio total: ${precio}
Ganancia: ${ganancia}
Forma de pago: {forma_pago}
Stock restante: {stock_restante}

Usar los valores reales devueltos por el script.

No escribir todo en una sola oración.
No usar paréntesis.
No mostrar el JSON crudo.
Usar una línea distinta para cada dato.
Aplicar signo `$` y separador de miles al precio y a la ganancia.

## Estado predeterminado

Cuando se registre una venta y el usuario no indique el estado, usar automáticamente:

Entregado

No pedir el estado como dato faltante.

Solo usar un estado diferente cuando el usuario lo indique expresamente.

## Límites temporales de Google Sheets

Los scripts controlan automáticamente la velocidad de solicitudes y reintentan
los errores 429.

Esperar a que termine la ejecución de la herramienta.

No responder antes de que el script termine.
No prometer una notificación futura.
No afirmar que la operación fue realizada si no se obtuvo `"ok": true`.

## Plataforma obligatoria en ventas

Antes de ejecutar `registrar-venta`, comprobar que el usuario haya indicado
la plataforma o medio por el que realizó la venta.

Si falta, preguntar:

¿Por qué medio realizaste la venta?

No ejecutar el script hasta obtener la plataforma.
Nunca inventar WhatsApp, Instagram, Tienda Nube, Mercado Libre ni otra opción.

El estado no es un dato obligatorio. Cuando no se indique, usar `Entregado`.

## Ubicación de las ventas

Las ventas deben registrarse en la fila inmediatamente posterior a la última
venta cuyo número de orden sea numérico.

Ignorar las filas de plantilla que tengan textos como:

- Orden
- Sabor
- d/m/yyyy
- $xx

Nunca elegir una fila vacía aleatoria ni escribir datos en columnas distintas
a las detectadas mediante los encabezados de la hoja.

## Cantidad y precio predeterminados al registrar ventas

Antes de ejecutar `registrar-venta`:

- Si no se indica cantidad, usar `--cantidad 1`.
- No preguntar la cantidad.
- Si no se indica un precio especial, no pedirlo.
- Omitir `--precio` para que el script use el precio de venta configurado en Productos.
- Si falta la plataforma, preguntar solamente:

¿Por qué medio realizaste la venta?

El estado sigue siendo `Entregado` cuando no se indique otro.

## Separación entre plataforma y forma de pago

No confundir el canal de venta con la forma de pago.

Ejemplo:

"por un amigo en efectivo"

Debe ejecutarse como:

--plataforma "Amigo"
--forma-pago "EFECTIVO"

Nunca usar "Transferencia" si el usuario dijo "efectivo".

Antes de ejecutar `registrar-venta`, convertir la forma de pago a mayúsculas.

Ejemplos:

efectivo → EFECTIVO
mercado pago → MERCADO PAGO FABRI
falta pagar → FALTA PAGAR
mitad efectivo y mitad mercado pago →
EFECTIVO + MERCADO PAGO FABRI

## Separación entre plataforma y forma de pago

No confundir el canal de venta con la forma de pago.

Ejemplo:

"por un amigo en efectivo"

Debe ejecutarse como:

--plataforma "Amigo"
--forma-pago "EFECTIVO"

Nunca usar "Transferencia" si el usuario dijo "efectivo".

Antes de ejecutar `registrar-venta`, convertir la forma de pago a mayúsculas.

Ejemplos:

efectivo → EFECTIVO
mercado pago → MERCADO PAGO FABRI
falta pagar → FALTA PAGAR
mitad efectivo y mitad mercado pago →
EFECTIVO + MERCADO PAGO FABRI
