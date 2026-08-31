#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import WorksheetNotFound


WORKSPACE = Path("/home/openclaw/.openclaw/workspace/vaprizziobot")

CREDENTIALS_FILE = (
    WORKSPACE
    / "credentials"
    / "vaprizzio-service-account.json"
)

SPREADSHEET_ID_FILE = WORKSPACE / "google_sheet_id.txt"

PRODUCT_SHEET = "Productos"
FIXED_EXPENSES_SHEET = "Gastos Fijos"
PROFITS_SHEET = "Ganancias"

# Las hojas antiguas de 2026 no llevan el año:
# Ventas Enero, Ventas Febrero, etc.
LEGACY_YEAR = 2026

MONTHS = {
    1: "Enero",
    2: "Febrero",
    3: "Marzo",
    4: "Abril",
    5: "Mayo",
    6: "Junio",
    7: "Julio",
    8: "Agosto",
    9: "Septiembre",
    10: "Octubre",
    11: "Noviembre",
    12: "Diciembre",
}

SALE_HEADERS = [
    "Orden",
    "Cantidad",
    "Cliente",
    "Vape",
    "Sabor",
    "Estado",
    "Fecha del pedido",
    "Precio Venta",
    "Plataforma de ventas",
    "Ganancia",
    "Forma de pago",
]


class VaprizzioError(Exception):
    pass


