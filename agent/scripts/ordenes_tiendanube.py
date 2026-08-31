#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


WORKSPACE = Path(
    "/home/openclaw/.openclaw/workspace/vaprizziobot"
)

TIENDANUBE_DIR = WORKSPACE / "tiendanube"

if str(TIENDANUBE_DIR) not in sys.path:
    sys.path.insert(0, str(TIENDANUBE_DIR))

from app.order_sync_v2 import (  # noqa: E402
    cancel_internal_order,
    set_internal_order_status,
)


def print_result(result) -> None:
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Administrar órdenes de Tiendanube y Google Sheets."
        )
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    status_parser = subparsers.add_parser(
        "estado",
        help="Cambiar el estado de una orden.",
    )

    status_parser.add_argument(
        "orden",
        help="Número interno de la orden en Sheets.",
    )

    status_parser.add_argument(
        "estado",
        choices=[
            "Recibido",
            "Aceptado",
            "En curso",
            "Enviado",
            "Entregado",
        ],
    )

    cancel_parser = subparsers.add_parser(
        "cancelar",
        help=(
            "Cancelar en Tiendanube y eliminar de Sheets."
        ),
    )

    cancel_parser.add_argument(
        "orden",
        help="Número interno de la orden en Sheets.",
    )

    args = parser.parse_args()

    if args.command == "estado":
        result = set_internal_order_status(
            args.orden,
            args.estado,
        )
        print_result(result)
        return

    if args.command == "cancelar":
        result = cancel_internal_order(args.orden)
        print_result(result)
        return


if __name__ == "__main__":
    main()
