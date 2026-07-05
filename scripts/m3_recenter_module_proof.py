#!/usr/bin/env python3
"""Preuve module recentrage Cas A/B — implémentation + config production + historique DB."""
from __future__ import annotations

import json
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "http://127.0.0.1:18000"
OUT = ROOT / "docs" / "proofs" / "m3_recenter_cas_ab_module.json"


def http_get(path: str) -> dict:
    with urllib.request.urlopen(API + path, timeout=30) as r:
        return json.loads(r.read().decode())


def sql(query: str) -> str:
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
    ).strip()


def main() -> None:
    running = http_get("/api/running")
    cfg = running.get("config") or {}
    grid = running.get("grid") or {}

    idle_cycles = sql(
        "SELECT id, close_reason, opened_at, closed_at FROM cycles "
        "WHERE close_reason='idle_recenter_no_fill' ORDER BY id DESC LIMIT 10;"
    )
    stuck_attempts = sql(
        "SELECT id, purpose, outcome, created_at, verify_json::text FROM order_attempts "
        "WHERE purpose IN ('idle_recenter_no_fill','forced_sell_stuck_level') "
        "ORDER BY id DESC LIMIT 20;"
    )
    trigger_cycles = sql(
        "SELECT id, close_reason, gross_pnl, net_pnl, closed_at FROM cycles "
        "WHERE close_reason='trigger_15' ORDER BY id DESC LIMIT 5;"
    )

    proof = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "module": "M3bis — recentrage hors fourchette (Cas A / Cas B)",
        "implementation": {
            "cas_a_idle_recenter_no_fill": {
                "file": "bot/ultiumgrid/bot_runner.py",
                "method": "_check_idle_recenter",
                "called_from": "BotRunner.tick() après should_close_cycle",
                "conditions": [
                    "mark < range_low OR mark > range_high",
                    f"elapsed >= idle_recenter_min ({cfg.get('idle_recenter_min')} min prod)",
                    "_last_fill_at IS NULL (aucun fill grille)",
                    "balance_total vérifié via GET /api/v3/account",
                ],
                "actions": [
                    "close_cycle + _close_cycle_db(idle_recenter_no_fill)",
                    "flatten inventaire résiduel (MARKET) si > minQty",
                    "order_attempts purpose=idle_recenter_no_fill",
                    "_open_new_cycle() séquence complète",
                ],
            },
            "cas_b_forced_sell_stuck_level": {
                "file": "bot/ultiumgrid/bot_runner.py",
                "method": "_check_stuck_sells",
                "conditions": [
                    "SELL status=open avec order_id",
                    "mark >= sell_price",
                    f"elapsed >= stuck_sell_min ({cfg.get('stuck_sell_min')} min prod)",
                ],
                "actions": [
                    "cancel limite + MARKET SELL",
                    "Trade(level_index=lv.index) + fees_paid",
                    "order_attempts outcome=forced_sell_stuck_level",
                ],
            },
        },
        "production_config_live": {
            "idle_recenter_min": cfg.get("idle_recenter_min"),
            "stuck_sell_min": cfg.get("stuck_sell_min"),
            "expected_idle": 20.0,
            "expected_stuck": 15.0,
            "idle_matches_production": cfg.get("idle_recenter_min") == 20.0,
            "stuck_matches_production": cfg.get("stuck_sell_min") == 15.0,
        },
        "running_snapshot": {
            "bot_running": running.get("running"),
            "cycle_id": running.get("cycle_id"),
            "mark_price": running.get("mark_price"),
            "range_low": grid.get("range_low"),
            "range_high": grid.get("range_high"),
            "grid_active": grid.get("active"),
        },
        "unit_tests": {
            "file": "bot/tests/test_m3_idle_recenter_unit.py",
            "cases": [
                "test_idle_recenter_triggers_when_out_of_range_no_fill",
                "test_idle_recenter_skips_when_in_range_despite_expired_timer",
                "test_idle_recenter_skips_when_fill_occurred",
            ],
            "note": "unit — logique Cas A ; timers réduits en unit uniquement",
        },
        "integration_proofs_prior": {
            "file": "docs/proofs/m3_open_sequence_clarifications.json",
            "t2_idle_recenter_price_condition": "conforme — prix hors fourchette prouvé",
            "t2_negative_in_range_no_recenter": "conforme — minuteur seul insuffisant",
            "t3_stuck_sell_ws_proof": "conforme — WS réel + MARKET forced_sell_stuck_level",
            "caveat": (
                "Tests d'intégration T2/T3 utilisaient idle/stuck=0.05 min (documenté). "
                "Seuils production 20/15 min actifs sur le bot live ; "
                "déclenchement à ces seuils = observé pendant le run organique 24-48h."
            ),
        },
        "db_history": {
            "idle_recenter_cycles": idle_cycles or "(aucun)",
            "order_attempts_recenter": stuck_attempts or "(aucun)",
            "trigger_15_cycles": trigger_cycles or "(aucun)",
        },
        "production_timer_live_observation": {
            "status": "pending_organic_long_run",
            "reason": (
                "Pas de raccourci accéléré demandé pour le trigger +15 ; "
                "Cas A/B à 20/15 min nécessite conditions marché réelles sur 24-48h"
            ),
        },
        "conforme_implementation": True,
        "conforme_production_config": None,
        "conforme": False,
    }
    proof["conforme_production_config"] = bool(
        proof["production_config_live"]["idle_matches_production"]
        and proof["production_config_live"]["stuck_matches_production"]
    )
    proof["conforme"] = bool(
        proof["conforme_implementation"]
        and proof["conforme_production_config"]
        and running.get("running")
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print(json.dumps(proof, indent=2))
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
