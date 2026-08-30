#!/usr/bin/env bash
set -euo pipefail

OPENCLAW_USER="openclaw"
OPENCLAW_BIN="/home/openclaw/.npm-global/bin/openclaw"
STATE_FILE="/run/openclaw-vaprizziobot-watchdog.failures"

output=""
if output="$(timeout 30s /usr/sbin/runuser -l "$OPENCLAW_USER" -c "$OPENCLAW_BIN channels status --probe" 2>&1)" \
  && printf '%s\n' "$output" | grep -F 'Telegram vaprizziobot (vaprizziobot):' | grep -q 'running, connected' \
  && printf '%s\n' "$output" | grep -F 'Telegram vaprizziobot (vaprizziobot):' | grep -q 'works'; then
  rm -f "$STATE_FILE"
  exit 0
fi

failures=0
if [[ -r "$STATE_FILE" ]]; then
  read -r failures < "$STATE_FILE" || failures=0
fi
failures=$((failures + 1))
printf '%s\n' "$failures" > "$STATE_FILE"

logger -t openclaw-telegram-watchdog "Telegram vaprizziobot no está operativo (control $failures/2): ${output//$'\n'/ }"
if (( failures < 2 )); then
  exit 0
fi

rm -f "$STATE_FILE"
systemctl restart openclaw-gateway.service
logger -t openclaw-telegram-watchdog "Gateway reiniciado para recuperar Telegram vaprizziobot"
