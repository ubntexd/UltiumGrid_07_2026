#!/usr/bin/env bash
set -euo pipefail
PIDFILE=/tmp/cloudflared-ultium.pid
LOG=/tmp/cloudflared-ultium-live.log
URLFILE=/tmp/ultium_public_url.txt
if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "already running pid=$(cat "$PIDFILE") url=$(cat "$URLFILE" 2>/dev/null || true)"
  exit 0
fi
nohup cloudflared tunnel --url http://127.0.0.1:8080 --no-autoupdate >"$LOG" 2>&1 &
echo $! >"$PIDFILE"
for i in $(seq 1 40); do
  URL=$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$LOG" | tail -1)
  if [[ -n "$URL" ]]; then
    CODE=$(curl -sS -o /dev/null -w "%{http_code}" --connect-timeout 5 "$URL/" || true)
    if [[ "$CODE" == "200" ]]; then
      echo "$URL" >"$URLFILE"
      echo "$URL"
      exit 0
    fi
  fi
  sleep 1
done
exit 1
