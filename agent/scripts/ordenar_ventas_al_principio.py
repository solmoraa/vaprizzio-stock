#!/usr/bin/env python3
"""Reubica filas de ventas debajo del encabezado sin modificar sus datos.

Se usa solo para corregir una hoja que recibió ventas al final por una versión
anterior del registrador. Por defecto solo informa el plan; requiere --apply
para escribir en Google Sheets.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


WORKSPACE = Path("/home/openclaw/.openclaw/workspace/vaprizziobot")
TIENDANUBE_DIR = WORKSPACE / "tiendanube"

if str(TIENDANUBE_DIR) not in sys.path:
    sys.path.insert(0, str(TIENDANUBE_DIR))

from app.sync_core import normalize, spreadsheet  # noqa: E402


def order_column(headers: list[str]) -> int:
    for index, header in enumerate(headers, start=1):
        if normalize(header) in {
            "orden",
            "numero de orden",
            "numero orden",
        }:
            return index

    raise ValueError("No encontré la columna Orden.")


def is_numeric_order(value: Any) -> bool:
    try:
        int(float(str(value).strip()))
        return True
    except (TypeError, ValueError):
        return False


def sale_rows(values: list[list[str]], column: int) -> list[int]:
    return [
        row_number
        for row_number, row in enumerate(values[1:], start=2)
        if len(row) >= column and is_numeric_order(row[column - 1])
    ]


def move_requests(
    *,
    sheet_id: int,
    rows: list[int],
) -> list[dict[str, Any]]:
    """Mueve todas las ventas al inicio y conserva su orden relativo.

    Cada movimiento inserta la fila en la posición 2. Al recorrer de abajo
    hacia arriba y compensar las filas ya movidas, el resultado conserva la
    secuencia original de las ventas.
    """
    requests: list[dict[str, Any]] = []

    for moved, original_row in enumerate(reversed(rows)):
        source_row = original_row + moved
        requests.append(
            {
                "moveDimension": {
                    "source": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": source_row - 1,
                        "endIndex": source_row,
                    },
                    # Índice base cero: 1 es la fila visible 2.
                    "destinationIndex": 1,
                }
            }
        )

    return requests


def organize_sheet(sheet_name: str, apply: bool) -> dict[str, Any]:
    book = spreadsheet()
    worksheet = book.worksheet(sheet_name)
    values = worksheet.get_all_values()

    if not values:
        raise ValueError(f"La hoja {sheet_name!r} está vacía.")

    column = order_column(values[0])
    rows = sale_rows(values, column)
    expected = list(range(2, len(rows) + 2))
    already_ordered = rows == expected

    result: dict[str, Any] = {
        "ok": True,
        "hoja": worksheet.title,
        "filas_de_ventas": rows,
        "filas_esperadas": expected,
        "ya_estaban_al_principio": already_ordered,
        "aplicado": False,
    }

    if not rows or already_ordered or not apply:
        return result

    worksheet.spreadsheet.batch_update(
        {"requests": move_requests(sheet_id=worksheet.id, rows=rows)}
    )
    result["aplicado"] = True
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ordena filas de ventas debajo del encabezado."
    )
    parser.add_argument("--hoja", required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplica el movimiento. Sin esta opción solo revisa.",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            organize_sheet(args.hoja, args.apply),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
