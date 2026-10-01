# HERRAMIENTAS DE VAPRIZZIO

Para administrar Vaprizzio se deben usar los scripts ubicados en:

`/home/openclaw/.openclaw/workspace/vaprizziobot/scripts`

Intérprete obligatorio:

`/home/openclaw/.openclaw/workspace/vaprizziobot/.venv/bin/python`

Antes de afirmar que una operación se completó:

1. ejecutar el script correcto mediante `exec`;
2. comprobar que el JSON devuelto tenga `"ok": true`;
3. responder usando los datos reales devueltos por el script.

Está prohibido sustituir Google Sheets por archivos Markdown o archivos locales.

<!-- INICIO HERRAMIENTA IMAGEN PRODUCTO -->

## Alta de producto con imagen de Telegram

Para crear un modelo nuevo, ejecutar:

`/home/openclaw/.openclaw/workspace/vaprizziobot/.venv/bin/python /home/openclaw/.openclaw/workspace/vaprizziobot/scripts/agregar_producto.py --marca "MODELO" --sabor "SABOR 1, SABOR 2" --stock STOCK --costo COSTO --precio PRECIO --imagen "{{MediaPath}}"`

Reglas:

- `{{MediaPath}}` debe ser la ruta local real de la imagen recibida.
- No ejecutar el alta de un modelo nuevo sin `--imagen`.
- Los sabores separados por comas pertenecen al mismo producto.
- La imagen se asigna como imagen principal del producto.
- Confirmar la operación solamente cuando el JSON tenga `"ok": true`.
- Verificar además que el JSON contenga `"imagen"` y `"image_id"`.

<!-- FIN HERRAMIENTA IMAGEN PRODUCTO -->

<!-- INICIO FIX SEPARACION SABORES TOOL -->

## Formato interno para varios sabores

Cuando haya varios sabores del mismo modelo, ejecutar:

`--sabor "SABOR 1 | SABOR 2 | SABOR 3"`

Usar `|` como separador interno aunque el usuario haya utilizado comas.

Ejemplo completo:

`/home/openclaw/.openclaw/workspace/vaprizziobot/.venv/bin/python /home/openclaw/.openclaw/workspace/vaprizziobot/scripts/agregar_producto.py --marca "MODELO" --sabor "Melon | Sandia | Caramelo" --stock 3 --costo 3000 --precio 7000 --imagen "{{MediaPath}}"`

Cada elemento separado por `|` debe crearse como una variante distinta del mismo producto.

<!-- FIN FIX SEPARACION SABORES TOOL -->

<!-- INICIO TOOL PROMOCIONES TIENDANUBE -->

## Herramienta de promociones

Ruta:

`/home/openclaw/.openclaw/workspace/vaprizziobot/scripts/promociones.py`

Python:

`/home/openclaw/.openclaw/workspace/vaprizziobot/.venv/bin/python`

Ejemplos:

`/home/openclaw/.openclaw/workspace/vaprizziobot/.venv/bin/python /home/openclaw/.openclaw/workspace/vaprizziobot/scripts/promociones.py aplicar --modelo "Ignite V250" --sabor "Watermelon Ice" --porcentaje 10`

`/home/openclaw/.openclaw/workspace/vaprizziobot/.venv/bin/python /home/openclaw/.openclaw/workspace/vaprizziobot/scripts/promociones.py aplicar --modelo "Ignite V250" --sabor "Watermelon Ice" --precio-promocional 15000`

`/home/openclaw/.openclaw/workspace/vaprizziobot/.venv/bin/python /home/openclaw/.openclaw/workspace/vaprizziobot/scripts/promociones.py aplicar --modelo "Ignite V250" --porcentaje 20`

`/home/openclaw/.openclaw/workspace/vaprizziobot/.venv/bin/python /home/openclaw/.openclaw/workspace/vaprizziobot/scripts/promociones.py aplicar --toda-tienda --porcentaje 20`

`/home/openclaw/.openclaw/workspace/vaprizziobot/.venv/bin/python /home/openclaw/.openclaw/workspace/vaprizziobot/scripts/promociones.py quitar --modelo "Ignite V250" --sabor "Watermelon Ice"`

`/home/openclaw/.openclaw/workspace/vaprizziobot/.venv/bin/python /home/openclaw/.openclaw/workspace/vaprizziobot/scripts/promociones.py quitar --toda-tienda`

