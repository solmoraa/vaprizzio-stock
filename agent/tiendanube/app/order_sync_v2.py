#!/usr/bin/env python3

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any

import gspread

from app import order_sync as legacy
from app.sync_core import (
    SyncError,
    api_request,
    normalize,
    parse_int,
    spreadsheet,
)


# ============================================================
# CONFIGURACIÓN VAPRIZZIO
# ============================================================

ESTADO_RECIBIDO = "Recibido"
ESTADO_ACEPTADO = "Aceptado"
ESTADO_EN_CURSO = "En curso"
ESTADO_ENVIADO = "Enviado"
ESTADO_ENTREGADO = "Entregado"

FORMA_MERCADO_PAGO = "MERCADO PAGO FABRI"
FORMA_OFFLINE = "EFECTIVO / TRANSFERENCIA"


# Los valores de la derecha deben coincidir con las opciones
# existentes en el desplegable de la columna Vape.
MODEL_ALIASES = {
    "ignite v nano": "Ignite v-nano",
    "ignite vnano": "Ignite v-nano",
    "v nano": "Ignite v-nano",
    "vnano": "Ignite v-nano",

    "ignite v150": "Ignite v150",
    "ignite v 150": "Ignite v150",
    "v150": "Ignite v150",

    "ignite v250": "Ignite v250",
    "ignite v 250": "Ignite v250",
    "v250": "Ignite v250",

    "ignite v300 slim": "Ignite v300 slim",
    "ignite v 300 slim": "Ignite v300 slim",
    "ignite v300": "Ignite v300 slim",
    "v300 slim": "Ignite v300 slim",

    "elfbar ice king": "ElfBar Ice king",
    "elf bar ice king": "ElfBar Ice king",
    "ice king": "ElfBar Ice king",
    "ice king 40k": "ElfBar Ice king",

    "elfbar te30k": "ElfBar TE30K",
    "elfbar te 30k": "ElfBar TE30K",
    "elf bar te30k": "ElfBar TE30K",
    "te30k": "ElfBar TE30K",
    "te 30k": "ElfBar TE30K",

    "elfbar create bc pro 40k": "ElfBar Create BC pro 40k",
    "elf bar create bc pro 40k": "ElfBar Create BC pro 40k",
    "create bc pro 40k": "ElfBar Create BC pro 40k",
    "bc pro 40k": "ElfBar Create BC pro 40k",

    "elfbar summer": "ElfBar Summer",
    "elf bar summer": "ElfBar Summer",

    "elfbar po 45k": "ElfBar po 45K",
    "elf bar po 45k": "ElfBar po 45K",
    "po 45k": "ElfBar po 45K",

    "geek bar pulse": "Geek Bar Pulse +",
    "geekbar pulse": "Geek Bar Pulse +",
    "geek bar pulse plus": "Geek Bar Pulse +",
    "geek bar pulse +": "Geek Bar Pulse +",

    "airmez 40k auris": "Airmez 40k auris",
    "airmez 40k": "Airmez 40k auris",

    "maskking 100k": "Maskking 100k",
    "mask king 100k": "Maskking 100k",

    "blvk": "BLVK",
    "lost mary mixer": "Lost Mary Mixer",
    "mayorista": "Mayorista",
    "dummy 8k": "Dummy 8k",
}


