from __future__ import annotations

from collections import defaultdict
from typing import Any

from app import order_sync as legacy
from app.order_sync_v2 import (
    sales_worksheets,
    sheet_columns,
)
from app.sync_core import (
    modify_existing_product,
    read_products,
    sheet_product_context,
)

from .common import (
    BusinessError,
    celda,
    columna_a_letra,
    dinero,
    estado_canonico,
    fecha_desde_value,
    fecha_para_sheet,
    normalizar,
    numero,
    texto,
)
from .products import resolver_producto


# Equivalencias historicas entre nombres de Productos/Tiendanube y Ventas.
# Esta tabla solo corrige diferencias conocidas entre ambas hojas. No es una
# lista de modelos permitidos: cualquier modelo nuevo conserva su nombre exacto
# del catalogo y se valida contra el desplegable real de Google Sheets.
ALIAS_MODELOS = {
    "elfbar bc 15k": "Elfbar 15K",
    "elfbar 15k": "Elfbar 15K",

    "elfbar te 30k": "Elfbar TE30K",
    "elfbar te30k": "Elfbar TE30K",

    "elfbar create bc pro 40k": (
        "Elfbar Create BC pro 40k"
    ),
    "elfbar create bc pro": (
        "Elfbar Create BC pro 40k"
    ),

    "elfbar ice king 40k": "Elfbar Ice king",
    "elfbar ice king": "Elfbar Ice king",

    "elfbar summer": "Elfbar Summer",

    "elfbar pro 45k": "Elfbar pro 45K",
    "elfbar bc pro 45k": "Elfbar pro 45K",
    "eflbar pro 45k": "Elfbar pro 45K",

    "ignite v nano": "Ignite v-nano",
    "ignite vnano": "Ignite v-nano",

    "ignite v155": "Ignite v155",
    "ignite v 155": "Ignite v155",

    "ignite v250": "Ignite v250",
    "ignite v250 black": "Ignite v250",
    "ignite v250 gold": "Ignite v250",
    "ignite v 250": "Ignite v250",

    "ignite v300 slim": "Ignite v300 slim",
    "ignite v 300 slim": "Ignite v300 slim",

    "lost mary mixer": "Lost Mary Mixer",
    "lost mary mixer 30k": "Lost Mary Mixer 30k",

    "lost mary mo 5k": "Lost Mary MO 5k",
    "lost mary mo5k": "Lost Mary MO 5k",
    "lost mary mo": "Lost Mary MO 5k",

    "geek bar pulse x": "Geek Bar Pulse x",
    "geekbar pulse x": "Geek Bar Pulse x",

    "blvk": "BLVK",
    "mayorista": "Mayorista",

    "airmez 40k auris": "Airmez 40k auris",
    "airmez 40k": "Airmez 40k auris",

    "dummy 8k": "Dummy 8k",

    "maskking extre 100k": "Maskking 100k",
    "maskking extreme 100k": "Maskking 100k",
    "maskking extre": "Maskking 100k",
    "maskking extreme": "Maskking 100k",
    "maskking 100k": "Maskking 100k",
    "maskking100k": "Maskking 100k",
    "maskking": "Maskking 100k",
}


def modelo_para_ventas(modelo_productos: Any) -> str:
    """
    Aplica una equivalencia historica exacta cuando existe.

    Los modelos nuevos se devuelven sin cambios. La fuente de verdad es el
    catalogo de Productos y el desplegable vivo de Google Sheets, no una lista
    estatica dentro del codigo.
    """
    original = texto(modelo_productos)

    if not original:
        raise BusinessError(
            "El producto no tiene modelo o marca."
        )

    normalized = normalizar(original)

    return ALIAS_MODELOS.get(normalized, original)


# ============================================================
# FORMAS DE PAGO
# ============================================================

def normalizar_forma_pago(value: Any) -> str:
    """
    Todas las formas de pago se guardan en mayúsculas.

    Ejemplos:
        efectivo
        -> EFECTIVO

        falta pagar
        -> FALTA PAGAR

        efectivo: $10.000 / mercado pago fabri: $14.000
        -> EFECTIVO: $10.000 / MERCADO PAGO FABRI: $14.000
    """
    raw = texto(value)

    if not raw:
        return ""

    normalized = normalizar(raw)

    aliases = {
        "efectivo": "EFECTIVO",
        "cash": "EFECTIVO",

        "falta pagar": "FALTA PAGAR",
        "pendiente": "FALTA PAGAR",
        "pendiente de pago": "FALTA PAGAR",

        "mercado pago fabri": "MERCADO PAGO FABRI",
        "mercadopago fabri": "MERCADO PAGO FABRI",
        "mp fabri": "MERCADO PAGO FABRI",

        "mercado pago": "MERCADO PAGO",
        "mercadopago": "MERCADO PAGO",

        "transferencia": "TRANSFERENCIA",
    }

    if normalized in aliases:
        return aliases[normalized]

    return raw.upper()


def tipo_forma_pago(value: Any) -> str:
    payment = normalizar_forma_pago(value)
    normalized = normalizar(payment)

    if normalized == "efectivo":
        return "efectivo"

    if normalized == "falta pagar":
        return "falta_pagar"

    if normalized == "mercado pago fabri":
        return "mercado_pago_fabri"

    separators = (
        "/",
        "+",
        "|",
        ";",
    )

    if any(separator in payment for separator in separators):
        return "combinado"

    payment_methods = (
        "efectivo",
        "mercado pago",
        "transferencia",
        "tarjeta",
    )

    detected = sum(
        1
        for method in payment_methods
        if method in normalized
    )

    if detected >= 2:
        return "combinado"

    return "otro"