`/home/openclaw/.openclaw/workspace/vaprizziobot/.venv/bin/python /home/openclaw/.openclaw/workspace/vaprizziobot/scripts/promociones.py listar --toda-tienda`

<!-- FIN TOOL PROMOCIONES TIENDANUBE -->

## Regla obligatoria para modificar costos

Cuando el usuario diga «cambiar/modificar el costo» de una marca, el valor es
SIEMPRE **Costo USDT del modelo**, nunca pesos. Ejecutar:

`/home/openclaw/.openclaw/workspace/vaprizziobot/.venv/bin/python /home/openclaw/.openclaw/workspace/vaprizziobot/scripts/modificar_producto.py --marca "MODELO" --costo VALOR_USDT`

No pasar `--sabor` para un cambio exclusivo de costo. El script actualiza una
sola fila por marca en la tabla auxiliar, calcula Pase y Costo Total USDT y
actualiza Costo/Ganancia de todos los sabores SOLO en Google Sheets. Nunca
sincronizar costos con Tiendanube. El precio de venta sí debe actualizarse en
Google Sheets y Tiendanube mediante `--precio`.


## Gestión estricta de modelos (regla obligatoria)
- Para crear un MODELO NUEVO usar exclusivamente:
  `python scripts/agente_vaprizzio.py crear-modelo --json '<JSON>'`
- Nunca elegir un modelo parecido ni existente. El nombre del modelo se compara de forma exacta.
- La creación acepta imagen, precio de venta, costo USDT y uno o varios sabores con stock individual.
- Ejemplo JSON:
  `{"modelo":"Nuevo Modelo 40k","imagen":"/ruta/imagen.jpg","costo_usdt":9.5,"precio_venta":25000,"sabores":[{"sabor":"MIAMI MINT","stock":4},{"sabor":"GRAPE ICE","stock":2}]}`
- El costo USDT se guarda y calcula solo en Google Sheets. El precio de venta y el stock se crean en Tiendanube y Sheets.
- Para borrar el modelo entero usar:
  `python scripts/agente_vaprizzio.py eliminar-modelo --json '{"modelo":"Nombre exacto"}'`
- El borrado elimina el producto y su imagen/variantes de Tiendanube, sus filas en Productos y su fila de costos USDT.

<!-- INICIO TOOL VAP VENTA 20260831 -->
## Contrato administrativo de ventas

`registrar-venta` recibe las claves `cliente`, `productos`, `plataforma`,
`plataforma_confirmada` y `forma_pago`. Cada elemento de `productos` contiene
`marca`, `sabor` y `cantidad`. Los nombres se resuelven contra el catalogo vivo;
los alias solo se aplican por igualdad exacta. Una solicitud admite un intento.

`registrar-ventas` recibe `ventas`, una lista de objetos con el mismo contrato.
Usarlo una sola vez si el usuario informa dos o más ventas. Cuando diga
expresamente «de la misma forma», la venta siguiente puede usar
`"misma_forma_anterior":true`: hereda solo plataforma y forma de pago, nunca
cliente, productos, cantidad ni fecha.

`modificar-plataforma-venta` requiere `orden` y `plataforma`; cambia solamente
ese campo y nunca vuelve a registrar la venta.

`cancelar-orden` requiere `orden` y `confirmar:true`. Elimina la orden y
repone su stock si es una venta manual; si corresponde a TiendaNube, usa la
cancelación de esa plataforma. Antes de ejecutarla, solicitar confirmación
explícita y no repetirla.

Validación diagnóstica de solo lectura, solo si un humano la pide expresamente:

`/home/openclaw/.openclaw/workspace/vaprizziobot/.venv/bin/python /home/openclaw/.openclaw/workspace/vaprizziobot/scripts/validar_catalogo_venta.py`

Para probar un caso concreto sin escribir:

`/home/openclaw/.openclaw/workspace/vaprizziobot/.venv/bin/python /home/openclaw/.openclaw/workspace/vaprizziobot/scripts/validar_catalogo_venta.py --modelo "Lost Mary Dura" --sabor "Grape Ice" --sabor "Watermelon Ice"`

Ambas validaciones no modifican stock ni ventas, pero consumen una lectura de
Google Sheets. Nunca ejecutarlas antes, durante o después de registrar una
venta y nunca ante un error de registro.
<!-- FIN TOOL VAP VENTA 20260831 -->

