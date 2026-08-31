from __future__ import annotations

from typing import Any

import gspread

from app.sync_core import normalize, parse_float, read_products
from .common import BusinessError

PASE_PORCENTAJE = 0.10


def _cell(row: list[Any], column: int | None) -> str:
    if not column or column > len(row):
        return ""
    return str(row[column - 1]).strip()


def _first_columns(row: list[Any]) -> dict[str, int]:
    """Mapea encabezados conservando siempre la primera aparición.

    Es importante porque la hoja tiene dos columnas llamadas Marca:
    A (catálogo) y M (tabla auxiliar). La segunda nunca debe pisar la primera.
    """
    result: dict[str, int] = {}
    for index, value in enumerate(row, start=1):
        key = normalize(value)
        if key and key not in result:
            result[key] = index
    return result


def _find_product_table(values: list[list[str]]) -> tuple[int, dict[str, int]]:
    required = {
        normalize("Marca"),
        normalize("Sabor"),
        normalize("Stock"),
        normalize("Costo"),
        normalize("Precio venta"),
        normalize("Ganancia"),
    }
    for row_number, row in enumerate(values, start=1):
        columns = _first_columns(row)
        if required.issubset(columns):
            return row_number, columns
    raise BusinessError(
        "No encontré la tabla principal de Productos con Marca, Sabor, "
        "Stock, Costo, Precio venta y Ganancia."
    )


def _find_cost_table(values: list[list[str]]) -> tuple[int, dict[str, int]]:
    required = {
        normalize("Marca"),
        normalize("Costo USDT"),
        normalize("Pase"),
        normalize("Costo Total USDT"),
        normalize("Valor USDT"),
    }
    for row_number, row in enumerate(values, start=1):
        # Acá sí buscamos todos los encabezados en la misma fila. Como Costo
        # USDT es único, esta detección encuentra la tabla auxiliar M:R.
        columns: dict[str, int] = {}
        for index, value in enumerate(row, start=1):
            key = normalize(value)
            if key:
                columns[key] = index
        if required.issubset(columns):
            return row_number, columns
    raise BusinessError(
        "No encontré la tabla auxiliar. Debe tener Marca, Costo USDT, "
        "Pase, Costo Total USDT y Valor USDT."
    )


def _global_usdt_value(
    values: list[list[str]], header_row: int, columns: dict[str, int]
) -> float:
    col = columns[normalize("Valor USDT")]
    for row in values[header_row:]:
        value = parse_float(_cell(row, col))
        if value is not None and value > 0:
            return float(value)
    raise BusinessError("No hay un Valor USDT válido en la tabla auxiliar.")


def _read_unique_cost_rows(
    values: list[list[str]], header_row: int, columns: dict[str, int]
) -> tuple[list[dict[str, Any]], list[int]]:
    """Devuelve una fila por marca y lista las filas duplicadas.

    Conserva la primera aparición de cada marca y usa el primer costo válido
    encontrado para esa marca. Nunca genera filas a partir de sabores.
    """
    marca_col = columns[normalize("Marca")]
    costo_col = columns[normalize("Costo USDT")]
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []

    for row_number, row in enumerate(values[header_row:], start=header_row + 1):
        marca = _cell(row, marca_col)
        if not marca:
            continue
        key = normalize(marca)
        if not key:
            continue
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(
            {
                "row": row_number,
                "marca": marca,
                "costo_usdt": parse_float(_cell(row, costo_col)),
            }
        )

    unique: list[dict[str, Any]] = []
    duplicates: list[int] = []
    for key in order:
        entries = grouped[key]
        keeper = entries[0]
        selected_cost = next(
            (
                float(item["costo_usdt"])
                for item in entries
                if item["costo_usdt"] is not None
                and float(item["costo_usdt"]) > 0
            ),
            None,
        )
        unique.append(
            {
                "row": int(keeper["row"]),
                "marca": str(keeper["marca"]).strip(),
                "costo_usdt": selected_cost,
            }
        )
        duplicates.extend(int(item["row"]) for item in entries[1:])
    return unique, duplicates


def _clean_duplicates(
    worksheet: gspread.Worksheet,
    duplicate_rows: list[int],
    columns: dict[str, int],
) -> None:
    if not duplicate_rows:
        return
    start_col = columns[normalize("Marca")]
    end_col = columns[normalize("Costo Total USDT")]
    updates = []
    width = end_col - start_col + 1
    for row_number in duplicate_rows:
        updates.append(
            {
                "range": (
                    f"{gspread.utils.rowcol_to_a1(row_number, start_col)}:"
                    f"{gspread.utils.rowcol_to_a1(row_number, end_col)}"
                ),
                "values": [[""] * width],
            }
        )
    worksheet.batch_update(updates, value_input_option="USER_ENTERED")


def _next_cost_row(
    values: list[list[str]], header_row: int, marca_column: int
) -> int:
    row_number = header_row + 1
    while row_number <= len(values):
        if not _cell(values[row_number - 1], marca_column):
            return row_number
        row_number += 1
    return len(values) + 1


