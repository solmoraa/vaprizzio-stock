#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from app.tools import (
    ToolError,
    buscar_orden,
    cambiar_estado,
    cancelar_orden,
    ejecutar_accion,
    interpretar_accion,
)


def print_json(value: Any) -> None:
    print(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vaprizzio-tools",
        description=(
            "Herramientas de gestión de pedidos "
            "de Vaprizzio."
        ),
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    search_parser = subparsers.add_parser(
        "buscar",
        help="Busca una orden sin modificarla.",
    )
    search_parser.add_argument(
        "orden",
        help="Número interno de la orden.",
    )

    status_parser = subparsers.add_parser(
        "estado",
        help="Cambia el estado de una orden.",
    )
    status_parser.add_argument(
        "orden",
        help="Número interno de la orden.",
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
        help="Cancela una orden en Tiendanube.",
    )
    cancel_parser.add_argument(
        "orden",
        help="Número interno de la orden.",
    )
    cancel_parser.add_argument(
        "--confirmar",
        action="store_true",
        help=(
            "Confirma la cancelación real de la orden."
        ),
    )

    interpret_parser = subparsers.add_parser(
        "interpretar",
        help=(
            "Interpreta una frase sin ejecutar cambios."
        ),
    )
    interpret_parser.add_argument(
        "texto",
        help="Frase a interpretar.",
    )

    execute_parser = subparsers.add_parser(
        "ejecutar",
        help=(
            "Interpreta y ejecuta una frase."
        ),
    )
    execute_parser.add_argument(
        "texto",
        help="Frase a ejecutar.",
    )
    execute_parser.add_argument(
        "--confirmar-cancelacion",
        action="store_true",
        help=(
            "Permite ejecutar una cancelación real."
        ),
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "buscar":
            result = buscar_orden(
                args.orden
            )

        elif args.command == "estado":
            result = cambiar_estado(
                args.orden,
                args.estado,
            )

        elif args.command == "cancelar":
            if not args.confirmar:
                print_json(
                    {
                        "ok": False,
                        "ejecutada": False,
                        "requiere_confirmacion": True,
                        "orden": str(args.orden),
                        "mensaje": (
                            "Para cancelar realmente, agregá "
                            "--confirmar."
                        ),
                    }
                )
                return 2

            result = cancelar_orden(
                args.orden
            )

        elif args.command == "interpretar":
            result = interpretar_accion(
                args.texto
            )

        elif args.command == "ejecutar":
            result = ejecutar_accion(
                args.texto,
                confirmar_cancelacion=(
                    args.confirmar_cancelacion
                ),
            )

        else:
            parser.error(
                "Comando no reconocido."
            )
            return 2

        print_json(result)

        return 0 if result.get("ok") else 1

    except ToolError as exc:
        print_json(
            {
                "ok": False,
                "error": str(exc),
                "tipo": "ToolError",
            }
        )
        return 1

    except KeyboardInterrupt:
        print_json(
            {
                "ok": False,
                "error": "Operación interrumpida.",
            }
        )
        return 130

    except Exception as exc:
        print_json(
            {
                "ok": False,
                "error": str(exc),
                "tipo": type(exc).__name__,
            }
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
