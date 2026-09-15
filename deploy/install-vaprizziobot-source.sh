#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${VAPRIZZIOBOT_TARGET:-/home/openclaw/.openclaw/workspace/vaprizziobot}"
BACKUP_ROOT="${VAPRIZZIOBOT_BACKUP_ROOT:-/home/openclaw/secure-backups}"
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP="${BACKUP_ROOT}/vaprizziobot-source-${STAMP}"

SOURCE_AGENT="$REPO_ROOT/agent"
SOURCE_SALES="$SOURCE_AGENT/tiendanube/app/business/sales.py"
SOURCE_SYNC="$SOURCE_AGENT/tiendanube/app/sync_core.py"
SOURCE_ORDER_SYNC="$SOURCE_AGENT/tiendanube/app/order_sync.py"
SOURCE_ORDER_SYNC_V2="$SOURCE_AGENT/tiendanube/app/order_sync_v2.py"
SOURCE_SCRIPT="$SOURCE_AGENT/scripts/agente_vaprizzio.py"
SOURCE_RECONCILE="$SOURCE_AGENT/scripts/reconciliar_pedidos_tiendanube.py"
SOURCE_PROMPT="$SOURCE_AGENT/AGENTS.md"
SOURCE_TOOLS="$SOURCE_AGENT/TOOLS.md"
SOURCE_SKILL="$SOURCE_AGENT/skills/vaprizzio-sheets/SKILL.md"

TARGET_SALES="$TARGET/tiendanube/app/business/sales.py"
TARGET_SYNC="$TARGET/tiendanube/app/sync_core.py"
TARGET_ORDER_SYNC="$TARGET/tiendanube/app/order_sync.py"
TARGET_ORDER_SYNC_V2="$TARGET/tiendanube/app/order_sync_v2.py"
TARGET_SCRIPT="$TARGET/scripts/agente_vaprizzio.py"
TARGET_RECONCILE="$TARGET/scripts/reconciliar_pedidos_tiendanube.py"
TARGET_PROMPT="$TARGET/AGENTS.md"
TARGET_TOOLS="$TARGET/TOOLS.md"
TARGET_SKILL="$TARGET/skills/vaprizzio-sheets/SKILL.md"

for path in \
  "$SOURCE_SALES" "$SOURCE_SYNC" "$SOURCE_ORDER_SYNC" "$SOURCE_ORDER_SYNC_V2" "$SOURCE_SCRIPT" "$SOURCE_RECONCILE" "$SOURCE_PROMPT" "$SOURCE_TOOLS" "$SOURCE_SKILL" \
  "$TARGET_SALES" "$TARGET_SYNC" "$TARGET_ORDER_SYNC" "$TARGET_ORDER_SYNC_V2" "$TARGET_SCRIPT" "$TARGET_PROMPT" "$TARGET_TOOLS" "$TARGET_SKILL"; do
  test -f "$path"
done
test -x "$TARGET/.venv/bin/python"

PROMPT_BYTES="$(wc -c < "$SOURCE_PROMPT")"
PROMPT_CHARS="$("$TARGET/.venv/bin/python" - "$SOURCE_PROMPT" <<'PY'
from pathlib import Path
import sys
print(len(Path(sys.argv[1]).read_text(encoding="utf-8")))
PY
)"
test "$PROMPT_BYTES" -lt 20000
test "$PROMPT_CHARS" -lt 20000

umask 077
mkdir -p \
  "$BACKUP/scripts" \
  "$BACKUP/tiendanube/app/business" \
  "$BACKUP/skills/vaprizzio-sheets"
cp -a "$TARGET_SALES" "$BACKUP/tiendanube/app/business/sales.py"
cp -a "$TARGET_SYNC" "$BACKUP/tiendanube/app/sync_core.py"
cp -a "$TARGET_ORDER_SYNC" "$BACKUP/tiendanube/app/order_sync.py"
cp -a "$TARGET_ORDER_SYNC_V2" "$BACKUP/tiendanube/app/order_sync_v2.py"
cp -a "$TARGET_SCRIPT" "$BACKUP/scripts/agente_vaprizzio.py"
if test -f "$TARGET_RECONCILE"; then
  cp -a "$TARGET_RECONCILE" "$BACKUP/scripts/reconciliar_pedidos_tiendanube.py"
fi
cp -a "$TARGET_PROMPT" "$BACKUP/AGENTS.md"
cp -a "$TARGET_TOOLS" "$BACKUP/TOOLS.md"
cp -a "$TARGET_SKILL" "$BACKUP/skills/vaprizzio-sheets/SKILL.md"

install -m 600 "$SOURCE_SALES" "$TARGET_SALES"
install -m 600 "$SOURCE_SYNC" "$TARGET_SYNC"
install -m 600 "$SOURCE_ORDER_SYNC" "$TARGET_ORDER_SYNC"
install -m 600 "$SOURCE_ORDER_SYNC_V2" "$TARGET_ORDER_SYNC_V2"
install -m 700 "$SOURCE_SCRIPT" "$TARGET_SCRIPT"
install -m 700 "$SOURCE_RECONCILE" "$TARGET_RECONCILE"
install -m 600 "$SOURCE_PROMPT" "$TARGET_PROMPT"
install -m 600 "$SOURCE_TOOLS" "$TARGET_TOOLS"
install -m 600 "$SOURCE_SKILL" "$TARGET_SKILL"

"$TARGET/.venv/bin/python" -m py_compile \
  "$TARGET_SALES" \
  "$TARGET_SYNC" \
  "$TARGET_ORDER_SYNC" \
  "$TARGET_ORDER_SYNC_V2" \
  "$TARGET_SCRIPT" \
  "$TARGET_RECONCILE"

# Autoprueba determinista y sin escrituras en Google Sheets.
"$TARGET/.venv/bin/python" - "$TARGET_SALES" <<'PY'
import ast
import json
import sys
from pathlib import Path
from typing import Any

path = Path(sys.argv[1])
tree = ast.parse(path.read_text(encoding="utf-8"))
wanted = {"ALIAS_MODELOS", "modelo_para_ventas"}
nodes = []
for node in tree.body:
    if isinstance(node, ast.Assign):
        if any(isinstance(target, ast.Name) and target.id in wanted for target in node.targets):
            nodes.append(node)
    elif isinstance(node, ast.FunctionDef) and node.name in wanted:
        nodes.append(node)

class BusinessError(Exception):
    pass

def texto(value: Any) -> str:
    return str(value or "").strip()

def normalizar(value: Any) -> str:
    return " ".join(texto(value).casefold().replace("-", " ").split())

namespace = {
    "Any": Any,
    "BusinessError": BusinessError,
    "texto": texto,
    "normalizar": normalizar,
}
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
resolve = namespace["modelo_para_ventas"]
cases = {
    "Lost Mary Dura": "Lost Mary Dura",
    "Lost Mary Future 90k": "Lost Mary Future 90k",
    "Elfbar BC 15k": "Elfbar 15K",
}
assert all(resolve(value) == expected for value, expected in cases.items())
print(json.dumps({"ok": True, "autoprueba_modelos_ventas": len(cases)}))
PY

printf 'Codigo fuente del agente administrativo instalado.\n'
printf 'Backup privado: %s\n' "$BACKUP"
printf 'AGENTS.md: %s caracteres, %s bytes UTF-8.\n' \
  "$PROMPT_CHARS" "$PROMPT_BYTES"
printf 'No se modificaron ventas, stock ni el agente comercial multicanal.\n'
