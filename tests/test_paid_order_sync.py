from __future__ import annotations

import ast
import re
import unicodedata
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SYNC_V2 = ROOT / "agent" / "tiendanube" / "app" / "order_sync_v2.py"
SYNC = ROOT / "agent" / "tiendanube" / "app" / "order_sync.py"
INSTALLER = ROOT / "deploy" / "install-vaprizziobot-source.sh"
RECONCILE = ROOT / "agent" / "scripts" / "reconciliar_pedidos_tiendanube.py"
RECONCILE_INSTALLER = ROOT / "deploy" / "install-vaprizzio-reconciliation-service.sh"


def load_payment_functions() -> dict[str, Any]:
    tree = ast.parse(SYNC_V2.read_text(encoding="utf-8"))
    wanted = {"PAID_PAYMENT_STATUSES", "clean_text", "order_is_paid_v2"}
    nodes = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id in wanted
                for target in node.targets
            )
        )
        or isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    namespace: dict[str, Any] = {
        "Any": Any,
        "re": re,
        "unicodedata": unicodedata,
    }
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(SYNC_V2), "exec"),
        namespace,
    )
    return namespace


class PaidOrderSyncTests(unittest.TestCase):
    def test_received_and_paid_statuses_register_a_sale(self) -> None:
        is_paid = load_payment_functions()["order_is_paid_v2"]

        for status in ("paid", "Recibido", "approved", "accredited"):
            with self.subTest(status=status):
                self.assertTrue(is_paid({"payment_status": status}))

        self.assertFalse(is_paid({"payment_status": "pending"}))

    def test_paid_order_does_not_force_delivered_status(self) -> None:
        source = SYNC_V2.read_text(encoding="utf-8")
        self.assertNotIn('preserved_status = "Entregado"', source)
        self.assertIn("preserved_status = read_existing_status(order_id)", source)

    def test_rewriting_an_existing_order_has_a_defined_start_row(self) -> None:
        source = SYNC.read_text(encoding="utf-8")
        self.assertIn("start_row = min(old_rows)", source)
        self.assertIn("start_row = last_data_row + 1", source)
        ast.parse(source)

    def test_installer_deploys_the_paid_order_sync_files(self) -> None:
        installer = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("SOURCE_ORDER_SYNC=", installer)
        self.assertIn("SOURCE_ORDER_SYNC_V2=", installer)
        self.assertIn('install -m 600 "$SOURCE_ORDER_SYNC" "$TARGET_ORDER_SYNC"', installer)
        self.assertIn('install -m 600 "$SOURCE_ORDER_SYNC_V2" "$TARGET_ORDER_SYNC_V2"', installer)

    def test_reconciliation_is_limited_and_uses_paid_order_filter(self) -> None:
        source = RECONCILE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertIn('"payment_status": "paid"', source)
        self.assertIn('"updated_at_min": iso_since(days)', source)
        self.assertIn('"per_page": 30', source)
        self.assertIn('if args.days < 1 or args.days > 31:', source)
        self.assertIn('previously_paid=known_before', source)
        self.assertTrue(any(
            isinstance(node, ast.FunctionDef) and node.name == "reconcile"
            for node in tree.body
        ))

    def test_reconciliation_installer_uses_a_user_timer(self) -> None:
        installer = RECONCILE_INSTALLER.read_text(encoding="utf-8")
        self.assertIn("systemctl --user daemon-reload", installer)
        self.assertIn(
            "systemctl --user restart vaprizzio-reconcile.timer",
            installer,
        )


if __name__ == "__main__":
    unittest.main()
