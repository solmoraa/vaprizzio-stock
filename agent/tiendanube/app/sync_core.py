#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fcntl

import gspread
import requests
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials


WORKSPACE = Path(
    "/home/openclaw/.openclaw/workspace/vaprizziobot"
)

TN_DIR = WORKSPACE / "tiendanube"
ENV_FILE = TN_DIR / "credentials" / ".env"
SHEET_ID_FILE = WORKSPACE / "google_sheet_id.txt"

load_dotenv(ENV_FILE)

API_BASE = os.environ.get(
    "TN_API_BASE",
    "https://api.tiendanube.com/v1",
).rstrip("/")

USER_AGENT = os.environ["TN_USER_AGENT"]

DATABASE_PATH = Path(
    os.environ.get(
        "TN_DATABASE",
        str(TN_DIR / "data" / "tiendanube.sqlite"),
    )
)

PRODUCT_SHEET = "Productos"

# Google Sheets limita las solicitudes por cuenta de servicio. El agente puede
# recibir varios mensajes a la vez (ventas, stock y sincronizaciones), por lo
# que todas las lecturas y escrituras pasan por este turno compartido. Así no
# se superponen procesos ni se reintenta una misma operación sin control.
SHEETS_LOCK_FILE = Path("/tmp/vaprizzio-google-sheets.lock")
SHEETS_LAST_CALL_FILE = Path(
    "/tmp/vaprizzio-google-sheets-last-call.txt"
)
SHEETS_MIN_SECONDS_BETWEEN_CALLS = 1.25

HEADERS = [
    "Marca",
    "Sabor",
    "Stock",
    "Costo",
    "Precio venta",
    "Ganancia",
    "TiendaNube Product ID",
    "TiendaNube Variant ID",
    "TiendaNube Location ID",
    "TiendaNube SKU",
    "Estado Sync",
]

CREDENTIAL_CANDIDATES = [
    WORKSPACE / "credentials" / "vaprizzio-service-account.json",
    WORKSPACE / "credentials" / "vaprizzio-7c0d5b8328cf.json",
    Path("/home/openclaw/vaprizzio-7c0d5b8328cf.json"),
]


class SyncError(RuntimeError):
    pass


def _sheets_retryable(error: Exception) -> bool:
    message = str(error).casefold()
    return any(
        text in message
        for text in (
            "[429]",
            "quota exceeded",
            "resource_exhausted",
            "ratelimitexceeded",
            "rate limit",
            "[500]",
            "[502]",
            "[503]",
            "[504]",
        )
    )


