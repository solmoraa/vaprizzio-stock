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


def imprimir(data: dict[str, Any]) -> None:
    print(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


def normalizar_numero(value: Any) -> str:
    numero = str(value or "").strip()

    if not numero:
        raise ValueError("Falta indicar el número de orden.")

    if numero.startswith("#"):
        numero = numero[1:].strip()

    return numero


def valor_celda(value: Any) -> str:
    if value is None:
        return ""

    return str(value).strip()


def buscar_con_business(numero: str) -> dict[str, Any] | None:
    """
    Primero intenta usar la función oficial del Business Core.
    """

    try:
        from app.business.sales import consultar_venta
    except Exception:
        return None

    try:
        resultado = consultar_venta(numero)
    except Exception:
        return None

    if not resultado:
        return None

    if isinstance(resultado, dict):
        resultado.setdefault("ok", True)
        resultado.setdefault("orden", numero)
        resultado.setdefault("fuente", "business_core")
        return resultado

    return {
        "ok": True,
        "orden": numero,
        "fuente": "business_core",
        "resultado": resultado,
    }


def obtener_filas(
    worksheet: Any,
    rows: Any,
) -> list[dict[str, Any]]:
    """
    Convierte el resultado de find_by_internal_order a una estructura
    JSON legible, independientemente de que devuelva números de fila,
    listas o diccionarios.
    """

    resultado: list[dict[str, Any]] = []

    if rows is None:
        return resultado

    if not isinstance(rows, list):
        rows = [rows]

    for item in rows:
        if isinstance(item, dict):
            resultado.append(item)
            continue

        if isinstance(item, (list, tuple)):
            resultado.append(
                {
                    "valores": [
                        valor_celda(value)
                        for value in item
                    ]
                }
            )
            continue

        try:
            row_number = int(item)
        except (TypeError, ValueError):
            resultado.append(
                {
                    "valor": valor_celda(item),
                }
            )
            continue

        values = worksheet.row_values(row_number)

        resultado.append(
            {
                "fila": row_number,
                "valores": [
                    valor_celda(value)
                    for value in values
                ],
            }
        )

    return resultado


def buscar_con_order_sync(numero: str) -> dict[str, Any] | None:
    """
    Busca la orden en las hojas mensuales mediante el sincronizador.
    """

    try:
        from app.order_sync_v2 import find_by_internal_order
    except Exception as exc:
        raise RuntimeError(
            f"No se pudo importar el buscador de órdenes: {exc}"
        ) from exc

    encontrado = find_by_internal_order(numero)

    if not encontrado:
        return None

    if not isinstance(encontrado, tuple):
        return {
            "ok": True,
            "orden": numero,
            "fuente": "order_sync",
            "resultado": encontrado,
        }

    worksheet = encontrado[0] if len(encontrado) > 0 else None
    rows = encontrado[1] if len(encontrado) > 1 else []
    tiendanube_order_id = (
        encontrado[2]
        if len(encontrado) > 2
        else None
    )

    filas = obtener_filas(worksheet, rows)

    return {
        "ok": True,
        "orden": numero,
        "hoja": (
            getattr(worksheet, "title", None)
            if worksheet is not None
            else None
        ),
        "tipo": (
            "tiendanube"
            if valor_celda(tiendanube_order_id)
            else "manual"
        ),
        "tiendanube_order_id": (
            tiendanube_order_id
            if valor_celda(tiendanube_order_id)
            else None
        ),
        "cantidad_filas": len(filas),
        "filas": filas,
        "fuente": "order_sync",
    }


def consultar_orden(numero: str) -> dict[str, Any]:
    resultado = buscar_con_business(numero)

    if resultado:
        return resultado

    resultado = buscar_con_order_sync(numero)

    if resultado:
        return resultado

    return {
        "ok": False,
        "orden": numero,
        "error": f"No se encontró la orden {numero}.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Consultar una orden de Vaprizzio."
    )

    parser.add_argument(
        "--orden",
        required=True,
        help="Número interno de la orden.",
    )

    args = parser.parse_args()

    try:
        numero = normalizar_numero(args.orden)
        resultado = consultar_orden(numero)
        imprimir(resultado)

        return 0 if resultado.get("ok") else 1

    except Exception as exc:
        imprimir(
            {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
