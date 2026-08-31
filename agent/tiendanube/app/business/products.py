from __future__ import annotations

from typing import Any

from app.sync_core import (
    find_sheet_product,
    read_products,
)

from .common import (
    BusinessError,
    normalizar,
    texto,
)


def _producto_a_dict(product: Any) -> dict[str, Any]:
    return {
        "fila": product.row,
        "marca": product.marca,
        "sabor": product.sabor,
        "stock": product.stock,
        "costo": product.costo,
        "precio": product.precio,
        "ganancia": product.ganancia,
        "tiendanube_product_id": product.product_id,
        "tiendanube_variant_id": product.variant_id,
        "tiendanube_location_id": product.location_id,
        "sku": product.sku,
        "estado_sync": product.estado,
    }


def buscar_producto(
    marca: Any,
    sabor: Any,
) -> dict[str, Any]:
    marca_text = texto(marca)
    sabor_text = texto(sabor)

    if not marca_text:
        raise BusinessError("Falta indicar el modelo o marca.")

    if not sabor_text:
        raise BusinessError("Falta indicar el sabor.")

    try:
        _, _, product = find_sheet_product(
            marca_text,
            sabor_text,
        )
    except Exception as exc:
        raise BusinessError(str(exc)) from exc

    return _producto_a_dict(product)


def buscar_por_sabor(
    sabor: Any,
) -> list[dict[str, Any]]:
    expected = normalizar(sabor)

    if not expected:
        raise BusinessError("Falta indicar el sabor.")

    try:
        _, _, _, products = read_products()
    except Exception as exc:
        raise BusinessError(
            f"No se pudo leer Productos: {exc}"
        ) from exc

    exact = [
        product
        for product in products
        if normalizar(product.sabor) == expected
    ]

    selected = exact or [
        product
        for product in products
        if expected in normalizar(product.sabor)
        or normalizar(product.sabor) in expected
    ]

    return [
        _producto_a_dict(product)
        for product in selected
    ]


def resolver_producto(
    *,
    marca: Any = None,
    sabor: Any,
) -> dict[str, Any]:
    if texto(marca):
        return buscar_producto(marca, sabor)

    matches = buscar_por_sabor(sabor)

    if not matches:
        raise BusinessError(
            f"No encontré el sabor {texto(sabor)!r} "
            "en la hoja Productos."
        )

    if len(matches) > 1:
        options = ", ".join(
            f"{item['marca']} / {item['sabor']}"
            for item in matches[:10]
        )

        raise BusinessError(
            f"El sabor {texto(sabor)!r} aparece en más "
            f"de un producto: {options}. "
            "Indicá también el modelo."
        )

    return matches[0]


def consultar_stock(
    *,
    marca: Any = None,
    sabor: Any = None,
    solo_disponibles: bool = False,
) -> dict[str, Any]:
    if texto(sabor):
        products = [
            resolver_producto(
                marca=marca,
                sabor=sabor,
            )
        ]
    else:
        try:
            _, _, _, sheet_products = read_products()
        except Exception as exc:
            raise BusinessError(
                f"No se pudo leer Productos: {exc}"
            ) from exc

        products = [
            _producto_a_dict(product)
            for product in sheet_products
        ]

        if texto(marca):
            expected = normalizar(marca)

            products = [
                item
                for item in products
                if expected in normalizar(item["marca"])
            ]

    if solo_disponibles:
        products = [
            item
            for item in products
            if (item["stock"] or 0) > 0
        ]

    products.sort(
        key=lambda item: (
            normalizar(item["marca"]),
            normalizar(item["sabor"]),
        )
    )

    return {
        "ok": True,
        "cantidad_productos": len(products),
        "productos": products,
        "fuente": "Google Sheets / Productos",
    }