def clean_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFD", text)

    text = "".join(
        character
        for character in text
        if unicodedata.category(character) != "Mn"
    )

    text = re.sub(r"[^a-z0-9+]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_cancelled(order: dict[str, Any]) -> bool:
    status = clean_text(order.get("status"))

    return bool(
        status in {"cancelled", "canceled", "cancelado"}
        or order.get("cancelled_at")
    )


def order_status_v2(order):
    """
    Estados utilizados:

    Recibido  -> pedido creado
    Aceptado  -> pago aprobado
    En curso  -> preparación
    Enviado   -> despachado
    Entregado -> entregado
    """

    preserved = str(
        order.get("_vaprizzio_preserved_status") or ""
    ).strip()

    if preserved in {
        ESTADO_EN_CURSO,
        ESTADO_ENVIADO,
        ESTADO_ENTREGADO,
    }:
        return preserved

    if legacy.order_is_paid(order):
        return ESTADO_ACEPTADO

    return ESTADO_RECIBIDO

def payment_method_v2(order: dict[str, Any]) -> str:
    details = order.get("payment_details") or {}

    if not isinstance(details, dict):
        details = {}

    candidates = [
        order.get("gateway"),
        order.get("gateway_name"),
        order.get("payment_method"),
        order.get("payment_provider"),
        details.get("method"),
        details.get("name"),
        details.get("gateway"),
        details.get("gateway_name"),
        details.get("provider"),
        details.get("type"),
    ]

    combined = " ".join(
        clean_text(value)
        for value in candidates
        if value not in (None, "")
    )

    if (
        "mercado pago" in combined
        or "mercadopago" in combined
    ):
        return FORMA_MERCADO_PAGO

    if (
        "efectivo" in combined
        or "transferencia" in combined
        or "offline" in combined
        or "cash" in combined
        or "bank transfer" in combined
    ):
        return FORMA_OFFLINE

    # Valor predeterminado solicitado.
    return FORMA_MERCADO_PAGO


# Guardamos las funciones originales antes de reemplazarlas
# para evitar que las funciones V2 se llamen a sí mismas.
_original_line_product_name = legacy.line_product_name
_original_line_flavor = legacy.line_flavor


def line_product_name_v2(
    line: dict[str, Any],
) -> str:
    original = _original_line_product_name(line).strip()
    normalized = clean_text(original)

    if normalized in MODEL_ALIASES:
        return MODEL_ALIASES[normalized]

    # También permite encontrar el modelo cuando Tiendanube
    # agrega textos como capacidad, cantidad de puffs, etc.
    ordered_aliases = sorted(
        MODEL_ALIASES.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    )

    for alias, canonical in ordered_aliases:
        if alias and alias in normalized:
            return canonical

    return original


def line_flavor_v2(
    line: dict[str, Any],
) -> str:
    flavor = _original_line_flavor(line)
    return str(flavor or "").strip().upper()


# ============================================================
# UTILIDADES DE GOOGLE SHEETS
# ============================================================

def get_spreadsheet():
    """
    Compatibilidad por si spreadsheet es una función o
    una instancia ya creada.
    """
    return spreadsheet() if callable(spreadsheet) else spreadsheet


def sales_worksheets() -> list[gspread.Worksheet]:
    book = get_spreadsheet()
    result = []

    for worksheet in book.worksheets():
        normalized_title = clean_text(worksheet.title)

        if normalized_title.startswith("ventas "):
            result.append(worksheet)

    return result


def sheet_columns(
    worksheet: gspread.Worksheet,
) -> dict[str, int]:
    headers = worksheet.row_values(1)

    return {
        normalize(header): position
        for position, header in enumerate(headers, start=1)
        if str(header).strip()
    }


def rows_for_tiendanube_id(
    worksheet: gspread.Worksheet,
    order_id: Any,
) -> list[int]:
    columns = sheet_columns(worksheet)
    column = columns.get(
        normalize("TiendaNube Order ID")
    )

    if not column:
        return []

    expected = str(order_id or "").strip()
    rows = []

    for row_number, row in enumerate(
        worksheet.get_all_values()[1:],
        start=2,
    ):
        current = (
            str(row[column - 1]).strip()
            if len(row) >= column
            else ""
        )

        if current == expected:
            rows.append(row_number)

    return rows


def find_by_tiendanube_id(
    order_id: Any,
) -> tuple[gspread.Worksheet, list[int]] | None:
    for worksheet in sales_worksheets():
        rows = rows_for_tiendanube_id(
            worksheet,
            order_id,
        )

        if rows:
            return worksheet, rows

    return None


def find_by_internal_order(
    internal_order: Any,
) -> tuple[
    gspread.Worksheet,
    list[int],
    str,
] | None:
    expected = str(internal_order or "").strip()

    for worksheet in sales_worksheets():
        columns = sheet_columns(worksheet)

        order_column = columns.get(normalize("Orden"))
        tn_column = columns.get(
            normalize("TiendaNube Order ID")
        )

        if not order_column:
            continue

        rows = []
        tiendanube_id = ""

        for row_number, row in enumerate(
            worksheet.get_all_values()[1:],
            start=2,
        ):
            current = (
                str(row[order_column - 1]).strip()
                if len(row) >= order_column
                else ""
            )

            if current != expected:
                continue

            rows.append(row_number)

            if tn_column and len(row) >= tn_column:
                possible_id = str(
                    row[tn_column - 1]
                ).strip()

                if possible_id:
                    tiendanube_id = possible_id

        if rows:
            return worksheet, rows, tiendanube_id

    return None


def delete_rows_descending(
    worksheet: gspread.Worksheet,
    rows: list[int],
) -> None:
    """
    Elimina desde abajo hacia arriba para no modificar la posición
    de las filas pendientes de borrar.
    """
    for row_number in sorted(set(rows), reverse=True):
        worksheet.delete_rows(row_number)


def delete_order_from_sheets(
    order_id: Any,
) -> dict[str, Any]:
    found = find_by_tiendanube_id(order_id)

    if not found:
        return {
            "deleted": False,
            "reason": "La orden no estaba en Google Sheets.",
            "order_id": str(order_id),
        }

    worksheet, rows = found
    delete_rows_descending(worksheet, rows)

    return {
        "deleted": True,
        "sheet": worksheet.title,
        "rows": rows,
        "order_id": str(order_id),
    }


def read_existing_status(
    order_id: Any,
) -> str:
    found = find_by_tiendanube_id(order_id)

    if not found:
        return ""

    worksheet, rows = found
    columns = sheet_columns(worksheet)
    status_column = columns.get(normalize("Estado"))

    if not status_column:
        return ""

    values = worksheet.get_all_values()

    for row_number in rows:
        if row_number > len(values):
            continue

        row = values[row_number - 1]

        value = (
            str(row[status_column - 1]).strip().upper()
            if len(row) >= status_column
            else ""
        )

        if value in {
            ESTADO_INICIAL,
            ESTADO_ENVIADO,
            ESTADO_ENTREGADO,
        }:
            return value

    return ""


def set_internal_order_status(
    internal_order: Any,
    new_status: str,
) -> dict[str, Any]:
    """
    Cambia el estado utilizando exactamente los valores válidos
    del desplegable de Google Sheets.

    Acepta mayúsculas, minúsculas y variantes comunes.
    """

    normalized_status = clean_text(new_status)

    status_mapping = {
        "recibido": "Recibido",
        "aceptado": "Aceptado",
        "en curso": "En curso",
        "preparando": "En curso",
        "preparado": "En curso",
        "enviado": "Enviado",
        "despachado": "Enviado",
        "entregado": "Entregado",
    }

    canonical_status = status_mapping.get(normalized_status)

    if not canonical_status:
        raise SyncError(
            "Estado inválido. Debe ser Recibido, Aceptado, "
            "En curso, Enviado o Entregado."
        )

    found = find_by_internal_order(internal_order)

    if not found:
        raise SyncError(
            f"No se encontró la orden {internal_order}."
        )

    worksheet, rows, tiendanube_id = found
    columns = sheet_columns(worksheet)
    status_column = columns.get(normalize("Estado"))

    if not status_column:
        raise SyncError(
            "No existe la columna Estado en Google Sheets."
        )

    for row_number in rows:
        worksheet.update_cell(
            row_number,
            status_column,
            canonical_status,
        )

    return {
        "ok": True,
        "orden": str(internal_order),
        "estado": canonical_status,
        "sheet": worksheet.title,
        "rows": rows,
        "tiendanube_order_id": tiendanube_id,
    }


def paint_mercado_pago_cells(
    worksheet: gspread.Worksheet,
    rows: list[int],
) -> None:
    """
    Aplica fondo verde a MERCADO PAGO FABRI.

    Se intenta copiar el formato de otra celda ya existente con
    el mismo valor. Si no existe, aplica un verde predeterminado.
    """
    columns = sheet_columns(worksheet)
    payment_column = columns.get(
        normalize("Forma de pago")
    )

    if not payment_column:
        return

    values = worksheet.get_all_values()
    source_row = None

    for row_number, row in enumerate(values[1:], start=2):
        value = (
            str(row[payment_column - 1]).strip().upper()
            if len(row) >= payment_column
            else ""
        )

        if value == FORMA_MERCADO_PAGO:
            source_row = row_number
            break

    requests = []

    for row_number in rows:
        value = worksheet.cell(
            row_number,
            payment_column,
        ).value

        if str(value or "").strip().upper() != FORMA_MERCADO_PAGO:
            continue

        if source_row and source_row != row_number:
            requests.append(
                {
                    "copyPaste": {
                        "source": {
                            "sheetId": worksheet.id,
                            "startRowIndex": source_row - 1,
                            "endRowIndex": source_row,
                            "startColumnIndex": payment_column - 1,
                            "endColumnIndex": payment_column,
                        },
                        "destination": {
                            "sheetId": worksheet.id,
                            "startRowIndex": row_number - 1,
                            "endRowIndex": row_number,
                            "startColumnIndex": payment_column - 1,
                            "endColumnIndex": payment_column,
                        },
                        "pasteType": "PASTE_FORMAT",
                        "pasteOrientation": "NORMAL",
                    }
                }
            )
        else:
            requests.append(
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": worksheet.id,
                            "startRowIndex": row_number - 1,
                            "endRowIndex": row_number,
                            "startColumnIndex": payment_column - 1,
                            "endColumnIndex": payment_column,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "backgroundColor": {
                                    "red": 0.30,
                                    "green": 0.69,
                                    "blue": 0.31,
                                },
                                "textFormat": {
                                    "bold": True,
                                },
                            }
                        },
                        "fields": (
                            "userEnteredFormat.backgroundColor,"
                            "userEnteredFormat.textFormat.bold"
                        ),
                    }
                }
            )

    if requests:
        get_spreadsheet().batch_update(
            {"requests": requests}
        )