def aplicar_formato_pago(
    worksheet: Any,
    rows: list[int],
    payment_column: int | None,
    payment_value: Any,
) -> None:
    if not payment_column or not rows:
        return

    payment_type = tipo_forma_pago(payment_value)

    backgrounds = {
        "efectivo": {
            "red": 1.0,
            "green": 1.0,
            "blue": 0.0,
        },
        "falta_pagar": {
            "red": 1.0,
            "green": 0.0,
            "blue": 0.0,
        },
        "mercado_pago_fabri": {
            "red": 0.0,
            "green": 1.0,
            "blue": 0.0,
        },
        "combinado": {
            "red": 1.0,
            "green": 1.0,
            "blue": 1.0,
        },
        "otro": {
            "red": 1.0,
            "green": 1.0,
            "blue": 1.0,
        },
    }

    background = backgrounds[payment_type]
    requests = []

    for row_number in rows:
        requests.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": row_number - 1,
                        "endRowIndex": row_number,
                        "startColumnIndex": (
                            payment_column - 1
                        ),
                        "endColumnIndex": payment_column,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": background,
                        }
                    },
                    "fields": (
                        "userEnteredFormat.backgroundColor"
                    ),
                }
            }
        )

    worksheet.spreadsheet.batch_update(
        {"requests": requests}
    )


# ============================================================
# PRODUCTOS
# ============================================================

def producto_por_variant_id(
    variant_id: Any,
    catalogo: list[Any] | None = None,
) -> dict[str, Any] | None:
    expected = texto(variant_id)

    if not expected:
        return None

    products = catalogo
    if products is None:
        _, _, _, products = read_products()

    for product in products:
        if texto(product.variant_id) == expected:
            return {
                "fila": product.row,
                "marca": product.marca,
                "sabor": product.sabor,
                "stock": product.stock,
                "costo": product.costo,
                "precio": product.precio,
                "ganancia": product.ganancia,
                "product_id": product.product_id,
                "variant_id": product.variant_id,
                "location_id": product.location_id,
                "sku": product.sku,
            }

    return None


def producto_catalogo_a_dict(product: Any) -> dict[str, Any]:
    """Convierte una fila ya leída de Productos al contrato de ventas."""
    return {
        "fila": product.row,
        "marca": product.marca,
        "sabor": product.sabor,
        "stock": product.stock,
        "costo": product.costo,
        "precio": product.precio,
        "ganancia": product.ganancia,
        "tiendanube_product_id": product.product_id,
        "tiendanube_variant_id": product.variant_id,
        "tiendanube_location_id": product.location_id,
        "sku": product.sku,
    }


def resolver_producto_desde_catalogo(
    *,
    catalogo: list[Any],
    marca: Any = None,
    sabor: Any,
    variant_id: Any = None,
) -> dict[str, Any]:
    """Resuelve una variante en un catálogo ya descargado.

    No se permite cambiar de familia por el sabor. Las equivalencias históricas
    se usan solo como alternativa exacta entre Productos y Ventas.
    """
    requested_model = texto(marca)
    requested_flavor = texto(sabor)
    requested_variant = texto(variant_id)

    if not requested_flavor:
        raise BusinessError("Falta indicar el sabor del producto.")

    if requested_variant:
        matches = [
            product for product in catalogo
            if texto(product.variant_id) == requested_variant
        ]
    else:
        flavor_key = normalizar(requested_flavor)
        candidates = catalogo

        if requested_model:
            requested_key = normalizar(requested_model)
            model_keys = {requested_key}
            model_keys.update(
                normalizar(product_model)
                for product_model, sales_model in ALIAS_MODELOS.items()
                if normalizar(sales_model) == requested_key
            )
            candidates = [
                product for product in catalogo
                if normalizar(product.marca) in model_keys
            ]

        matches = [
            product for product in candidates
            if normalizar(product.sabor) == flavor_key
        ]

    if len(matches) == 1:
        return producto_catalogo_a_dict(matches[0])

    if not matches:
        target = (
            f"{requested_model} / {requested_flavor}"
            if requested_model else requested_flavor
        )
        raise BusinessError(
            f"No encontré exactamente el producto {target!r} "
            "en Productos."
        )

    options = ", ".join(
        f"{product.marca} / {product.sabor}"
        for product in matches[:10]
    )
    raise BusinessError(
        f"Hay más de una coincidencia para {requested_flavor!r}: {options}. "
        "Indicá el modelo exacto."
    )


