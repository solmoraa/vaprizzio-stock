#!/usr/bin/env python3

from __future__ import annotations

import json
import time
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import gspread
from gspread.utils import rowcol_to_a1

from app.sync_core import (
    DATABASE_PATH,
    SyncError,
    api_request,
    get_product,
    get_variant,
    localized_text,
    money_output,
    normalize,
    parse_float,
    parse_int,
    read_products,
    spreadsheet,
    synchronize_variant_to_sheet,
)


EVENT_DATABASE = Path(DATABASE_PATH)
PLATFORM_NAME = "Tienda Nube"

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

BASE_SALE_HEADERS = [
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

TECHNICAL_HEADERS = [
    "TiendaNube Order ID",
    "TiendaNube Order Number",
    "TiendaNube Product ID",
    "TiendaNube Variant ID",
    "TiendaNube Line ID",
    "Última sincronización",
]


def initialize_database() -> None:
    with sqlite3.connect(EVENT_DATABASE) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS webhook_events (
                event_key TEXT PRIMARY KEY,
                store_id TEXT NOT NULL,
                event TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                received_at TEXT NOT NULL,
                processed_at TEXT,
                status TEXT NOT NULL,
                error TEXT
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS synchronized_orders (
                order_id TEXT PRIMARY KEY,
                order_number TEXT,
                order_status TEXT,
                updated_at TEXT NOT NULL,
                payload_json TEXT
            )
            """
        )

        connection.commit()


def event_key(
    store_id: Any,
    event: Any,
    resource_id: Any,
) -> str:
    return f"{store_id}:{event}:{resource_id}"


def begin_event(
    store_id: Any,
    event: Any,
    resource_id: Any,
) -> bool:
    initialize_database()

    key = event_key(store_id, event, resource_id)
    now = datetime.now().isoformat(timespec="seconds")

    with sqlite3.connect(EVENT_DATABASE) as connection:
        existing = connection.execute(
            """
            SELECT status
            FROM webhook_events
            WHERE event_key = ?
            """,
            (key,),
        ).fetchone()

        if existing and existing[0] == "PROCESSED":
            return False

        connection.execute(
            """
            INSERT INTO webhook_events (
                event_key,
                store_id,
                event,
                resource_id,
                received_at,
                status
            )
            VALUES (?, ?, ?, ?, ?, 'PROCESSING')
            ON CONFLICT(event_key) DO UPDATE SET
                received_at = excluded.received_at,
                status = 'PROCESSING',
                error = NULL
            """,
            (
                key,
                str(store_id),
                str(event),
                str(resource_id),
                now,
            ),
        )

        connection.commit()

    return True


def finish_event(
    store_id: Any,
    event: Any,
    resource_id: Any,
) -> None:
    key = event_key(store_id, event, resource_id)

    with sqlite3.connect(EVENT_DATABASE) as connection:
        connection.execute(
            """
            UPDATE webhook_events
            SET
                processed_at = ?,
                status = 'PROCESSED',
                error = NULL
            WHERE event_key = ?
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                key,
            ),
        )

        connection.commit()


