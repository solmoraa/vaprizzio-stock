from __future__ import annotations

from typing import Any

from app.sync_core import modify_existing_product

from .common import (
    BusinessError,
    entero,
)
from .products import resolver_producto


def validar_disponibilidad(
    *,
    marca: Any = None,
    sabor: Any,
    cantidad: Any,
) -> dict[str, Any]:
    quantity = entero(
        cantidad,
        nombre="cantidad",
    )

    if quantity <= 0:
        raise BusinessError(
            "La cantidad debe ser mayor a cero."
        )

    product = resolver_producto(
        marca=marca,
        sabor=sabor,
    )

    stock = product["stock"]

    if stock is None:
        raise BusinessError(
            f"No pude determinar el stock de "
            f"{product['marca']} / {product['sabor']}."
        )

    if stock < quantity:
        raise BusinessError(
            f"Stock insuficiente para "
            f"{product['marca']} / {product['sabor']}. "
            f"Disponible: {stock}. Solicitado: {quantity}."
        )

    return product


def restar_stock(
    *,
    marca: Any = None,
    sabor: Any,
    cantidad: Any,
) -> dict[str, Any]:
    product = validar_disponibilidad(
        marca=marca,
        sabor=sabor,
        cantidad=cantidad,
    )

    quantity = entero(cantidad)

    try:
        result = modify_existing_product(
            marca=product["marca"],
            sabor=product["sabor"],
            subtract_stock=quantity,
        )
    except Exception as exc:
        raise BusinessError(
            "No se pudo descontar stock de "
            f"{product['marca']} / {product['sabor']}: "
            f"{exc}"
        ) from exc

    return {
        "ok": True,
        "operacion": "restar",
        "cantidad": quantity,
        "producto": product,
        "resultado": result,
    }


def sumar_stock(
    *,
    marca: Any = None,
    sabor: Any,
    cantidad: Any,
) -> dict[str, Any]:
    quantity = entero(
        cantidad,
        nombre="cantidad",
    )

    if quantity <= 0:
        raise BusinessError(
            "La cantidad debe ser mayor a cero."
        )

    product = resolver_producto(
        marca=marca,
        sabor=sabor,
    )

    try:
        result = modify_existing_product(
            marca=product["marca"],
            sabor=product["sabor"],
            add_stock=quantity,
        )
    except Exception as exc:
        raise BusinessError(
            "No se pudo reponer stock de "
            f"{product['marca']} / {product['sabor']}: "
            f"{exc}"
        ) from exc

    return {
        "ok": True,
        "operacion": "sumar",
        "cantidad": quantity,
        "producto": product,
        "resultado": result,
    }
