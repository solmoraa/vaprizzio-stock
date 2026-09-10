#!/usr/bin/env bash
set -euo pipefail

# Instala un respaldo periódico de Tiendanube a Google Sheets. El webhook
# sigue siendo inmediato; este timer recupera pagos si el proveedor no logró
# alcanzar el webhook público.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${VAPRIZZIOBOT_TARGET:-/home/openclaw/.openclaw/workspace/vaprizziobot}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_SOURCE="$REPO_ROOT/deploy/vaprizzio-reconcile.service"
TIMER_SOURCE="$REPO_ROOT/deploy/vaprizzio-reconcile.timer"
SCRIPT="$TARGET/scripts/reconciliar_pedidos_tiendanube.py"

test -x "$TARGET/.venv/bin/python"
test -x "$SCRIPT"
test -f "$SERVICE_SOURCE"
test -f "$TIMER_SOURCE"

install -d -m 700 "$UNIT_DIR"
install -m 644 "$SERVICE_SOURCE" "$UNIT_DIR/vaprizzio-reconcile.service"
install -m 644 "$TIMER_SOURCE" "$UNIT_DIR/vaprizzio-reconcile.timer"

systemctl --user daemon-reload
# Si existía un timer previo, enable --now no recarga su configuración en
# memoria. Reiniciarlo garantiza que tome el intervalo actual de 5 minutos.
systemctl --user enable vaprizzio-reconcile.timer
systemctl --user restart vaprizzio-reconcile.timer

printf 'Respaldo de Tiendanube habilitado cada 5 minutos.\n'
systemctl --user list-timers vaprizzio-reconcile.timer --no-pager
