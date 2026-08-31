#!/usr/bin/env bash

set -e

PROJECT_DIR="/home/openclaw/.openclaw/workspace/vaprizziobot"

cd "$PROJECT_DIR"

export PYTHONPATH="$PROJECT_DIR/tiendanube"

exec "$PROJECT_DIR/.venv/bin/python" \
  "$PROJECT_DIR/scripts/herramientas_vaprizzio.py" \
  "$@"