def fail_event(
    store_id: Any,
    event: Any,
    resource_id: Any,
    error: Exception,
) -> None:
    key = event_key(store_id, event, resource_id)

    with sqlite3.connect(EVENT_DATABASE) as connection:
        connection.execute(
            """
            UPDATE webhook_events
            SET
                processed_at = ?,
                status = 'ERROR',
                error = ?
            WHERE event_key = ?
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                f"{type(error).__name__}: {error}"[:3000],
                key,
            ),
        )

        connection.commit()


def localized(value: Any) -> str:
    return localized_text(value).strip()


def parse_datetime(value: Any) -> datetime:
    text = str(value or "").strip()

    if not text:
        return datetime.now()

    candidates = [
        text,
        text.replace("Z", "+00:00"),
    ]

    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)

            if parsed.tzinfo is not None:
                parsed = parsed.astimezone().replace(tzinfo=None)

            return parsed
        except ValueError:
            pass

    for pattern in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y",
    ):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            pass

    return datetime.now()


def sales_sheet_name(date: datetime) -> str:
    month = MONTHS[date.month]

    if date.year <= 2026:
        return f"Ventas {month}"

    return f"Ventas {month} {date.year}"


def get_or_create_sales_sheet(
    date: datetime,
) -> gspread.Worksheet:
    book = spreadsheet()
    name = sales_sheet_name(date)

    try:
        worksheet = book.worksheet(name)
    except gspread.WorksheetNotFound:
        previous_date = (
            datetime(date.year - 1, 12, 1)
            if date.month == 1
            else datetime(date.year, date.month - 1, 1)
        )

        previous_name = sales_sheet_name(previous_date)

        try:
            previous = book.worksheet(previous_name)
            worksheet = book.duplicate_sheet(
                source_sheet_id=previous.id,
                new_sheet_name=name,
            )

            worksheet.batch_clear(
                [
                    (
                        f"A2:"
                        f"{rowcol_to_a1(worksheet.row_count, 20)}"
                    )
                ]
            )
        except gspread.WorksheetNotFound:
            worksheet = book.add_worksheet(
                title=name,
                rows=1000,
                cols=20,
            )

            worksheet.update(
                range_name="A1:K1",
                values=[BASE_SALE_HEADERS],
            )

    ensure_sales_headers(worksheet)
    return worksheet


def ensure_sales_headers(
    worksheet: gspread.Worksheet,
) -> dict[str, int]:
    values = worksheet.get_all_values()

    if not values:
        worksheet.update(
            range_name="A1:K1",
            values=[BASE_SALE_HEADERS],
        )
        values = worksheet.get_all_values()

    header_row = None

    for row_number, row in enumerate(values[:20], start=1):
        normalized = {
            normalize(value)
            for value in row
            if str(value).strip()
        }

        if (
            normalize("Orden") in normalized
            and normalize("Cantidad") in normalized
            and normalize("Cliente") in normalized
        ):
            header_row = row_number
            break

    if header_row is None:
        raise SyncError(
            f"No encontré los encabezados en {worksheet.title}."
        )

    headers = list(values[header_row - 1])
    normalized_headers = {
        normalize(item)
        for item in headers
        if str(item).strip()
    }

    # Los campos comerciales centrales deben estar junto al cliente, como en
    # las hojas mensuales históricas. Si una hoja fue creada con una plantilla
    # incompleta, insertar columnas desplaza los datos existentes sin
    # sobrescribir Fecha, Precio ni los identificadores de Tiendanube.
    missing_core = [
        header
        for header in ("Vape", "Sabor")
        if normalize(header) not in normalized_headers
    ]

    if missing_core:
        client_column = next(
            index
            for index, header in enumerate(headers, start=1)
            if normalize(header) == normalize("Cliente")
        )
        start_index = client_column
        worksheet.spreadsheet.batch_update(
            {
                "requests": [
                    {
                        "insertDimension": {
                            "range": {
                                "sheetId": worksheet.id,
                                "dimension": "COLUMNS",
                                "startIndex": start_index,
                                "endIndex": (
                                    start_index
                                    + len(missing_core)
                                ),
                            },
                            "inheritFromBefore": True,
                        }
                    }
                ]
            }
        )
        values = worksheet.get_all_values()
        headers = list(values[header_row - 1])

        for offset, header in enumerate(missing_core):
            headers[start_index + offset] = header

    for required in BASE_SALE_HEADERS + TECHNICAL_HEADERS:
        if normalize(required) not in {
            normalize(item)
            for item in headers
        }:
            headers.append(required)

    worksheet.update(
        range_name=(
            f"A{header_row}:"
            f"{rowcol_to_a1(header_row, len(headers))}"
        ),
        values=[headers],
        value_input_option="USER_ENTERED",
    )

    return {
        normalize(header): index
        for index, header in enumerate(headers, start=1)
        if str(header).strip()
    }


def order_customer(order: dict[str, Any]) -> str:
    contact_name = str(
        order.get("contact_name")
        or order.get("contact_identification")
        or ""
    ).strip()

    customer = order.get("customer") or {}

    customer_name = " ".join(
        part
        for part in [
            str(customer.get("name") or "").strip(),
            str(customer.get("last_name") or "").strip(),
        ]
        if part
    ).strip()

    shipping = order.get("shipping_address") or {}

    shipping_name = " ".join(
        part
        for part in [
            str(shipping.get("name") or "").strip(),
            str(shipping.get("last_name") or "").strip(),
        ]
        if part
    ).strip()

    return (
        contact_name
        or customer_name
        or shipping_name
        or "Cliente Tiendanube"
    )


def order_status(order: dict[str, Any]) -> str:
    status = normalize(order.get("status"))
    payment = normalize(order.get("payment_status"))
    shipping = normalize(order.get("shipping_status"))

    if status in {"cancelled", "canceled"}:
        return "Cancelado"

    if shipping in {"delivered", "entregado"}:
        return "Entregado"

    if shipping in {"shipped", "enviado"}:
        return "Enviado"

    if payment in {"paid", "pagado"}:
        return "Pagado"

    if payment in {
        "pending",
        "authorized",
        "pendiente",
    }:
        return "Pendiente"

    return str(
        order.get("status")
        or order.get("payment_status")
        or "Abierto"
    ).strip().title()


def payment_method(order: dict[str, Any]) -> str:
    details = order.get("payment_details") or {}
    gateway = str(
        order.get("gateway")
        or order.get("payment_method")
        or details.get("method")
        or details.get("name")
        or ""
    ).strip()

    if not gateway:
        return "TIENDA NUBE"

    return gateway.upper()


def line_product_name(
    line: dict[str, Any],
) -> str:
    return localized(
        line.get("name")
        or line.get("product_name")
        or line.get("title")
    )


def line_flavor(
    line: dict[str, Any],
) -> str:
    value = (
        line.get("variant_values")
        or line.get("variant_value")
        or line.get("values")
    )

    if isinstance(value, list):
        parts = []

        for item in value:
            if isinstance(item, dict):
                text = localized(item)
            else:
                text = str(item or "").strip()

            if text:
                parts.append(text)

        if parts:
            return " / ".join(parts)

    if isinstance(value, dict):
        text = localized(value)

        if text:
            return text

    variant_name = str(
        line.get("variant_name")
        or line.get("variant")
        or ""
    ).strip()

    return variant_name


def line_quantity(
    line: dict[str, Any],
) -> int:
    return max(
        parse_int(line.get("quantity")) or 1,
        1,
    )


def line_unit_price(
    line: dict[str, Any],
) -> float:
    return (
        parse_float(line.get("price"))
        or parse_float(line.get("unit_price"))
        or 0.0
    )


def line_product_id(
    line: dict[str, Any],
) -> str:
    return str(
        line.get("product_id")
        or (
            line.get("product", {}).get("id")
            if isinstance(line.get("product"), dict)
            else ""
        )
        or ""
    )


def line_variant_id(
    line: dict[str, Any],
) -> str:
    return str(
        line.get("variant_id")
        or (
            line.get("variant", {}).get("id")
            if isinstance(line.get("variant"), dict)
            else ""
        )
        or ""
    )


def line_id(
    line: dict[str, Any],
    position: int,
) -> str:
    return str(
        line.get("id")
        or line.get("order_product_id")
        or f"position-{position}"
    )



# Caché breve de Productos para evitar superar la cuota de Google Sheets.
# Todos los webhooks recibidos durante esta ventana comparten una lectura.
_PRODUCTS_CACHE: dict[str, Any] = {
    "timestamp": 0.0,
    "products": [],
}
_PRODUCTS_CACHE_TTL_SECONDS = 90


def cached_products() -> list[Any]:
    now = time.monotonic()

    if (
        _PRODUCTS_CACHE["products"]
        and now - _PRODUCTS_CACHE["timestamp"]
        < _PRODUCTS_CACHE_TTL_SECONDS
    ):
        return _PRODUCTS_CACHE["products"]

    _, _, _, products = read_products()

    _PRODUCTS_CACHE["products"] = products
    _PRODUCTS_CACHE["timestamp"] = now

    return products


def invalidate_products_cache() -> None:
    _PRODUCTS_CACHE["timestamp"] = 0.0
    _PRODUCTS_CACHE["products"] = []



def product_cost_by_variant(
    variant_id: str,
) -> float:
    """
    Obtiene el costo usando una única lectura cacheada de Productos,
    en vez de volver a leer toda la hoja por cada producto y webhook.
    """
    products = cached_products()

    for product in products:
        if str(product.variant_id) == str(variant_id):
            return product.costo or 0.0

    return 0.0


def next_internal_order(
    worksheet: gspread.Worksheet,
    columns: dict[str, int],
) -> int:
    values = worksheet.get_all_values()
    order_column = columns[normalize("Orden")]
    current = []

    for row in values[1:]:
        index = order_column - 1

        if index >= len(row):
            continue

        number = parse_int(row[index])

        if number is not None:
            current.append(number)

    return max(current, default=0) + 1


def existing_order_rows(
    worksheet: gspread.Worksheet,
    columns: dict[str, int],
    order_id: str,
) -> list[int]:
    values = worksheet.get_all_values()
    column = columns[normalize("TiendaNube Order ID")]
    result = []

    for row_number, row in enumerate(values[1:], start=2):
        value = (
            str(row[column - 1]).strip()
            if len(row) >= column
            else ""
        )

        if value == str(order_id):
            result.append(row_number)

    return result


def write_rows_at_positions(
    worksheet: gspread.Worksheet,
    rows: list[int],
    values: list[list[Any]],
    total_columns: int,
) -> None:
    """Escribe cada línea en su fila exacta sin tocar las vecinas.

    Una orden de Tiendanube puede recibir una línea nueva después de haber
    sido registrada. No se puede reescribir un bloque continuo en ese caso:
    entre una línea existente y la nueva puede haber una venta manual.
    """
    updates = [
        {
            "range": (
                f"A{row_number}:"
                f"{rowcol_to_a1(row_number, total_columns)}"
            ),
            "values": [row_values],
        }
        for row_number, row_values in zip(rows, values)
    ]

    if updates:
        worksheet.batch_update(
            updates,
            value_input_option="USER_ENTERED",
        )


def insert_empty_order_rows(
    worksheet: gspread.Worksheet,
    *,
    row: int,
    count: int,
    total_columns: int,
) -> None:
    """Abre espacio para una orden sin sobrescribir ventas posteriores."""
    if count <= 0:
        return

    worksheet.insert_rows(
        [[""] * total_columns for _ in range(count)],
        row=row,
        value_input_option="RAW",
        inherit_from_before=True,
    )


def write_order_rows(
    order: dict[str, Any],
) -> dict[str, Any]:
    order_id = str(order.get("id") or "").strip()

    if not order_id:
        raise SyncError("La orden no tiene ID.")

    order_number = str(
        order.get("number")
        or order.get("order_number")
        or order_id
    )

    created = parse_datetime(
        order.get("created_at")
        or order.get("completed_at")
        or order.get("updated_at")
    )

    worksheet = get_or_create_sales_sheet(created)
    columns = ensure_sales_headers(worksheet)
    old_rows = existing_order_rows(
        worksheet,
        columns,
        order_id,
    )

    old_values = worksheet.get_all_values()
    internal_order = None

    if old_rows:
        first = old_rows[0]
        column = columns[normalize("Orden")]

        if first <= len(old_values):
            internal_order = parse_int(
                old_values[first - 1][column - 1]
                if len(old_values[first - 1]) >= column
                else ""
            )

    if internal_order is None:
        internal_order = next_internal_order(
            worksheet,
            columns,
        )

    products = (
        order.get("products")
        or order.get("items")
        or order.get("line_items")
        or []
    )

    if not isinstance(products, list):
        products = []

    customer = order_customer(order)
    status = order_status(order)
    payment = payment_method(order)
    sync_time = datetime.now().strftime(
        "%d/%m/%Y %H:%M:%S"
    )

    headers_by_column = {
        column: key
        for key, column in columns.items()
    }

    total_columns = max(columns.values())
    rows_to_write = []

    for position, line in enumerate(products, start=1):
        if not isinstance(line, dict):
            continue

        quantity = line_quantity(line)
        unit_price = line_unit_price(line)
        total_price = unit_price * quantity
        product_id = line_product_id(line)
        variant_id = line_variant_id(line)
        cost = product_cost_by_variant(variant_id)
        gain = total_price - cost * quantity

        values = {
            normalize("Orden"): internal_order,
            normalize("Cantidad"): quantity,
            normalize("Cliente"): customer,
            normalize("Vape"): line_product_name(line),
            normalize("Sabor"): line_flavor(line),
            normalize("Estado"): status,
            normalize("Fecha del pedido"): created.strftime(
                "%d/%m/%Y"
            ),
            normalize("Precio Venta"): total_price,
            normalize("Plataforma de ventas"): PLATFORM_NAME,
            normalize("Ganancia"): gain,
            normalize("Forma de pago"): payment,
            normalize("TiendaNube Order ID"): order_id,
            normalize("TiendaNube Order Number"): order_number,
            normalize("TiendaNube Product ID"): product_id,
            normalize("TiendaNube Variant ID"): variant_id,
            normalize("TiendaNube Line ID"): line_id(
                line,
                position,
            ),
            normalize("Última sincronización"): sync_time,
        }

        complete_row = []

        for column in range(1, total_columns + 1):
            key = headers_by_column.get(column, "")
            complete_row.append(values.get(key, ""))

        rows_to_write.append(complete_row)

    if not rows_to_write:
        rows_to_write.append(
            [
                (
                    internal_order
                    if column == columns[normalize("Orden")]
                    else customer
                    if column == columns[normalize("Cliente")]
                    else status
                    if column == columns[normalize("Estado")]
                    else created.strftime("%d/%m/%Y")
                    if column == columns[normalize("Fecha del pedido")]
                    else PLATFORM_NAME
                    if column == columns[
                        normalize("Plataforma de ventas")
                    ]
                    else order_id
                    if column == columns[
                        normalize("TiendaNube Order ID")
                    ]
                    else order_number
                    if column == columns[
                        normalize("TiendaNube Order Number")
                    ]
                    else sync_time
                    if column == columns[
                        normalize("Última sincronización")
                    ]
                    else ""
                )
                for column in range(1, total_columns + 1)
            ]
        )

    if old_rows:
        # Las filas propias pueden no ser contiguas. Nunca se borra el rango
        # entre ellas porque podría contener una venta manual de otro cliente.
        target_rows = sorted(set(old_rows))
        start_row = min(target_rows)

        if len(rows_to_write) > len(target_rows):
            insert_empty_order_rows(
                worksheet,
                row=max(target_rows) + 1,
                count=len(rows_to_write) - len(target_rows),
                total_columns=total_columns,
            )
            target_rows.extend(
                range(
                    max(target_rows) + 1,
                    max(target_rows) + 1
                    + len(rows_to_write) - len(target_rows),
                )
            )

        elif len(rows_to_write) < len(target_rows):
            # Si Tiendanube quitó líneas, se eliminan solo las filas que
            # pertenecen a esta orden, de abajo hacia arriba.
            for row_number in reversed(target_rows[len(rows_to_write):]):
                worksheet.delete_rows(row_number)
            target_rows = target_rows[:len(rows_to_write)]
    else:
        values = worksheet.get_all_values()

        order_column = columns[normalize("Orden")] - 1
        last_data_row = 1  # encabezado

        for row_index, row in enumerate(values[1:], start=2):
            if (
                len(row) > order_column
                and str(row[order_column]).strip()
            ):
                last_data_row = row_index

        start_row = last_data_row + 1

        # La primera fila posterior puede ser un total o contenido manual.
        # Se desplaza antes de escribir, en vez de pisarla.
        row_has_content = (
            start_row <= len(values)
            and any(str(cell).strip() for cell in values[start_row - 1])
        )

        if row_has_content:
            insert_empty_order_rows(
                worksheet,
                row=start_row,
                count=len(rows_to_write),
                total_columns=total_columns,
            )

        end_required = start_row + len(rows_to_write) - 1

        if end_required > worksheet.row_count:
            worksheet.add_rows(
                end_required - worksheet.row_count + 20
            )

        target_rows = list(range(start_row, end_required + 1))

    write_rows_at_positions(
        worksheet,
        target_rows,
        rows_to_write,
        total_columns,
    )

    return {
        "sheet": worksheet.title,
        "order_id": order_id,
        "order_number": order_number,
        "rows": len(rows_to_write),
        "status": status,
    }


def synchronize_stock_for_order(
    order: dict[str, Any],
) -> list[dict[str, Any]]:
    lines = (
        order.get("products")
        or order.get("items")
        or order.get("line_items")
        or []
    )

    if not isinstance(lines, list):
        return []

    worksheet, _, columns, products = read_products()
    sheet_by_variant = {
        str(product.variant_id): product
        for product in products
        if product.variant_id
    }

    synchronized = []
    processed_variants = set()

    for line in lines:
        if not isinstance(line, dict):
            continue

        product_id = line_product_id(line)
        variant_id = line_variant_id(line)

        if (
            not product_id
            or not variant_id
            or variant_id in processed_variants
        ):
            continue

        processed_variants.add(variant_id)
        sheet_product = sheet_by_variant.get(variant_id)

        if not sheet_product:
            synchronized.append(
                {
                    "variant_id": variant_id,
                    "ok": False,
                    "error": "No existe en Productos.",
                }
            )
            continue

        product = get_product(product_id)
        variant = get_variant(product_id, variant_id)
        product_name = localized(product.get("name"))

        data = synchronize_variant_to_sheet(
            worksheet,
            columns,
            sheet_product.row,
            product_name=product_name,
            variant=variant,
        )

        synchronized.append(
            {
                "variant_id": variant_id,
                "stock": data["Stock"],
                "ok": True,
            }
        )

    return synchronized


def save_order_snapshot(
    order: dict[str, Any],
) -> None:
    initialize_database()

    order_id = str(order.get("id") or "")
    order_number = str(
        order.get("number")
        or order.get("order_number")
        or ""
    )
    status = str(order.get("status") or "")

    with sqlite3.connect(EVENT_DATABASE) as connection:
        connection.execute(
            """
            INSERT INTO synchronized_orders (
                order_id,
                order_number,
                order_status,
                updated_at,
                payload_json
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(order_id) DO UPDATE SET
                order_number = excluded.order_number,
                order_status = excluded.order_status,
                updated_at = excluded.updated_at,
                payload_json = excluded.payload_json
            """,
            (
                order_id,
                order_number,
                status,
                datetime.now().isoformat(timespec="seconds"),
                json.dumps(
                    order,
                    ensure_ascii=False,
                    default=str,
                ),
            ),
        )

        connection.commit()


def get_order(order_id: Any) -> dict[str, Any]:
    order = api_request(
        "GET",
        f"orders/{order_id}",
    )

    if not isinstance(order, dict):
        raise SyncError(
            f"Respuesta inválida al consultar la orden {order_id}."
        )

    return order


def order_payment_status(
    order: dict[str, Any],
) -> str:
    payment_status = (
        order.get("payment_status")
        or order.get("paymentStatus")
        or ""
    )

    return normalize(str(payment_status))


def order_is_paid(
    order: dict[str, Any],
) -> bool:
    if order.get("paid_at"):
        return True

    return order_payment_status(order) == "paid"


def previous_order_snapshot(
    order_id: Any,
) -> dict[str, Any] | None:
    initialize_database()

    with sqlite3.connect(EVENT_DATABASE) as connection:
        row = connection.execute(
            """
            SELECT payload_json
            FROM synchronized_orders
            WHERE order_id = ?
            """,
            (str(order_id),),
        ).fetchone()

    if not row or not row[0]:
        return None

    try:
        payload = json.loads(row[0])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None

    return payload if isinstance(payload, dict) else None


def order_was_paid(
    order_id: Any,
) -> bool:
    snapshot = previous_order_snapshot(order_id)

    if not snapshot:
        return False

    return order_is_paid(snapshot)


def order_revision(
    order: dict[str, Any],
) -> str:
    revision = (
        order.get("updated_at")
        or order.get("modified_at")
        or order.get("paid_at")
        or order.get("cancelled_at")
        or ""
    )

    payment_status = order_payment_status(order)
    status = normalize(str(order.get("status") or ""))
    fulfillment_status = normalize(
        str(
            order.get("fulfillment_status")
            or order.get("shipping_status")
            or ""
        )
    )

    return (
        f"{revision}:"
        f"{payment_status}:"
        f"{status}:"
        f"{fulfillment_status}"
    )


def run_with_sheets_retry(
    operation,
    *,
    attempts: int = 5,
):
    import time

    delay = 5

    for attempt in range(1, attempts + 1):
        try:
            return operation()

        except gspread.exceptions.APIError as error:
            response = getattr(error, "response", None)
            status_code = getattr(response, "status_code", None)

            is_quota_error = (
                status_code == 429
                or "429" in str(error)
                or "quota exceeded" in str(error).lower()
            )

            if not is_quota_error or attempt >= attempts:
                raise

            print(
                "Google Sheets alcanzó la cuota. "
                f"Reintento {attempt}/{attempts} "
                f"en {delay} segundos.",
                flush=True,
            )

            time.sleep(delay)
            delay *= 2


def synchronize_order(
    order: dict[str, Any],
    event: str,
    *,
    previously_paid: bool,
) -> dict[str, Any]:
    currently_paid = order_is_paid(order)

    # Una orden recién creada todavía no debe registrarse.
    if event == "order/created":
        return {
            "ok": True,
            "ignored": True,
            "reason": "La orden todavía no fue pagada.",
        }

    # No se escribe una venta pendiente o rechazada.
    if (
        event in {"order/updated", "order/edited"}
        and not currently_paid
        and not previously_paid
    ):
        save_order_snapshot(order)

        return {
            "ok": True,
            "ignored": True,
            "reason": "La orden no está pagada.",
        }

    # Una cancelación solo modifica ventas que ya existían.
    if (
        event == "order/cancelled"
        and not previously_paid
        and not currently_paid
    ):
        save_order_snapshot(order)

        return {
            "ok": True,
            "ignored": True,
            "reason": "La orden cancelada nunca fue registrada.",
        }

    sale_result = run_with_sheets_retry(
        lambda: write_order_rows(order)
    )

    stock_result: list[dict[str, Any]] = []

    # El stock solo requiere sincronización al pagar,
    # editar productos o cancelar la orden.
    if event in {
        "order/paid",
        "order/edited",
        "order/cancelled",
    }:
        stock_result = run_with_sheets_retry(
            lambda: synchronize_stock_for_order(order)
        )

    save_order_snapshot(order)

    return {
        "ok": True,
        "event": event,
        "paid": currently_paid,
        "sale": sale_result,
        "stock": stock_result,
    }


def process_webhook(
    store_id: Any,
    event: Any,
    resource_id: Any,
) -> dict[str, Any]:
    event = str(event)

    allowed_events = {
        "order/created",
        "order/updated",
        "order/paid",
        "order/cancelled",
        "order/edited",
    }

    if event not in allowed_events:
        return {
            "ok": True,
            "ignored": True,
            "event": event,
        }

    # order/created se confirma sin consultar ni tocar Sheets.
    if event == "order/created":
        if not begin_event(
            store_id,
            event,
            resource_id,
        ):
            return {
                "ok": True,
                "duplicate": True,
            }

        try:
            result = {
                "ok": True,
                "ignored": True,
                "event": event,
                "reason": "La venta se registrará cuando esté pagada.",
            }

            finish_event(
                store_id,
                event,
                resource_id,
            )

            return result

        except Exception as error:
            fail_event(
                store_id,
                event,
                resource_id,
                error,
            )
            raise

    # Se consulta una sola vez la orden por evento.
    order = get_order(resource_id)
    previously_paid = order_was_paid(resource_id)

    # Tiendanube puede enviar más de un order/updated para la
    # misma orden. La revisión permite procesar cambios nuevos,
    # pero ignorar reintentos idénticos.
    event_identity = (
        f"{event}@{order_revision(order)}"
    )

    if not begin_event(
        store_id,
        event_identity,
        resource_id,
    ):
        return {
            "ok": True,
            "duplicate": True,
            "event": event,
        }

    try:
        result = synchronize_order(
            order,
            event,
            previously_paid=previously_paid,
        )

        finish_event(
            store_id,
            event_identity,
            resource_id,
        )

        return result

    except Exception as error:
        fail_event(
            store_id,
            event_identity,
            resource_id,
            error,
        )
        raise