def resolver_producto_venta(
    *,
    marca: Any = None,
    sabor: Any,
    variant_id: Any = None,
    catalogo: list[Any] | None = None,
) -> dict[str, Any]:
    """
    Resuelve un producto sin cambiar nunca de modelo.

    Reglas:
    - Si llega Variant ID, se usa esa variante.
    - Si llega una marca/modelo, solo se busca dentro de ese modelo.
    - Nunca se ignora la marca para elegir otro producto por sabor.
    - Solo se permite buscar únicamente por sabor cuando el usuario
      realmente no indicó ningún modelo.
    """
    if catalogo is not None:
        return resolver_producto_desde_catalogo(
            catalogo=catalogo,
            marca=marca,
            sabor=sabor,
            variant_id=variant_id,
        )

    by_variant = producto_por_variant_id(variant_id)

    if by_variant:
        return by_variant

    requested_model = texto(marca)
    requested_flavor = texto(sabor)

    if not requested_flavor:
        raise BusinessError(
            "Falta indicar el sabor del producto."
        )

    if not requested_model:
        return resolver_producto(
            marca=None,
            sabor=requested_flavor,
        )

    direct_error: Exception | None = None

    try:
        return resolver_producto(
            marca=requested_model,
            sabor=requested_flavor,
        )
    except Exception as exc:
        direct_error = exc

    sales_model = normalizar(requested_model)

    possible_product_models = []

    for product_alias, sales_value in ALIAS_MODELOS.items():
        if normalizar(sales_value) == sales_model:
            possible_product_models.append(product_alias)

    unique_models = []

    for model in possible_product_models:
        normalized_model = normalizar(model)

        if normalized_model not in {
            normalizar(item)
            for item in unique_models
        }:
            unique_models.append(model)

    matches = []
    errors = []

    for product_model in unique_models:
        try:
            product = resolver_producto(
                marca=product_model,
                sabor=requested_flavor,
            )

            key = (
                normalizar(product["marca"]),
                normalizar(product["sabor"]),
            )

            if key not in {
                (
                    normalizar(item["marca"]),
                    normalizar(item["sabor"]),
                )
                for item in matches
            }:
                matches.append(product)

        except Exception as exc:
            errors.append(str(exc))

    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        options = ", ".join(
            f"{item['marca']} / {item['sabor']}"
            for item in matches
        )

        raise BusinessError(
            f"El modelo {requested_model!r} y el sabor "
            f"{requested_flavor!r} coinciden con varios productos: "
            f"{options}. Indicá el modelo exacto."
        )

    detail = str(direct_error or "")

    if errors:
        detail = errors[-1]

    raise BusinessError(
        f"No encontré exactamente el producto "
        f"{requested_model!r} / {requested_flavor!r}. "
        "No se eligió otro modelo automáticamente. "
        f"{detail}"
    )



