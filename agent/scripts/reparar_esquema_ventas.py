#!/usr/bin/env python3
"""Alinea los encabezados de una hoja mensual de ventas.

La corrección es idempotente: solo agrega columnas comerciales faltantes
mediante ``ensure_sales_headers`` y conserva todas las ventas existentes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


WORKSPACE = Path("/home/openclaw/.openclaw/workspace/vaprizziobot")
TIENDANUBE_DIR = WORKSPACE / "tiendanube"

if str(TIENDANUBE_DIR) not in sys.path:
    sys.path.insert(0, str(TIENDANUBE_DIR))

from app import order_sync as legacy  # noqa: E402
from app.sync_core import spreadsheet  # noqa: E402


def repair(sheet_name: str) -> dict[str, object]:
    book = legacy.run_with_sheets_retry(spreadsheet)
    worksheet = book.worksheet(sheet_name)
    columns = legacy.run_with_sheets_retry(
        lambda: legacy.ensure_sales_headers(worksheet)
    )

    return {
        "ok": True,
        "hoja": worksheet.title,
        "columnas": columns,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Repara los encabezados de una hoja de ventas."
    )
    parser.add_argument("--hoja", required=True)
    args = parser.parse_args()
    print(json.dumps(repair(args.hoja), ensure_ascii=False))


if __name__ == "__main__":
    main()
