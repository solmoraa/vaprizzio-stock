#!/usr/bin/env python3
"""Corrige la normalizacion de modelos del agente administrativo de Telegram.

Este archivo solo modifica el workspace ``vaprizziobot``. No forma parte del
backend que responde mensajes de WhatsApp, Instagram o Messenger.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any


PATCH_MARKER = "FIX CATALOGO DINAMICO VENTA 20260831"
PROMPT_LIMIT = 20_000

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
        "geek bar": "Geek Bar Pulse X",
        "geek bar pulse x": "Geek Bar Pulse X",
        "airmez": "Airmez Bluetooth 40k (Vape con Auriculares)",
        "maskking": "Maskking Extre 100K",
        "dummy": "Dummy 8k",
    }

    return exact_aliases.get(key, original)
'''


AGENT_RULES = r'''

<!-- INICIO REGLAS CRITICAS VAP VENTA 20260831 -->
## Ventas administrativas: reglas criticas

- Solo aplica al agente administrativo de Telegram. No modifica el agente
  comercial de WhatsApp, Instagram ni Messenger.
- Para cada venta usar modelo, sabor, cantidades, cliente, plataforma y pago del
  mensaje actual. No reutilizar esos datos desde mensajes o intentos anteriores.
- Modelos y sabores son dinamicos: provienen de Google Sheets. Conservar el
  nombre completo dado por el usuario y aplicar solo alias exactos no ambiguos.
  Nunca transformar por coincidencia parcial. `Lost Mary Dura`, `Lost Mary
  Mixer 30k` y futuros `Lost Mary ...` son productos diferentes.
- `Lost Mary` sin el resto del modelo es ambiguo: preguntar cual es y no
  convertirlo automaticamente en `Lost Mary Mixer 30k`.
- Cada producto debe conservar su combinacion modelo + sabor; nunca sustituirlo
  por otro modelo que tenga un sabor parecido.
- La plataforma debe estar expresamente indicada. Si falta, preguntar solo
  `¿Por qué medio realizaste la venta?`; no deducir venta presencial. Cuando
  este confirmada enviar `"plataforma_confirmada":true`. Frases como `me hablo
  por WhatsApp`, `me escribio por Instagram`, `me contacto por Messenger` o
  `la venta fue por Tienda Nube` confirman expresamente la plataforma; no
  volver a preguntarla.
- Usar la clave `forma_pago`. Efectivo se envia como `EFECTIVO`; no confundir
  forma de pago con plataforma.
- Registrar con una sola ejecucion de `agente_vaprizzio.py registrar-venta`.
  No consultar stock antes y no repetir una venta que devolvio `ok:false`.
- Ante `ok:false`, afirmar que no se registro nada e indicar solo el dato
  comercial a revisar. No mostrar scripts, comandos, JSON, rutas, hojas, filas,
  limites de API, dispatcher, trazas ni otros diagnosticos internos.
- Para cambiar solo el canal de una venta existente usar una vez
  `modificar-plataforma-venta`; no volver a registrar ni cambiar stock.
<!-- FIN REGLAS CRITICAS VAP VENTA 20260831 -->
'''


TOOL_RULES = r'''

<!-- INICIO TOOL VAP VENTA 20260831 -->
## Contrato administrativo de ventas

`registrar-venta` recibe las claves `cliente`, `productos`, `plataforma`,
`plataforma_confirmada` y `forma_pago`. Cada elemento de `productos` contiene
`marca`, `sabor` y `cantidad`. Los nombres se resuelven contra el catalogo vivo;
los alias solo se aplican por igualdad exacta. Una solicitud admite un intento.

`modificar-plataforma-venta` requiere `orden` y `plataforma`; cambia solamente
ese campo y nunca vuelve a registrar la venta.

Validacion de solo lectura de todos los modelos y sabores visibles en la hoja:

`/home/openclaw/.openclaw/workspace/vaprizziobot/.venv/bin/python /home/openclaw/.openclaw/workspace/vaprizziobot/scripts/validar_catalogo_venta.py`

Para probar un caso concreto sin escribir:

`/home/openclaw/.openclaw/workspace/vaprizziobot/.venv/bin/python /home/openclaw/.openclaw/workspace/vaprizziobot/scripts/validar_catalogo_venta.py --modelo "Lost Mary Dura" --sabor "Grape Ice" --sabor "Watermelon Ice"`

Ambas validaciones hacen una sola lectura general y no modifican stock ni ventas.
<!-- FIN TOOL VAP VENTA 20260831 -->
'''


OWNED_BLOCKS = (
    (
        "INICIO FIX PLATAFORMA VENTA 20260813",
        "FIN FIX PLATAFORMA VENTA 20260813",
    ),
    (
        "INICIO FIX REGISTRO VENTA 20260814",
        "FIN FIX REGISTRO VENTA 20260814",
    ),
    (
        "INICIO FIX CATALOGO DINAMICO VENTA 20260830",
        "FIN FIX CATALOGO DINAMICO VENTA 20260830",
    ),
    (
        "INICIO REGLAS CRITICAS VAP VENTA 20260831",
        "FIN REGLAS CRITICAS VAP VENTA 20260831",
    ),
)

OWNED_TOOL_BLOCKS = (
    (
        "INICIO TOOL MODIFICAR PLATAFORMA 20260813",
        "FIN TOOL MODIFICAR PLATAFORMA 20260813",
    ),
    (
        "INICIO TOOL REGISTRO VENTA 20260814",
        "FIN TOOL REGISTRO VENTA 20260814",
    ),
    (
        "INICIO TOOL CATALOGO DINAMICO VENTA 20260830",
        "FIN TOOL CATALOGO DINAMICO VENTA 20260830",
    ),
    (
        "INICIO TOOL VAP VENTA 20260831",
        "FIN TOOL VAP VENTA 20260831",
    ),
)


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


def remove_marked_blocks(
    source: str,
    markers: tuple[tuple[str, str], ...],
) -> str:
    """Quita solo bloques administrados por estos instaladores."""
    for start, end in markers:
        pattern = re.compile(
            rf"\s*<!--\s*{re.escape(start)}\s*-->.*?"
            rf"<!--\s*{re.escape(end)}\s*-->\s*",
            re.DOTALL,
        )
        source = pattern.sub("\n\n", source)
    return source.rstrip()


def remove_duplicate_section(source: str, heading: str) -> str:
    """Conserva la primera aparicion de una seccion Markdown repetida."""
    pattern = re.compile(
        rf"(?ms)^##\s+{re.escape(heading)}\s*\n.*?(?=^##\s+|\Z)"
    )
    matches = list(pattern.finditer(source))
    for match in reversed(matches[1:]):
        source = source[: match.start()] + source[match.end() :]
    return source


def compact_legacy_prompt(source: str) -> str:
    # El prompt historico tenia estas dos secciones duplicadas textualmente.
    # Se conserva su primera aparicion y se elimina solo la copia redundante.
    for heading in (
        "Interpretación de plataforma y forma de pago",
        "Mayúsculas en forma de pago",
    ):
        source = remove_duplicate_section(source, heading)

    # Esta regla estatica fue la causa del incidente: al existir ahora varios
    # modelos Lost Mary, el nombre de familia ya no identifica a uno concreto.
    source = re.sub(
        r"(?mi)^\s*-\s*`Lost Mary`\s*(?:→|->)\s*`Lost Mary Mixer(?: 30k)?`\s*$\n?",
        "",
        source,
    )
    return source.rstrip()


def prompt_size(source: str) -> dict[str, int]:
    return {
        "caracteres": len(source),
        "bytes_utf8": len(source.encode("utf-8")),
    }


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
    # Se reemplaza siempre para reparar instalaciones parciales o versiones
    # anteriores que ya tengan un marcador pero con una funcion desactualizada.
    source = re.sub(
        r"(?m)^# FIX CATALOGO DINAMICO VENTA \d+\r?\n",
        "",
        source,
    )
    source = replace_top_level_function(
        source,
        "normalizar_modelo",
        f"# {PATCH_MARKER}\n{NORMALIZER_SOURCE}",
    )
    ast.parse(source)
    validate_normalizer(source)

    agent_text = append_once(
        compact_legacy_prompt(
            remove_marked_blocks(
                agents_md.read_text(encoding="utf-8"),
                OWNED_BLOCKS,
            ),
        ),
        AGENT_RULES,
        "FIN REGLAS CRITICAS VAP VENTA 20260831",
    )
    size = prompt_size(agent_text)
    if max(size.values()) >= PROMPT_LIMIT:
        raise RuntimeError(
            "AGENTS.md sigue excediendo el limite seguro despues de consolidar "
            f"las reglas administradas: {size}. No se modifico el workspace."
        )

    tools_text = append_once(
        remove_marked_blocks(
            tools_md.read_text(encoding="utf-8"),
            OWNED_TOOL_BLOCKS,
        ),
        TOOL_RULES,
        "FIN TOOL VAP VENTA 20260831",
    )

    # Escribir recien despues de que todas las validaciones hayan pasado evita
    # dejar una instalacion a medias si el prompt base ya era demasiado grande.
    agent_script.write_text(source, encoding="utf-8")
    agents_md.write_text(agent_text, encoding="utf-8")
    tools_md.write_text(tools_text, encoding="utf-8")

    return {
        "ok": True,
        "target": str(target),
        "normalizacion": "alias exactos y catalogo dinamico",
        "autopruebas": 6,
        "agents_md": size,
        "limite_exclusivo": PROMPT_LIMIT,
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
