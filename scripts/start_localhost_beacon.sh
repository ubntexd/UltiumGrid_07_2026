#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIDFILE="${XDG_RUNTIME_DIR:-/tmp}/ultiumgrid-port-beacon.pid"
LOGFILE="${XDG_RUNTIME_DIR:-/tmp}/ultiumgrid-port-beacon.log"

if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "beacon already running pid=$(cat "$PIDFILE")"
  exit 0
fi

nohup python3 "$ROOT/scripts/port_beacon.py" >"$LOGFILE" 2>&1 &
echo $! >"$PIDFILE"
echo "beacon started pid=$(cat "$PIDFILE") log=$LOGFILE"
