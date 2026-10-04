from __future__ import annotations

import ast
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SALES = ROOT / "agent" / "tiendanube" / "app" / "business" / "sales.py"
AGENT = ROOT / "agent" / "scripts" / "agente_vaprizzio.py"
PROMPT = ROOT / "agent" / "AGENTS.md"
SKILL = ROOT / "agent" / "skills" / "vaprizzio-sheets" / "SKILL.md"
INSTALLER = ROOT / "deploy" / "install-vaprizziobot-source.sh"
SCHEMA_REPAIR = ROOT / "agent" / "scripts" / "reparar_esquema_ventas.py"


class BusinessError(Exception):
    pass


def texto(value: Any) -> str:
    return str(value or "").strip()


def normalizar(value: Any) -> str:
    return " ".join(texto(value).casefold().replace("-", " ").split())


def columna_a_letra(number: int) -> str:
    return chr(64 + number)


def load_resolution_functions() -> dict[str, Any]:
    tree = ast.parse(SALES.read_text(encoding="utf-8"))
    wanted = {
        "ALIAS_MODELOS",
        "modelo_para_ventas",
        "opciones_dropdown_celda",
        "modelo_dropdown_exacto",
    }
    nodes = []

    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            names = {
                target.id
                for target in (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                if isinstance(target, ast.Name)
            }
            if names & wanted:
                nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in wanted:
            nodes.append(node)

    namespace = {
        "Any": Any,
        "BusinessError": BusinessError,
        "texto": texto,
        "normalizar": normalizar,
        "columna_a_letra": columna_a_letra,
    }
    module = ast.Module(body=nodes, type_ignores=[])
    exec(compile(module, str(SALES), "exec"), namespace)
    return namespace


def load_insertion_functions() -> dict[str, Any]:
    tree = ast.parse(SALES.read_text(encoding="utf-8"))
    nodes = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in {
            "fila_es_orden_numerica",
            "encontrar_fila_insercion",
        }
    ]

    def celda(row, column):
        if not column or len(row) < column:
            return ""
        return row[column - 1]

    namespace = {
        "Any": Any,
        "BusinessError": BusinessError,
        "normalizar": normalizar,
        "celda": celda,
    }
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(SALES), "exec"),
        namespace,
    )
    return namespace


def load_batch_functions() -> dict[str, Any]:
    tree = ast.parse(AGENT.read_text(encoding="utf-8"))
    wanted = {
        "normalizar_modelo",
        "normalizar_productos",
        "requerido",
        "datos_venta",
        "ejecutar_registrar_ventas",
    }
    nodes = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    registered: list[dict[str, Any]] = []

    def registrar_ventas_manuales(ventas):
        registered.extend(ventas)
        return {"ok": True, "ventas": ventas}

    namespace = {
        "Any": Any,
        "BusinessError": BusinessError,
        "registrar_ventas_manuales": registrar_ventas_manuales,
    }
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(AGENT), "exec"),
        namespace,
    )
    namespace["registered"] = registered
    return namespace


class SpreadsheetFixture:
    def __init__(self, *, condition: str, values: list[dict[str, str]], rows=None):
        self.condition = condition
        self.condition_values = values
        self.rows = rows or []
        self.requested_ranges: list[str] = []

    def fetch_sheet_metadata(self, params):
        return {
            "sheets": [{
                "data": [{
                    "rowData": [{
                        "values": [{
                            "dataValidation": {
                                "condition": {
                                    "type": self.condition,
                                    "values": self.condition_values,
                                }
                            }
                        }]
                    }]
                }]
            }]
        }

    def values_get(self, source_range):
        self.requested_ranges.append(source_range)
        return {"values": self.rows}


class WorksheetFixture:
    title = "Ventas Agosto"

    def __init__(self, spreadsheet):
        self.spreadsheet = spreadsheet


class SalesModelResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.namespace = load_resolution_functions()

    def test_new_models_are_not_blocked_or_fuzzily_replaced(self) -> None:
        resolve = self.namespace["modelo_para_ventas"]
        self.assertEqual(resolve("Lost Mary Dura"), "Lost Mary Dura")
        self.assertEqual(resolve("Lost Mary Galaxy 90k"), "Lost Mary Galaxy 90k")
        self.assertNotEqual(resolve("Lost Mary Dura"), "Lost Mary Mixer 30k")

    def test_only_exact_legacy_equivalences_are_applied(self) -> None:
        resolve = self.namespace["modelo_para_ventas"]
        self.assertEqual(resolve("Elfbar BC 15k"), "Elfbar 15K")
        self.assertEqual(resolve("Elfbar Ice King 40k"), "Elfbar Ice king")

    def test_sale_does_not_read_dropdown_metadata_per_row(self) -> None:
        spreadsheet = SpreadsheetFixture(
            condition="ONE_OF_RANGE",
            values=[{"userEnteredValue": "Catalogo!A2:A"}],
            rows=[["Lost Mary Mixer 30k"], ["Lost Mary Dura"], ["Lost Mary Dura"]],
        )
        worksheet = WorksheetFixture(spreadsheet)
        exact = self.namespace["modelo_dropdown_exacto"](
            worksheet,
            2,
            4,
            "Lost Mary Dura",
        )
        self.assertEqual(exact, "Lost Mary Dura")
        self.assertEqual(spreadsheet.requested_ranges, [])

    def test_missing_dropdown_metadata_does_not_reject_catalog_model(self) -> None:
        spreadsheet = SpreadsheetFixture(condition="CUSTOM_FORMULA", values=[])
        worksheet = WorksheetFixture(spreadsheet)
        exact = self.namespace["modelo_dropdown_exacto"](
            worksheet,
            2,
            4,
            "Lost Mary Dura",
        )
        self.assertEqual(exact, "Lost Mary Dura")

    def test_stale_dropdown_never_replaces_catalog_model(self) -> None:
        spreadsheet = SpreadsheetFixture(
            condition="ONE_OF_LIST",
            values=[{"userEnteredValue": "Lost Mary Mixer 30k"}],
        )
        worksheet = WorksheetFixture(spreadsheet)
        exact = self.namespace["modelo_dropdown_exacto"](
            worksheet,
            2,
            4,
            "Lost Mary Dura",
        )
        self.assertEqual(exact, "Lost Mary Dura")
        self.assertEqual(spreadsheet.requested_ranges, [])

    def test_multiple_sales_inherit_only_explicit_previous_payment_and_channel(self) -> None:
        namespace = load_batch_functions()
        result = namespace["ejecutar_registrar_ventas"]({
            "ventas": [
                {
                    "cliente": "Juan",
                    "productos": [{"marca": "Elfbar Ice King", "sabor": "Mango"}],
                    "plataforma": "Instagram",
                    "plataforma_confirmada": True,
                    "forma_pago": "Mercado Pago",
                },
                {
                    "cliente": "Martina",
                    "productos": [{"marca": "Elfbar Ice King", "sabor": "Miami Mint"}],
                    "misma_forma_anterior": True,
                },
            ]
        })
        self.assertTrue(result["ok"])
        second = namespace["registered"][1]
        self.assertEqual(second["cliente"], "Martina")
        self.assertEqual(second["plataforma"], "Instagram")
        self.assertEqual(second["forma_pago"], "Mercado Pago")
        self.assertEqual(second["productos"][0]["marca"], "Elfbar Ice King 40k")

    def test_sale_prompt_never_uses_catalog_validator_automatically(self) -> None:
        prompt = PROMPT.read_text(encoding="utf-8")
        tools = (ROOT / "agent" / "TOOLS.md").read_text(encoding="utf-8")
        self.assertIn("nunca una\n  ejecución por cada venta", prompt)
        self.assertIn("Nunca ejecutarlas antes, durante o después", tools)
        self.assertIn('"registrar-ventas": ejecutar_registrar_ventas', AGENT.read_text(encoding="utf-8"))

    def test_manual_sale_reuses_catalog_and_skips_historical_chip_scans(self) -> None:
        source = SALES.read_text(encoding="utf-8")
        register_start = source.index("def registrar_venta_manual(")
        register_end = source.index("def registrar_ventas_manuales(", register_start)
        write_start = source.index("def escribir_filas_venta(")
        write_end = source.index("# ============================================================\n# STOCK", write_start)
        register = source[register_start:register_end]
        write = source[write_start:write_end]
        self.assertIn("catalogo_contexto = sheet_product_context()", register)
        self.assertIn("catalogo=catalogo", register)
        self.assertNotIn("aplicar_chips_vape(", write)
        self.assertIn("sheet_context=item.get(\"sheet_context\")", source)

    def test_new_sales_are_appended_after_the_last_order(self) -> None:
        insertion = load_insertion_functions()["encontrar_fila_insercion"]

        class Worksheet:
            title = "Ventas Octubre"

            def get_all_values(self):
                return [
                    ["Orden", "Cantidad", "Precio Venta", "Ganancia"],
                    ["1", "1", "100", "20"],
                    ["2", "1", "100", "20"],
                    ["", "", "200", "40"],
                ]

        row, summary = insertion(
            Worksheet(),
            {
                normalizar("Orden"): 1,
                normalizar("Precio Venta"): 3,
                normalizar("Ganancia"): 4,
            },
        )
        self.assertEqual((row, summary), (4, 4))

        source = SALES.read_text(encoding="utf-8")
        write_start = source.index("def escribir_filas_venta(")
        write_end = source.index("# ============================================================\n# STOCK", write_start)
        write = source[write_start:write_end]
        self.assertIn("row=summary_row", write)
        self.assertIn("source_row = start_row - 1", write)
        self.assertNotIn("inherit_from_before=False", write)

    def test_sale_schema_and_cancellation_are_available_to_the_agent(self) -> None:
        sync = (ROOT / "agent" / "tiendanube" / "app" / "order_sync.py").read_text(
            encoding="utf-8"
        )
        agent = AGENT.read_text(encoding="utf-8")
        prompt = PROMPT.read_text(encoding="utf-8")

        self.assertIn('for header in ("Vape", "Sabor")', sync)
        self.assertIn('"insertDimension"', sync)
        self.assertIn('"cancelar-orden": ejecutar_cancelar_orden', agent)
        self.assertIn('sheet_name=payload.get("hoja")', agent)
        self.assertIn("cancelar-orden", prompt)

    def test_schema_repair_is_idempotent_and_uses_sheets_retry(self) -> None:
        source = SCHEMA_REPAIR.read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertTrue(any(
            isinstance(node, ast.FunctionDef) and node.name == "repair"
            for node in tree.body
        ))
        self.assertIn("legacy.ensure_sales_headers(worksheet)", source)
        self.assertIn("legacy.run_with_sheets_retry", source)

    def test_stale_conflicting_lists_were_removed(self) -> None:
        sales_source = SALES.read_text(encoding="utf-8")
        agent_source = AGENT.read_text(encoding="utf-8")
        self.assertNotIn("MODELOS_VENTAS", sales_source)
        self.assertNotIn("get_close_matches", sales_source)
        self.assertNotIn("MODEL_ALIASES =", agent_source)
        self.assertNotIn('"lost mary": "Lost Mary Mixer 30k"', agent_source)
        self.assertNotIn("def validar_familia_modelo", agent_source)

    def test_explicit_contact_phrase_confirms_platform(self) -> None:
        prompt = PROMPT.read_text(encoding="utf-8")
        skill = SKILL.read_text(encoding="utf-8")
        self.assertIn("me hablo\n  por WhatsApp", prompt)
        self.assertIn("me habló por WhatsApp", skill)
        self.assertEqual(skill.count("## Separación entre plataforma y forma de pago"), 1)
        self.assertLess(len(prompt), 20_000)
        self.assertLess(len(prompt.encode("utf-8")), 20_000)

    def test_multiple_sales_prompt_keeps_payment_and_channel_already_given(self) -> None:
        prompt = PROMPT.read_text(encoding="utf-8")
        skill = SKILL.read_text(encoding="utf-8")
        self.assertIn("registrar-ventas", prompt)
        self.assertIn("pago por MP", prompt)
        self.assertIn("me habló por WhatsApp", prompt)
        self.assertIn("misma_forma_anterior:true", prompt)
        self.assertIn("No vuelvas a pedir datos ya dichos", skill)
        self.assertIn("misma_forma_anterior:true", skill)

    def test_installer_only_targets_administrative_agent(self) -> None:
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("VAPRIZZIOBOT_TARGET", source)
        self.assertIn('SOURCE_AGENT="$REPO_ROOT/agent"', source)
        self.assertIn('SOURCE_SALES="$SOURCE_AGENT/tiendanube/app/business/sales.py"', source)
        self.assertIn('SOURCE_TOOLS="$SOURCE_AGENT/TOOLS.md"', source)
        self.assertNotIn("chat-vaprizzio-test.service", source)
        self.assertNotIn("extensions/vaprizzio-tools", source)


if __name__ == "__main__":
    unittest.main()
