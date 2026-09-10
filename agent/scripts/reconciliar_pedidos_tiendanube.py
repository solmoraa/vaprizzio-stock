#!/usr/bin/env python3
"""Recupera ventas pagadas si un webhook de Tiendanube no llegó.

El webhook sigue siendo la vía inmediata. Este proceso es un respaldo
idempotente: consulta solamente pedidos pagos recientes y vuelve a escribir
la misma orden por su ID de Tiendanube, sin duplicar filas ni descontar stock.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


WORKSPACE = Path("/home/openclaw/.openclaw/workspace/vaprizziobot")
TN_DIR = WORKSPACE / "tiendanube"

if str(TN_DIR) not in sys.path:
    sys.path.insert(0, str(TN_DIR))

from app import order_sync as legacy  # noqa: E402
from app.order_sync_v2 import (  # noqa: E402
    is_cancelled,
    order_is_paid_v2,
    synchronize_order,
)
from app.sync_core import SyncError, api_request  # noqa: E402


def iso_since(days: int) -> str:
    """Fecha UTC para el filtro oficial ``updated_at_min``."""
    instant = datetime.now(timezone.utc) - timedelta(days=days)
    return instant.replace(microsecond=0).isoformat()


def paid_orders_updated_since(days: int) -> list[dict[str, Any]]:
    """Lee todas las órdenes pagas modificadas en el período indicado."""
    page = 1
    result: list[dict[str, Any]] = []

    while True:
        response = api_request(
            "GET",
            "orders",
            params={
                "payment_status": "paid",
                "updated_at_min": iso_since(days),
                "page": page,
                "per_page": 30,
            },
        )

        if not isinstance(response, list):
            raise SyncError(
                "Tiendanube devolvió una lista de órdenes inválida."
            )

        result.extend(
            order for order in response if isinstance(order, dict)
        )

        if len(response) < 30:
            return result

        page += 1


def reconcile(days: int) -> dict[str, Any]:
    """Registra en Sheets pedidos pagos ausentes y actualiza los existentes."""
    summary: dict[str, Any] = {
        "ok": True,
        "days": days,
        "found": 0,
        "registered": 0,
        "already_known": 0,
        "skipped": 0,
        "errors": [],
        "orders": [],
    }

    for listed_order in paid_orders_updated_since(days):
        order_id = str(listed_order.get("id") or "").strip()

        if not order_id:
            summary["skipped"] += 1
            continue

        summary["found"] += 1

        try:
            # La lista de órdenes puede contener datos parciales. Consultamos
            # cada orden para conservar productos, variantes y forma de pago.
            order = legacy.get_order(order_id)

            if is_cancelled(order) or not order_is_paid_v2(order):
                summary["skipped"] += 1
                continue

            known_before = legacy.order_was_paid(order_id)
            result = synchronize_order(
                order,
                "order/updated",
                previously_paid=known_before,
            )

            if known_before:
                summary["already_known"] += 1
            else:
                summary["registered"] += 1

            summary["orders"].append(
                {
                    "id": order_id,
                    "number": str(order.get("number") or ""),
                    "sheet": (result.get("sale") or {}).get("sheet"),
                    "new": not known_before,
                }
            )
        except Exception as error:  # continúa con los demás pedidos
            summary["errors"].append(
                {
                    "id": order_id,
                    "error": f"{type(error).__name__}: {error}",
                }
            )

    summary["ok"] = not summary["errors"]
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Respaldo de Tiendanube a Google Sheets."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Días hacia atrás a revisar (predeterminado: 7).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emite el resultado en JSON.",
    )
    args = parser.parse_args()

    if args.days < 1 or args.days > 31:
        raise SystemExit("--days debe estar entre 1 y 31.")

    result = reconcile(args.days)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, default=str))
    else:
        print(
            "Tiendanube revisada: "
            f"{result['found']} pagas, "
            f"{result['registered']} incorporadas, "
            f"{result['already_known']} ya registradas."
        )

        for error in result["errors"]:
            print(f"ERROR orden {error['id']}: {error['error']}")

    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
