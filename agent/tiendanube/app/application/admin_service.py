from __future__ import annotations

from typing import Any

from app.business.common import BusinessError
from app.business.products import consultar_stock
from app.business.sales import (
    consultar_venta,
    registrar_venta_manual,
    registrar_ventas_manuales,
)


class ApplicationError(ValueError):
    """Error comercial seguro para devolver al cliente de la API."""


class AdminApplicationService:
    """Orquesta casos de uso sin depender de HTTP, Telegram ni systemd."""

    @staticmethod
    def _run(operation: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            result = operation(*args, **kwargs)
        except BusinessError as exc:
            raise ApplicationError(str(exc)) from exc

        if not isinstance(result, dict):
            return {"ok": True, "resultado": result}
        return result

    def stock(
        self,
        *,
        marca: Any = None,
        sabor: Any = None,
        solo_disponibles: bool = False,
    ) -> dict[str, Any]:
        return self._run(
            consultar_stock,
            marca=marca,
            sabor=sabor,
            solo_disponibles=solo_disponibles,
        )

    def order(self, order_id: Any, *, hoja: Any = None) -> dict[str, Any]:
        return self._run(consultar_venta, order_id, hoja=hoja)

    def create_sale(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._run(
            registrar_venta_manual,
            cliente=payload.get("cliente"),
            productos=payload.get("productos"),
            plataforma=payload.get("plataforma"),
            forma_pago=payload.get("forma_pago"),
            estado=payload.get("estado") or "Entregado",
            fecha=payload.get("fecha"),
        )

    def create_sales(self, payload: dict[str, Any]) -> dict[str, Any]:
        sales = payload.get("ventas")
        return self._run(registrar_ventas_manuales, sales)