# ============================================================
# SINCRONIZACIÓN V2
# ============================================================

# Se reemplazan solo las reglas de negocio.
legacy.order_status = order_status_v2
legacy.payment_method = payment_method_v2
legacy.line_product_name = line_product_name_v2
legacy.line_flavor = line_flavor_v2


def write_order_rows(
    order: dict[str, Any],
) -> dict[str, Any]:
    order_id = str(order.get("id") or "").strip()

    if not order_id:
        raise SyncError("La orden no tiene ID.")

    preserved_status = "Entregado"

    copied_order = dict(order)

    if preserved_status:
        copied_order[
            "_vaprizzio_preserved_status"
        ] = preserved_status

    result = legacy.write_order_rows(copied_order)

    found = find_by_tiendanube_id(order_id)

    if found:
        worksheet, rows = found

        try:
            paint_mercado_pago_cells(
                worksheet,
                rows,
            )
        except Exception as error:
            # El formato no debe impedir que la venta se registre.
            result["format_warning"] = str(error)

    return result


def synchronize_order(
    order: dict[str, Any],
    event: str,
    *,
    previously_paid: bool,
) -> dict[str, Any]:
    """
    Registra una venta solamente cuando Tiendanube confirma
    que el pago está aprobado.

    Reglas:
    - order/created nunca registra ventas.
    - pedidos pendientes o rechazados no se escriben en Sheets.
    - el stock se sincroniza cuando el pedido pasa por primera vez
      de no pagado a pagado.
    - order/edited vuelve a sincronizar la venta y el stock.
    - order/cancelled elimina la venta y sincroniza el stock repuesto.
    """
    order_id = str(order.get("id") or "").strip()

    if event == "order/cancelled" or is_cancelled(order):
        deletion = legacy.run_with_sheets_retry(
            lambda: delete_order_from_sheets(order_id)
        )

        stock_result = legacy.run_with_sheets_retry(
            lambda: legacy.synchronize_stock_for_order(order)
        )

        legacy.save_order_snapshot(order)

        return {
            "ok": True,
            "event": event,
            "cancelled": True,
            "sale_deleted": deletion,
            "stock": stock_result,
        }

    currently_paid = legacy.order_is_paid(order)
    payment_became_paid = (
        currently_paid
        and not previously_paid
    )

    if event == "order/created":
        return {
            "ok": True,
            "ignored": True,
            "event": event,
            "paid": currently_paid,
            "reason": (
                "La venta se registrará cuando el pago "
                "esté aprobado."
            ),
        }

    # Aunque Tiendanube envíe order/paid, verificamos siempre
    # el estado real de la orden mediante la API.
    if not currently_paid:
        legacy.save_order_snapshot(order)

        return {
            "ok": True,
            "ignored": True,
            "event": event,
            "paid": False,
            "reason": (
                "Tiendanube todavía no confirmó el pago. "
                "No se registró la venta ni se actualizó el stock."
            ),
        }

    sale_result = legacy.run_with_sheets_retry(
        lambda: write_order_rows(order)
    )

    stock_result: list[dict[str, Any]] = []

    # El pago puede confirmarse mediante order/paid o order/updated.
    # Por eso se detecta el cambio real de estado y no solamente
    # el nombre del webhook.
    if payment_became_paid or event == "order/edited":
        stock_result = legacy.run_with_sheets_retry(
            lambda: legacy.synchronize_stock_for_order(order)
        )

    legacy.save_order_snapshot(order)

    return {
        "ok": True,
        "event": event,
        "paid": True,
        "payment_became_paid": payment_became_paid,
        "sale": sale_result,
        "stock": stock_result,
    }