def emit(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def normalize(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFD", text)

    return "".join(
        character
        for character in text
        if unicodedata.category(character) != "Mn"
    )


def parse_number(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value or "").strip()

    if not text:
        return 0.0

    text = (
        text.replace("$", "")
        .replace("ARS", "")
        .replace("ars", "")
        .replace(" ", "")
    )

    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    elif text.count(".") == 1:
        left, right = text.split(".")

        if (
            left.isdigit()
            and right.isdigit()
            and len(right) == 3
        ):
            text = left + right
    elif text.count(".") > 1:
        text = text.replace(".", "")

    try:
        return round(float(text), 2)
    except ValueError as exc:
        raise VaprizzioError(
            f"No se pudo interpretar el importe '{value}'."
        ) from exc


def parse_positive_int(value: Any, name: str) -> int:
    number = parse_number(value)

    if not number.is_integer():
        raise VaprizzioError(
            f"{name} debe ser un número entero."
        )

    result = int(number)

    if result < 0:
        raise VaprizzioError(
            f"{name} no puede ser negativo."
        )

    return result


def parse_date(value: str | None) -> datetime:
    if not value:
        return datetime.now()

    formats = (
        "%d/%m/%Y",
        "%Y-%m-%d",
        "%d-%m-%Y",
    )

    for date_format in formats:
        try:
            return datetime.strptime(value, date_format)
        except ValueError:
            continue

    raise VaprizzioError(
        "La fecha debe usar DD/MM/AAAA o AAAA-MM-DD."
    )


def format_date(value: datetime) -> str:
    return value.strftime("%d/%m/%Y")


def spreadsheet_id() -> str:
    if not SPREADSHEET_ID_FILE.exists():
        raise VaprizzioError(
            "No existe google_sheet_id.txt."
        )

    content = SPREADSHEET_ID_FILE.read_text(
        encoding="utf-8"
    ).strip()

    if not content:
        raise VaprizzioError(
            "google_sheet_id.txt está vacío."
        )

    match = re.search(
        r"/spreadsheets/d/([A-Za-z0-9_-]+)",
        content,
    )

    return match.group(1) if match else content


def open_spreadsheet() -> gspread.Spreadsheet:
    if not CREDENTIALS_FILE.exists():
        raise VaprizzioError(
            f"No existe la credencial: {CREDENTIALS_FILE}"
        )

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    credentials = Credentials.from_service_account_file(
        str(CREDENTIALS_FILE),
        scopes=scopes,
    )

    client = gspread.authorize(credentials)
    return client.open_by_key(spreadsheet_id())


def worksheet_by_name(
    book: gspread.Spreadsheet,
    name: str,
) -> gspread.Worksheet:
    try:
        return book.worksheet(name)
    except WorksheetNotFound as exc:
        raise VaprizzioError(
            f"No existe la hoja '{name}'."
        ) from exc


def headers(ws: gspread.Worksheet) -> list[str]:
    return [
        str(value).strip()
        for value in ws.row_values(1)
    ]


def header_map(ws: gspread.Worksheet) -> dict[str, int]:
    return {
        normalize(header): position
        for position, header in enumerate(
            headers(ws),
            start=1,
        )
        if header
    }


def find_column(
    mapping: dict[str, int],
    alternatives: list[str],
) -> int:
    for alternative in alternatives:
        key = normalize(alternative)

        if key in mapping:
            return mapping[key]

    raise VaprizzioError(
        "No se encontró una columna compatible con: "
        + ", ".join(alternatives)
    )


def sales_sheet_title(date_value: datetime) -> str:
    month = MONTHS[date_value.month]

    if date_value.year == LEGACY_YEAR:
        return f"Ventas {month}"

    return f"Ventas {month} {date_value.year}"


def profit_header(date_value: datetime) -> str:
    return (
        f"Ganancia {MONTHS[date_value.month]} "
        f"{date_value.year}"
    )


def month_from_sales_title(
    title: str,
) -> tuple[int, int] | None:
    normalized = normalize(title)

    for month_number, month_name in MONTHS.items():
        base = normalize(f"Ventas {month_name}")

        if normalized == base:
            return month_number, LEGACY_YEAR

        match = re.fullmatch(
            rf"{re.escape(base)}\s+(\d{{4}})",
            normalized,
        )

        if match:
            return month_number, int(match.group(1))

    return None


def find_sales_template(
    book: gspread.Spreadsheet,
    excluded_title: str,
) -> gspread.Worksheet | None:
    candidates = []

    for ws in book.worksheets():
        month_data = month_from_sales_title(ws.title)

        if (
            month_data is not None
            and normalize(ws.title)
            != normalize(excluded_title)
        ):
            month, year = month_data
            candidates.append((year, month, ws))

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: (item[0], item[1]),
        reverse=True,
    )

    return candidates[0][2]


def copy_format(
    book: gspread.Spreadsheet,
    source_sheet_id: int,
    destination_sheet_id: int,
    source_range: dict[str, int],
    destination_range: dict[str, int],
) -> None:
    book.batch_update(
        {
            "requests": [
                {
                    "copyPaste": {
                        "source": {
                            "sheetId": source_sheet_id,
                            **source_range,
                        },
                        "destination": {
                            "sheetId": destination_sheet_id,
                            **destination_range,
                        },
                        "pasteType": "PASTE_FORMAT",
                        "pasteOrientation": "NORMAL",
                    }
                }
            ]
        }
    )


def ensure_month_sheet(
    book: gspread.Spreadsheet,
    date_value: datetime,
) -> tuple[gspread.Worksheet, bool]:
    title = sales_sheet_title(date_value)

    try:
        return book.worksheet(title), False
    except WorksheetNotFound:
        pass

    template = find_sales_template(book, title)

    if template is not None:
        new_sheet_id = book.duplicate_sheet(
            source_sheet_id=template.id,
            new_sheet_name=title,
        )

        ws = book.get_worksheet_by_id(new_sheet_id)

        if ws.row_count > 1:
            ws.batch_clear(
                [f"A2:K{ws.row_count}"]
            )

        return ws, True

    ws = book.add_worksheet(
        title=title,
        rows=1000,
        cols=len(SALE_HEADERS),
    )

    ws.update(
        range_name="A1:K1",
        values=[SALE_HEADERS],
        value_input_option="USER_ENTERED",
    )

    ws.format(
        "A1:K1",
        {
            "textFormat": {"bold": True},
            "horizontalAlignment": "CENTER",
        },
    )

    ws.freeze(rows=1)

    return ws, True


def product_sheet(
    book: gspread.Spreadsheet,
) -> gspread.Worksheet:
    return worksheet_by_name(book, PRODUCT_SHEET)


def find_product(
    ws: gspread.Worksheet,
    brand: str,
    flavor: str,
) -> tuple[int, dict[str, Any]]:
    mapping = header_map(ws)

    brand_column = find_column(
        mapping,
        ["Marca", "Vape", "Producto"],
    )

    flavor_column = find_column(
        mapping,
        ["Sabor", "Gusto"],
    )

    stock_column = find_column(
        mapping,
        ["Stock", "Cantidad"],
    )

    cost_column = find_column(
        mapping,
        ["Costo", "Precio costo"],
    )

    price_column = find_column(
        mapping,
        ["Precio venta", "Precio de venta"],
    )

    profit_column = find_column(
        mapping,
        ["Ganancia", "Ganancia unitaria"],
    )

    rows = ws.get_all_values()[1:]

    for row_number, row in enumerate(rows, start=2):
        def value(column: int) -> str:
            if len(row) < column:
                return ""

            return row[column - 1]

        if (
            normalize(value(brand_column))
            == normalize(brand)
            and normalize(value(flavor_column))
            == normalize(flavor)
        ):
            return row_number, {
                "marca": value(brand_column),
                "sabor": value(flavor_column),
                "stock": parse_positive_int(
                    value(stock_column) or 0,
                    "Stock",
                ),
                "costo": parse_number(
                    value(cost_column) or 0
                ),
                "precio": parse_number(
                    value(price_column) or 0
                ),
                "columns": {
                    "marca": brand_column,
                    "sabor": flavor_column,
                    "stock": stock_column,
                    "costo": cost_column,
                    "precio": price_column,
                    "ganancia": profit_column,
                },
            }

    raise VaprizzioError(
        f"No existe el producto "
        f"'{brand} - {flavor}'."
    )


def next_order(ws: gspread.Worksheet) -> int:
    mapping = header_map(ws)

    order_column = find_column(
        mapping,
        ["Orden", "# Orden", "Número de orden"],
    )

    result = 0

    for value in ws.col_values(order_column)[1:]:
        try:
            result = max(
                result,
                int(float(value)),
            )
        except (TypeError, ValueError):
            continue

    return result + 1


def find_sale(
    ws: gspread.Worksheet,
    order: int,
) -> tuple[int, dict[str, str]]:
    mapping = header_map(ws)

    order_column = find_column(
        mapping,
        ["Orden", "# Orden", "Número de orden"],
    )

    rows = ws.get_all_values()

    for row_number, row in enumerate(
        rows[1:],
        start=2,
    ):
        value = (
            row[order_column - 1]
            if len(row) >= order_column
            else ""
        )

        try:
            current_order = int(float(value))
        except (TypeError, ValueError):
            continue

        if current_order == order:
            sale = {}

            for header, column in mapping.items():
                sale[header] = (
                    row[column - 1]
                    if len(row) >= column
                    else ""
                )

            return row_number, sale

    raise VaprizzioError(
        f"No existe la orden {order} "
        f"en '{ws.title}'."
    )


def sale_value(
    sale: dict[str, str],
    alternatives: list[str],
    default: str = "",
) -> str:
    for alternative in alternatives:
        key = normalize(alternative)

        if key in sale:
            return sale[key]

    return default


def build_sale_row(
    ws: gspread.Worksheet,
    values: dict[str, Any],
) -> list[Any]:
    current_headers = headers(ws)
    row = [""] * len(current_headers)

    aliases = {
        "orden": values["orden"],
        "# orden": values["orden"],
        "numero de orden": values["orden"],
        "cantidad": values["cantidad"],
        "cliente": values["cliente"],
        "vape": values["marca"],
        "marca": values["marca"],
        "producto": values["marca"],
        "sabor": values["sabor"],
        "gusto": values["sabor"],
        "estado": values["estado"],
        "fecha del pedido": values["fecha"],
        "fecha": values["fecha"],
        "precio venta": values["precio_total"],
        "precio de venta": values["precio_total"],
        "plataforma de ventas": values["plataforma"],
        "plataforma": values["plataforma"],
        "ganancia": values["ganancia"],
        "forma de pago": values["forma_pago"],
    }

    for position, header in enumerate(current_headers):
        key = normalize(header)

        if key in aliases:
            row[position] = aliases[key]

    return row


def fixed_expenses_layout(
    ws: gspread.Worksheet,
) -> dict[str, int]:
    values = ws.get_all_values()

    for row_number, row in enumerate(
        values[:10],
        start=1,
    ):
        normalized_row = [
            normalize(value)
            for value in row
        ]

        name_column = None
        amount_column = None

        for column_number, cell in enumerate(
            normalized_row,
            start=1,
        ):
            if (
                "gasto" in cell
                or "concepto" in cell
                or "descripcion" in cell
                or cell == "nombre"
            ):
                name_column = column_number

            if (
                "monto" in cell
                or "importe" in cell
                or "precio" in cell
                or "valor" in cell
                or cell == "costo"
            ):
                amount_column = column_number

        if name_column and amount_column:
            return {
                "header_row": row_number,
                "name_column": name_column,
                "amount_column": amount_column,
            }

    # Estructura predeterminada:
    # A = nombre del gasto, B = importe
    return {
        "header_row": 1,
        "name_column": 1,
        "amount_column": 2,
    }


def fixed_expenses_data(
    ws: gspread.Worksheet,
) -> tuple[
    dict[str, int],
    int | None,
    list[dict[str, Any]],
]:
    layout = fixed_expenses_layout(ws)
    values = ws.get_all_values()

    total_row = None
    expenses = []

    for row_number in range(
        layout["header_row"] + 1,
        len(values) + 1,
    ):
        row = values[row_number - 1]

        name = (
            row[layout["name_column"] - 1]
            if len(row) >= layout["name_column"]
            else ""
        )

        amount = (
            row[layout["amount_column"] - 1]
            if len(row) >= layout["amount_column"]
            else ""
        )

        if normalize(name) in {
            "total",
            "total gastos",
            "total gastos fijos",
        }:
            total_row = row_number
            break

        if name.strip():
            expenses.append(
                {
                    "row": row_number,
                    "nombre": name,
                    "monto": parse_number(amount),
                }
            )

    return layout, total_row, expenses


def calculate_fixed_expenses_total(
    book: gspread.Spreadsheet,
) -> float:
    ws = worksheet_by_name(
        book,
        FIXED_EXPENSES_SHEET,
    )

    _, _, expenses = fixed_expenses_data(ws)

    return round(
        sum(expense["monto"] for expense in expenses),
        2,
    )


def update_total_formula(
    ws: gspread.Worksheet,
    layout: dict[str, int],
    total_row: int,
) -> None:
    amount_letter = gspread.utils.rowcol_to_a1(
        1,
        layout["amount_column"],
    ).replace("1", "")

    first_data_row = layout["header_row"] + 1
    last_data_row = total_row - 1

    formula = (
        f"=SUM({amount_letter}{first_data_row}:"
        f"{amount_letter}{last_data_row})"
    )

    ws.update_cell(
        total_row,
        layout["amount_column"],
        formula,
    )


def upsert_fixed_expense(
    book: gspread.Spreadsheet,
    name: str,
    amount: float,
    only_existing: bool,
) -> dict[str, Any]:
    ws = worksheet_by_name(
        book,
        FIXED_EXPENSES_SHEET,
    )

    layout, total_row, expenses = (
        fixed_expenses_data(ws)
    )

    for expense in expenses:
        if normalize(expense["nombre"]) == normalize(name):
            ws.update_cell(
                expense["row"],
                layout["amount_column"],
                amount,
            )

            return {
                "creado": False,
                "fila": expense["row"],
                "nombre": expense["nombre"],
                "monto_anterior": expense["monto"],
                "monto_nuevo": amount,
            }

    if only_existing:
        raise VaprizzioError(
            f"No existe el gasto fijo '{name}'."
        )

    if total_row is None:
        insert_row = max(
            layout["header_row"] + 1,
            len(ws.get_all_values()) + 1,
        )

        ws.insert_row(
            [""] * max(
                layout["name_column"],
                layout["amount_column"],
            ),
            index=insert_row,
        )

        total_row = insert_row + 1

        ws.update_cell(
            total_row,
            layout["name_column"],
            "TOTAL",
        )
    else:
        insert_row = total_row

        ws.insert_row(
            [""] * max(
                layout["name_column"],
                layout["amount_column"],
            ),
            index=insert_row,
            inherit_from_before=True,
        )

        total_row += 1

    ws.update_cell(
        insert_row,
        layout["name_column"],
        name,
    )

    ws.update_cell(
        insert_row,
        layout["amount_column"],
        amount,
    )

    update_total_formula(
        ws,
        layout,
        total_row,
    )

    return {
        "creado": True,
        "fila": insert_row,
        "nombre": name,
        "monto_nuevo": amount,
    }


def sum_sales_profit(
    ws: gspread.Worksheet,
) -> float:
    mapping = header_map(ws)

    profit_column = find_column(
        mapping,
        ["Ganancia", "Ganancias"],
    )

    total = 0.0

    for value in ws.col_values(profit_column)[1:]:
        total += parse_number(value)

    return round(total, 2)


def find_profit_header_row(
    ws: gspread.Worksheet,
) -> int:
    values = ws.get_all_values()

    for row_number, row in enumerate(
        values[:10],
        start=1,
    ):
        if any(
            normalize(value).startswith("ganancia")
            for value in row
        ):
            return row_number

    return 1


def ensure_profit_column(
    book: gspread.Spreadsheet,
    date_value: datetime,
) -> tuple[gspread.Worksheet, int, int, bool]:
    ws = worksheet_by_name(book, PROFITS_SHEET)

    header_row = find_profit_header_row(ws)
    target_header = profit_header(date_value)

    row_values = ws.row_values(header_row)

    for column, value in enumerate(
        row_values,
        start=1,
    ):
        if normalize(value) == normalize(target_header):
            return ws, header_row, column, False

    new_column = max(len(row_values), 1) + 1

    if new_column > ws.col_count:
        ws.add_cols(new_column - ws.col_count)

    source_column = max(new_column - 1, 1)

    copy_format(
        book=book,
        source_sheet_id=ws.id,
        destination_sheet_id=ws.id,
        source_range={
            "startRowIndex": 0,
            "endRowIndex": ws.row_count,
            "startColumnIndex": source_column - 1,
            "endColumnIndex": source_column,
        },
        destination_range={
            "startRowIndex": 0,
            "endRowIndex": ws.row_count,
            "startColumnIndex": new_column - 1,
            "endColumnIndex": new_column,
        },
    )

    ws.batch_clear(
        [
            f"{gspread.utils.rowcol_to_a1(1, new_column)}:"
            f"{gspread.utils.rowcol_to_a1(ws.row_count, new_column)}"
        ]
    )

    ws.update_cell(
        header_row,
        new_column,
        target_header,
    )

    return ws, header_row, new_column, True


def update_month_profit(
    book: gspread.Spreadsheet,
    date_value: datetime,
) -> dict[str, Any]:
    title = sales_sheet_title(date_value)

    try:
        sales_ws = book.worksheet(title)
        gross_profit = sum_sales_profit(sales_ws)
    except WorksheetNotFound:
        gross_profit = 0.0

    fixed_expenses = calculate_fixed_expenses_total(
        book
    )

    net_profit = round(
        gross_profit - fixed_expenses,
        2,
    )

    profit_ws, header_row, column, created = (
        ensure_profit_column(
            book,
            date_value,
        )
    )

    value_row = header_row + 1

    profit_ws.update_cell(
        value_row,
        column,
        net_profit,
    )

    return {
        "hoja_ventas": title,
        "columna_ganancias": profit_header(date_value),
        "columna_creada": created,
        "ganancia_ventas": gross_profit,
        "gastos_fijos": fixed_expenses,
        "ganancia_neta": net_profit,
    }


def cmd_create_month(args: argparse.Namespace) -> None:
    book = open_spreadsheet()
    date_value = parse_date(args.fecha)

    ws, created = ensure_month_sheet(
        book,
        date_value,
    )

    profit = update_month_profit(
        book,
        date_value,
    )

    emit(
        {
            "ok": True,
            "accion": "crear_mes",
            "hoja": ws.title,
            "creada": created,
            "ganancias": profit,
        }
    )


def cmd_add_product(args: argparse.Namespace) -> None:
    book = open_spreadsheet()
    ws = product_sheet(book)

    try:
        find_product(ws, args.marca, args.sabor)
        raise VaprizzioError(
            "El producto ya existe."
        )
    except VaprizzioError as exc:
        if "No existe el producto" not in str(exc):
            raise

    mapping = header_map(ws)

    brand_column = find_column(
        mapping,
        ["Marca", "Vape", "Producto"],
    )
    flavor_column = find_column(
        mapping,
        ["Sabor", "Gusto"],
    )
    stock_column = find_column(
        mapping,
        ["Stock", "Cantidad"],
    )
    cost_column = find_column(
        mapping,
        ["Costo", "Precio costo"],
    )
    price_column = find_column(
        mapping,
        ["Precio venta", "Precio de venta"],
    )
    profit_column = find_column(
        mapping,
        ["Ganancia", "Ganancia unitaria"],
    )

    stock = parse_positive_int(args.stock, "Stock")
    cost = parse_number(args.costo)
    price = parse_number(args.precio)
    profit = round(price - cost, 2)

    row_length = max(mapping.values())
    row = [""] * row_length

    row[brand_column - 1] = args.marca
    row[flavor_column - 1] = args.sabor
    row[stock_column - 1] = stock
    row[cost_column - 1] = cost
    row[price_column - 1] = price
    row[profit_column - 1] = profit

    ws.append_row(
        row,
        value_input_option="USER_ENTERED",
    )

    emit(
        {
            "ok": True,
            "accion": "agregar_producto",
            "marca": args.marca,
            "sabor": args.sabor,
            "stock": stock,
            "costo": cost,
            "precio_venta": price,
            "ganancia_unitaria": profit,
        }
    )


def cmd_update_product(args: argparse.Namespace) -> None:
    book = open_spreadsheet()
    ws = product_sheet(book)

    row, product = find_product(
        ws,
        args.marca,
        args.sabor,
    )

    columns = product["columns"]

    values = {
        "marca": args.nueva_marca or product["marca"],
        "sabor": args.nuevo_sabor or product["sabor"],
        "stock": (
            parse_positive_int(args.stock, "Stock")
            if args.stock is not None
            else product["stock"]
        ),
        "costo": (
            parse_number(args.costo)
            if args.costo is not None
            else product["costo"]
        ),
        "precio": (
            parse_number(args.precio)
            if args.precio is not None
            else product["precio"]
        ),
    }

    values["ganancia"] = round(
        values["precio"] - values["costo"],
        2,
    )

    for field, value in values.items():
        ws.update_cell(
            row,
            columns[field],
            value,
        )

    emit(
        {
            "ok": True,
            "accion": "modificar_producto",
            **values,
        }
    )


def cmd_stock(args: argparse.Namespace) -> None:
    book = open_spreadsheet()
    ws = product_sheet(book)

    if args.marca and args.sabor:
        _, product = find_product(
            ws,
            args.marca,
            args.sabor,
        )

        product.pop("columns", None)

        emit(
            {
                "ok": True,
                "accion": "consultar_stock",
                "producto": product,
            }
        )

        return

    mapping = header_map(ws)

    brand_column = find_column(
        mapping,
        ["Marca", "Vape", "Producto"],
    )
    flavor_column = find_column(
        mapping,
        ["Sabor", "Gusto"],
    )
    stock_column = find_column(
        mapping,
        ["Stock", "Cantidad"],
    )
    price_column = find_column(
        mapping,
        ["Precio venta", "Precio de venta"],
    )

    products = []

    for row in ws.get_all_values()[1:]:
        def value(column: int) -> str:
            return (
                row[column - 1]
                if len(row) >= column
                else ""
            )

        if not value(brand_column):
            continue

        stock = parse_positive_int(
            value(stock_column) or 0,
            "Stock",
        )

        if (
            args.solo_bajo is not None
            and stock > args.solo_bajo
        ):
            continue

        products.append(
            {
                "marca": value(brand_column),
                "sabor": value(flavor_column),
                "stock": stock,
                "precio": parse_number(
                    value(price_column)
                ),
            }
        )

    emit(
        {
            "ok": True,
            "accion": "consultar_stock",
            "productos": products,
        }
    )


def cmd_add_sale(args: argparse.Namespace) -> None:
    book = open_spreadsheet()
    date_value = parse_date(args.fecha)

    sales_ws, created = ensure_month_sheet(
        book,
        date_value,
    )

    products_ws = product_sheet(book)

    product_row, product = find_product(
        products_ws,
        args.marca,
        args.sabor,
    )

    quantity = parse_positive_int(
        args.cantidad,
        "Cantidad",
    )

    if quantity < 1:
        raise VaprizzioError(
            "La cantidad debe ser como mínimo 1."
        )

    if product["stock"] < quantity:
        raise VaprizzioError(
            f"Stock insuficiente. "
            f"Disponible: {product['stock']}."
        )

    unit_price = (
        parse_number(args.precio)
        if args.precio is not None
        else product["precio"]
    )

    total_price = round(
        unit_price * quantity,
        2,
    )

    profit = round(
        (unit_price - product["costo"])
        * quantity,
        2,
    )

    order = args.orden or next_order(sales_ws)

    try:
        find_sale(sales_ws, order)
        raise VaprizzioError(
            f"La orden {order} ya existe."
        )
    except VaprizzioError as exc:
        if "No existe la orden" not in str(exc):
            raise

    data = {
        "orden": order,
        "cantidad": quantity,
        "cliente": args.cliente,
        "marca": product["marca"],
        "sabor": product["sabor"],
        "estado": args.estado,
        "fecha": format_date(date_value),
        "precio_total": total_price,
        "plataforma": args.plataforma,
        "ganancia": profit,
        "forma_pago": args.forma_pago,
    }

    new_stock = product["stock"] - quantity

    products_ws.update_cell(
        product_row,
        product["columns"]["stock"],
        new_stock,
    )

    try:
        sales_ws.append_row(
            build_sale_row(sales_ws, data),
            value_input_option="USER_ENTERED",
        )
    except Exception:
        products_ws.update_cell(
            product_row,
            product["columns"]["stock"],
            product["stock"],
        )
        raise

    profit_data = update_month_profit(
        book,
        date_value,
    )

    emit(
        {
            "ok": True,
            "accion": "registrar_venta",
            "hoja": sales_ws.title,
            "hoja_creada": created,
            "precio_unitario": unit_price,
            "stock_restante": new_stock,
            **data,
            "resumen_ganancias": profit_data,
        }
    )


def cmd_delete_sale(args: argparse.Namespace) -> None:
    book = open_spreadsheet()
    date_value = parse_date(args.fecha)

    title = (
        args.hoja
        if args.hoja
        else sales_sheet_title(date_value)
    )

    ws = worksheet_by_name(book, title)
    row, sale = find_sale(ws, args.orden)

    brand = sale_value(
        sale,
        ["Vape", "Marca", "Producto"],
    )
    flavor = sale_value(
        sale,
        ["Sabor", "Gusto"],
    )
    quantity = parse_positive_int(
        sale_value(sale, ["Cantidad"], "0"),
        "Cantidad",
    )

    products_ws = product_sheet(book)
    product_row, product = find_product(
        products_ws,
        brand,
        flavor,
    )

    restored_stock = product["stock"] + quantity

    products_ws.update_cell(
        product_row,
        product["columns"]["stock"],
        restored_stock,
    )

    try:
        ws.delete_rows(row)
    except Exception:
        products_ws.update_cell(
            product_row,
            product["columns"]["stock"],
            product["stock"],
        )
        raise

    detected = month_from_sales_title(title)

    if detected:
        month, year = detected
        profit_date = datetime(year, month, 1)
    else:
        profit_date = date_value

    profit_data = update_month_profit(
        book,
        profit_date,
    )

    emit(
        {
            "ok": True,
            "accion": "eliminar_venta",
            "orden": args.orden,
            "hoja": title,
            "marca": brand,
            "sabor": flavor,
            "cantidad": quantity,
            "stock_actual": restored_stock,
            "resumen_ganancias": profit_data,
        }
    )


def cmd_update_sale(args: argparse.Namespace) -> None:
    book = open_spreadsheet()
    search_date = parse_date(args.fecha_busqueda)

    title = (
        args.hoja
        if args.hoja
        else sales_sheet_title(search_date)
    )

    ws = worksheet_by_name(book, title)
    row, old_sale = find_sale(ws, args.orden)

    old_brand = sale_value(
        old_sale,
        ["Vape", "Marca", "Producto"],
    )
    old_flavor = sale_value(
        old_sale,
        ["Sabor", "Gusto"],
    )
    old_quantity = parse_positive_int(
        sale_value(old_sale, ["Cantidad"], "1"),
        "Cantidad",
    )

    products_ws = product_sheet(book)

    old_product_row, old_product = find_product(
        products_ws,
        old_brand,
        old_flavor,
    )

    restored_old_stock = (
        old_product["stock"] + old_quantity
    )

    products_ws.update_cell(
        old_product_row,
        old_product["columns"]["stock"],
        restored_old_stock,
    )

    try:
        new_brand = args.marca or old_brand
        new_flavor = args.sabor or old_flavor

        new_product_row, new_product = find_product(
            products_ws,
            new_brand,
            new_flavor,
        )

        if (
            normalize(new_brand) == normalize(old_brand)
            and normalize(new_flavor)
            == normalize(old_flavor)
        ):
            new_product["stock"] = restored_old_stock

        quantity = (
            parse_positive_int(
                args.cantidad,
                "Cantidad",
            )
            if args.cantidad is not None
            else old_quantity
        )

        if new_product["stock"] < quantity:
            raise VaprizzioError(
                "Stock insuficiente para modificar "
                "la venta."
            )

        unit_price = (
            parse_number(args.precio)
            if args.precio is not None
            else new_product["precio"]
        )

        total_price = round(
            unit_price * quantity,
            2,
        )

        profit = round(
            (unit_price - new_product["costo"])
            * quantity,
            2,
        )

        sale_date = (
            parse_date(args.fecha)
            if args.fecha
            else parse_date(
                sale_value(
                    old_sale,
                    ["Fecha del pedido", "Fecha"],
                )
            )
        )

        data = {
            "orden": args.orden,
            "cantidad": quantity,
            "cliente": (
                args.cliente
                or sale_value(
                    old_sale,
                    ["Cliente"],
                )
            ),
            "marca": new_product["marca"],
            "sabor": new_product["sabor"],
            "estado": (
                args.estado
                or sale_value(
                    old_sale,
                    ["Estado"],
                    "Entregado",
                )
            ),
            "fecha": format_date(sale_date),
            "precio_total": total_price,
            "plataforma": (
                args.plataforma
                or sale_value(
                    old_sale,
                    [
                        "Plataforma de ventas",
                        "Plataforma",
                    ],
                )
            ),
            "ganancia": profit,
            "forma_pago": (
                args.forma_pago
                or sale_value(
                    old_sale,
                    ["Forma de pago"],
                )
            ),
        }

        row_values = build_sale_row(ws, data)
        end_column = len(row_values)

        ws.update(
            range_name=(
                f"A{row}:"
                f"{gspread.utils.rowcol_to_a1(row, end_column)}"
            ),
            values=[row_values],
            value_input_option="USER_ENTERED",
        )

        new_stock = new_product["stock"] - quantity

        products_ws.update_cell(
            new_product_row,
            new_product["columns"]["stock"],
            new_stock,
        )

    except Exception:
        products_ws.update_cell(
            old_product_row,
            old_product["columns"]["stock"],
            old_product["stock"],
        )
        raise

    detected = month_from_sales_title(title)

    if detected:
        month, year = detected
        profit_date = datetime(year, month, 1)
    else:
        profit_date = search_date

    profit_data = update_month_profit(
        book,
        profit_date,
    )

    emit(
        {
            "ok": True,
            "accion": "modificar_venta",
            "hoja": title,
            "precio_unitario": unit_price,
            "stock_restante": new_stock,
            **data,
            "resumen_ganancias": profit_data,
        }
    )


def cmd_add_expense(args: argparse.Namespace) -> None:
    book = open_spreadsheet()

    result = upsert_fixed_expense(
        book,
        args.nombre,
        parse_number(args.monto),
        only_existing=False,
    )

    current_date = parse_date(args.fecha)

    profit_data = update_month_profit(
        book,
        current_date,
    )

    emit(
        {
            "ok": True,
            "accion": "agregar_o_actualizar_gasto_fijo",
            **result,
            "total_gastos_fijos": (
                profit_data["gastos_fijos"]
            ),
            "resumen_ganancias": profit_data,
        }
    )


def cmd_update_expense(args: argparse.Namespace) -> None:
    book = open_spreadsheet()

    result = upsert_fixed_expense(
        book,
        args.nombre,
        parse_number(args.monto),
        only_existing=True,
    )

    current_date = parse_date(args.fecha)

    profit_data = update_month_profit(
        book,
        current_date,
    )

    emit(
        {
            "ok": True,
            "accion": "modificar_gasto_fijo",
            **result,
            "total_gastos_fijos": (
                profit_data["gastos_fijos"]
            ),
            "resumen_ganancias": profit_data,
        }
    )


def cmd_list_expenses(
    args: argparse.Namespace,
) -> None:
    book = open_spreadsheet()

    ws = worksheet_by_name(
        book,
        FIXED_EXPENSES_SHEET,
    )

    _, _, expenses = fixed_expenses_data(ws)

    emit(
        {
            "ok": True,
            "accion": "consultar_gastos_fijos",
            "gastos": expenses,
            "total": round(
                sum(
                    expense["monto"]
                    for expense in expenses
                ),
                2,
            ),
        }
    )


def cmd_update_profit(
    args: argparse.Namespace,
) -> None:
    book = open_spreadsheet()
    date_value = parse_date(args.fecha)

    result = update_month_profit(
        book,
        date_value,
    )

    emit(
        {
            "ok": True,
            "accion": "actualizar_ganancias",
            **result,
        }
    )


def cmd_update_all_profits(
    args: argparse.Namespace,
) -> None:
    book = open_spreadsheet()
    results = []

    for ws in book.worksheets():
        detected = month_from_sales_title(ws.title)

        if not detected:
            continue

        month, year = detected

        results.append(
            update_month_profit(
                book,
                datetime(year, month, 1),
            )
        )

    emit(
        {
            "ok": True,
            "accion": "actualizar_todas_las_ganancias",
            "meses_actualizados": results,
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(
        dest="command",
        required=True,
    )

    command = commands.add_parser("crear-mes")
    command.add_argument("--fecha")
    command.set_defaults(function=cmd_create_month)

    command = commands.add_parser("agregar-producto")
    command.add_argument("--marca", required=True)
    command.add_argument("--sabor", required=True)
    command.add_argument("--stock", required=True)
    command.add_argument("--costo", required=True)
    command.add_argument("--precio", required=True)
    command.set_defaults(function=cmd_add_product)

    command = commands.add_parser("modificar-producto")
    command.add_argument("--marca", required=True)
    command.add_argument("--sabor", required=True)
    command.add_argument("--nueva-marca")
    command.add_argument("--nuevo-sabor")
    command.add_argument("--stock")
    command.add_argument("--costo")
    command.add_argument("--precio")
    command.set_defaults(function=cmd_update_product)

    command = commands.add_parser("consultar-stock")
    command.add_argument("--marca")
    command.add_argument("--sabor")
    command.add_argument("--solo-bajo", type=int)
    command.set_defaults(function=cmd_stock)

    command = commands.add_parser("registrar-venta")
    command.add_argument("--orden", type=int)
    command.add_argument("--cantidad", required=True)
    command.add_argument("--cliente", required=True)
    command.add_argument("--marca", required=True)
    command.add_argument("--sabor", required=True)
    command.add_argument(
        "--estado",
        default="Entregado",
    )
    command.add_argument("--fecha")
    command.add_argument("--precio")
    command.add_argument("--plataforma", required=True)
    command.add_argument(
        "--forma-pago",
        required=True,
    )
    command.set_defaults(function=cmd_add_sale)

    command = commands.add_parser("modificar-venta")
    command.add_argument("--orden", type=int, required=True)
    command.add_argument("--hoja")
    command.add_argument("--fecha-busqueda")
    command.add_argument("--cantidad")
    command.add_argument("--cliente")
    command.add_argument("--marca")
    command.add_argument("--sabor")
    command.add_argument("--estado")
    command.add_argument("--fecha")
    command.add_argument("--precio")
    command.add_argument("--plataforma")
    command.add_argument("--forma-pago")
    command.set_defaults(function=cmd_update_sale)

    command = commands.add_parser("eliminar-venta")
    command.add_argument("--orden", type=int, required=True)
    command.add_argument("--hoja")
    command.add_argument("--fecha")
    command.set_defaults(function=cmd_delete_sale)

    command = commands.add_parser("agregar-gasto")
    command.add_argument("--nombre", required=True)
    command.add_argument("--monto", required=True)
    command.add_argument("--fecha")
    command.set_defaults(function=cmd_add_expense)

    command = commands.add_parser("modificar-gasto")
    command.add_argument("--nombre", required=True)
    command.add_argument("--monto", required=True)
    command.add_argument("--fecha")
    command.set_defaults(function=cmd_update_expense)

    command = commands.add_parser("consultar-gastos")
    command.set_defaults(function=cmd_list_expenses)

    command = commands.add_parser("actualizar-ganancias")
    command.add_argument("--fecha")
    command.set_defaults(function=cmd_update_profit)

    command = commands.add_parser(
        "actualizar-todas-ganancias"
    )
    command.set_defaults(
        function=cmd_update_all_profits
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        args.function(args)
    except VaprizzioError as exc:
        emit(
            {
                "ok": False,
                "error": str(exc),
            }
        )
        raise SystemExit(1)
    except Exception as exc:
        emit(
            {
                "ok": False,
                "error": (
                    f"{type(exc).__name__}: {exc}"
                ),
            }
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
