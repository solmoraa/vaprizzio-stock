from __future__ import annotations

from collections import defaultdict
from difflib import get_close_matches
from typing import Any

from app import order_sync as legacy
from app.order_sync_v2 import (
    sales_worksheets,
    sheet_columns,
)
from app.sync_core import (
    modify_existing_product,
    read_products,
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
from .stock import (
    restar_stock,
    sumar_stock,
)


# ============================================================
# MODELOS PERMITIDOS EN LA LISTA DESPLEGABLE DE VENTAS
# ============================================================

MODELOS_VENTAS = (
    "Elfbar 15K",
    "Elfbar TE30K",
    "Elfbar Create BC pro 40k",
    "Elfbar Ice king",
    "Elfbar Summer",
    "Elfbar pro 45K",
    "Ignite v-nano",
    "Ignite v155",
    "Ignite v250",
    "Ignite v300 slim",
    "Lost Mary Mixer",
    "Lost Mary Mixer 30k",
    "Lost Mary MO 5k",
    "Geek Bar Pulse x",
    "BLVK",
    "Mayorista",
    "Airmez 40k auris",
    "Dummy 8k",
    "Maskking 100k",
)


# Conversión desde el nombre usado en Productos/Tiendanube
# hacia el nombre permitido en la lista desplegable de Ventas.
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
    Convierte el modelo de Productos al valor exacto que
    acepta la lista desplegable de la hoja Ventas.
    """
    original = texto(modelo_productos)

    if not original:
        raise BusinessError(
            "El producto no tiene modelo o marca."
        )

    normalized = normalizar(original)

    direct = ALIAS_MODELOS.get(normalized)

    if direct:
        return direct

    for allowed in MODELOS_VENTAS:
        if normalizar(allowed) == normalized:
            return allowed

    normalized_options = {
        normalizar(option): option
        for option in MODELOS_VENTAS
    }

    close = get_close_matches(
        normalized,
        list(normalized_options),
        n=1,
        cutoff=0.82,
    )

    if close:
        return normalized_options[close[0]]

    raise BusinessError(
        f"El modelo {original!r} no está configurado "
        "en la lista desplegable de Ventas. "
        "Hay que agregar su equivalencia en "
        "ALIAS_MODELOS."
    )


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
) -> dict[str, Any] | None:
    expected = texto(variant_id)

    if not expected:
        return None

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


def resolver_producto_venta(
    *,
    marca: Any = None,
    sabor: Any,
    variant_id: Any = None,
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

def fila_es_orden_numerica(
    row: list[Any],
    order_column: int,
) -> bool:
    value = celda(row, order_column)

    try:
        int(value)
        return True
    except (TypeError, ValueError):
        return False


def encontrar_fila_insercion(
    worksheet: Any,
    columns: dict[str, int],
) -> tuple[int, int | None]:
    values = worksheet.get_all_values()

    order_column = columns.get(normalizar("Orden"))

    if not order_column:
        raise BusinessError(
            f"No encontré la columna Orden en "
            f"{worksheet.title}."
        )

    numeric_rows = [
        row_number
        for row_number, row in enumerate(
            values[1:],
            start=2,
        )
        if fila_es_orden_numerica(
            row,
            order_column,
        )
    ]

    start_row = (
        max(numeric_rows) + 1
        if numeric_rows
        else 2
    )

    price_column = columns.get(
        normalizar("Precio Venta")
    )
    gain_column = columns.get(
        normalizar("Ganancia")
    )

    summary_row = None

    for row_number in range(
        start_row,
        len(values) + 1,
    ):
        row = values[row_number - 1]

        order_value = celda(
            row,
            order_column,
        )
        price_value = celda(
            row,
            price_column,
        )
        gain_value = celda(
            row,
            gain_column,
        )

        if (
            not order_value
            and (price_value or gain_value)
        ):
            summary_row = row_number
            break

    return start_row, summary_row


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

        if condition_type != "ONE_OF_LIST":
            return []

        result = []

        for value in values:
            option = texto(
                value.get("userEnteredValue")
            )

            if option:
                result.append(option)

        return result

    except Exception:
        # Si Google no devuelve los metadatos, se utiliza
        # el listado local como alternativa.
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

    options = opciones_dropdown_celda(
        worksheet,
        row_number,
        column_number,
    )

    requested_normalized = normalizar(requested)

    # Primero se compara contra las opciones reales de la hoja.
    for option in options:
        if normalizar(option) == requested_normalized:
            return option

    # También permite encontrar una opción aunque difiera
    # levemente en espacios, guiones o mayúsculas.
    for option in options:
        option_normalized = normalizar(option)

        if (
            requested_normalized in option_normalized
            or option_normalized in requested_normalized
        ):
            return option

    # Alternativa basada en el listado enviado por el usuario.
    for option in MODELOS_VENTAS:
        if normalizar(option) == requested_normalized:
            return option

    raise BusinessError(
        f"El modelo {requested!r} no coincide con ninguna "
        "opción del desplegable de la columna Vape."
    )


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
    start_row, summary_row = encontrar_fila_insercion(
        worksheet,
        columns,
    )

    row_count = len(items)
    end_row = start_row + row_count - 1

    max_column = max(
        max(columns.values(), default=11),
        20,
    )

    if (
        summary_row is not None
        and end_row >= summary_row
    ):
        rows_needed = end_row - summary_row + 1

        worksheet.insert_rows(
            [
                [""] * max_column
                for _ in range(rows_needed)
            ],
            row=summary_row,
            value_input_option="USER_ENTERED",
            inherit_from_before=True,
        )

    if end_row > worksheet.row_count:
        worksheet.add_rows(
            end_row - worksheet.row_count + 10
        )

    source_row = start_row - 1

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

    # Copia un chip de Vape ya existente para conservar
    # exactamente el valor, validación y color configurados
    # en Google Sheets.
    aplicar_chips_vape(
        worksheet=worksheet,
        rows=written_rows,
        items=items,
        vape_column=columns.get(
            normalizar("Vape")
        ),
        sabor_column=columns.get(
            normalizar("Sabor")
        ),
    )

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

    result = restar_stock(
        marca=marca,
        sabor=sabor,
        cantidad=cantidad,
    )

    stock_result = result.get("resultado") or {}

    if not stock_result.get("ok"):
        raise BusinessError(
            "Tiendanube no confirmó el descuento de stock."
        )

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

    return result


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

    result = sumar_stock(
        marca=marca,
        sabor=sabor,
        cantidad=cantidad,
    )

    stock_result = result.get("resultado") or {}

    if not stock_result.get("ok"):
        raise BusinessError(
            "Tiendanube no confirmó la reposición de stock."
        )

    return result



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

    items = normalizar_items(productos)

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