def _calculate(costo_usdt: float, valor_usdt: float) -> dict[str, float]:
    if costo_usdt <= 0:
        raise BusinessError("El Costo USDT debe ser mayor que cero.")
    if valor_usdt <= 0:
        raise BusinessError("El Valor USDT debe ser mayor que cero.")
    pase = round(float(costo_usdt) * PASE_PORCENTAJE, 4)
    total = round(float(costo_usdt) + pase, 4)
    costo_ars = round(total * float(valor_usdt), 2)
    return {
        "costo_usdt": round(float(costo_usdt), 4),
        "pase": pase,
        "costo_total_usdt": total,
        "valor_usdt": float(valor_usdt),
        "costo_ars": costo_ars,
    }


def _update_products_for_brand(
    worksheet: gspread.Worksheet,
    values: list[list[str]],
    product_header: int,
    product_columns: dict[str, int],
    marca: str,
    costo_ars: float,
) -> list[dict[str, Any]]:
    marca_col = product_columns[normalize("Marca")]
    sabor_col = product_columns[normalize("Sabor")]
    costo_col = product_columns[normalize("Costo")]
    precio_col = product_columns[normalize("Precio venta")]
    ganancia_col = product_columns[normalize("Ganancia")]
    expected = normalize(marca)
    updates: list[dict[str, Any]] = []
    updated: list[dict[str, Any]] = []

    for row_number, row in enumerate(values[product_header:], start=product_header + 1):
        product_brand = _cell(row, marca_col)
        flavor = _cell(row, sabor_col)
        if not product_brand or normalize(product_brand) != expected:
            continue
        price = parse_float(_cell(row, precio_col))
        gain = "" if price is None else round(float(price) - costo_ars, 2)
        updates.extend(
            [
                {
                    "range": gspread.utils.rowcol_to_a1(row_number, costo_col),
                    "values": [[costo_ars]],
                },
                {
                    "range": gspread.utils.rowcol_to_a1(row_number, ganancia_col),
                    "values": [[gain]],
                },
            ]
        )
        updated.append(
            {
                "marca": product_brand,
                "sabor": flavor,
                "costo": costo_ars,
                "ganancia": None if gain == "" else gain,
            }
        )

    if updates:
        worksheet.batch_update(updates, value_input_option="USER_ENTERED")
    return updated


def obtener_costo_modelo(marca: str) -> dict[str, Any]:
    worksheet, _, _, _ = read_products()
    values = worksheet.get_all_values()
    _, cost_columns = _find_cost_table(values)
    cost_header, cost_columns = _find_cost_table(values)
    valor = _global_usdt_value(values, cost_header, cost_columns)
    rows, _ = _read_unique_cost_rows(values, cost_header, cost_columns)
    found = next((r for r in rows if normalize(r["marca"]) == normalize(marca)), None)
    if not found or found["costo_usdt"] is None:
        raise BusinessError(f"No hay Costo USDT configurado para {marca!r}.")
    return {"marca": found["marca"], **_calculate(float(found["costo_usdt"]), valor)}


def asegurar_costo_modelo(
    marca: str, costo_usdt: float | None = None
) -> dict[str, Any]:
    worksheet, _, _, _ = read_products()
    values = worksheet.get_all_values()
    cost_header, cost_columns = _find_cost_table(values)
    valor = _global_usdt_value(values, cost_header, cost_columns)
    rows, duplicates = _read_unique_cost_rows(values, cost_header, cost_columns)
    _clean_duplicates(worksheet, duplicates, cost_columns)

    found = next((r for r in rows if normalize(r["marca"]) == normalize(marca)), None)
    if found:
        selected = costo_usdt if costo_usdt is not None else found["costo_usdt"]
        if selected is None:
            raise BusinessError(f"La marca {marca!r} no tiene Costo USDT.")
        row_number = int(found["row"])
        canonical = str(found["marca"])
    else:
        if costo_usdt is None:
            raise BusinessError(f"La marca {marca!r} no existe en la tabla de costos.")
        row_number = _next_cost_row(
            values, cost_header, cost_columns[normalize("Marca")]
        )
        canonical = str(marca).strip()
        selected = costo_usdt

    calculated = _calculate(float(selected), valor)
    updates = [
        {
            "range": gspread.utils.rowcol_to_a1(row_number, cost_columns[normalize("Marca")]),
            "values": [[canonical]],
        },
        {
            "range": gspread.utils.rowcol_to_a1(row_number, cost_columns[normalize("Costo USDT")]),
            "values": [[calculated["costo_usdt"]]],
        },
        {
            "range": gspread.utils.rowcol_to_a1(row_number, cost_columns[normalize("Pase")]),
            "values": [[calculated["pase"]]],
        },
        {
            "range": gspread.utils.rowcol_to_a1(row_number, cost_columns[normalize("Costo Total USDT")]),
            "values": [[calculated["costo_total_usdt"]]],
        },
    ]
    worksheet.batch_update(updates, value_input_option="USER_ENTERED")
    return {"marca": canonical, **calculated}