@contextmanager
def _sheets_turn():
    """Serializa llamadas a Sheets incluso entre procesos distintos."""
    SHEETS_LOCK_FILE.touch(exist_ok=True)
    with SHEETS_LOCK_FILE.open("r+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _last_sheets_call() -> float:
    try:
        return float(SHEETS_LAST_CALL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0.0


def _mark_sheets_call() -> None:
    SHEETS_LAST_CALL_FILE.write_text(
        str(time.monotonic()),
        encoding="utf-8",
    )


def run_sheets_request(operation, *, description: str):
    """Ejecuta una llamada de Sheets espaciada y con reintentos seguros.

    Se reintenta únicamente la llamada a Google Sheets; no se repiten cambios
    ya enviados a Tiendanube. Un batch_update es idempotente porque escribe
    los valores finales de cada celda.
    """
    # Dos ventanas completas de cuota mantienen la operación debajo del límite
    # de ejecución del agente, sin volver a bombardear a Google.
    delays = (65, 70)

    with _sheets_turn():
        for attempt in range(len(delays) + 1):
            wait = (
                SHEETS_MIN_SECONDS_BETWEEN_CALLS
                - (time.monotonic() - _last_sheets_call())
            )
            if wait > 0:
                time.sleep(wait)

            try:
                result = operation()
                _mark_sheets_call()
                return result
            except Exception as error:
                # La solicitud igualmente cuenta para Google aunque responda
                # con error, por eso se registra antes de esperar el retry.
                _mark_sheets_call()
                if not _sheets_retryable(error) or attempt == len(delays):
                    raise
                time.sleep(delays[attempt])

    raise SyncError(f"No se pudo completar {description} en Google Sheets.")


@dataclass
class SheetProduct:
    row: int
    marca: str
    sabor: str
    stock: int | None
    costo: float | None
    precio: float | None
    ganancia: float | None
    product_id: str
    variant_id: str
    location_id: str
    sku: str
    estado: str


def normalize(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(
        char
        for char in text
        if not unicodedata.combining(char)
    )
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def localized_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(
            value.get("es")
            or value.get("pt")
            or value.get("en")
            or next(iter(value.values()), "")
        )

    return str(value or "")


def parse_float(value: Any) -> float | None:
    if value in (None, ""):
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    text = text.replace("$", "").replace(" ", "")

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        parts = text.split(",")

        if len(parts[-1]) <= 2:
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "." in text:
        parts = text.split(".")

        if len(parts[-1]) == 3:
            text = text.replace(".", "")

    try:
        return float(text)
    except ValueError:
        return None


def parse_int(value: Any) -> int | None:
    number = parse_float(value)

    if number is None:
        return None

    return int(number)


def money_output(value: Any) -> float | None:
    number = parse_float(value)

    if number is None:
        return None

    return round(number, 2)


def credential_path() -> Path:
    for candidate in CREDENTIAL_CANDIDATES:
        if candidate.exists():
            return candidate

    raise SyncError(
        "No se encontró el JSON de la cuenta de servicio de Google."
    )


def spreadsheet():
    if not SHEET_ID_FILE.exists():
        raise SyncError("No existe google_sheet_id.txt.")

    sheet_id = SHEET_ID_FILE.read_text(
        encoding="utf-8"
    ).strip()

    credentials = Credentials.from_service_account_file(
        credential_path(),
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )

    client = gspread.authorize(credentials)
    return client.open_by_key(sheet_id)


def products_worksheet():
    return spreadsheet().worksheet(PRODUCT_SHEET)


def store_credentials() -> tuple[int, str]:
    if not DATABASE_PATH.exists():
        raise SyncError(
            "No existe la base SQLite de Tiendanube."
        )

    with sqlite3.connect(DATABASE_PATH) as connection:
        row = connection.execute(
            """
            SELECT store_id, access_token
            FROM stores
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()

    if not row:
        raise SyncError(
            "No hay ninguna tienda conectada en SQLite."
        )

    return int(row[0]), str(row[1])


def api_headers() -> dict[str, str]:
    _, access_token = store_credentials()

    return {
        "Authorization": f"Bearer {access_token}",
        "Authentication": f"bearer {access_token}",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def api_request(
    method: str,
    path: str,
    *,
    payload: Any = None,
    params: dict[str, Any] | None = None,
) -> Any:
    store_id, _ = store_credentials()

    url = f"{API_BASE}/{store_id}/{path.lstrip('/')}"

    last_error: Exception | None = None

    for attempt in range(5):
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=api_headers(),
                json=payload,
                params=params,
                timeout=35,
            )

            if response.status_code == 429:
                wait = min(2 ** attempt, 12)
                time.sleep(wait)
                continue

            if not response.ok:
                try:
                    detail = response.json()
                except ValueError:
                    detail = response.text[:1500]

                raise SyncError(
                    f"Tiendanube respondió HTTP "
                    f"{response.status_code}: {detail}"
                )

            if not response.content:
                return {}

            try:
                return response.json()
            except ValueError:
                return response.text

        except requests.RequestException as error:
            last_error = error

            if attempt == 4:
                break

            time.sleep(min(2 ** attempt, 12))

    raise SyncError(
        f"No se pudo conectar con Tiendanube: {last_error}"
    )


def find_header_row(values: list[list[str]]) -> int:
    required = {
        normalize("Marca"),
        normalize("Sabor"),
        normalize("Stock"),
        normalize("TiendaNube Product ID"),
        normalize("TiendaNube Variant ID"),
    }

    for row_number, row in enumerate(values, start=1):
        normalized = {
            normalize(cell)
            for cell in row
            if str(cell).strip()
        }

        if required.issubset(normalized):
            return row_number

    raise SyncError(
        "No se encontró la cabecera correcta en Productos."
    )


def header_map(
    values: list[list[str]],
    header_row: int,
) -> dict[str, int]:
    row = values[header_row - 1]

    result: dict[str, int] = {}

    for index, value in enumerate(row, start=1):
        key = normalize(value)

        if key:
            # La hoja Productos también contiene una tabla auxiliar a la
            # derecha con encabezados repetidos (por ejemplo, "Marca").
            # Para leer el catálogo principal siempre debe ganar la primera
            # aparición del encabezado, no la última.
            result.setdefault(key, index)

    return result


def cell(
    row: list[str],
    column: int | None,
) -> str:
    if not column:
        return ""

    index = column - 1

    if index >= len(row):
        return ""

    return str(row[index]).strip()


def read_products() -> tuple[
    gspread.Worksheet,
    int,
    dict[str, int],
    list[SheetProduct],
]:
    def read_values():
        worksheet = products_worksheet()
        return worksheet, worksheet.get_all_values()

    worksheet, values = run_sheets_request(
        read_values,
        description="la lectura del catálogo Productos",
    )
    header_row = find_header_row(values)
    columns = header_map(values, header_row)

    def col(name: str) -> int:
        key = normalize(name)

        if key not in columns:
            raise SyncError(
                f"Falta la columna obligatoria: {name}"
            )

        return columns[key]

    products: list[SheetProduct] = []

    for row_number, row in enumerate(
        values[header_row:],
        start=header_row + 1,
    ):
        marca = cell(row, col("Marca"))
        sabor = cell(row, col("Sabor"))

        if not marca and not sabor:
            continue

        products.append(
            SheetProduct(
                row=row_number,
                marca=marca,
                sabor=sabor,
                stock=parse_int(
                    cell(row, col("Stock"))
                ),
                costo=parse_float(
                    cell(row, col("Costo"))
                ),
                precio=parse_float(
                    cell(row, col("Precio venta"))
                ),
                ganancia=parse_float(
                    cell(row, col("Ganancia"))
                ),
                product_id=cell(
                    row,
                    col("TiendaNube Product ID"),
                ),
                variant_id=cell(
                    row,
                    col("TiendaNube Variant ID"),
                ),
                location_id=cell(
                    row,
                    col("TiendaNube Location ID"),
                ),
                sku=cell(
                    row,
                    col("TiendaNube SKU"),
                ),
                estado=cell(
                    row,
                    col("Estado Sync"),
                ),
            )
        )

    return worksheet, header_row, columns, products


def find_sheet_product(
    marca: str,
    sabor: str,
) -> tuple[
    gspread.Worksheet,
    dict[str, int],
    SheetProduct,
]:
    worksheet, _, columns, products = read_products()
    product = find_sheet_product_in_catalog(products, marca, sabor)
    return worksheet, columns, product


def find_sheet_product_in_catalog(
    products: list[SheetProduct],
    marca: str,
    sabor: str,
) -> SheetProduct:
    """Busca una variante dentro de un catálogo ya cargado una sola vez."""
    marca_n = normalize(marca)
    sabor_n = normalize(sabor)

    exact = [
        product
        for product in products
        if normalize(product.marca) == marca_n
        and normalize(product.sabor) == sabor_n
    ]

    if len(exact) == 1:
        return exact[0]

    flexible = [
        product
        for product in products
        if (
            marca_n in normalize(product.marca)
            or normalize(product.marca) in marca_n
        )
        and (
            sabor_n in normalize(product.sabor)
            or normalize(product.sabor) in sabor_n
        )
    ]

    if len(flexible) == 1:
        return flexible[0]

    if not flexible:
        raise SyncError(
            f"No encontré {marca} / {sabor} en Productos."
        )

    options = [
        f"{item.marca} / {item.sabor}"
        for item in flexible[:10]
    ]

    raise SyncError(
        "Hay más de una coincidencia: "
        + "; ".join(options)
    )


def sheet_product_context() -> tuple[
    gspread.Worksheet,
    dict[str, int],
    list[SheetProduct],
]:
    """Carga Productos una sola vez para una operación administrativa.

    Una venta ya valida cada modelo contra este catálogo. Descargar la misma
    hoja otra vez por cada variante no agrega seguridad y puede agotar la cuota
    de Google Sheets.
    """
    worksheet, _, columns, products = read_products()
    return worksheet, columns, products


def sheet_updates(
    columns: dict[str, int],
    row: int,
    values: dict[str, Any],
) -> list[dict[str, Any]]:
    updates = []

    for header, value in values.items():
        column = columns.get(normalize(header))

        if not column:
            continue

        updates.append(
            {
                "range": gspread.utils.rowcol_to_a1(row, column),
                "values": [[value]],
            }
        )

    return updates


def update_sheet_rows(
    worksheet: gspread.Worksheet,
    columns: dict[str, int],
    rows: list[tuple[int, dict[str, Any]]],
) -> None:
    updates = [
        update
        for row, values in rows
        for update in sheet_updates(columns, row, values)
    ]

    if updates:
        run_sheets_request(
            lambda: worksheet.batch_update(
                updates,
                value_input_option="USER_ENTERED",
            ),
            description="la actualización del catálogo Productos",
        )


def update_sheet_cells(
    worksheet: gspread.Worksheet,
    columns: dict[str, int],
    row: int,
    values: dict[str, Any],
) -> None:
    update_sheet_rows(worksheet, columns, [(row, values)])


def variant_flavor(variant: dict[str, Any]) -> str:
    parts = []

    for value in variant.get("values") or []:
        text = localized_text(value).strip()

        if text:
            parts.append(text)

    return " / ".join(parts)


def total_stock(variant: dict[str, Any]) -> int | None:
    levels = variant.get("inventory_levels") or []

    if levels:
        stocks = [
            parse_int(level.get("stock"))
            for level in levels
            if level.get("stock") not in (None, "")
        ]

        stocks = [
            stock
            for stock in stocks
            if stock is not None
        ]

        if stocks:
            return sum(stocks)

    return parse_int(variant.get("stock"))


def variant_location_ids(
    variant: dict[str, Any],
) -> str:
    levels = variant.get("inventory_levels") or []

    return ",".join(
        str(level.get("location_id"))
        for level in levels
        if level.get("location_id")
    )


def get_product(product_id: str) -> dict[str, Any]:
    return api_request(
        "GET",
        f"products/{product_id}",
    )


def get_variant(
    product_id: str,
    variant_id: str,
) -> dict[str, Any]:
    return api_request(
        "GET",
        f"products/{product_id}/variants/{variant_id}",
    )


def synchronize_variant_to_sheet(
    worksheet: gspread.Worksheet,
    columns: dict[str, int],
    row: int,
    *,
    product_name: str,
    variant: dict[str, Any],
    current_cost: float | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Sincroniza datos comerciales sin pisar el costo local.

    Tiendanube no es la fuente de verdad del costo. El costo en pesos se
    calcula desde la tabla auxiliar USDT de Google Sheets. Por eso una
    sincronización de stock, precio, SKU o nombre nunca debe copiar
    ``variant.cost`` sobre la columna Costo.
    """
    price = money_output(variant.get("price"))
    stock = total_stock(variant)

    # La operación de venta ya conoce el costo de Productos. Reutilizarlo evita
    # otra lectura de Google; los demás flujos mantienen el comportamiento
    # anterior cuando no lo proveen.
    if current_cost is None:
        current_row = run_sheets_request(
            lambda: worksheet.row_values(row),
            description="la lectura de una fila de Productos",
        )
        cost_column = columns.get(normalize("Costo"))
        current_cost = parse_float(cell(current_row, cost_column))

    gain = None
    if price is not None and current_cost is not None:
        gain = round(price - current_cost, 2)

    data = {
        "Marca": product_name,
        "Sabor": variant_flavor(variant),
        "Stock": "" if stock is None else stock,
        "Costo": "" if current_cost is None else current_cost,
        "Precio venta": "" if price is None else price,
        "Ganancia": "" if gain is None else gain,
        "TiendaNube Product ID": str(
            variant.get("product_id") or ""
        ),
        "TiendaNube Variant ID": str(
            variant.get("id") or ""
        ),
        "TiendaNube Location ID": (
            variant_location_ids(variant)
        ),
        "TiendaNube SKU": str(
            variant.get("sku") or ""
        ),
        "Estado Sync": "SINCRONIZADO",
    }

    if write:
        update_sheet_cells(
            worksheet,
            columns,
            row,
            data,
        )

    return data


def stock_payload_for_variant(
    variant: dict[str, Any],
    stock: int,
    selected_location: str | None = None,
) -> dict[str, Any]:
    """
    Construye el payload de stock usando inventory_levels.

    Usa una Location ID real de la variante. Tolera que una sincronizacion
    antigua haya guardado varias IDs separadas por comas.
    """
    if stock < 0:
        raise SyncError(
            "El stock no puede ser negativo."
        )

    levels = variant.get("inventory_levels") or []

    if levels:
        location_id = _location_id_for_variation(
            variant,
            selected_location,
            0,
        )

        payload_level: dict[str, Any] = {
            "stock": stock,
        }

        if location_id:
            payload_level["location_id"] = str(
                location_id
            )

        return {
            "inventory_levels": [
                payload_level
            ]
        }

    # Para variantes antiguas que todavía no devuelven
    # inventory_levels, Tiendanube conserva compatibilidad.
    return {
        "stock": stock,
    }


def _location_id_for_variation(
    variant: dict[str, Any],
    selected_location: str | None,
    variation: int,
) -> str | None:
    """Elige una ubicacion valida sin depender de IDs concatenados en Sheets.

    La sincronizacion historica guardaba todas las ubicaciones separadas por
    comas. Ese valor no es un Location ID valido y Tiendanube lo rechaza. Si no
    hay una unica preferencia, se elige una ubicacion real de la variante. Para
    descuentos se prioriza una que tenga stock suficiente.
    """
    levels = variant.get("inventory_levels") or []

    if not levels:
        return None

    available = {
        str(level.get("location_id") or "").strip(): level
        for level in levels
        if str(level.get("location_id") or "").strip()
    }
    configured = [
        token
        for token in re.split(
            r"[,;\s]+",
            str(selected_location or "").strip(),
        )
        if token in available
    ]

    if len(set(configured)) == 1:
        return configured[0]

    if variation < 0:
        required = abs(variation)
        sufficient = [
            level
            for level in levels
            if (parse_int(level.get("stock")) or 0) >= required
            and level.get("location_id")
        ]

        if sufficient:
            return str(sufficient[0]["location_id"])

        positive = [
            level
            for level in levels
            if (parse_int(level.get("stock")) or 0) > 0
            and level.get("location_id")
        ]

        if positive:
            return str(positive[0]["location_id"])

    first_location = levels[0].get("location_id")
    return str(first_location) if first_location else None


def _live_product_and_variant(
    sheet_product: SheetProduct,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resuelve y repara IDs usando modelo + sabor exactos.

    Los IDs de Google Sheets son una cache. Si faltan o quedaron viejos, nunca
    se debe concluir que el producto no existe: se consulta Tiendanube y se
    selecciona una sola variante con coincidencia exacta. El SKU es opcional.
    """
    expected_model = normalize(sheet_product.marca)
    expected_flavor = normalize(sheet_product.sabor)

    if sheet_product.product_id and sheet_product.variant_id:
        try:
            product = get_product(sheet_product.product_id)
            variant = get_variant(
                sheet_product.product_id,
                sheet_product.variant_id,
            )
            live_model = normalize(
                localized_text(product.get("name"))
            )
            live_flavor = normalize(variant_flavor(variant))

            if (
                live_model == expected_model
                and live_flavor == expected_flavor
            ):
                return product, variant
        except Exception:
            # Los IDs se reparan mediante la busqueda exacta siguiente.
            pass

    product = find_product_by_name(
        sheet_product.marca,
        exact_only=True,
    )

    if not product:
        raise SyncError(
            f"No existe el modelo exacto {sheet_product.marca!r} "
            "en Tiendanube."
        )

    product_id = str(product.get("id") or "").strip()

    if not product_id:
        raise SyncError(
            f"El modelo {sheet_product.marca!r} no tiene Product ID."
        )

    product = get_product(product_id)
    variants = product.get("variants") or []
    matches = [
        variant
        for variant in variants
        if normalize(variant_flavor(variant)) == expected_flavor
    ]

    if len(matches) != 1:
        available = ", ".join(
            variant_flavor(variant)
            for variant in variants
            if variant_flavor(variant)
        )
        raise SyncError(
            f"No pude identificar una unica variante exacta para "
            f"{sheet_product.marca} / {sheet_product.sabor}. "
            f"Sabores visibles en Tiendanube: {available or 'ninguno'}."
        )

    variant = dict(matches[0])
    variant.setdefault("product_id", product_id)
    return product, variant




def modify_existing_product(
    *,
    marca: str,
    sabor: str,
    stock: int | None = None,
    add_stock: int | None = None,
    subtract_stock: int | None = None,
    cost: float | None = None,
    price: float | None = None,
    new_flavor: str | None = None,
    new_model: str | None = None,
    sku: str | None = None,
    sheet_context: tuple[
        gspread.Worksheet,
        dict[str, int],
        SheetProduct,
    ] | None = None,
    pending_sheet_updates: list[tuple[int, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    if sheet_context is None:
        worksheet, columns, sheet_product = find_sheet_product(
            marca,
            sabor,
        )
    else:
        worksheet, columns, sheet_product = sheet_context

    product, variant = _live_product_and_variant(sheet_product)
    product_id = str(
        product.get("id")
        or variant.get("product_id")
        or ""
    ).strip()
    variant_id = str(variant.get("id") or "").strip()

    if not product_id or not variant_id:
        raise SyncError(
            "Tiendanube no devolvio los IDs del producto y su variante."
        )

    payload: dict[str, Any] = {}

    current_stock = total_stock(variant)

    if stock is not None:
        if stock < 0:
            raise SyncError(
                "El stock no puede ser negativo."
            )

        payload.update(
            stock_payload_for_variant(
                variant,
                stock,
                sheet_product.location_id,
            )
        )

    variations = [
        value
        for value in (add_stock, subtract_stock)
        if value is not None
    ]

    if len(variations) > 1:
        raise SyncError(
            "No se puede sumar y restar stock en la misma operacion."
        )

    stock_variation = None

    if add_stock is not None:
        stock_variation = int(add_stock)

    if subtract_stock is not None:
        stock_variation = -int(subtract_stock)

    if stock is not None and stock_variation is not None:
        raise SyncError(
            "No se puede fijar y variar el stock en la misma operacion."
        )

    if cost is not None:
        if cost <= 0:
            raise SyncError(
                "El costo debe ser mayor que cero."
            )

        payload["cost"] = str(cost)

    if price is not None:
        if price <= 0:
            raise SyncError(
                "El precio debe ser mayor que cero."
            )

        payload["price"] = str(price)

    if new_flavor:
        current_values = variant.get("values") or []

        if current_values:
            language = (
                next(iter(current_values[0].keys()), "es")
            )

            payload["values"] = [
                {
                    language: new_flavor,
                }
            ]
        else:
            payload["values"] = [
                {
                    "es": new_flavor,
                }
            ]

    if sku is not None:
        payload["sku"] = sku

    if not payload and not new_model:
        if stock_variation is None:
            raise SyncError(
                "No se indicó ningún dato para modificar."
            )

    if stock_variation is not None:
        variation_payload: dict[str, Any] = {
            "action": "variation",
            "value": stock_variation,
            "id": variant_id,
        }
        location_id = _location_id_for_variation(
            variant,
            sheet_product.location_id,
            stock_variation,
        )

        if location_id:
            variation_payload["location_id"] = location_id

        # La operacion atomica evita calcular un stock final con datos viejos y
        # funciona aunque la variante no tenga SKU.
        api_request(
            "POST",
            f"products/{product_id}/variants/stock",
            payload=variation_payload,
        )

    if payload:
        variant = api_request(
            "PUT",
            (
                f"products/{product_id}/"
                f"variants/{variant_id}"
            ),
            payload=payload,
        )

    product_name = localized_text(
        product.get("name")
    ).strip()

    if new_model:
        product = api_request(
            "PUT",
            f"products/{product_id}",
            payload={
                "name": {
                    "es": new_model,
                }
            },
        )

        product_name = localized_text(
            product.get("name")
        ).strip()

    variant = get_variant(
        product_id,
        variant_id,
    )
    variant.setdefault("product_id", product_id)

    sheet_data = synchronize_variant_to_sheet(
        worksheet,
        columns,
        sheet_product.row,
        product_name=product_name,
        variant=variant,
        current_cost=getattr(sheet_product, "costo", None),
        write=pending_sheet_updates is None,
    )

    if pending_sheet_updates is not None:
        pending_sheet_updates.append(
            (sheet_product.row, sheet_data)
        )

    return {
        "ok": True,
        "mensaje": "Producto sincronizado correctamente.",
        "marca": sheet_data["Marca"],
        "sabor": sheet_data["Sabor"],
        "stock_anterior": current_stock,
        "stock": sheet_data["Stock"],
        "costo": sheet_data["Costo"],
        "precio": sheet_data["Precio venta"],
        "ganancia": sheet_data["Ganancia"],
        "product_id": sheet_data[
            "TiendaNube Product ID"
        ],
        "variant_id": sheet_data[
            "TiendaNube Variant ID"
        ],
    }


def _batch_stock_value(
    change: dict[str, Any],
    *names: str,
) -> int | None:
    values = [
        change[name]
        for name in names
        if change.get(name) is not None
    ]

    if not values:
        return None

    if len(values) > 1:
        raise SyncError(
            "Cada variante debe indicar una sola cantidad de stock."
        )

    try:
        value = int(values[0])
    except (TypeError, ValueError) as error:
        raise SyncError(
            "Las cantidades de stock deben ser números enteros."
        ) from error

    if value < 0:
        raise SyncError(
            "Las cantidades de stock no pueden ser negativas."
        )

    return value


def modify_existing_products(
    changes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Actualiza varias variantes usando una lectura y una escritura Sheets.

    Las mutaciones de Tiendanube continúan siendo una por variante, porque son
    atómicas por variante. La hoja, en cambio, se actualiza juntas una vez que
    se conocen todos los valores finales. Si Google responde 429, únicamente
    se reintenta esa escritura idempotente: no se repiten descuentos ni sumas.
    """
    if not isinstance(changes, list) or not changes:
        raise SyncError("Debe indicar al menos una variante para actualizar.")

    worksheet, columns, products = sheet_product_context()
    prepared: list[tuple[dict[str, Any], SheetProduct]] = []
    used_rows: set[int] = set()

    # Validar y resolver todo antes de modificar Tiendanube. Esto evita que un
    # sabor mal escrito deje a medias un lote que sí era válido.
    for index, raw_change in enumerate(changes, start=1):
        if not isinstance(raw_change, dict):
            raise SyncError(
                f"La variante {index} debe tener modelo, sabor y cantidad."
            )

        change = dict(raw_change)
        marca = str(
            change.get("marca")
            or change.get("modelo")
            or change.get("vape")
            or ""
        ).strip()
        sabor = str(change.get("sabor") or "").strip()

        if not marca or not sabor:
            raise SyncError(
                f"Falta el modelo o sabor de la variante {index}."
            )

        # Se aceptan las dos formas que usa el lenguaje del agente.
        stock = _batch_stock_value(change, "stock")
        add_stock = _batch_stock_value(
            change,
            "add_stock",
            "sumar_stock",
            "agregar_stock",
        )
        subtract_stock = _batch_stock_value(
            change,
            "subtract_stock",
            "restar_stock",
            "quitar_stock",
        )

        if sum(value is not None for value in (
            stock,
            add_stock,
            subtract_stock,
        )) != 1:
            raise SyncError(
                f"La variante {index} debe fijar, sumar o restar stock, "
                "pero no más de una opción."
            )

        sheet_product = find_sheet_product_in_catalog(
            products,
            marca,
            sabor,
        )

        if sheet_product.row in used_rows:
            raise SyncError(
                f"La variante {marca} / {sabor} está repetida en el lote."
            )

        used_rows.add(sheet_product.row)
        change["marca"] = marca
        change["sabor"] = sabor
        change["stock"] = stock
        change["add_stock"] = add_stock
        change["subtract_stock"] = subtract_stock
        prepared.append((change, sheet_product))

    pending_sheet_updates: list[tuple[int, dict[str, Any]]] = []
    results: list[dict[str, Any]] = []

    try:
        for change, sheet_product in prepared:
            result = modify_existing_product(
                marca=change["marca"],
                sabor=change["sabor"],
                stock=change["stock"],
                add_stock=change["add_stock"],
                subtract_stock=change["subtract_stock"],
                sheet_context=(worksheet, columns, sheet_product),
                pending_sheet_updates=pending_sheet_updates,
            )
            results.append(result)
    except Exception:
        # Si Tiendanube aceptó una parte antes de un error posterior, dejar la
        # hoja consistente con esa parte y no inducir al usuario a repetirla.
        if pending_sheet_updates:
            update_sheet_rows(
                worksheet,
                columns,
                pending_sheet_updates,
            )
        raise

    update_sheet_rows(
        worksheet,
        columns,
        pending_sheet_updates,
    )

    return {
        "ok": True,
        "mensaje": "Stock de las variantes sincronizado correctamente.",
        "productos": results,
    }


def find_product_by_name(
    model: str,
    *,
    exact_only: bool = False,
) -> dict[str, Any] | None:
    page = 1
    target = normalize(model)

    while True:
        products = api_request(
            "GET",
            "products",
            params={
                "page": page,
                "per_page": 100,
            },
        )

        if not products:
            break

        exact = [
            product
            for product in products
            if normalize(
                localized_text(product.get("name"))
            ) == target
        ]

        if exact:
            return exact[0]

        if exact_only:
            if len(products) < 100:
                break

            page += 1
            continue

        flexible = [
            product
            for product in products
            if (
                target
                in normalize(
                    localized_text(product.get("name"))
                )
                or normalize(
                    localized_text(product.get("name"))
                )
                in target
            )
        ]

        if len(flexible) == 1:
            return flexible[0]

        if len(products) < 100:
            break

        page += 1

    return None


def next_empty_row(
    worksheet: gspread.Worksheet,
) -> int:
    values = worksheet.get_all_values()

    for row_number in range(2, len(values) + 1):
        row = values[row_number - 1]

        if not any(str(value).strip() for value in row):
            return row_number

    return len(values) + 1


def append_variant_to_sheet(
    *,
    product_name: str,
    product_id: str,
    variant: dict[str, Any],
) -> dict[str, Any]:
    worksheet, _, columns, _ = read_products()
    row = next_empty_row(worksheet)

    if not variant.get("product_id"):
        variant["product_id"] = product_id

    data = synchronize_variant_to_sheet(
        worksheet,
        columns,
        row,
        product_name=product_name,
        variant=variant,
    )

    return data


def create_variant(
    *,
    model: str,
    flavor: str,
    stock: int,
    cost: float,
    price: float,
    sku: str = "",
) -> dict[str, Any]:
    product = find_product_by_name(model)

    if not product:
        raise SyncError(
            f"No existe el modelo {model} en Tiendanube."
        )

    product_id = str(product["id"])

    payload: dict[str, Any] = {
        "values": [
            {
                "es": flavor,
            }
        ],
        "price": str(price),
        "cost": str(cost),
        "sku": sku or None,
    }

    existing_variants = product.get("variants") or []
    uses_locations = any(
        variant.get("inventory_levels")
        for variant in existing_variants
    )

    if uses_locations:
        location_ids = []

        for existing in existing_variants:
            for level in (
                existing.get("inventory_levels") or []
            ):
                location_id = level.get("location_id")

                if (
                    location_id
                    and location_id not in location_ids
                ):
                    location_ids.append(location_id)

        if len(location_ids) > 1:
            raise SyncError(
                "El modelo usa varias ubicaciones. "
                "Falta indicar la ubicación para el stock."
            )

        level = {
            "stock": stock,
        }

        if location_ids:
            level["location_id"] = location_ids[0]

        payload["inventory_levels"] = [level]
    else:
        payload["stock"] = stock

    variant = api_request(
        "POST",
        f"products/{product_id}/variants",
        payload=payload,
    )

    product_name = localized_text(
        product.get("name")
    ).strip()

    data = append_variant_to_sheet(
        product_name=product_name,
        product_id=product_id,
        variant=variant,
    )

    return {
        "ok": True,
        "mensaje": "Variante creada y sincronizada.",
        "marca": data["Marca"],
        "sabor": data["Sabor"],
        "stock": data["Stock"],
        "costo": data["Costo"],
        "precio": data["Precio venta"],
        "ganancia": data["Ganancia"],
        "product_id": data[
            "TiendaNube Product ID"
        ],
        "variant_id": data[
            "TiendaNube Variant ID"
        ],
    }


def create_product(
    *,
    model: str,
    flavor: str,
    stock: int,
    cost: float,
    price: float,
    sku: str = "",
    published: bool = True,
) -> dict[str, Any]:
    if find_product_by_name(model):
        raise SyncError(
            "El modelo ya existe. Usá agregar-variante."
        )

    payload = {
        "name": {
            "es": model,
        },
        "published": published,
        "attributes": [
            {
                "es": "Sabor",
            }
        ],
        "variants": [
            {
                "values": [
                    {
                        "es": flavor,
                    }
                ],
                "price": str(price),
                "cost": str(cost),
                "stock": stock,
                "sku": sku or None,
            }
        ],
    }

    product = api_request(
        "POST",
        "products",
        payload=payload,
    )

    variants = product.get("variants") or []

    if not variants:
        raise SyncError(
            "Tiendanube creó el producto sin variante."
        )

    variant = variants[0]
    product_name = localized_text(
        product.get("name")
    ).strip()

    data = append_variant_to_sheet(
        product_name=product_name,
        product_id=str(product["id"]),
        variant=variant,
    )

    return {
        "ok": True,
        "mensaje": "Producto creado y sincronizado.",
        "marca": data["Marca"],
        "sabor": data["Sabor"],
        "stock": data["Stock"],
        "costo": data["Costo"],
        "precio": data["Precio venta"],
        "ganancia": data["Ganancia"],
        "product_id": data[
            "TiendaNube Product ID"
        ],
        "variant_id": data[
            "TiendaNube Variant ID"
        ],
    }


def delete_variant(
    *,
    model: str,
    flavor: str,
) -> dict[str, Any]:
    worksheet, _, product = find_sheet_product(
        model,
        flavor,
    )

    api_request(
        "DELETE",
        (
            f"products/{product.product_id}/"
            f"variants/{product.variant_id}"
        ),
    )

    worksheet.delete_rows(product.row)

    return {
        "ok": True,
        "mensaje": "Variante eliminada de Tiendanube y Sheets.",
        "marca": product.marca,
        "sabor": product.sabor,
    }


def delete_product(
    *,
    model: str,
) -> dict[str, Any]:
    product = find_product_by_name(model)

    if not product:
        raise SyncError(
            f"No existe el modelo {model}."
        )

    product_id = str(product["id"])

    api_request(
        "DELETE",
        f"products/{product_id}",
    )

    worksheet, _, columns, products = read_products()

    rows = sorted(
        [
            item.row
            for item in products
            if item.product_id == product_id
        ],
        reverse=True,
    )

    for row in rows:
        worksheet.delete_rows(row)

    return {
        "ok": True,
        "mensaje": "Modelo eliminado de Tiendanube y Sheets.",
        "marca": localized_text(product.get("name")),
        "filas_eliminadas": len(rows),
    }


def consult_product(
    *,
    model: str,
    flavor: str,
) -> dict[str, Any]:
    worksheet, columns, product = find_sheet_product(
        model,
        flavor,
    )

    variant = get_variant(
        product.product_id,
        product.variant_id,
    )

    remote_product = get_product(product.product_id)

    data = synchronize_variant_to_sheet(
        worksheet,
        columns,
        product.row,
        product_name=localized_text(
            remote_product.get("name")
        ),
        variant=variant,
    )

    return {
        "ok": True,
        "mensaje": "Producto consultado y actualizado.",
        "marca": data["Marca"],
        "sabor": data["Sabor"],
        "stock": data["Stock"],
        "costo": data["Costo"],
        "precio": data["Precio venta"],
        "ganancia": data["Ganancia"],
        "product_id": data[
            "TiendaNube Product ID"
        ],
        "variant_id": data[
            "TiendaNube Variant ID"
        ],
    }
