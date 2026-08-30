#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${VAPRIZZIOBOT_TARGET:-/home/openclaw/.openclaw/workspace/vaprizziobot}"
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_ROOT="${VAPRIZZIOBOT_BACKUP_ROOT:-/home/openclaw/secure-backups}"
BACKUP="${BACKUP_ROOT}/vaprizziobot-dynamic-catalog-${STAMP}"

test -f "$TARGET/scripts/agente_vaprizzio.py"
test -f "$TARGET/AGENTS.md"
test -f "$TARGET/TOOLS.md"
test -x "$TARGET/.venv/bin/python"

umask 077
mkdir -p "$BACKUP"
cp -a "$TARGET/scripts/agente_vaprizzio.py" "$BACKUP/"
cp -a "$TARGET/AGENTS.md" "$TARGET/TOOLS.md" "$BACKUP/"
test -f "$TARGET/scripts/validar_catalogo_venta.py" \
  && cp -a "$TARGET/scripts/validar_catalogo_venta.py" "$BACKUP/" \
  || true

install -m 700 \
  "$SOURCE_DIR/vaprizziobot/validar_catalogo_venta.py" \
  "$TARGET/scripts/validar_catalogo_venta.py"

"$TARGET/.venv/bin/python" \
  "$SOURCE_DIR/vaprizziobot/patch_dynamic_catalog.py" \
  "$TARGET"

"$TARGET/.venv/bin/python" -m py_compile \
  "$TARGET/scripts/agente_vaprizzio.py" \
  "$TARGET/scripts/validar_catalogo_venta.py"

# Prueba local del normalizador. No importa modulos de Google ni toca la hoja.
"$TARGET/.venv/bin/python" - "$TARGET/scripts/agente_vaprizzio.py" <<'PY'
import ast
import json
import sys
from pathlib import Path
from typing import Any

path = Path(sys.argv[1])
tree = ast.parse(path.read_text(encoding="utf-8"))
node = next(
    item for item in tree.body
    if isinstance(item, ast.FunctionDef) and item.name == "normalizar_modelo"
)
namespace = {"Any": Any}
exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
normalize = namespace["normalizar_modelo"]
cases = {
    "Lost Mary Dura": "Lost Mary Dura",
    "Lost Mary Mixer 30k": "Lost Mary Mixer 30k",
    "Lost Mary Future 90k": "Lost Mary Future 90k",
    "Ice King": "Elfbar Ice King 40k",
}
assert all(normalize(value) == wanted for value, wanted in cases.items())
print(json.dumps({"ok": True, "autoprueba_modelos": len(cases)}))
PY

printf 'Correccion del catalogo dinamico instalada.\n'
printf 'Backup privado: %s\n' "$BACKUP"
printf 'No se modificaron ventas, stock ni el agente comercial multicanal.\n'
printf 'Ejecute ahora la validacion de solo lectura indicada en la salida final.\n'
