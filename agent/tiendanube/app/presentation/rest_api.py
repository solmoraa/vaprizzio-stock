from __future__ import annotations

import hmac
import re
from typing import Any, Protocol
from urllib.parse import parse_qs


class AdminService(Protocol):
    def stock(self, **kwargs: Any) -> dict[str, Any]: ...
    def order(self, order_id: Any, **kwargs: Any) -> dict[str, Any]: ...
    def create_sale(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def create_sales(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class AdminRestApi:
    """Enrutador REST independiente del servidor HTTP concreto."""

    def __init__(self, service: AdminService, token: str) -> None:
        self.service = service
        self.token = token

    def _authorize(self, authorization: str | None) -> tuple[int, dict[str, Any]] | None:
        if not self.token:
            return 503, {"ok": False, "error": "REST_API_TOKEN_NOT_CONFIGURED"}
        expected = f"Bearer {self.token}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            return 401, {"ok": False, "error": "UNAUTHORIZED"}
        return None

    def dispatch(
        self,
        method: str,
        path: str,
        *,
        query: str = "",
        body: dict[str, Any] | None = None,
        authorization: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        denied = self._authorize(authorization)
        if denied:
            return denied

        payload = body or {}
        params = parse_qs(query)

        try:
            if method == "GET" and path == "/api/v1/stock":
                available = (params.get("solo_disponibles", [""])[0].lower() in {"1", "true", "si"})
                result = self.service.stock(
                    marca=params.get("marca", [None])[0],
                    sabor=params.get("sabor", [None])[0],
                    solo_disponibles=available,
                )
                return 200, {"ok": True, "data": result}

            order_match = re.fullmatch(r"/api/v1/orders/([^/]+)", path)
            if method == "GET" and order_match:
                result = self.service.order(order_match.group(1), hoja=params.get("hoja", [None])[0])
                return 200, {"ok": True, "data": result}

            if method == "POST" and path == "/api/v1/sales":
                result = self.service.create_sale(payload)
                return 201, {"ok": True, "data": result}

            if method == "POST" and path == "/api/v1/sales/batch":
                result = self.service.create_sales(payload)
                return 201, {"ok": True, "data": result}

            return 404, {"ok": False, "error": "ROUTE_NOT_FOUND"}
        except ValueError as exc:
            return 400, {"ok": False, "error": str(exc)}
