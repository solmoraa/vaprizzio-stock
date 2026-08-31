from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SYNC = ROOT / "agent" / "tiendanube" / "app" / "sync_core.py"
INSTALLER = ROOT / "deploy" / "install-vaprizziobot-source.sh"


class SyncError(RuntimeError):
    pass


def normalize(value: Any) -> str:
    return " ".join(
        re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).split()
    )


def localized_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("es") or value.get("en") or "")
    return str(value or "")


def variant_flavor(variant: dict[str, Any]) -> str:
    return " / ".join(
        localized_text(value).strip()
        for value in variant.get("values") or []
        if localized_text(value).strip()
    )


def parse_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def load_functions(names: set[str], extra: dict[str, Any] | None = None):
    tree = ast.parse(SYNC.read_text(encoding="utf-8"))
    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    namespace = {
        "Any": Any,
        "SheetProduct": Any,
        "SyncError": SyncError,
        "normalize": normalize,
        "localized_text": localized_text,
        "variant_flavor": variant_flavor,
        "parse_int": parse_int,
        "re": re,
    }
    namespace.update(extra or {})
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(SYNC), "exec"),
        namespace,
    )
    return namespace


class StockSyncRecoveryTests(unittest.TestCase):
    def test_concatenated_location_ids_no_longer_break_stock_update(self) -> None:
        namespace = load_functions(
            {"_location_id_for_variation", "stock_payload_for_variant"}
        )
        variant = {
            "inventory_levels": [
                {"location_id": "loc-a", "stock": 5},
                {"location_id": "loc-b", "stock": 2},
            ]
        }
        location = namespace["_location_id_for_variation"](
            variant,
            "loc-a,loc-b",
            -2,
        )
        self.assertEqual(location, "loc-a")
        payload = namespace["stock_payload_for_variant"](
            variant,
            4,
            "loc-a,loc-b",
        )
        self.assertEqual(
            payload,
            {"inventory_levels": [{"stock": 4, "location_id": "loc-a"}]},
        )

    def test_stale_ids_are_recovered_by_exact_model_and_flavor(self) -> None:
        calls = []

        def get_product(product_id):
            calls.append(("get_product", product_id))
            if product_id == "old-product":
                raise SyncError("stale")
            return {
                "id": "live-product",
                "name": {"es": "Lost Mary Dura"},
                "variants": [
                    {
                        "id": "grape-variant",
                        "product_id": "live-product",
                        "values": [{"es": "Grape Ice"}],
                        "sku": None,
                    },
                    {
                        "id": "watermelon-variant",
                        "product_id": "live-product",
                        "values": [{"es": "Watermelon Ice"}],
                        "sku": None,
                    },
                ],
            }

        def get_variant(product_id, variant_id):
            raise SyncError("stale")

        def find_product_by_name(model, *, exact_only=False):
            calls.append(("find", model, exact_only))
            return {"id": "live-product", "name": {"es": "Lost Mary Dura"}}

        namespace = load_functions(
            {"_live_product_and_variant"},
            {
                "get_product": get_product,
                "get_variant": get_variant,
                "find_product_by_name": find_product_by_name,
            },
        )
        sheet_product = SimpleNamespace(
            marca="Lost Mary Dura",
            sabor="Grape Ice",
            product_id="old-product",
            variant_id="old-variant",
        )
        product, variant = namespace["_live_product_and_variant"](sheet_product)
        self.assertEqual(product["id"], "live-product")
        self.assertEqual(variant["id"], "grape-variant")
        self.assertIsNone(variant["sku"])
        self.assertIn(("find", "Lost Mary Dura", True), calls)

    def test_stock_changes_use_atomic_variation_endpoint(self) -> None:
        source = SYNC.read_text(encoding="utf-8")
        self.assertIn('"action": "variation"', source)
        self.assertIn('f"products/{product_id}/variants/stock"', source)
        self.assertNotIn("(current_stock or 0) - subtract_stock", source)
        self.assertIn("exact_only=True", source)

    def test_subtract_stock_sends_variant_id_without_sku(self) -> None:
        calls = []
        sheet_product = SimpleNamespace(row=7, location_id="loc-a")
        live_variant = {
            "id": "variant-22",
            "product_id": "product-11",
            "inventory_levels": [{"location_id": "loc-a", "stock": 5}],
            "values": [{"es": "Grape Ice"}],
            "sku": None,
        }

        def api_request(method, path, payload=None, params=None):
            calls.append((method, path, payload))
            return []

        namespace = load_functions(
            {"modify_existing_product"},
            {
                "find_sheet_product": lambda marca, sabor: (
                    object(),
                    {},
                    sheet_product,
                ),
                "_live_product_and_variant": lambda product: (
                    {"id": "product-11", "name": {"es": "Lost Mary Dura"}},
                    dict(live_variant),
                ),
                "total_stock": lambda variant: 5,
                "stock_payload_for_variant": lambda *args: {},
                "_location_id_for_variation": lambda *args: "loc-a",
                "api_request": api_request,
                "get_variant": lambda product_id, variant_id: dict(live_variant),
                "synchronize_variant_to_sheet": lambda *args, **kwargs: {
                    "Marca": "Lost Mary Dura",
                    "Sabor": "Grape Ice",
                    "Stock": 4,
                    "Costo": 1,
                    "Precio venta": 2,
                    "Ganancia": 1,
                    "TiendaNube Product ID": "product-11",
                    "TiendaNube Variant ID": "variant-22",
                },
            },
        )
        result = namespace["modify_existing_product"](
            marca="Lost Mary Dura",
            sabor="Grape Ice",
            subtract_stock=1,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(
            calls,
            [(
                "POST",
                "products/product-11/variants/stock",
                {
                    "action": "variation",
                    "value": -1,
                    "id": "variant-22",
                    "location_id": "loc-a",
                },
            )],
        )

    def test_installer_deploys_sync_core(self) -> None:
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertIn('SOURCE_SYNC="$SOURCE_AGENT/tiendanube/app/sync_core.py"', source)
        self.assertIn('install -m 600 "$SOURCE_SYNC" "$TARGET_SYNC"', source)


if __name__ == "__main__":
    unittest.main()