def normalizar_items(
    productos: Any,
    *,
    catalogo: list[Any] | None = None,
    sheet_context: tuple[Any, dict[str, int]] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(productos, list) or not productos:
        raise BusinessError(
            "La venta debe contener al menos un producto."
        )

    grouped: dict[
        tuple[str, str],
        dict[str, Any],
    ] = {}

    for index, raw_item in enumerate(
        productos,
        start=1,
    ):
        if not isinstance(raw_item, dict):
            raise BusinessError(
                f"El producto {index} debe ser un objeto JSON."
            )

        marca = texto(
            raw_item.get("marca")
            or raw_item.get("modelo")
            or raw_item.get("vape")
        )

        sabor = texto(
            raw_item.get("sabor")
            or raw_item.get("flavor")
        )

        if not sabor:
            raise BusinessError(
                f"Falta el sabor del producto {index}."
            )

        try:
            cantidad = int(
                raw_item.get("cantidad", 1)
            )
        except (TypeError, ValueError) as exc:
            raise BusinessError(
                f"Cantidad inválida en el producto {index}."
            ) from exc

        if cantidad <= 0:
            raise BusinessError(
                "La cantidad debe ser mayor a cero."
            )

        product = resolver_producto_venta(
            marca=marca or None,
            sabor=sabor,
            variant_id=(
                raw_item.get("variant_id")
                or raw_item.get("tiendanube_variant_id")
            ),
            catalogo=catalogo,
        )

        product_context = None
        if catalogo is not None and sheet_context is not None:
            catalog_product = next(
                (
                    entry for entry in catalogo
                    if normalizar(entry.marca) == normalizar(product["marca"])
                    and normalizar(entry.sabor) == normalizar(product["sabor"])
                ),
                None,
            )
            if catalog_product is None:
                raise BusinessError(
                    "El producto resuelto no pertenece al catálogo cargado."
                )
            product_context = (
                sheet_context[0],
                sheet_context[1],
                catalog_product,
            )

        stock = product["stock"]

        if stock is None:
            raise BusinessError(
                f"No pude determinar el stock de "
                f"{product['marca']} / {product['sabor']}."
            )

        price_value = raw_item.get(
            "precio_unitario",
            raw_item.get("precio"),
        )

        if price_value in (None, ""):
            price_value = product["precio"]

        if price_value is None:
            raise BusinessError(
                f"{product['marca']} / "
                f"{product['sabor']} no tiene precio."
            )

        unit_price = numero(
            price_value,
            nombre="precio unitario",
        )

        raw_unit_cost = product.get("costo")

        if raw_unit_cost in (None, ""):

            raise BusinessError(

                f"{product.get('marca')} / {product.get('sabor')} no tiene "

                "Costo cargado en Productos."

            )

        unit_cost = numero(raw_unit_cost, nombre="costo unitario")

        raw_unit_gain = product.get("ganancia")
        if raw_unit_gain in (None, ""):
            raise BusinessError(
                f"{product.get('marca')} / {product.get('sabor')} no tiene "
                "Ganancia cargada en Productos."
            )
        unit_gain = numero(raw_unit_gain, nombre="ganancia unitaria")

        sales_model = modelo_para_ventas(
            product["marca"]
        )

        key = (
            normalizar(product["marca"]),
            normalizar(product["sabor"]),
        )

        if key not in grouped:
            grouped[key] = {
                "marca_productos": product["marca"],
                "marca_ventas": sales_model,
                "sabor": product["sabor"],
                "cantidad": 0,
                "precio_unitario": dinero(unit_price),
                "precio_total": 0,
                "costo_unitario": dinero(unit_cost),
                "ganancia_unitaria": dinero(unit_gain),
                "ganancia_total": 0,
                "producto": product,
                "sheet_context": product_context,
            }

        item = grouped[key]
        item["cantidad"] += cantidad
        item["precio_total"] = dinero(
            float(item["precio_total"])
            + unit_price * cantidad
        )
        item["ganancia_total"] = dinero(
            float(item["ganancia_total"])
            + unit_gain * cantidad
        )

    result = list(grouped.values())

    for item in result:
        available = item["producto"]["stock"] or 0

        if item["cantidad"] > available:
            raise BusinessError(
                f"Stock insuficiente para "
                f"{item['marca_productos']} / "
                f"{item['sabor']}. "
                f"Disponible: {available}. "
                f"Solicitado: {item['cantidad']}."
            )

    return result


# ============================================================
# TABLA DE VENTAS
# ============================================================

def encontrar_fila_insercion(
    worksheet: Any,
    columns: dict[str, int],
) -> tuple[int, int | None]:
    order_column = columns.get(normalizar("Orden"))

    if not order_column:
        raise BusinessError(
            f"No encontré la columna Orden en "
            f"{worksheet.title}."
        )

    # Las ventas más recientes van inmediatamente debajo del encabezado.
    # Antes se buscaba la última orden numérica y se escribía después; eso
    # mandaba las ventas nuevas al final de hojas que ya tenían historial.
    # La inserción posterior desplaza filas, fórmulas y totales sin borrarlos.
    return 2, None


def copiar_formato_y_validacion(
    worksheet: Any,
    source_row: int,
    start_row: int,
    row_count: int,
    end_column: int,
) -> None:
    if (
        source_row < 2
        or row_count <= 0
        or end_column <= 0
    ):
        return

    source_range = {
        "sheetId": worksheet.id,
        "startRowIndex": source_row - 1,
        "endRowIndex": source_row,
        "startColumnIndex": 0,
        "endColumnIndex": end_column,
    }

    destination_range = {
        "sheetId": worksheet.id,
        "startRowIndex": start_row - 1,
        "endRowIndex": start_row - 1 + row_count,
        "startColumnIndex": 0,
        "endColumnIndex": end_column,
    }

    requests = [
        {
            "copyPaste": {
                "source": source_range,
                "destination": destination_range,
                "pasteType": "PASTE_FORMAT",
                "pasteOrientation": "NORMAL",
            }
        },
        {
            "copyPaste": {
                "source": source_range,
                "destination": destination_range,
                "pasteType": "PASTE_DATA_VALIDATION",
                "pasteOrientation": "NORMAL",
            }
        },
    ]

    try:
        worksheet.spreadsheet.batch_update(
            {"requests": requests}
        )
    except Exception:
        # La venta sigue siendo válida aunque no pueda
        # copiarse algún formato secundario.
        pass



def opciones_dropdown_celda(
    worksheet: Any,
    row_number: int,
    column_number: int,
) -> list[str]:
    """
    Lee las opciones reales configuradas en el desplegable
    de una celda de Google Sheets.

    Esto permite escribir exactamente el mismo texto que
    utiliza la validación y evita que aparezca un chip gris.
    """
    if row_number <= 0 or column_number <= 0:
        return []

    try:
        column_letter = columna_a_letra(column_number)

        safe_title = worksheet.title.replace(
            "'",
            "''",
        )

        cell_range = (
            f"'{safe_title}'!"
            f"{column_letter}{row_number}"
        )

        metadata = (
            worksheet.spreadsheet.fetch_sheet_metadata(
                params={
                    "includeGridData": "true",
                    "ranges": cell_range,
                }
            )
        )

        sheets = metadata.get("sheets") or []

        if not sheets:
            return []

        data_blocks = (
            sheets[0].get("data") or []
        )

        if not data_blocks:
            return []

        row_data = (
            data_blocks[0].get("rowData") or []
        )

        if not row_data:
            return []

        cells = row_data[0].get("values") or []

        if not cells:
            return []

        validation = (
            cells[0].get("dataValidation") or {}
        )

        condition = validation.get("condition") or {}
        condition_type = condition.get("type")
        values = condition.get("values") or []

        result = []

        if condition_type == "ONE_OF_LIST":
            for value in values:
                option = texto(
                    value.get("userEnteredValue")
                )

                if option:
                    result.append(option)

        elif condition_type == "ONE_OF_RANGE":
            # Los desplegables modernos suelen tomar sus opciones de un rango
            # auxiliar. Antes se ignoraba ese caso y se caia en la lista fija
            # antigua, por lo que todo modelo nuevo era rechazado.
            for value in values:
                source_range = texto(
                    value.get("userEnteredValue")
                )

                if not source_range:
                    continue

                source_range = source_range.removeprefix("=")

                try:
                    response = (
                        worksheet.spreadsheet.values_get(
                            source_range
                        )
                    )
                except Exception:
                    continue

                for row in response.get("values") or []:
                    for cell_value in row:
                        option = texto(cell_value)

                        if option:
                            result.append(option)

        else:
            return []

        # Evitar que una opcion repetida en el rango produzca ambiguedad.
        unique = []
        seen = set()

        for option in result:
            key = normalizar(option)

            if key and key not in seen:
                unique.append(option)
                seen.add(key)

        return unique

    except Exception:
        # Si Google no devuelve metadatos, el llamador conserva el nombre
        # exacto que ya fue validado contra Productos.
        return []


def modelo_dropdown_exacto(
    worksheet: Any,
    row_number: int,
    column_number: int,
    requested_model: Any,
) -> str:
    """
    Devuelve el valor exacto del desplegable correspondiente
    al modelo solicitado.
    """
    requested = texto(requested_model)

    if not requested:
        raise BusinessError(
            "No se pudo determinar el modelo para Ventas."
        )

    # El modelo ya fue validado contra Productos. Consultar los metadatos del
    # desplegable por cada fila convierte una simple venta en varias llamadas a
    # Google Sheets y puede provocar 429. La fila anterior conserva la misma
    # validación al copiar su formato; si una lista está atrasada, no se debe
    # rechazar ni reemplazar un modelo válido del catálogo.
    del worksheet, row_number, column_number
    return requested


def buscar_celda_modelo_existente(
    *,
    destination_worksheet: Any,
    requested_model: Any,
    destination_row: int,
) -> tuple[Any, int, int] | None:
    """
    Busca en todas las hojas de Ventas una celda que ya tenga
    seleccionado el modelo solicitado como chip válido.

    Devuelve:
        worksheet, fila, columna
    """
    expected = normalizar(requested_model)

    if not expected:
        return None

    for source_worksheet in sales_worksheets():
        try:
            source_columns = sheet_columns(
                source_worksheet
            )
        except Exception:
            continue

        vape_column = source_columns.get(
            normalizar("Vape")
        )

        if not vape_column:
            continue

        try:
            values = source_worksheet.get_all_values()
        except Exception:
            continue

        for row_number, row in enumerate(
            values[1:],
            start=2,
        ):
            # No utilizar como origen la misma celda
            # que estamos intentando corregir.
            if (
                source_worksheet.id
                == destination_worksheet.id
                and row_number == destination_row
            ):
                continue

            value = celda(row, vape_column)

            if normalizar(value) == expected:
                return (
                    source_worksheet,
                    row_number,
                    vape_column,
                )

    return None


def copiar_chip_vape_existente(
    *,
    worksheet: Any,
    target_row: int,
    target_column: int,
    model: Any,
) -> bool:
    """
    Copia una celda que ya contiene el chip correcto.

    PASTE_NORMAL copia:
    - el valor exacto;
    - la validación del desplegable;
    - el estilo del chip;
    - el color correspondiente al modelo.
    """
    source = buscar_celda_modelo_existente(
        destination_worksheet=worksheet,
        requested_model=model,
        destination_row=target_row,
    )

    if source is None:
        return False

    (
        source_worksheet,
        source_row,
        source_column,
    ) = source

    request = {
        "requests": [
            {
                "copyPaste": {
                    "source": {
                        "sheetId": source_worksheet.id,
                        "startRowIndex": source_row - 1,
                        "endRowIndex": source_row,
                        "startColumnIndex": (
                            source_column - 1
                        ),
                        "endColumnIndex": source_column,
                    },
                    "destination": {
                        "sheetId": worksheet.id,
                        "startRowIndex": target_row - 1,
                        "endRowIndex": target_row,
                        "startColumnIndex": (
                            target_column - 1
                        ),
                        "endColumnIndex": target_column,
                    },
                    "pasteType": "PASTE_NORMAL",
                    "pasteOrientation": "NORMAL",
                }
            }
        ]
    }

    worksheet.spreadsheet.batch_update(request)
    return True


def aplicar_chips_vape(
    *,
    worksheet: Any,
    rows: list[int],
    items: list[dict[str, Any]],
    vape_column: int | None,
    sabor_column: int | None,
) -> None:
    """
    Aplica los chips de Vape después de escribir la venta.

    También vuelve a escribir el sabor en mayúsculas para
    garantizar que copiar el chip no altere otras columnas.
    """
    if not vape_column:
        raise BusinessError(
            f"No encontré la columna Vape en "
            f"{worksheet.title}."
        )

    for row_number, item in zip(rows, items):
        model = item["marca_ventas"]

        copied = copiar_chip_vape_existente(
            worksheet=worksheet,
            target_row=row_number,
            target_column=vape_column,
            model=model,
        )

        if not copied:
            # Si todavía no existe ninguna venta histórica
            # con ese modelo, se escribe el valor exacto.
            # La validación copiada de la fila seguirá activa.
            worksheet.update_cell(
                row_number,
                vape_column,
                model,
            )

        if sabor_column:
            worksheet.update_cell(
                row_number,
                sabor_column,
                texto(item["sabor"]),
            )

def escribir_filas_venta(
    *,
    worksheet: Any,
    columns: dict[str, int],
    internal_order: int,
    items: list[dict[str, Any]],
    cliente: str,
    estado: str,
    fecha: str,
    plataforma: str,
    forma_pago: str,
) -> list[int]:
    start_row, _ = encontrar_fila_insercion(
        worksheet,
        columns,
    )

    row_count = len(items)
    end_row = start_row + row_count - 1

    max_column = max(
        max(columns.values(), default=11),
        20,
    )

    # Crear lugar al principio de la tabla, sin sobrescribir ventas ni la
    # fila de totales. Las filas existentes se desplazan hacia abajo.
    worksheet.insert_rows(
        [
            [""] * max_column
            for _ in range(row_count)
        ],
        row=start_row,
        value_input_option="USER_ENTERED",
        inherit_from_before=False,
    )

    # Tras insertar, la antigua primera fila de datos queda debajo de las
    # nuevas. La usamos como plantilla para conservar formato y desplegables.
    source_row = start_row + row_count

    copiar_formato_y_validacion(
        worksheet,
        source_row,
        start_row,
        row_count,
        max_column,
    )

    rows_to_write = []

    def set_value(
        row: list[Any],
        header: str,
        value: Any,
    ) -> None:
        column = columns.get(normalizar(header))

        if column:
            row[column - 1] = value

    for offset, item in enumerate(items):
        row = [""] * max_column
        product = item["producto"]
        target_row = start_row + offset

        set_value(row, "Orden", internal_order)
        set_value(row, "Cantidad", item["cantidad"])
        set_value(row, "Cliente", cliente)
        vape_column = columns.get(
            normalizar("Vape")
        )

        if not vape_column:
            raise BusinessError(
                f"No encontré la columna Vape en "
                f"{worksheet.title}."
            )

        exact_vape = modelo_dropdown_exacto(
            worksheet,
            target_row,
            vape_column,
            item["marca_ventas"],
        )

        set_value(row, "Vape", exact_vape)

        # Conservar el nombre exacto del sabor guardado
        # en la hoja Productos.
        set_value(
            row,
            "Sabor",
            texto(item["sabor"]),
        )
        set_value(row, "Estado", estado)
        set_value(row, "Fecha del pedido", fecha)

        set_value(
            row,
            "Precio Venta",
            item["precio_total"],
        )
        set_value(
            row,
            "Plataforma de ventas",
            plataforma,
        )
        set_value(
            row,
            "Ganancia",
            item["ganancia_total"],
        )
        set_value(
            row,
            "Forma de pago",
            forma_pago,
        )

        # Las ventas externas no tienen Order ID de
        # Tiendanube, pero guardamos los IDs del producto
        # para poder cancelar con precisión.
        set_value(row, "TiendaNube Order ID", "")
        set_value(row, "TiendaNube Order Number", "")
        set_value(
            row,
            "TiendaNube Product ID",
            product.get("tiendanube_product_id")
            or product.get("product_id")
            or "",
        )
        set_value(
            row,
            "TiendaNube Variant ID",
            product.get("tiendanube_variant_id")
            or product.get("variant_id")
            or "",
        )
        set_value(row, "TiendaNube Line ID", "")
        set_value(row, "Última sincronización", "")

        rows_to_write.append(row)

    end_column = columna_a_letra(max_column)

    worksheet.update(
        range_name=(
            f"A{start_row}:"
            f"{end_column}{end_row}"
        ),
        values=rows_to_write,
        value_input_option="USER_ENTERED",
    )

    written_rows = list(
        range(start_row, end_row + 1)
    )

    # El formato y la validación se copiaron en bloque desde la fila anterior.
    # No se buscan ni copian chips históricos: eso recorría todas las hojas de
    # ventas por cada artículo y era una causa de saturación de Google Sheets.

    aplicar_formato_pago(
        worksheet,
        written_rows,
        columns.get(normalizar("Forma de pago")),
        forma_pago,
    )

    return written_rows


# ============================================================
# STOCK
# ============================================================

def restar_stock_item(
    item: dict[str, Any],
) -> dict[str, Any]:
    """
    Descuenta primero el stock real en Tiendanube.

    restar_stock() llama a modify_existing_product(), que:
    1. consulta la variante de Tiendanube;
    2. ejecuta el PUT;
    3. vuelve a consultar la variante;
    4. actualiza la fila de Productos.

    Si Tiendanube falla, la venta no debe continuar.
    """
    marca = (
        item.get("marca_productos")
        or item.get("marca")
    )
    sabor = item.get("sabor")
    cantidad = item.get("cantidad")

    if not marca or not sabor:
        raise BusinessError(
            "El producto no tiene marca o sabor para "
            "sincronizar con Tiendanube."
        )

    product = item.get("producto") or {}
    available = product.get("stock")
    if available is None or cantidad is None or cantidad > available:
        raise BusinessError(
            f"Stock insuficiente para {marca} / {sabor}."
        )

    try:
        stock_result = modify_existing_product(
            marca=marca,
            sabor=sabor,
            subtract_stock=int(cantidad),
            sheet_context=item.get("sheet_context"),
        )
    except Exception as exc:
        raise BusinessError(
            f"No se pudo descontar stock de {marca} / {sabor}: {exc}"
        ) from exc

    item["stock_anterior"] = stock_result.get(
        "stock_anterior"
    )
    item["stock_restante"] = stock_result.get("stock")
    item["tiendanube_product_id"] = stock_result.get(
        "product_id"
    )
    item["tiendanube_variant_id"] = stock_result.get(
        "variant_id"
    )

    # El lote comparte el catálogo para no volver a leer Google Sheets. Dejar
    # su stock al día mantiene la validación correcta si dos ventas del mismo
    # mensaje incluyen la misma variante.
    remaining = stock_result.get("stock")
    item["producto"]["stock"] = remaining
    context = item.get("sheet_context")
    if context is not None:
        context[2].stock = remaining

    return {
        "ok": True,
        "operacion": "restar",
        "cantidad": cantidad,
        "producto": product,
        "resultado": stock_result,
    }


def sumar_stock_item(
    item: dict[str, Any],
) -> dict[str, Any]:
    """
    Repone stock en Tiendanube y sincroniza Productos.
    """
    marca = (
        item.get("marca_productos")
        or item.get("marca")
    )
    sabor = item.get("sabor")
    cantidad = item.get("cantidad")

    if not marca or not sabor:
        raise BusinessError(
            "El producto no tiene marca o sabor para "
            "reponer stock en Tiendanube."
        )

    try:
        stock_result = modify_existing_product(
            marca=marca,
            sabor=sabor,
            add_stock=int(cantidad),
            sheet_context=item.get("sheet_context"),
        )
    except Exception as exc:
        raise BusinessError(
            f"No se pudo reponer stock de {marca} / {sabor}: {exc}"
        ) from exc

    context = item.get("sheet_context")
    if context is not None:
        context[2].stock = stock_result.get("stock")

    return {
        "ok": True,
        "operacion": "sumar",
        "cantidad": cantidad,
        "producto": item.get("producto") or {},
        "resultado": stock_result,
    }



def rollback_reponer_stock(
    changed_items: list[dict[str, Any]],
) -> list[str]:
    errors = []

    for item in reversed(changed_items):
        try:
            sumar_stock_item(item)
        except Exception as exc:
            errors.append(str(exc))

    return errors


# ============================================================
# REGISTRAR VENTA MANUAL
# ============================================================

def registrar_venta_manual(
    *,
    cliente: Any,
    productos: Any,
    plataforma: Any = "WhatsApp",
    forma_pago: Any = "",
    estado: Any = "Recibido",
    fecha: Any = None,
    catalogo_contexto: tuple[Any, dict[str, int], list[Any]] | None = None,
) -> dict[str, Any]:
    customer = texto(cliente)

    if not customer:
        raise BusinessError(
            "Falta indicar el cliente."
        )

    platform = texto(plataforma) or "WhatsApp"
    payment = normalizar_forma_pago(forma_pago)
    canonical_status = estado_canonico(estado)

    created = fecha_desde_value(fecha)
    date_text = fecha_para_sheet(created)

    if catalogo_contexto is None:
        catalogo_contexto = sheet_product_context()

    products_worksheet, product_columns, catalogo = catalogo_contexto
    items = normalizar_items(
        productos,
        catalogo=catalogo,
        sheet_context=(products_worksheet, product_columns),
    )

    worksheet = legacy.get_or_create_sales_sheet(
        created
    )
    columns = legacy.ensure_sales_headers(
        worksheet
    )
    internal_order = legacy.next_internal_order(
        worksheet,
        columns,
    )

    changed_stock = []

    try:
        for item in items:
            restar_stock_item(item)
            changed_stock.append(item)

        rows = escribir_filas_venta(
            worksheet=worksheet,
            columns=columns,
            internal_order=internal_order,
            items=items,
            cliente=customer,
            estado=canonical_status,
            fecha=date_text,
            plataforma=platform,
            forma_pago=payment,
        )

    except Exception as exc:
        rollback_errors = rollback_reponer_stock(
            changed_stock
        )

        message = (
            f"No se pudo registrar la venta: {exc}"
        )

        if rollback_errors:
            message += (
                " También falló la reposición de stock: "
                + "; ".join(rollback_errors)
            )

        raise BusinessError(message) from exc

    return {
        "ok": True,
        "tipo": "venta_manual",
        "orden": internal_order,
        "hoja": worksheet.title,
        "filas": rows,
        "cliente": customer,
        "estado": canonical_status,
        "fecha": date_text,
        "plataforma": platform,
        "forma_pago": payment,
        "productos": [
            {
                "marca_productos": (
                    item["marca_productos"]
                ),
                "marca_ventas": (
                    item["marca_ventas"]
                ),
                "sabor": item["sabor"],
                "cantidad": item["cantidad"],
                "precio_total": (
                    item["precio_total"]
                ),
                "ganancia": (
                    item["ganancia_total"]
                ),
            }
            for item in items
        ],
        "stock_sincronizado": [
            "Tiendanube",
            "Google Sheets / Productos",
        ],
        "stock_resultados": [
            {
                "marca": item["marca_productos"],
                "sabor": item["sabor"],
                "stock_anterior": item.get(
                    "stock_anterior"
                ),
                "stock_restante": item.get(
                    "stock_restante"
                ),
                "product_id": item.get(
                    "tiendanube_product_id"
                ),
                "variant_id": item.get(
                    "tiendanube_variant_id"
                ),
            }
            for item in items
        ],
    }


def registrar_ventas_manuales(
    ventas: list[dict[str, Any]],
) -> dict[str, Any]:
    """Registra varias ventas explícitas reutilizando una sola lectura.

    Cada venta sigue conservando su propia orden, cliente, pago y plataforma.
    No se reutilizan datos implícitamente: esa decisión se hace antes, en el
    contrato del agente, solo si el usuario dijo que era "de la misma forma".
    """
    if not isinstance(ventas, list) or not ventas:
        raise BusinessError("Debe indicar al menos una venta.")

    if len(ventas) > 10:
        raise BusinessError("Se pueden registrar hasta 10 ventas por vez.")

    catalogo_contexto = sheet_product_context()
    results = []

    for index, venta in enumerate(ventas, start=1):
        try:
            results.append(
                registrar_venta_manual(
                    cliente=venta["cliente"],
                    productos=venta["productos"],
                    plataforma=venta["plataforma"],
                    forma_pago=venta["forma_pago"],
                    estado=venta.get("estado") or "Entregado",
                    fecha=venta.get("fecha"),
                    catalogo_contexto=catalogo_contexto,
                )
            )
        except Exception as exc:
            # Las ventas anteriores ya tienen número de orden y no se deben
            # repetir. Devolverlas explícitamente evita que el agente afirme
            # que no se registró nada ante un fallo posterior.
            return {
                "ok": True,
                "tipo": "ventas_manuales_parciales",
                "ventas_registradas": results,
                "venta_pendiente": index,
                "error": str(exc),
            }

    return {
        "ok": True,
        "tipo": "ventas_manuales",
        "cantidad": len(results),
        "ventas": results,
    }


# ============================================================
# CONSULTAR Y CANCELAR VENTA MANUAL
# ============================================================

def encontrar_venta_manual(
    numero_orden: Any,
    hoja: Any = None,
) -> tuple[Any, list[int], dict[str, int]]:
    expected_order = texto(numero_orden)

    if not expected_order:
        raise BusinessError(
            "Falta indicar el número de orden."
        )

    expected_sheet = normalizar(hoja)
    matches = []

    for worksheet in sales_worksheets():
        if (
            expected_sheet
            and normalizar(worksheet.title)
            != expected_sheet
        ):
            continue

        columns = sheet_columns(worksheet)
        order_column = columns.get(
            normalizar("Orden")
        )

        if not order_column:
            continue

        values = worksheet.get_all_values()
        rows = []

        for row_number, row in enumerate(
            values[1:],
            start=2,
        ):
            if (
                celda(row, order_column)
                == expected_order
            ):
                rows.append(row_number)

        if rows:
            matches.append(
                (worksheet, rows, columns)
            )

    if not matches:
        raise BusinessError(
            f"No encontré la orden {expected_order}."
        )

    if len(matches) > 1:
        sheets = ", ".join(
            worksheet.title
            for worksheet, _, _ in matches
        )

        raise BusinessError(
            f"La orden {expected_order} aparece en "
            f"varias hojas: {sheets}. Indicá la hoja."
        )

    worksheet, rows, columns = matches[0]
    values = worksheet.get_all_values()

    order_id_column = columns.get(
        normalizar("TiendaNube Order ID")
    )

    tiendanube_ids = {
        celda(
            values[row_number - 1],
            order_id_column,
        )
        for row_number in rows
        if celda(
            values[row_number - 1],
            order_id_column,
        )
    }

    if tiendanube_ids:
        raise BusinessError(
            f"La orden {expected_order} pertenece a "
            "Tiendanube y no debe cancelarse como manual."
        )

    return worksheet, rows, columns


def consultar_venta(
    numero_orden: Any,
    *,
    hoja: Any = None,
) -> dict[str, Any]:
    worksheet, rows, columns = encontrar_venta_manual(
        numero_orden,
        hoja,
    )

    values = worksheet.get_all_values()
    details = []

    for row_number in rows:
        row = values[row_number - 1]

        details.append(
            {
                "fila": row_number,
                "orden": celda(
                    row,
                    columns.get(normalizar("Orden")),
                ),
                "cantidad": celda(
                    row,
                    columns.get(
                        normalizar("Cantidad")
                    ),
                ),
                "cliente": celda(
                    row,
                    columns.get(
                        normalizar("Cliente")
                    ),
                ),
                "marca": celda(
                    row,
                    columns.get(normalizar("Vape")),
                ),
                "sabor": celda(
                    row,
                    columns.get(normalizar("Sabor")),
                ),
                "estado": celda(
                    row,
                    columns.get(normalizar("Estado")),
                ),
                "fecha": celda(
                    row,
                    columns.get(
                        normalizar(
                            "Fecha del pedido"
                        )
                    ),
                ),
                "forma_pago": celda(
                    row,
                    columns.get(
                        normalizar("Forma de pago")
                    ),
                ),
            }
        )

    return {
        "ok": True,
        "tipo": "manual",
        "orden": texto(numero_orden),
        "hoja": worksheet.title,
        "filas": details,
    }


def cancelar_venta_manual(
    numero_orden: Any,
    *,
    hoja: Any = None,
) -> dict[str, Any]:
    worksheet, rows, columns = encontrar_venta_manual(
        numero_orden,
        hoja,
    )

    values = worksheet.get_all_values()

    grouped: dict[
        tuple[str, str],
        dict[str, Any],
    ] = defaultdict(
        lambda: {
            "marca_productos": "",
            "marca_ventas": "",
            "sabor": "",
            "cantidad": 0,
        }
    )

    for row_number in rows:
        row = values[row_number - 1]

        marca_ventas = celda(
            row,
            columns.get(normalizar("Vape")),
        )
        sabor = celda(
            row,
            columns.get(normalizar("Sabor")),
        )
        quantity_text = celda(
            row,
            columns.get(normalizar("Cantidad")),
        )
        variant_id = celda(
            row,
            columns.get(
                normalizar(
                    "TiendaNube Variant ID"
                )
            ),
        )

        try:
            quantity = int(quantity_text)
        except (TypeError, ValueError) as exc:
            raise BusinessError(
                f"Cantidad inválida en la fila "
                f"{row_number}: {quantity_text!r}."
            ) from exc

        product = resolver_producto_venta(
            marca=marca_ventas,
            sabor=sabor,
            variant_id=variant_id,
        )

        product_model = (
            product.get("marca")
            or product.get("marca_productos")
        )

        key = (
            normalizar(product_model),
            normalizar(product["sabor"]),
        )

        grouped[key]["marca_productos"] = (
            product_model
        )
        grouped[key]["marca_ventas"] = (
            marca_ventas
        )
        grouped[key]["sabor"] = product["sabor"]
        grouped[key]["cantidad"] += quantity

    restored = []

    try:
        for item in grouped.values():
            sumar_stock_item(item)
            restored.append(item)

    except Exception as exc:
        rollback_errors = []

        for item in reversed(restored):
            try:
                restar_stock_item(item)
            except Exception as rollback_exc:
                rollback_errors.append(
                    str(rollback_exc)
                )

        message = (
            f"No se pudo reponer todo el stock: {exc}"
        )

        if rollback_errors:
            message += (
                " También falló la reversión: "
                + "; ".join(rollback_errors)
            )

        raise BusinessError(message) from exc

    try:
        for row_number in sorted(
            set(rows),
            reverse=True,
        ):
            worksheet.delete_rows(row_number)

    except Exception as exc:
        rollback_errors = []

        for item in restored:
            try:
                restar_stock_item(item)
            except Exception as rollback_exc:
                rollback_errors.append(
                    str(rollback_exc)
                )

        message = (
            "Se repuso el stock, pero no se pudieron "
            f"borrar las filas: {exc}"
        )

        if rollback_errors:
            message += (
                " También falló la reversión del stock: "
                + "; ".join(rollback_errors)
            )

        raise BusinessError(message) from exc

    return {
        "ok": True,
        "tipo": "cancelacion_manual",
        "orden": texto(numero_orden),
        "hoja": worksheet.title,
        "filas_eliminadas": rows,
        "productos_repuestos": [
            {
                "marca": item["marca_productos"],
                "sabor": item["sabor"],
                "cantidad": item["cantidad"],
            }
            for item in restored
        ],
        "stock_sincronizado": [
            "Google Sheets",
            "Tiendanube",
        ],
    }
