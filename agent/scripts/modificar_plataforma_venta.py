#!/usr/bin/env python3
"""Modifica solamente la plataforma de una venta manual en Google Sheets."""

from __future__ import annotations

import argparse
import fcntl
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

import gspread


WORKSPACE = Path("/home/openclaw/.openclaw/workspace/vaprizziobot")
SHEET_ID_FILE = WORKSPACE / "google_sheet_id.txt"
CREDENTIALS_FILE = WORKSPACE / "credentials" / "vaprizzio-service-account.json"
LOCK_FILE = WORKSPACE / ".modificar-plataforma-venta.lock"

ORDER_HEADERS = {
    "orden", "numero de orden", "nro de orden", "nro orden",
    "numero orden", "n orden",
}
PLATFORM_HEADERS = {
    "plataforma", "plataforma de venta", "plataforma de ventas",
    "medio de venta", "canal de venta", "canal",
}


class PlatformUpdateError(RuntimeError):
    """Error de negocio seguro para mostrar al agente administrativo."""


def normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold().strip()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def normalize_order(value: Any) -> str:
    raw = str(value or "").strip()
    if re.fullmatch(r"\d+[.,]0+", raw):
        raw = re.split(r"[.,]", raw, maxsplit=1)[0]
    text = normalize(raw)
    text = re.sub(r"^(orden|pedido|venta)\s*", "", text).strip()
    return text


def header_positions(rows: list[list[str]]) -> tuple[int, int, int] | None:
    for row_index, row in enumerate(rows[:25], start=1):
        normalized = [normalize(cell) for cell in row]
        order_column = next(
            (i for i, cell in enumerate(normalized, start=1) if cell in ORDER_HEADERS),
            None,
        )
        platform_column = next(
            (i for i, cell in enumerate(normalized, start=1) if cell in PLATFORM_HEADERS),
            None,
        )
        if order_column and platform_column:
            return row_index, order_column, platform_column
    return None


def open_spreadsheet() -> Any:
    if not SHEET_ID_FILE.is_file():
        raise PlatformUpdateError("No está configurado el archivo de la planilla.")
    if not CREDENTIALS_FILE.is_file():
        raise PlatformUpdateError("No están configuradas las credenciales de Google Sheets.")
    spreadsheet_id = SHEET_ID_FILE.read_text(encoding="utf-8").strip()
    if not spreadsheet_id:
        raise PlatformUpdateError("El identificador de la planilla está vacío.")
    client = gspread.service_account(filename=str(CREDENTIALS_FILE))
    return client.open_by_key(spreadsheet_id)


def cambiar_plataforma_venta(
    orden: Any,
    plataforma: Any,
    hoja: str | None = None,
) -> dict[str, Any]:
    expected_order = normalize_order(orden)
    new_platform = str(plataforma or "").strip()
    if not expected_order:
        raise PlatformUpdateError("Falta indicar la orden de la venta.")
    if not new_platform:
        raise PlatformUpdateError("Falta indicar la nueva plataforma de venta.")
    if (
        len(new_platform) > 80
        or any(char in new_platform for char in "\r\n\t")
        or new_platform.lstrip().startswith(("=", "+", "-", "@"))
    ):
        raise PlatformUpdateError("La plataforma indicada no es válida.")

    spreadsheet = open_spreadsheet()
    if hoja:
        try:
            worksheets = [spreadsheet.worksheet(hoja)]
        except gspread.WorksheetNotFound as exc:
            raise PlatformUpdateError(f'No existe la hoja "{hoja}".') from exc
    else:
        worksheets = [
            worksheet for worksheet in spreadsheet.worksheets()
            if normalize(worksheet.title).startswith("ventas")
        ]

    matches: list[tuple[Any, int, int, str]] = []
    for worksheet in worksheets:
        rows = worksheet.get_all_values()
        positions = header_positions(rows)
        if not positions:
            continue
        header_row, order_column, platform_column = positions
        for row_number, row in enumerate(rows[header_row:], start=header_row + 1):
            order_value = row[order_column - 1] if len(row) >= order_column else ""
            if normalize_order(order_value) != expected_order:
                continue
            previous = row[platform_column - 1] if len(row) >= platform_column else ""
            matches.append((worksheet, row_number, platform_column, previous))

    if not matches:
        raise PlatformUpdateError(
            f"No se encontró la orden {orden} en las hojas de ventas."
        )
    matched_sheets = {match[0].title for match in matches}
    if len(matched_sheets) > 1:
        sheet_names = ", ".join(sorted(matched_sheets))
        raise PlatformUpdateError(
            f"La orden {orden} aparece en varias hojas ({sheet_names}). Indicá la hoja."
        )

    worksheet = matches[0][0]
    previous_values = [match[3] for match in matches]
    updated_rows: list[int] = []
    for _, row_number, platform_column, previous in matches:
        if normalize(previous) == normalize(new_platform):
            continue
        worksheet.update_cell(row_number, platform_column, new_platform)
        updated_rows.append(row_number)

    unique_previous = list(dict.fromkeys(previous_values))

    return {
        "ok": True,
        "orden": str(orden),
        "hoja": worksheet.title,
        "filas": [match[1] for match in matches],
        "filas_actualizadas": updated_rows,
        "plataforma_anterior": (
            unique_previous[0] if len(unique_previous) == 1 else unique_previous
        ),
        "plataforma": new_platform,
        "campos_modificados": ["plataforma"] if updated_rows else [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Modifica únicamente la plataforma de una venta manual."
    )
    parser.add_argument("--orden", required=True)
    parser.add_argument("--plataforma", required=True)
    parser.add_argument("--hoja")
    args = parser.parse_args()
    try:
        LOCK_FILE.touch(mode=0o600, exist_ok=True)
        with LOCK_FILE.open("r+", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result = cambiar_plataforma_venta(
                orden=args.orden,
                plataforma=args.plataforma,
                hoja=args.hoja,
            )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
