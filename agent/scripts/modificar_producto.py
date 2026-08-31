#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT = Path(
    "/home/openclaw/.openclaw/workspace/vaprizziobot"
)
TIENDANUBE = PROJECT / "tiendanube"

if str(TIENDANUBE) not in sys.path:
    sys.path.insert(0, str(TIENDANUBE))


from app.business.costs import (  # noqa: E402
    actualizar_costo_usdt_marca,
)
from app.sync_core import (  # noqa: E402
    SyncError,
    modify_existing_product,
)


def imprimir(data: dict[str, Any]) -> None:
    print(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


def entero_no_negativo(
    value: str | None,
    nombre: str,
) -> int | None:
    if value is None:
        return None

    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{nombre} debe ser un número entero."
        ) from exc

    if number < 0:
        raise ValueError(
            f"{nombre} no puede ser negativo."
        )

    return number


def numero_positivo(
    value: str | None,
    nombre: str,
) -> float | None:
    if value is None:
        return None

    normalized = str(value).strip().replace(",", ".")

    try:
        number = float(normalized)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{nombre} debe ser un número."
        ) from exc

    if number <= 0:
        raise ValueError(
            f"{nombre} debe ser mayor que cero."
        )

    return number


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Modificar un producto sincronizando Tiendanube y Google Sheets. "
            "El parámetro --costo siempre representa el costo USDT del modelo."
        )
    )

    parser.add_argument(
        "--marca",
        required=True,
    )
    parser.add_argument(
        "--sabor",
        help=(
            "Sabor a modificar. Es obligatorio para stock, precio, SKU o "
            "cambios de nombre; no hace falta para cambiar solo el costo USDT."
        ),
    )
    parser.add_argument(
        "--nueva-marca",
    )
    parser.add_argument(
        "--nuevo-sabor",
    )

    stock_group = parser.add_mutually_exclusive_group()

    stock_group.add_argument(
        "--stock",
        help="Establecer el stock absoluto.",
    )
    stock_group.add_argument(
        "--sumar-stock",
        help="Sumar unidades al stock actual.",
    )
    stock_group.add_argument(
        "--restar-stock",
        help="Restar unidades al stock actual.",
    )

    parser.add_argument(
        "--costo",
        dest="costo_usdt",
        help=(
            "Costo USDT del modelo. Actualiza una sola fila de la tabla de "
            "costos y recalcula Costo/Ganancia de todos sus sabores."
        ),
    )
    parser.add_argument(
        "--costo-usdt",
        dest="costo_usdt",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--precio",
    )
    parser.add_argument(
        "--sku",
    )

    return parser


def main() -> int:
    args = construir_parser().parse_args()

    try:
        stock = entero_no_negativo(
            args.stock,
            "El stock",
        )
        add_stock = entero_no_negativo(
            args.sumar_stock,
            "La cantidad a sumar",
        )
        subtract_stock = entero_no_negativo(
            args.restar_stock,
            "La cantidad a restar",
        )
        costo_usdt = numero_positivo(
            args.costo_usdt,
            "El costo USDT",
        )
        price = numero_positivo(
            args.precio,
            "El precio",
        )

        modifica_variante = any(
            value is not None
            for value in (
                stock,
                add_stock,
                subtract_stock,
                price,
                args.nuevo_sabor,
                args.nueva_marca,
                args.sku,
            )
        )

        if modifica_variante and not str(args.sabor or "").strip():
            raise ValueError(
                "Debés indicar --sabor para modificar stock, precio, SKU "
                "o el nombre de una variante. Para cambiar solamente el "
                "costo USDT alcanza con --marca y --costo."
            )

        if not modifica_variante and costo_usdt is None:
            raise ValueError(
                "No se indicó ningún cambio."
            )

        result: dict[str, Any] = {
            "ok": True,
            "accion": "modificar_producto",
        }

        # IMPORTANTE: nunca enviamos el costo USDT a modify_existing_product,
        # porque esa función interpreta 'cost' como pesos y lo escribiría como
        # $7,50 en la columna Costo. El costo se procesa exclusivamente por
        # marca mediante actualizar_costo_usdt_marca().
        if modifica_variante:
            variant_result = modify_existing_product(
                marca=args.marca,
                sabor=str(args.sabor),
                stock=stock,
                add_stock=add_stock,
                subtract_stock=subtract_stock,
                cost=None,
                price=price,
                new_flavor=args.nuevo_sabor,
                new_model=args.nueva_marca,
                sku=args.sku,
            )
            result["variante"] = variant_result

        if costo_usdt is not None:
            cost_result = actualizar_costo_usdt_marca(
                marca=args.marca,
                costo_usdt=costo_usdt,
            )
            result["costo_usdt"] = cost_result
            result["mensaje"] = (
                f"Costo USDT de {cost_result.get('marca', args.marca)} "
                f"actualizado a {costo_usdt:g}. Se recalcularon "
                f"{cost_result.get('productos_actualizados', 0)} productos."
            )

        # Un cambio de costo USDT afecta solamente Google Sheets. Tiendanube
        # se modifica únicamente si hubo cambios de variante (precio de venta,
        # stock, SKU o nombres).
        result["sincronizado_tiendanube"] = bool(modifica_variante)
        result["sincronizado_google_sheets"] = True

        imprimir(result)
        return 0

    except (ValueError, SyncError, Exception) as exc:
        imprimir(
            {
                "ok": False,
                "accion": "modificar_producto",
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
