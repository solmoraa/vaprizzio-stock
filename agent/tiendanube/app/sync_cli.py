#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys

from app.sync_core import (
    SyncError,
    consult_product,
    create_product,
    create_variant,
    delete_product,
    delete_variant,
    modify_existing_product,
)


def emit(data, exit_code=0):
    print(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        )
    )
    raise SystemExit(exit_code)


def parser():
    root = argparse.ArgumentParser(
        description=(
            "Sincronización Tiendanube + Google Sheets"
        )
    )

    sub = root.add_subparsers(
        dest="command",
        required=True,
    )

    p = sub.add_parser("consultar")
    p.add_argument("--marca", required=True)
    p.add_argument("--sabor", required=True)

    p = sub.add_parser("modificar")
    p.add_argument("--marca", required=True)
    p.add_argument("--sabor", required=True)
    p.add_argument("--stock", type=int)
    p.add_argument("--sumar-stock", type=int)
    p.add_argument("--restar-stock", type=int)
    p.add_argument("--costo", type=float)
    p.add_argument("--precio", type=float)
    p.add_argument("--nuevo-sabor")
    p.add_argument("--nuevo-modelo")
    p.add_argument("--sku")

    p = sub.add_parser("agregar-variante")
    p.add_argument("--marca", required=True)
    p.add_argument("--sabor", required=True)
    p.add_argument("--stock", type=int, required=True)
    p.add_argument("--costo", type=float, required=True)
    p.add_argument("--precio", type=float, required=True)
    p.add_argument("--sku", default="")

    p = sub.add_parser("agregar-producto")
    p.add_argument("--marca", required=True)
    p.add_argument("--sabor", required=True)
    p.add_argument("--stock", type=int, required=True)
    p.add_argument("--costo", type=float, required=True)
    p.add_argument("--precio", type=float, required=True)
    p.add_argument("--sku", default="")
    p.add_argument(
        "--no-publicar",
        action="store_true",
    )

    p = sub.add_parser("eliminar-variante")
    p.add_argument("--marca", required=True)
    p.add_argument("--sabor", required=True)

    p = sub.add_parser("eliminar-producto")
    p.add_argument("--marca", required=True)

    return root


def main():
    args = parser().parse_args()

    try:
        if args.command == "consultar":
            result = consult_product(
                model=args.marca,
                flavor=args.sabor,
            )

        elif args.command == "modificar":
            result = modify_existing_product(
                marca=args.marca,
                sabor=args.sabor,
                stock=args.stock,
                add_stock=args.sumar_stock,
                subtract_stock=args.restar_stock,
                cost=args.costo,
                price=args.precio,
                new_flavor=args.nuevo_sabor,
                new_model=args.nuevo_modelo,
                sku=args.sku,
            )

        elif args.command == "agregar-variante":
            result = create_variant(
                model=args.marca,
                flavor=args.sabor,
                stock=args.stock,
                cost=args.costo,
                price=args.precio,
                sku=args.sku,
            )

        elif args.command == "agregar-producto":
            result = create_product(
                model=args.marca,
                flavor=args.sabor,
                stock=args.stock,
                cost=args.costo,
                price=args.precio,
                sku=args.sku,
                published=not args.no_publicar,
            )

        elif args.command == "eliminar-variante":
            result = delete_variant(
                model=args.marca,
                flavor=args.sabor,
            )

        elif args.command == "eliminar-producto":
            result = delete_product(
                model=args.marca,
            )

        else:
            raise SyncError(
                "Comando no implementado."
            )

        emit(result)

    except SyncError as error:
        emit(
            {
                "ok": False,
                "error": str(error),
            },
            1,
        )

    except Exception as error:
        emit(
            {
                "ok": False,
                "error": (
                    f"Error inesperado: "
                    f"{type(error).__name__}: {error}"
                ),
            },
            1,
        )


if __name__ == "__main__":
    main()