def actualizar_costo_usdt_marca(marca: str, costo_usdt: float) -> dict[str, Any]:
    info = asegurar_costo_modelo(marca, costo_usdt)
    worksheet, _, _, _ = read_products()
    values = worksheet.get_all_values()
    product_header, product_columns = _find_product_table(values)
    updated = _update_products_for_brand(
        worksheet,
        values,
        product_header,
        product_columns,
        str(info["marca"]),
        float(info["costo_ars"]),
    )
    if not updated:
        raise BusinessError(
            f"El costo se guardó, pero no encontré productos de la marca {marca!r}."
        )
    return {
        "ok": True,
        "mensaje": "Costo USDT actualizado solo en Google Sheets.",
        **info,
        "productos_actualizados": len(updated),
        "productos": updated,
        "sincronizado_tiendanube": False,
        "sincronizado_google_sheets": True,
    }


def actualizar_valor_usdt(valor_usdt: float) -> dict[str, Any]:
    if valor_usdt <= 0:
        raise BusinessError("El Valor USDT debe ser mayor que cero.")

    worksheet, _, _, _ = read_products()
    values = worksheet.get_all_values()
    product_header, product_columns = _find_product_table(values)
    cost_header, cost_columns = _find_cost_table(values)
    rows, duplicates = _read_unique_cost_rows(values, cost_header, cost_columns)
    _clean_duplicates(worksheet, duplicates, cost_columns)

    # Un único Valor USDT global, en la primera fila debajo de los encabezados.
    worksheet.update_cell(
        cost_header + 1,
        cost_columns[normalize("Valor USDT")],
        float(valor_usdt),
    )

    table_updates: list[dict[str, Any]] = []
    costs_by_brand: dict[str, dict[str, Any]] = {}
    for item in rows:
        costo_usdt = item["costo_usdt"]
        if costo_usdt is None or float(costo_usdt) <= 0:
            continue
        calculated = _calculate(float(costo_usdt), float(valor_usdt))
        row_number = int(item["row"])
        table_updates.extend(
            [
                {
                    "range": gspread.utils.rowcol_to_a1(row_number, cost_columns[normalize("Pase")]),
                    "values": [[calculated["pase"]]],
                },
                {
                    "range": gspread.utils.rowcol_to_a1(row_number, cost_columns[normalize("Costo Total USDT")]),
                    "values": [[calculated["costo_total_usdt"]]],
                },
            ]
        )
        costs_by_brand[normalize(item["marca"])] = {
            "marca": item["marca"],
            **calculated,
        }
    if table_updates:
        worksheet.batch_update(table_updates, value_input_option="USER_ENTERED")

    marca_col = product_columns[normalize("Marca")]
    sabor_col = product_columns[normalize("Sabor")]
    costo_col = product_columns[normalize("Costo")]
    precio_col = product_columns[normalize("Precio venta")]
    ganancia_col = product_columns[normalize("Ganancia")]
    product_updates: list[dict[str, Any]] = []
    updated: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for row_number, row in enumerate(values[product_header:], start=product_header + 1):
        marca = _cell(row, marca_col)
        sabor = _cell(row, sabor_col)
        if not marca:
            continue
        info = costs_by_brand.get(normalize(marca))
        if info is None:
            skipped.append({"marca": marca, "sabor": sabor})
            continue
        costo_ars = float(info["costo_ars"])
        price = parse_float(_cell(row, precio_col))
        gain = "" if price is None else round(float(price) - costo_ars, 2)
        product_updates.extend(
            [
                {
                    "range": gspread.utils.rowcol_to_a1(row_number, costo_col),
                    "values": [[costo_ars]],
                },
                {
                    "range": gspread.utils.rowcol_to_a1(row_number, ganancia_col),
                    "values": [[gain]],
                },
            ]
        )
        updated.append(
            {
                "marca": marca,
                "sabor": sabor,
                "costo": costo_ars,
                "ganancia": None if gain == "" else gain,
            }
        )
    if product_updates:
        worksheet.batch_update(product_updates, value_input_option="USER_ENTERED")

    return {
        "ok": True,
        "mensaje": "Valor USDT actualizado y catálogo recalculado solo en Google Sheets.",
        "valor_usdt": float(valor_usdt),
        "marcas_configuradas": len(costs_by_brand),
        "productos_actualizados": len(updated),
        "productos_sin_costo_usdt": skipped,
        "sincronizado_tiendanube": False,
        "sincronizado_google_sheets": True,
    }


def recalcular_catalogo_desde_tabla_usdt() -> dict[str, Any]:
    """Repara Costo y Ganancia de todo Productos usando la tabla USDT actual."""
    worksheet, _, _, _ = read_products()
    values = worksheet.get_all_values()
    cost_header, cost_columns = _find_cost_table(values)
    valor_usdt = _global_usdt_value(values, cost_header, cost_columns)
    return actualizar_valor_usdt(valor_usdt)