# process_webhook original llama a legacy.synchronize_order.
# Lo apuntamos a la implementación V2.
legacy.synchronize_order = synchronize_order

process_webhook = legacy.process_webhook
get_order = legacy.get_order


# ============================================================
# OPERACIONES DESDE EL CHAT
# ============================================================

def cancel_tiendanube_order(
    tiendanube_order_id: Any,
) -> dict[str, Any]:
    """
    Cancela una orden en Tiendanube.

    La fila de Google Sheets se elimina únicamente después de que
    Tiendanube confirme correctamente la cancelación.

    Tiendanube también:
    - repone el stock;
    - envía el correo al cliente;
    - registra la cancelación como solicitada por el cliente.
    """
    order_id = str(tiendanube_order_id or "").strip()

    if not order_id:
        raise SyncError(
            "No se recibió el ID de Tiendanube."
        )

    payload = {
        "reason": "customer",
        "email": True,
        "restock": True,
    }

    # api_request declara payload como argumento keyword-only.
    # Si Tiendanube responde con error, api_request debe lanzar una
    # excepción y la ejecución no llegará al borrado de Sheets.
    try:
        result = api_request(
            "POST",
            f"orders/{order_id}/cancel",
            payload=payload,
        )
    except Exception as exc:
        raise SyncError(
            f"No se pudo cancelar la orden {order_id} "
            f"en Tiendanube. La venta NO fue eliminada "
            f"de Google Sheets. Error: {exc}"
        ) from exc

    # Solo se elimina después de una respuesta exitosa de Tiendanube.
    try:
        deletion = delete_order_from_sheets(order_id)
    except Exception as exc:
        raise SyncError(
            f"La orden {order_id} fue cancelada en Tiendanube, "
            f"pero no se pudo eliminar de Google Sheets. "
            f"Debe revisarse manualmente. Error: {exc}"
        ) from exc

    return {
        "ok": True,
        "tiendanube_order_id": order_id,
        "reason": "customer",
        "email": True,
        "restock": True,
        "tiendanube": result,
        "sheets": deletion,
    }


