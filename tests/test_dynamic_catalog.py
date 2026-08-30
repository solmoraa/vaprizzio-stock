from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATCHER = ROOT / "deploy" / "vaprizziobot" / "patch_dynamic_catalog.py"
VALIDATOR = ROOT / "deploy" / "vaprizziobot" / "validar_catalogo_venta.py"
INSTALLER = ROOT / "deploy" / "install-vaprizziobot-dynamic-catalog-fix.sh"


OLD_AGENT = '''
from typing import Any

def limpiar_texto_modelo(value: Any) -> str:
    return str(value or "").strip().lower()

def normalizar_modelo(value: Any) -> Any:
    text = limpiar_texto_modelo(value)
    if "lost mary" in text:
        return "Lost Mary Mixer 30k"
    if "ice king" in text:
        return "Elfbar Ice King 40k"
    return value

def validar_familia_modelo(value: Any) -> None:
    return None
'''


class DynamicCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="vaprizziobot-dynamic-")
        self.workspace = Path(self.temporary.name)
        (self.workspace / "scripts").mkdir(parents=True)
        (self.workspace / "AGENTS.md").write_text("# agente\n", encoding="utf-8")
        (self.workspace / "TOOLS.md").write_text("# tools\n", encoding="utf-8")
        (self.workspace / "scripts" / "agente_vaprizzio.py").write_text(
            OLD_AGENT,
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def patch(self) -> dict:
        output = subprocess.check_output(
            [sys.executable, str(PATCHER), str(self.workspace)],
            text=True,
            encoding="utf-8",
        )
        return json.loads(output)

    def load_agent(self):
        path = self.workspace / "scripts" / "agente_vaprizzio.py"
        spec = importlib.util.spec_from_file_location("fixture_agent", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_preserves_current_and_future_models(self) -> None:
        self.assertTrue(self.patch()["ok"])
        normalize = self.load_agent().normalizar_modelo
        expected = {
            "Lost Mary Dura": "Lost Mary Dura",
            "Lost Mary Mixer 30k": "Lost Mary Mixer 30k",
            "Lost Mary Galaxy 50k": "Lost Mary Galaxy 50k",
            "Nuevo Modelo 100k": "Nuevo Modelo 100k",
            "Ice King": "Elfbar Ice King 40k",
        }
        self.assertEqual({name: normalize(name) for name in expected}, expected)

    def test_patch_is_idempotent_and_blocks_stale_context(self) -> None:
        self.patch()
        self.patch()
        agent = (self.workspace / "scripts" / "agente_vaprizzio.py").read_text(
            encoding="utf-8"
        )
        rules = (self.workspace / "AGENTS.md").read_text(encoding="utf-8")
        self.assertEqual(agent.count("FIX CATALOGO DINAMICO VENTA 20260830"), 1)
        self.assertEqual(rules.count("FIN FIX CATALOGO DINAMICO VENTA 20260830"), 1)
        self.assertIn("mensaje actual", rules)
        self.assertIn("No consultar stock antes", rules)
        self.assertIn("Lost Mary Dura", rules)

    def test_validator_reviews_new_models_and_flavors_in_one_batch(self) -> None:
        self.patch()
        business = self.workspace / "tiendanube" / "app" / "business"
        business.mkdir(parents=True)
        for package in (
            self.workspace / "tiendanube" / "__init__.py",
            self.workspace / "tiendanube" / "app" / "__init__.py",
            business / "__init__.py",
        ):
            package.write_text("", encoding="utf-8")
        (business / "products.py").write_text(
            '''
def consultar_stock(marca=None, sabor=None, solo_disponibles=False):
    return {"productos": [
        {"marca":"Lost Mary Dura", "sabor":"Blueberry Watermelon"},
        {"marca":"Lost Mary Dura", "sabor":"Nuevo Sabor Futuro"},
        {"marca":"Modelo Recien Ingresado 80k", "sabor":"Cherry Galaxy"},
    ]}
''',
            encoding="utf-8",
        )
        deployed = self.workspace / "scripts" / "validar_catalogo_venta.py"
        shutil.copyfile(VALIDATOR, deployed)
        output = subprocess.check_output(
            [sys.executable, str(deployed)], text=True, encoding="utf-8"
        )
        result = json.loads(output)
        self.assertEqual(
            {
                "ok": result["ok"],
                "solo_lectura": result["solo_lectura"],
                "filas_revisadas": result["filas_revisadas"],
                "modelos_revisados": result["modelos_revisados"],
                "sabores_revisados": result["sabores_revisados"],
            },
            {
                "ok": True,
                "solo_lectura": True,
                "filas_revisadas": 3,
                "modelos_revisados": 2,
                "sabores_revisados": 3,
            },
        )
        self.assertEqual(result["modelos_alterados_por_alias"], [])
        self.assertEqual(result["combinaciones_duplicadas"], [])

    def test_installer_is_isolated_from_commercial_chat(self) -> None:
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("VAPRIZZIOBOT_TARGET", source)
        self.assertIn("scripts/agente_vaprizzio.py", source)
        self.assertNotIn("chat-vaprizzio-test.service", source)
        self.assertNotIn("extensions/vaprizzio-tools", source)
        self.assertNotIn("skills/ventas", source)

    def test_validator_is_read_only_and_uses_one_general_query(self) -> None:
        source = VALIDATOR.read_text(encoding="utf-8")
        self.assertEqual(source.count("consultar_stock("), 1)
        self.assertIn("marca=None", source)
        self.assertIn("solo_disponibles=False", source)
        for forbidden in (
            "registrar_venta",
            "restar_stock",
            "sumar_stock",
            "update_cell",
            "append_row",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
