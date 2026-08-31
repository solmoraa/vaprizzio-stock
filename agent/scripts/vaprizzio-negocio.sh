#!/usr/bin/env bash

set -euo pipefail

PROJECT="/home/openclaw/.openclaw/workspace/vaprizziobot"

cd "$PROJECT"

export PYTHONPATH="$PROJECT/tiendanube${PYTHONPATH:+:$PYTHONPATH}"

exec "$PROJECT/.venv/bin/python" \
    "$PROJECT/scripts/vaprizzio_negocio.py" \
    "$@"
