#!/usr/bin/env python3
"""Preuves Bot Égaliseur — checks automatisables + collecte pour tests live T1–T8.

Usage:
  PYTHONPATH=bot:egaliseur python3 scripts/m_egaliseur_proof.py --check
  PYTHONPATH=bot:egaliseur python3 scripts/m_egaliseur_proof.py --test 5
  PYTHONPATH=bot:egaliseur python3 scripts/m_egaliseur_proof.py --collect --out docs/proofs/m_egaliseur/snapshot.json
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))
sys.path.insert(0, str(ROOT / "egaliseur"))
load_dotenv(ROOT / ".env", override=True)

API = os.getenv("PROOF_API_URL", "http://localhost:18000")
PROOFS = ROOT / "docs" / "proofs" / "m_egaliseur"
ENGINE_SRC = ROOT / "egaliseur" / "ultium_egaliseur" / "engine.py"


def _get(path: str) -> dict:
    r = requests.get(f"{API}{path}", timeout=30)
    r.raise_for_status()
    return r.json()


def _docker_ps(service: str) -> str:
    r = subprocess.run(
        ["docker", "compose", "ps", "-q", service],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return (r.stdout or "").strip()


def check_stack() -> dict:
    from ultiumgrid.bot_runner import build_client_from_env

    client = build_client_from_env()
    sym = "BTCUSDT"
    filters = client.get_symbol_filters(sym)
    info = client.exchange_info(sym)
    trail = None
    for s in info.get("symbols") or []:
        if s.get("symbol") == sym:
            for f in s.get("filters") or []:
                if f.get("filterType") == "TRAILING_DELTA":
                    trail = f
    status = _get("/api/egaliseur/status")
    cfg = _get("/api/egaliseur/config")
    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "api": API,
        "docker": {
            "egaliseur": bool(_docker_ps("egaliseur")),
            "bot": bool(_docker_ps("bot")),
        },
        "egaliseur_status": status,
        "egaliseur_config": cfg,
        "trailing_delta_filter_raw": trail,
        "trailing_delta_bounds_code": {
            "min_bips": filters.trailing_delta_min_bips,
            "max_bips": filters.trailing_delta_max_bips,
        },
        "open_orders": client.open_orders(sym, force=True),
    }


def test_5_no_buy() -> dict:
    tree = ast.parse(ENGINE_SRC.read_text())
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "place_order":
                for kw in node.keywords:
                    if kw.arg == "side" and isinstance(kw.value, ast.Constant):
                        if kw.value.value == "BUY":
                            violations.append(f"place_order BUY keyword line {node.lineno}")
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    if node.args[1].value == "BUY":
                        violations.append(f"place_order BUY arg line {node.lineno}")
    src = ENGINE_SRC.read_text()
    return {
        "test": "T5_no_buy",
        "ok": not violations and '"BUY"' not in src and "'BUY'" not in src,
        "violations": violations,
        "engine_path": str(ENGINE_SRC),
    }


def test_7_reconciliation_formula() -> dict:
    """Vérifie que le superviseur inclut trailing_active (lecture code SQL)."""
    wd = ROOT / "supervisor" / "ultium_supervisor" / "watchdog.py"
    text = wd.read_text()
    ok = "trailing_active" in text and "journal_only" in text
    return {
        "test": "T7_reconciliation_sql",
        "ok": ok,
        "watchdog_includes_trailing_active": "trailing_active" in text,
        "watchdog_includes_journal_only": "journal_only" in text,
    }


def collect_snapshot() -> dict:
    snap = check_stack()
    snap["actions"] = _get("/api/egaliseur/actions?limit=20")
    snap["bags_active"] = _get("/api/egaliseur/bags?scope=active")
    snap["bags_sold"] = _get("/api/egaliseur/bags?scope=sold")
    snap["test_5"] = test_5_no_buy()
    snap["test_7"] = test_7_reconciliation_formula()
    try:
        snap["supervision"] = _get("/api/supervision")
    except Exception as exc:
        snap["supervision_error"] = str(exc)
    return snap


def main() -> int:
    parser = argparse.ArgumentParser(description="Preuves Bot Égaliseur")
    parser.add_argument("--check", action="store_true", help="État stack + bornes trailing")
    parser.add_argument("--test", type=int, choices=[5, 7], help="Test automatisé")
    parser.add_argument("--collect", action="store_true", help="Snapshot complet")
    parser.add_argument("--out", type=str, default="", help="Fichier JSON de sortie")
    args = parser.parse_args()

    PROOFS.mkdir(parents=True, exist_ok=True)

    if args.test == 5:
        result = test_5_no_buy()
    elif args.test == 7:
        result = test_7_reconciliation_formula()
    elif args.collect:
        result = collect_snapshot()
    elif args.check:
        result = check_stack()
    else:
        parser.print_help()
        return 0

    out = Path(args.out) if args.out else PROOFS / (
        f"m_egaliseur_{'check' if args.check else 'T' + str(args.test) if args.test else 'snapshot'}.json"
    )
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"ok": result.get("ok", True), "written": str(out)}, indent=2))
    if result.get("ok") is False:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
