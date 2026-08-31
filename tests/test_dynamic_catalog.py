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
        self.assertEqual(agent.count("FIX CATALOGO DINAMICO VENTA 20260831"), 1)
        self.assertEqual(rules.count("FIN REGLAS CRITICAS VAP VENTA 20260831"), 1)
        self.assertIn("mensaje actual", rules)
        self.assertIn("No consultar stock antes", rules)
        self.assertIn("Lost Mary Dura", rules)
        self.assertLess(len(rules), 20_000)
        self.assertLess(len(rules.encode("utf-8")), 20_000)

    def test_replaces_a_previously_marked_but_stale_normalizer(self) -> None:
        self.patch()
        path = self.workspace / "scripts" / "agente_vaprizzio.py"
        stale = path.read_text(encoding="utf-8").replace(
            'return exact_aliases.get(key, original)',
            'return "Lost Mary Mixer 30k"',
        )
        path.write_text(stale, encoding="utf-8")
        self.patch()
        self.assertEqual(
            self.load_agent().normalizar_modelo("Lost Mary Dura"),
            "Lost Mary Dura",
        )

    def test_consolidates_previous_rule_blocks(self) -> None:
        old = '''
<!-- INICIO FIX PLATAFORMA VENTA 20260813 -->viejo<!-- FIN FIX PLATAFORMA VENTA 20260813 -->
<!-- INICIO FIX REGISTRO VENTA 20260814 -->viejo<!-- FIN FIX REGISTRO VENTA 20260814 -->
<!-- INICIO FIX CATALOGO DINAMICO VENTA 20260830 -->viejo<!-- FIN FIX CATALOGO DINAMICO VENTA 20260830 -->

## Interpretación de plataforma y forma de pago
regla conservada

## Mayúsculas en forma de pago
regla conservada

## Interpretación de plataforma y forma de pago
copia redundante

## Mayúsculas en forma de pago
copia redundante

## Alias de modelos
- `Lost Mary` → `Lost Mary Mixer`
- `Ice King` → `Elfbar Ice King 40k`
'''
        agents = self.workspace / "AGENTS.md"
        agents.write_text("# agente\n" + old, encoding="utf-8")
        self.patch()
        rules = agents.read_text(encoding="utf-8")
        self.assertNotIn("20260813", rules)
        self.assertNotIn("20260814", rules)
        self.assertNotIn("20260830", rules)
        self.assertEqual(rules.count("INICIO REGLAS CRITICAS VAP VENTA 20260831"), 1)
        self.assertEqual(
            rules.count("## Interpretación de plataforma y forma de pago"),
            1,
        )
        self.assertEqual(rules.count("## Mayúsculas en forma de pago"), 1)
        self.assertNotIn("`Lost Mary` → `Lost Mary Mixer`", rules)
        self.assertIn("`Ice King` → `Elfbar Ice King 40k`", rules)

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
            [
                sys.executable,
                str(deployed),
                "--modelo",
                "Lost Mary Dura",
                "--sabor",
                "Blueberry Watermelon",
                "--sabor",
                "Nuevo Sabor Futuro",
            ],
            text=True,
            encoding="utf-8",
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
        self.assertTrue(result["agents_md"]["menor_a_20000"])
        self.assertEqual(
            result["prueba_solicitada"]["modelo_catalogo"],
            "Lost Mary Dura",
        )
        self.assertEqual(len(result["prueba_solicitada"]["sabores"]), 2)

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

    def test_prompt_over_limit_fails_without_partial_writes(self) -> None:
        agents = self.workspace / "AGENTS.md"
        agents.write_text("x" * 20_000, encoding="utf-8")
        original_agent = (
            self.workspace / "scripts" / "agente_vaprizzio.py"
        ).read_text(encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(PATCHER), str(self.workspace)],
            text=True,
            encoding="utf-8",
            capture_output=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(agents.read_text(encoding="utf-8"), "x" * 20_000)
        self.assertEqual(
            (self.workspace / "scripts" / "agente_vaprizzio.py").read_text(
                encoding="utf-8"
            ),
            original_agent,
        )


if __name__ == "__main__":
    unittest.main()
