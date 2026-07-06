#!/usr/bin/env python3
"""Analyse trades complète — CLI (même logique que Observatory / n8n)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "observatory"))

from app.collector import collect_full_report  # noqa: E402
from app.vps_metrics import collect_vps_metrics  # noqa: E402

OUT = ROOT / "docs" / "proofs" / "hourly_trade_analysis_latest.json"


def main() -> int:
    report = collect_full_report()
    report["vps"] = collect_vps_metrics()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"written": str(OUT), "summary": report["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
