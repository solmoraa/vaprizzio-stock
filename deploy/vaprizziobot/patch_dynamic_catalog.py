#!/usr/bin/env python3
"""Corrige la normalizacion de modelos del agente administrativo de Telegram.

Este archivo solo modifica el workspace ``vaprizziobot``. No forma parte del
backend que responde mensajes de WhatsApp, Instagram o Messenger.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any


PATCH_MARKER = "FIX CATALOGO DINAMICO VENTA 20260830"

NORMALIZER_SOURCE = '''def normalizar_modelo(value: Any) -> Any:
    """Normaliza solo alias exactos; los modelos del catalogo son dinamicos."""
    import re
    import unicodedata

    if not isinstance(value, str):
        return value

    original = " ".join(value.strip().split())
    if not original:
        return value

    key = unicodedata.normalize("NFKD", original)
    key = "".join(char for char in key if not unicodedata.combining(char))
    key = re.sub(r"[^a-z0-9]+", " ", key.casefold()).strip()

    # Solo abreviaturas que identifican un modelo sin ambiguedad. Nunca usar
    # coincidencias parciales: por ejemplo, "Lost Mary Dura" no puede caer en
    # el alias historico "Lost Mary" -> "Lost Mary Mixer 30k".
    exact_aliases = {
        "ice king": "Elfbar Ice King 40k",
        "elfbar ice king": "Elfbar Ice King 40k",
        "elfbar 15k": "Elfbar BC 15k",
        "bc 15k": "Elfbar BC 15k",
        "te30k": "Elfbar TE30K",
        "te 30k": "Elfbar TE30K",
        "bc pro": "Elfbar Create BC pro 40k",
        "elfbar bc pro": "Elfbar Create BC pro 40k",
        "pro 45k": "Elfbar pro 45K",
        "elfbar pro 45k": "Elfbar pro 45K",
        "ignite nano": "Ignite v-nano",
        "ignite 155": "Ignite v155",
        "v155": "Ignite v155",
        "ignite 250": "Ignite v250",
        "v250": "Ignite v250",
        "ignite 300": "Ignite v300 slim",
        "v300": "Ignite v300 slim",
        "pulse x": "Geek Bar Pulse X",
        "geek bar pulse x": "Geek Bar Pulse X",
    }

    return exact_aliases.get(key, original)
'''


AGENT_RULES = r'''

<!-- INICIO FIX CATALOGO DINAMICO VENTA 20260830 -->
## Modelos y sabores dinamicos para registrar ventas

- Estas reglas pertenecen exclusivamente al agente administrativo de Telegram.
- Para una venta, tomar el modelo y el sabor del mensaje actual. Si el usuario
  los nombra, nunca reutilizar un modelo o sabor de una conversacion anterior.
- Los modelos y sabores se leen de Google Sheets y pueden cambiar sin modificar
  el agente. Pasar sus nombres completos tal como los escribio el usuario.
- Aplicar alias solamente cuando todo el nombre recibido sea una abreviatura
  exacta y no ambigua. Nunca aplicar un alias por coincidencia parcial.
- En particular, `Lost Mary Dura`, `Lost Mary Mixer 30k` y cualquier futuro
  modelo `Lost Mary ...` son modelos distintos. `Lost Mary Dura` nunca se
  convierte en `Lost Mary Mixer 30k`.
- Un sabor nuevo no autoriza a sustituir el modelo por otro que tenga un sabor
  parecido. La combinacion modelo + sabor debe conservarse.
- Para registrar una venta ejecutar directamente una sola vez
  `agente_vaprizzio.py registrar-venta`. No consultar stock antes: ese comando
  ya lee el catalogo, valida stock, registra la venta y descuenta la cantidad.
- Si devuelve `ok:false`, no volver a consultar la hoja ni repetir la venta en
  ese turno. Informar solamente que la venta no fue registrada y el dato
  comercial que el resultado indique que debe revisarse.
- No mencionar al usuario nombres de scripts, JSON, dispatcher, filas, hojas,
  limites de API ni diagnosticos internos.
<!-- FIN FIX CATALOGO DINAMICO VENTA 20260830 -->
'''


TOOL_RULES = r'''

<!-- INICIO TOOL CATALOGO DINAMICO VENTA 20260830 -->
## Catalogo dinamico del registrador de ventas

`registrar-venta` recibe el modelo y el sabor actuales sin traducirlos mediante
listas estaticas. El propio registro resuelve la combinacion contra Google
Sheets. Los alias solo se aplican por igualdad exacta.

Validacion de solo lectura de todos los modelos y sabores visibles en la hoja:

`/home/openclaw/.openclaw/workspace/vaprizziobot/.venv/bin/python /home/openclaw/.openclaw/workspace/vaprizziobot/scripts/validar_catalogo_venta.py`

La validacion hace una sola lectura general y no modifica stock ni ventas.
<!-- FIN TOOL CATALOGO DINAMICO VENTA 20260830 -->
'''


def replace_top_level_function(source: str, name: str, replacement: str) -> str:
    tree = ast.parse(source)
    node = next(
        (
            item
            for item in tree.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name == name
        ),
        None,
    )
    if node is None or node.end_lineno is None:
        raise RuntimeError(f"No se encontro la funcion {name}.")

    lines = source.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in source else "\n"
    rendered = replacement.replace("\n", newline)
    if not rendered.endswith(newline):
        rendered += newline
    lines[node.lineno - 1 : node.end_lineno] = [rendered]
    return "".join(lines)


def append_once(source: str, block: str, end_marker: str) -> str:
    if end_marker in source:
        return source
    newline = "\r\n" if "\r\n" in source else "\n"
    rendered = block.replace("\n", newline)
    return source.rstrip() + rendered + newline


def validate_normalizer(source: str) -> None:
    tree = ast.parse(source)
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == "normalizar_modelo"
    )
    namespace: dict[str, Any] = {"Any": Any}
    ast.fix_missing_locations(node)
    exec(compile(ast.Module(body=[node], type_ignores=[]), "normalizer", "exec"), namespace)
    normalize = namespace["normalizar_modelo"]

    expected = {
        "Lost Mary Dura": "Lost Mary Dura",
        "Lost Mary Mixer 30k": "Lost Mary Mixer 30k",
        "Lost Mary Galaxy 50k": "Lost Mary Galaxy 50k",
        "Nuevo Modelo Futuro 90k": "Nuevo Modelo Futuro 90k",
        "Ice King": "Elfbar Ice King 40k",
        "Pulse X": "Geek Bar Pulse X",
    }
    failures = {
        value: {"expected": wanted, "actual": normalize(value)}
        for value, wanted in expected.items()
        if normalize(value) != wanted
    }
    if failures:
        raise RuntimeError(f"Fallo la autoprueba de modelos: {failures}")


def patch_workspace(target: Path) -> dict[str, Any]:
    agent_script = target / "scripts" / "agente_vaprizzio.py"
    agents_md = target / "AGENTS.md"
    tools_md = target / "TOOLS.md"

    for path in (agent_script, agents_md, tools_md):
        if not path.is_file():
            raise FileNotFoundError(f"No existe el archivo requerido: {path}")

    source = agent_script.read_text(encoding="utf-8")
    if PATCH_MARKER not in source:
        source = replace_top_level_function(
            source,
            "normalizar_modelo",
            f"# {PATCH_MARKER}\n{NORMALIZER_SOURCE}",
        )
    ast.parse(source)
    validate_normalizer(source)
    agent_script.write_text(source, encoding="utf-8")

    agent_text = append_once(
        agents_md.read_text(encoding="utf-8"),
        AGENT_RULES,
        "FIN FIX CATALOGO DINAMICO VENTA 20260830",
    )
    agents_md.write_text(agent_text, encoding="utf-8")

    tools_text = append_once(
        tools_md.read_text(encoding="utf-8"),
        TOOL_RULES,
        "FIN TOOL CATALOGO DINAMICO VENTA 20260830",
    )
    tools_md.write_text(tools_text, encoding="utf-8")

    return {
        "ok": True,
        "target": str(target),
        "normalizacion": "alias exactos y catalogo dinamico",
        "autopruebas": 6,
    }


def main() -> int:
    if len(sys.argv) != 2:
        print("Uso: patch_dynamic_catalog.py RUTA_WORKSPACE", file=sys.stderr)
        return 2
    try:
        print(json.dumps(patch_workspace(Path(sys.argv[1]).resolve()), ensure_ascii=False))
        return 0
    except Exception as exc:  # el instalador necesita un diagnostico claro
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
