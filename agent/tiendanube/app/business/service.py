from __future__ import annotations

from typing import Any

from app.order_sync_v2 import (
    cancel_internal_order,
    set_internal_order_status,
)

from .common import BusinessError, normalizar
from .products import consultar_stock
from .sales import (
    cancelar_venta_manual,
    consultar_venta,
    registrar_venta_manual,
)


def ejecutar_accion(
    accion: Any,
    datos: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = datos or {}
    action = normalizar(accion)

    if action in {
        "registrar venta",
        "registrar venta manual",
        "crear venta",
        "venta manual",
    }:
        return registrar_venta_manual(
            cliente=payload.get("cliente"),
            productos=payload.get("productos"),
            plataforma=payload.get(
                "plataforma",
                "WhatsApp",
            ),
            forma_pago=payload.get(
                "forma_pago",
                "",
            ),
            estado=payload.get(
                "estado",
                "Recibido",
            ),
            fecha=payload.get("fecha"),
        )

    if action in {
        "cancelar venta manual",
        "eliminar venta manual",
        "borrar venta manual",
    }:
        return cancelar_venta_manual(
            payload.get(
                "orden",
                payload.get("numero_orden"),
            ),
            hoja=payload.get("hoja"),
        )

    if action in {
        "consultar venta",
        "buscar venta",
    }:
        return consultar_venta(
            payload.get(
                "orden",
                payload.get("numero_orden"),
            ),
            hoja=payload.get("hoja"),
        )

    if action in {
        "consultar stock",
        "buscar stock",
        "ver stock",
    }:
        return consultar_stock(
            marca=payload.get(
                "marca",
                payload.get("modelo"),
            ),
            sabor=payload.get("sabor"),
            solo_disponibles=bool(
                payload.get("solo_disponibles", False)
            ),
        )

    if action in {
        "cambiar estado",
        "actualizar estado",
    }:
        order = payload.get(
            "orden",
            payload.get("numero_orden"),
        )
        state = payload.get("estado")

        try:
            return set_internal_order_status(
                order,
                state,
            )
        except Exception as exc:
            raise BusinessError(str(exc)) from exc

    if action in {
        "cancelar orden",
        "cancelar pedido",
        "cancelar venta",
    }:
        order = payload.get(
            "orden",
            payload.get("numero_orden"),
        )

        try:
            return cancel_internal_order(order)
        except Exception as exc:
            raise BusinessError(str(exc)) from exc

    raise BusinessError(
        f"Acción desconocida: {accion!r}."
    )
