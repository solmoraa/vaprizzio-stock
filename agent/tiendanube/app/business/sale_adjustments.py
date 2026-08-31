from __future__ import annotations

from typing import Any

from .common import BusinessError, celda, dinero, normalizar, numero, texto
from .sales import (
    encontrar_venta_manual,
    resolver_producto_venta,
    restar_stock_item,
    sumar_stock_item,
)


def _entero(value: Any, nombre: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise BusinessError(f"{nombre} debe ser un número entero.") from exc
    return result


def _buscar_fila_producto(
    *,
    worksheet: Any,
    rows: list[int],
    columns: dict[str, int],
    marca: Any = None,
    sabor: Any = None,
    variant_id: Any = None,
) -> tuple[int, list[Any], dict[str, Any]]:
    values = worksheet.get_all_values()
    requested_brand = normalizar(marca)
    requested_flavor = normalizar(sabor)
    requested_variant = texto(variant_id)
    matches: list[tuple[int, list[Any], dict[str, Any]]] = []

    for row_number in rows:
        row = values[row_number - 1]
        row_brand = celda(row, columns.get(normalizar("Vape")))
        row_flavor = celda(row, columns.get(normalizar("Sabor")))
        row_variant = celda(
            row,
            columns.get(normalizar("TiendaNube Variant ID")),
        )

        if requested_variant and row_variant != requested_variant:
            continue
        if requested_brand and normalizar(row_brand) != requested_brand:
            # La columna Vape puede usar un alias distinto al de Productos;
            # resolver_producto_venta hace la validación definitiva.
            pass
        if requested_flavor and normalizar(row_flavor) != requested_flavor:
            continue

        try:
            product = resolver_producto_venta(
                marca=row_brand,
                sabor=row_flavor,
                variant_id=row_variant or requested_variant,
            )
        except Exception:
            continue

        if requested_brand and normalizar(product.get("marca")) != requested_brand:
            # Aceptar también el nombre visible guardado en Ventas.
            if normalizar(row_brand) != requested_brand:
                continue
        if requested_flavor and normalizar(product.get("sabor")) != requested_flavor:
            continue

        matches.append((row_number, row, product))

    if not matches:
        raise BusinessError(
            "No encontré ese producto dentro de la venta indicada. "
            "Indicá la marca y el sabor exactos."
        )
    if len(matches) > 1:
        options = ", ".join(
            f"fila {row_number}: {product.get('marca')} / {product.get('sabor')}"
            for row_number, _, product in matches
        )
        raise BusinessError(
            "El producto coincide con más de una fila: " + options
        )

    return matches[0]


def ajustar_cantidad_venta_manual(
    numero_orden: Any,
    *,
    marca: Any = None,
    sabor: Any = None,
    variant_id: Any = None,
    cambio: Any = None,
    cantidad_nueva: Any = None,
    precio_unitario: Any = None,
    hoja: Any = None,
) -> dict[str, Any]:
    """Aumenta o reduce un producto de una venta manual ya registrada.

    - Si aumenta, descuenta la diferencia del stock.
    - Si reduce, repone la diferencia al stock.
    - Recalcula Precio Venta y Ganancia de la fila.
    - La ganancia usa siempre la columna Ganancia actual de Productos.
    - Si la cantidad nueva queda en cero, elimina esa fila de la venta.
    """
    worksheet, rows, columns = encontrar_venta_manual(numero_orden, hoja)
    row_number, row, product = _buscar_fila_producto(
        worksheet=worksheet,
        rows=rows,
        columns=columns,
        marca=marca,
        sabor=sabor,
        variant_id=variant_id,
    )

    quantity_column = columns.get(normalizar("Cantidad"))
    price_column = columns.get(normalizar("Precio Venta"))
    gain_column = columns.get(normalizar("Ganancia"))

    if not quantity_column or not price_column or not gain_column:
        raise BusinessError(
            "La hoja Ventas debe tener las columnas Cantidad, "
            "Precio Venta y Ganancia."
        )

    old_quantity = _entero(celda(row, quantity_column), "La cantidad actual")
    if old_quantity <= 0:
        raise BusinessError("La cantidad actual de la venta es inválida.")

    if cantidad_nueva not in (None, ""):
        new_quantity = _entero(cantidad_nueva, "La cantidad nueva")
        delta = new_quantity - old_quantity
    else:
        if cambio in (None, ""):
            raise BusinessError(
                "Indicá cambio (por ejemplo 2 o -1) o cantidad_nueva."
            )
        delta = _entero(cambio, "El cambio")
        if delta == 0:
            raise BusinessError("El cambio no puede ser cero.")
        new_quantity = old_quantity + delta

    if new_quantity < 0:
        raise BusinessError(
            f"No se pueden restar {-delta} unidades: la venta tiene "
            f"{old_quantity}."
        )

    old_total_price = numero(
        celda(row, price_column),
        nombre="precio total de la venta",
    )

    if precio_unitario not in (None, ""):
        unit_price = numero(precio_unitario, nombre="precio unitario")
    else:
        unit_price = old_total_price / old_quantity

    raw_cost = product.get("costo")
    if raw_cost in (None, ""):
        raise BusinessError(
            f"{product.get('marca')} / {product.get('sabor')} no tiene "
            "Costo cargado en Productos. No se modificó la venta."
        )
    unit_cost = numero(raw_cost, nombre="costo unitario")

    raw_gain = product.get("ganancia")
    if raw_gain in (None, ""):
        raise BusinessError(
            f"{product.get('marca')} / {product.get('sabor')} no tiene "
            "Ganancia cargada en Productos. No se modificó la venta."
        )
    unit_gain = numero(raw_gain, nombre="ganancia unitaria")

    stock_item = {
        "marca_productos": product.get("marca"),
        "sabor": product.get("sabor"),
        "cantidad": abs(delta),
    }

    stock_changed = False
    try:
        if delta > 0:
            restar_stock_item(stock_item)
            stock_changed = True
        elif delta < 0:
            sumar_stock_item(stock_item)
            stock_changed = True

        if new_quantity == 0:
            worksheet.delete_rows(row_number)
            return {
                "ok": True,
                "tipo": "ajuste_venta_manual",
                "orden": texto(numero_orden),
                "hoja": worksheet.title,
                "fila_eliminada": row_number,
                "marca": product.get("marca"),
                "sabor": product.get("sabor"),
                "cantidad_anterior": old_quantity,
                "cantidad_nueva": 0,
                "cambio": delta,
                "stock_actualizado": True,
            }

        new_total_price = dinero(unit_price * new_quantity)
        new_total_gain = dinero(unit_gain * new_quantity)

        worksheet.update_cell(row_number, quantity_column, new_quantity)
        worksheet.update_cell(row_number, price_column, new_total_price)
        worksheet.update_cell(row_number, gain_column, new_total_gain)

    except Exception as exc:
        if stock_changed:
            try:
                rollback_item = dict(stock_item)
                if delta > 0:
                    sumar_stock_item(rollback_item)
                elif delta < 0:
                    restar_stock_item(rollback_item)
            except Exception as rollback_exc:
                raise BusinessError(
                    f"Falló el ajuste de la venta: {exc}. También falló "
                    f"la reversión del stock: {rollback_exc}"
                ) from exc
        raise BusinessError(f"No se pudo ajustar la venta: {exc}") from exc

    return {
        "ok": True,
        "tipo": "ajuste_venta_manual",
        "orden": texto(numero_orden),
        "hoja": worksheet.title,
        "fila": row_number,
        "marca": product.get("marca"),
        "sabor": product.get("sabor"),
        "cantidad_anterior": old_quantity,
        "cantidad_nueva": new_quantity,
        "cambio": delta,
        "precio_unitario": dinero(unit_price),
        "precio_venta_anterior": dinero(old_total_price),
        "precio_venta_nuevo": new_total_price,
        "costo_unitario": dinero(unit_cost),
        "ganancia_unitaria": dinero(unit_gain),
        "ganancia_nueva": new_total_gain,
        "stock_actualizado": True,
    }
