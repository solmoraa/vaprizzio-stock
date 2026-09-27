from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "agent" / "tiendanube"
sys.path.insert(0, str(APP_ROOT))

from app.presentation.rest_api import AdminRestApi  # noqa: E402


class FakeService:
    def stock(self, **kwargs: Any) -> dict[str, Any]:
        return {"productos": [], "filtros": kwargs}

    def order(self, order_id: Any, **kwargs: Any) -> dict[str, Any]:
        return {"orden": order_id, **kwargs}

    def create_sale(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"orden": 101, "cliente": payload.get("cliente")}

    def create_sales(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"cantidad": len(payload.get("ventas", []))}


class RestApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = AdminRestApi(FakeService(), "token-seguro")  # type: ignore[arg-type]

    def test_requires_bearer_token(self) -> None:
        status, body = self.api.dispatch("GET", "/api/v1/stock")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "UNAUTHORIZED")

    def test_exposes_stock_and_orders_as_resources(self) -> None:
        status, body = self.api.dispatch(
            "GET",
            "/api/v1/stock",
            query="marca=Elfbar&solo_disponibles=true",
            authorization="Bearer token-seguro",
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["data"]["filtros"]["solo_disponibles"])

        status, body = self.api.dispatch(
            "GET",
            "/api/v1/orders/183",
            authorization="Bearer token-seguro",
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["orden"], "183")

    def test_creates_one_or_several_sales(self) -> None:
        status, body = self.api.dispatch(
            "POST",
            "/api/v1/sales",
            body={"cliente": "Sol"},
            authorization="Bearer token-seguro",
        )
        self.assertEqual(status, 201)
        self.assertEqual(body["data"]["cliente"], "Sol")

        status, body = self.api.dispatch(
            "POST",
            "/api/v1/sales/batch",
            body={"ventas": [{}, {}]},
            authorization="Bearer token-seguro",
        )
        self.assertEqual(status, 201)
        self.assertEqual(body["data"]["cantidad"], 2)


if __name__ == "__main__":
    unittest.main()