def cancel_internal_order(
    internal_order: Any,
) -> dict[str, Any]:
    found = find_by_internal_order(internal_order)

    if not found:
        raise SyncError(
            f"No se encontró la orden {internal_order}."
        )

    _, _, tiendanube_id = found

    if not tiendanube_id:
        raise SyncError(
            f"La orden {internal_order} no tiene "
            "TiendaNube Order ID."
        )

    return cancel_tiendanube_order(tiendanube_id)


# BEGIN WEBHOOK CANCELLED V2

# Guardamos una referencia a la implementación anterior.
# Los eventos normales continúan siendo procesados por order_sync.py.
_legacy_process_webhook_v2 = process_webhook


def _extract_webhook_event_and_order_id(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[str, str]:
    """
    Extrae de forma tolerante el evento y el ID de la orden.

    Soporta:
    - process_webhook(payload)
    - process_webhook(event, order_id)
    - process_webhook(event=..., order_id=...)
    - nombres como topic, resource_id o id
    """
    import inspect

    event = ""
    order_id = ""

    # Primero intentamos interpretar los parámetros usando la firma
    # real de la función anterior.
    try:
        signature = inspect.signature(_legacy_process_webhook_v2)
        bound = signature.bind_partial(*args, **kwargs)
        values = dict(bound.arguments)
    except (TypeError, ValueError):
        values = dict(kwargs)

    event_keys = (
        "event",
        "topic",
        "webhook_event",
        "event_name",
    )

    order_keys = (
        "order_id",
        "resource_id",
        "tiendanube_order_id",
        "id",
    )

    for key in event_keys:
        value = values.get(key)

        if value is not None:
            candidate = str(value).strip().lower()

            if candidate:
                event = candidate
                break

    for key in order_keys:
        value = values.get(key)

        if value is not None:
            candidate = str(value).strip()

            if candidate:
                order_id = candidate
                break

    # Revisamos diccionarios recibidos en args o kwargs.
    containers: list[Any] = list(args) + list(kwargs.values())

    for value in containers:
        if not isinstance(value, dict):
            continue

        if not event:
            for key in event_keys:
                candidate = value.get(key)

                if candidate is not None:
                    candidate_text = str(candidate).strip().lower()

                    if candidate_text:
                        event = candidate_text
                        break

        if not order_id:
            for key in order_keys:
                candidate = value.get(key)

                if candidate is not None:
                    candidate_text = str(candidate).strip()

                    if candidate_text:
                        order_id = candidate_text
                        break

    # Último recurso para firmas posicionales simples.
    if not event:
        for value in args:
            if isinstance(value, str) and "/" in value:
                candidate = value.strip().lower()

                if candidate.startswith("order/"):
                    event = candidate
                    break

    if not order_id and event:
        # En una firma del tipo (event, id), normalmente el ID es
        # el último argumento numérico o texto numérico.
        for value in reversed(args):
            if isinstance(value, bool):
                continue

            if isinstance(value, int):
                order_id = str(value)
                break

            if isinstance(value, str):
                candidate = value.strip()

                if candidate.isdigit():
                    order_id = candidate
                    break

    return event, order_id


def process_webhook(
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Procesa los webhooks de Tiendanube.

    order/cancelled:
        elimina de Sheets todas las filas asociadas al ID de
        Tiendanube.

    Otros eventos:
        conservan exactamente el procesamiento anterior.
    """
    event, order_id = _extract_webhook_event_and_order_id(
        args,
        kwargs,
    )

    normalized_event = event.strip().lower()

    if normalized_event == "order/cancelled":
        if not order_id:
            raise SyncError(
                "Se recibió order/cancelled sin el ID "
                "de la orden."
            )

        deletion = delete_order_from_sheets(order_id)

        return {
            "ok": True,
            "handled_by": "order_sync_v2",
            "event": normalized_event,
            "tiendanube_order_id": order_id,
            "sheets": deletion,
        }

    return _legacy_process_webhook_v2(
        *args,
        **kwargs,
    )


# END WEBHOOK CANCELLED V2
