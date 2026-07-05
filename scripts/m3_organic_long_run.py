#!/usr/bin/env python3
"""Run organique Module 3 — 24-48h, sans intervention, superviseur actif.

Échantillonne l'état toutes les INTERVAL_S secondes jusqu'à DURATION_H heures.
Ne modifie pas la config (seuils production : cycle_trigger=15, idle=20, stuck=15).

Usage:
  python3 scripts/m3_organic_long_run.py
  python3 scripts/m3_organic_long_run.py --duration-h 48 --interval-s 300
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_API = "http://127.0.0.1:18000"
OUT_DIR = ROOT / "docs" / "proofs" / "m3_organic_long_run"


def http_get(base: str, path: str) -> dict | None:
    try:
        with urllib.request.urlopen(base + path, timeout=45) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"_error": str(exc)}


def sql(query: str) -> str:
    try:
        return subprocess.check_output(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "db",
                "psql",
                "-U",
                "ultium",
                "-d",
                "ultiumgrid",
                "-t",
                "-A",
                "-c",
                query,
            ],
            cwd=ROOT,
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
    except subprocess.CalledProcessError as exc:
        return f"SQL_ERROR: {exc.output}"


def snapshot(api: str, seq: int) -> dict:
    ts = datetime.now(timezone.utc)
    running = http_get(api, "/api/running") or {}
    supervision = http_get(api, "/api/supervision") or {}
    cfg = running.get("config") or {}
    grid = running.get("grid") or {}
    recap = running.get("grid_recap") or {}

    matched_sql = ""
    if running.get("cycle_id"):
        matched_sql = sql(
            f"SELECT count(*) FROM trades WHERE cycle_id={running['cycle_id']} "
            "AND level_index IS NOT NULL;"
        )

    recent_cycles = sql(
        "SELECT id, status, close_reason, gross_pnl, net_pnl, "
        "to_char(closed_at,'YYYY-MM-DD HH24:MI:SS') "
        "FROM cycles ORDER BY id DESC LIMIT 5;"
    )
    recent_attempts = sql(
        "SELECT id, purpose, outcome, "
        "to_char(created_at,'YYYY-MM-DD HH24:MI:SS') "
        "FROM order_attempts WHERE purpose IN "
        "('idle_recenter_no_fill','forced_sell_stuck_level') "
        "ORDER BY id DESC LIMIT 5;"
    )

    hb = (supervision.get("states") or {}).get("heartbeat", {}).get("value") or {}
    recon = (supervision.get("states") or {}).get("reconciliation", {}).get("value") or {}

    return {
        "seq": seq,
        "ts_utc": ts.isoformat(),
        "running": running.get("running"),
        "cycle_id": running.get("cycle_id"),
        "symbol": running.get("symbol"),
        "mark_price": running.get("mark_price"),
        "mark_source": running.get("mark_source"),
        "grid_profit": grid.get("grid_profit"),
        "floating_profit": grid.get("floating_profit"),
        "gross_pnl": grid.get("gross_pnl"),
        "cycle_trigger_usd": cfg.get("cycle_trigger_usd"),
        "idle_recenter_min": cfg.get("idle_recenter_min"),
        "stuck_sell_min": cfg.get("stuck_sell_min"),
        "range_low": grid.get("range_low"),
        "range_high": grid.get("range_high"),
        "open_orders_n": len(running.get("open_orders") or []),
        "total_matched_trades_ui": recap.get("total_matched_trades"),
        "grid_matched_sql": matched_sql,
        "supervisor_http_ok": hb.get("http_ok"),
        "supervisor_bot_hb_age_s": hb.get("bot_heartbeat_age_s"),
        "supervisor_recon_delta_usdt": recon.get("delta_usdt"),
        "supervisor_alerts_active": sum(
            1 for a in (supervision.get("alerts") or []) if a.get("status") == "active"
        ),
        "db_recent_cycles": recent_cycles,
        "db_recent_recenter_attempts": recent_attempts,
        "api_errors": {
            k: v.get("_error")
            for k, v in [("running", running), ("supervision", supervision)]
            if isinstance(v, dict) and v.get("_error")
        },
    }


def load_manifest() -> dict:
    p = OUT_DIR / "manifest.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def save_manifest(m: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "manifest.json").write_text(json.dumps(m, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Module 3 run organique longue durée")
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--duration-h", type=float, default=48.0)
    parser.add_argument("--interval-s", type=int, default=300)
    parser.add_argument("--resume", action="store_true", help="Reprendre depuis manifest existant")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest() if args.resume else {}
    now = datetime.now(timezone.utc)

    if not manifest.get("started_at_utc"):
        manifest = {
            "started_at_utc": now.isoformat(),
            "target_end_at_utc": (now + timedelta(hours=args.duration_h)).isoformat(),
            "duration_h": args.duration_h,
            "interval_s": args.interval_s,
            "api_base": args.api,
            "status": "running",
            "objectives": {
                "trigger_15_organic": "cycle clos close_reason=trigger_15 sans seuil réduit",
                "cas_a_production": "idle_recenter_no_fill à idle_recenter_min=20",
                "cas_b_production": "forced_sell_stuck_level à stuck_sell_min=15",
                "no_manual_intervention": True,
                "supervisor_active": True,
            },
            "snapshots_count": 0,
            "events": [],
        }
        save_manifest(manifest)
        print(f"Started organic run — target end {manifest['target_end_at_utc']}", flush=True)
    else:
        print(f"Resuming organic run from {manifest['started_at_utc']}", flush=True)

    target_end = datetime.fromisoformat(manifest["target_end_at_utc"])
    seq = manifest.get("snapshots_count", 0)
    last_cycle = None
    last_matched = None

    while datetime.now(timezone.utc) < target_end:
        snap = snapshot(args.api, seq)
        snap_path = OUT_DIR / "snapshots" / f"{seq:05d}_{snap['ts_utc'].replace(':', '-')}.json"
        snap_path.parent.mkdir(parents=True, exist_ok=True)
        snap_path.write_text(json.dumps(snap, indent=2), encoding="utf-8")

        events = manifest.setdefault("events", [])
        cid = snap.get("cycle_id")
        matched = snap.get("total_matched_trades_ui")
        if last_cycle is not None and cid != last_cycle:
            events.append(
                {
                    "ts": snap["ts_utc"],
                    "type": "cycle_change",
                    "from": last_cycle,
                    "to": cid,
                    "recent_cycles": snap.get("db_recent_cycles"),
                }
            )
        if last_matched is not None and matched != last_matched:
            events.append(
                {
                    "ts": snap["ts_utc"],
                    "type": "matched_trades_change",
                    "from": last_matched,
                    "to": matched,
                }
            )
        if snap.get("db_recent_recenter_attempts") and "idle_recenter" in str(
            snap.get("db_recent_recenter_attempts")
        ):
            events.append(
                {
                    "ts": snap["ts_utc"],
                    "type": "recenter_activity",
                    "detail": snap.get("db_recent_recenter_attempts"),
                }
            )
        if "trigger_15" in str(snap.get("db_recent_cycles")):
            events.append(
                {
                    "ts": snap["ts_utc"],
                    "type": "trigger_15_observed",
                    "detail": snap.get("db_recent_cycles"),
                }
            )

        last_cycle = cid
        last_matched = matched
        seq += 1
        manifest["snapshots_count"] = seq
        manifest["last_snapshot_utc"] = snap["ts_utc"]
        manifest["last_gross_pnl"] = snap.get("gross_pnl")
        save_manifest(manifest)

        print(
            f"[{snap['ts_utc']}] seq={seq} cycle={cid} gross={snap.get('gross_pnl')} "
            f"matched={matched} sup_hb={snap.get('supervisor_bot_hb_age_s')}s",
            flush=True,
        )

        remaining = (target_end - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            break
        time.sleep(min(args.interval_s, remaining))

    manifest["status"] = "completed"
    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    save_manifest(manifest)

    summary = {
        "snapshots": seq,
        "events": manifest.get("events", []),
        "trigger_15_seen": any(e.get("type") == "trigger_15_observed" for e in manifest.get("events", [])),
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
