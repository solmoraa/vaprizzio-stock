#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
TIENDANUBE = PROJECT / "tiendanube"

if str(TIENDANUBE) not in sys.path:
    sys.path.insert(0, str(TIENDANUBE))

from app.business import (  # noqa: E402
    cancelar_venta_manual,
    consultar_stock,
    consultar_venta,
    ejecutar_accion,
    registrar_venta_manual,
)
from app.business.common import BusinessError  # noqa: E402


def imprimir(data: Any) -> None:
    print(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


def cargar_json(value: str) -> Any:
    raw = value.strip()

    if raw.startswith("@"):
        path = Path(raw[1:]).expanduser()

        if not path.exists():
            raise BusinessError(
                f"No existe el archivo {path}."
            )

        raw = path.read_text(encoding="utf-8")

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BusinessError(
            f"JSON inválido: {exc}"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vaprizzio-negocio",
        description=(
            "Ventas manuales, stock y pedidos de Vaprizzio."
        ),
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    sale = subparsers.add_parser(
        "venta",
        help="Registrar una venta manual.",
    )
    sale.add_argument(
        "--cliente",
        required=True,
    )
    sale.add_argument(
        "--producto",
        action="append",
        required=True,
        help=(
            'JSON por producto. Ejemplo: '
            '\'{"marca":"Elfbar Ice King 40k",'
            '"sabor":"Miami Mint","cantidad":2}\''
        ),
    )
    sale.add_argument(
        "--plataforma",
        default="WhatsApp",
    )
    sale.add_argument(
        "--pago",
        default="",
    )
    sale.add_argument(
        "--estado",
        default="Recibido",
    )
    sale.add_argument(
        "--fecha",
        default=None,
    )

    stock = subparsers.add_parser(
        "stock",
        help="Consultar stock desde Google Sheets.",
    )
    stock.add_argument("--marca")
    stock.add_argument("--sabor")
    stock.add_argument(
        "--disponibles",
        action="store_true",
    )

    consult = subparsers.add_parser(
        "buscar-venta",
        help="Consultar una venta manual.",
    )
    consult.add_argument("orden")
    consult.add_argument("--hoja")

    cancel = subparsers.add_parser(
        "cancelar-manual",
        help=(
            "Cancelar una venta externa, reponer stock "
            "y borrar las filas."
        ),
    )
    cancel.add_argument("orden")
    cancel.add_argument("--hoja")
    cancel.add_argument(
        "--confirmar",
        action="store_true",
    )

    action = subparsers.add_parser(
        "accion",
        help="Ejecutar una acción mediante JSON.",
    )
    action.add_argument("nombre")
    action.add_argument(
        "datos",
        help='JSON o @archivo.json',
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "venta":
            products = [
                cargar_json(item)
                for item in args.producto
            ]

            result = registrar_venta_manual(
                cliente=args.cliente,
                productos=products,
                plataforma=args.plataforma,
                forma_pago=args.pago,
                estado=args.estado,
                fecha=args.fecha,
            )

        elif args.command == "stock":
            result = consultar_stock(
                marca=args.marca,
                sabor=args.sabor,
                solo_disponibles=args.disponibles,
            )

        elif args.command == "buscar-venta":
            result = consultar_venta(
                args.orden,
                hoja=args.hoja,
            )

        elif args.command == "cancelar-manual":
            if not args.confirmar:
                raise BusinessError(
                    "La cancelación modifica Tiendanube y "
                    "Google Sheets. Volvé a ejecutar agregando "
                    "--confirmar."
                )

            result = cancelar_venta_manual(
                args.orden,
                hoja=args.hoja,
            )

        elif args.command == "accion":
            payload = cargar_json(args.datos)

            if not isinstance(payload, dict):
                raise BusinessError(
                    "Los datos de la acción deben ser "
                    "un objeto JSON."
                )

            result = ejecutar_accion(
                args.nombre,
                payload,
            )

        else:
            parser.error("Comando desconocido.")
            return 2

        imprimir(result)
        return 0

    except BusinessError as exc:
        imprimir(
            {
                "ok": False,
                "error": str(exc),
            }
        )
        return 1

    except Exception as exc:
        imprimir(
            {
                "ok": False,
                "error": (
                    f"{type(exc).__name__}: {exc}"
                ),
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
