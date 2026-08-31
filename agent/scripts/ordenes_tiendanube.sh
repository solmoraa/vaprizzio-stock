#!/usr/bin/env bash

set -e

WORKSPACE="/home/openclaw/.openclaw/workspace/vaprizziobot"
PYTHON="$WORKSPACE/.venv/bin/python"

cd "$WORKSPACE"

if [ ! -x "$PYTHON" ]; then
    echo "ERROR: no existe el Python del entorno virtual:"
    echo "$PYTHON"
    exit 1
fi

exec "$PYTHON" "$WORKSPACE/scripts/ordenes_tiendanube.py" "$@"
