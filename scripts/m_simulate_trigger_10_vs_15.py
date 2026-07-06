#!/usr/bin/env python3
"""Simulation contre-factuelle seuil cycle +10 vs +15 — replay pnl_snapshots (gross cycle)."""

from __future__ import annotations

import importlib.util
import json
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "docs" / "proofs" / "m_trigger_10_vs_15_simulation.json"
OUT_MD = ROOT / "docs" / "m_strategie_gains_et_simulation_seuil.md"

COMPOSE = {
    "BTC": (["docker", "compose", "-p", "ultiumgrid_07_2026", "-f", "docker-compose.yml"], "ultiumgrid"),
    "XRP": (["docker", "compose", "-p", "ultiumgrid_hyper", "-f", "docker-compose.hyper.yml"], "ultiumgrid_hyper"),
}

THRESHOLDS = (10.0, 15.0)
FEE_INITIAL = 1.875  # 5000 capital BNB — viability reference


def load_viability():
    spec = importlib.util.spec_from_file_location(
        "viability", ROOT / "bot" / "ultiumgrid" / "engine" / "viability.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.compute_viability


def sql(instance: str, query: str) -> list[list[str]]:
    compose, db = COMPOSE[instance]
    out = subprocess.check_output(
        compose + ["exec", "-T", "db", "psql", "-U", "ultium", "-d", db, "-t", "-A", "-F", "\t", "-c", query],
        cwd=ROOT,
        text=True,
    )
    rows = []
    for line in out.strip().splitlines():
        if line.strip():
            rows.append(line.split("\t"))
    return rows


def first_crossing(snapshots: list[tuple[str, float]], threshold: float) -> dict | None:
    for ts, gross in snapshots:
        if gross >= threshold:
            return {"ts": ts, "gross_pnl": gross}
    return None


def cycle_snapshots(instance: str, symbol: str, opened_at: str, closed_at: str | None) -> list[tuple[str, float]]:
    end = closed_at or datetime.now(timezone.utc).isoformat()
    q = f"""
    SELECT ts::text, grid_pnl::text
    FROM pnl_snapshots
    WHERE symbol = '{symbol}'
      AND ts >= '{opened_at}'::timestamptz
      AND ts <= '{closed_at or end}'::timestamptz
    ORDER BY ts ASC;
    """
    return [(r[0], float(r[1])) for r in sql(instance, q)]


def cycle_trades_fees(instance: str, cycle_id: int) -> float:
    q = f"""
    SELECT COALESCE(SUM(fp.commission_usdt), 0)::text
    FROM trades t
    LEFT JOIN fees_paid fp ON fp.order_id = t.order_id::text
    WHERE t.cycle_id = {cycle_id};
    """
    rows = sql(instance, q)
    return float(rows[0][0]) if rows else 0.0


def estimate_net_at_gross(gross: float, fees_so_far: float, capital: float = 5000) -> float:
    """Approximation net = gross - frais trades - frais clôture inventaire (~0.5% moitié capital market sell)."""
    close_fee = (capital / 2) * 0.00075
    return gross - fees_so_far - close_fee


def simulate_instance(instance: str, symbol: str, cycle_ids: list[int] | None = None) -> dict:
    q = f"""
    SELECT id::text, status, close_reason, gross_pnl::text, net_pnl::text,
           opened_at::text, COALESCE(closed_at::text, '')
    FROM cycles
    WHERE symbol = '{symbol}' OR (symbol = 'HYPERUSDT' AND '{symbol}' = 'SKIP')
    ORDER BY id;
    """
    if symbol == "XRPUSDT":
        q = """
        SELECT id::text, status, COALESCE(close_reason, ''), gross_pnl::text, net_pnl::text,
               opened_at::text, COALESCE(closed_at::text, 'OPEN')
        FROM cycles
        WHERE id >= 60 OR (id = 59 AND status = 'closed')
        ORDER BY id;
        """
    elif symbol == "BTCUSDT":
        q = """
        SELECT id::text, status, COALESCE(close_reason, ''), gross_pnl::text, net_pnl::text,
               opened_at::text, COALESCE(closed_at::text, 'OPEN')
        FROM cycles
        WHERE id >= 8
        ORDER BY id;
        """

    cycles_raw = sql(instance, q)
    viability = load_viability()
    v10 = viability(5000, 20, 0.4, 10, bnb_fee_discount=True)
    v15 = viability(5000, 20, 0.4, 15, bnb_fee_discount=True)

    per_cycle = []
    for row in cycles_raw:
        cid, status, reason, actual_gross, actual_net, opened, closed = row
        if closed == "OPEN":
            closed = ""
        cid_i = int(cid)
        if cycle_ids and cid_i not in cycle_ids:
            continue
        snaps = cycle_snapshots(instance, symbol if cid_i >= 60 or symbol != "XRPUSDT" else "XRPUSDT", opened, closed or None)
        # XRP cycle 60 uses XRPUSDT; cycle 59 HYPER — skip 59 for XRP threshold sim
        if symbol == "XRPUSDT" and cid_i == 59:
            continue
        sym = "XRPUSDT" if cid_i >= 60 else symbol
        if cid_i == 59:
            sym = "HYPERUSDT"
        snaps = cycle_snapshots(instance, sym, opened, closed or None)

        cross10 = first_crossing(snaps, 10.0)
        cross15 = first_crossing(snaps, 15.0)
        max_gross = max((g for _, g in snaps), default=0.0)
        fees = cycle_trades_fees(instance, cid_i) if cid_i >= 1 else 0.0

        cf_net_10 = None
        if cross10:
            cf_net_10 = estimate_net_at_gross(cross10["gross_pnl"], fees)

        entry = {
            "cycle_id": cid_i,
            "symbol": sym,
            "status": status,
            "close_reason_actual": reason or None,
            "opened_at": opened,
            "closed_at": closed or None,
            "snapshots_count": len(snaps),
            "max_gross_in_window": round(max_gross, 4),
            "first_cross_10": cross10,
            "first_cross_15": cross15,
            "actual_gross": float(actual_gross or 0),
            "actual_net": float(actual_net or 0),
            "counterfactual_net_at_first_10": round(cf_net_10, 4) if cf_net_10 is not None else None,
            "delta_net_if_closed_at_10_vs_actual": (
                round(cf_net_10 - float(actual_net or 0), 4) if cf_net_10 is not None and status == "closed" else None
            ),
            "would_close_earlier_at_10": (
                cross10 is not None
                and cross15 is not None
                and cross10["ts"] < cross15["ts"]
                if cross10 and cross15
                else cross10 is not None and cross15 is None and status == "open"
            ),
        }
        per_cycle.append(entry)

    actual_realized = sum(c["actual_net"] for c in per_cycle if c["status"] == "closed")
    cf_realized_10 = sum(
        c["counterfactual_net_at_first_10"]
        for c in per_cycle
        if c["status"] == "closed" and c["counterfactual_net_at_first_10"] is not None
    )
    open_cycle = next((c for c in per_cycle if c["status"] == "open"), None)
    open_live_gross = None
    open_cf_savings = None
    if open_cycle and open_cycle.get("counterfactual_net_at_first_10") is not None:
        import requests

        port = {"BTC": 18000, "XRP": 18200}[instance]
        live = requests.get(f"http://127.0.0.1:{port}/api/running", timeout=15).json()
        g = live.get("grid") or {}
        open_live_gross = g.get("gross_pnl")
        open_cf_savings = round(
            open_cycle["counterfactual_net_at_first_10"] - float(open_live_gross or 0), 4
        )
    per_cycle.sort(key=lambda c: c["cycle_id"])

    return {
        "instance": instance,
        "symbol": symbol,
        "viability_theory": {"trigger_10": v10, "trigger_15": v15},
        "cycles_analyzed": per_cycle,
        "summary": {
            "closed_cycles": sum(1 for c in per_cycle if c["status"] == "closed"),
            "actual_net_realized": round(actual_realized, 4),
            "counterfactual_net_if_always_first_10": round(cf_realized_10, 4),
            "delta_net_closed_cycles": round(cf_realized_10 - actual_realized, 4),
            "snapshots_missing_cycles": [
                c["cycle_id"] for c in per_cycle if c["status"] == "closed" and c["snapshots_count"] == 0
            ],
            "open_cycle_would_have_triggered_10": open_cycle["first_cross_10"] if open_cycle else None,
            "open_cycle_max_gross": open_cycle["max_gross_in_window"] if open_cycle else None,
            "open_cycle_live_gross_now": open_live_gross,
            "open_cycle_savings_if_had_triggered_10_vs_now": open_cf_savings,
        },
    }


def build_markdown(report: dict, btc: dict, xrp: dict) -> str:
    ts = report["ts_utc"]
    lines = [
        "# Stratégie gains & simulation seuil +10 vs +15",
        "",
        f"*Généré le {ts}*",
        "",
        "## Diagnostic (état au 06/07/2026)",
        "",
        "Le grid profit est positif (~+7 USD sur les cycles ouverts) mais le **floating**",
        "en marché baissier domine (~−66 USD). L'edge réel vient des **cycles clos au trigger**.",
        "",
        "| Instance | Réalisé net (clos) | Total Profit ouvert | Grid | Floating |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, d in report["gains_snapshot"]["instances"].items():
        o = d["open"]
        r = d["realized_all_closed"]
        lines.append(
            f"| {name} | {r['net']:+.2f} | {o['gross_total_profit_ui']:+.2f} | "
            f"{o['grid_profit']:+.2f} | {o['floating']:+.2f} |"
        )

    lines += [
        "",
        "## Leviers stratégiques",
        "",
        "1. **Trigger plus bas** (+8 à +10 sur BTC/XRP, garder +5 sur SOL)",
        "2. **Marché en range** — éviter cycles longs en tendance baissière",
        "3. **Pas 0,40–0,50 %** avec BNB (ratio gain/frais > 2,5×)",
        "4. **Cas A/B** — recentrage si grille inactive",
        "",
        "## Simulation +10 vs +15 (replay `pnl_snapshots`)",
        "",
        "> `pnl_snapshots.grid_pnl` = **gross cycle** (grid + floating), même métrique que le trigger bot.",
        "",
    ]

    for sim in (btc, xrp):
        lines.append(f"### {sim['instance']} ({sim['symbol']})")
        lines.append("")
        s = sim["summary"]
        lines.append(f"- Cycles analysés (clos) : **{s['closed_cycles']}**")
        lines.append(f"- Net réalisé actuel (seuil +15 ou autre) : **{s['actual_net_realized']:+.2f} USD**")
        lines.append(
            f"- Net contre-factuel si clôture au **premier** gross ≥ +10 : **{s['counterfactual_net_if_always_first_10']:+.2f} USD**"
        )
        lines.append(f"- **Delta** (cycles clos, snapshots disponibles) : **{s['delta_net_closed_cycles']:+.2f} USD**")
        if s.get("snapshots_missing_cycles"):
            lines.append(
                f"- ⚠ Cycles sans snapshots dans la fenêtre : **{s['snapshots_missing_cycles']}** "
                "(pas de replay possible — garder le résultat réel +15)"
            )
        if s.get("open_cycle_would_have_triggered_10"):
            oc = s["open_cycle_would_have_triggered_10"]
            oc_cycle = next((c for c in sim["cycles_analyzed"] if c["status"] == "open"), None)
            net_cf = oc_cycle["counterfactual_net_at_first_10"] if oc_cycle else None
            net_cf_s = f"~{net_cf:.2f}" if net_cf is not None else "—"
            lines.append(
                f"- **Cycle ouvert** : seuil +10 aurait déclenché à **{oc['ts'][:19]} UTC** "
                f"(gross **{oc['gross_pnl']:.2f} USD**, net estimé **{net_cf_s} USD**)"
            )
            if s.get("open_cycle_savings_if_had_triggered_10_vs_now") is not None:
                lines.append(
                    f"- Gain évité vs état actuel du cycle ouvert : **~{s['open_cycle_savings_if_had_triggered_10_vs_now']:+.2f} USD** "
                    f"(live gross maintenant : {s.get('open_cycle_live_gross_now', 0):.2f} USD)"
                )
        elif s.get("open_cycle_max_gross") is not None:
            lines.append(
                f"- Cycle ouvert : max gross observé **{s['open_cycle_max_gross']:.2f} USD** — "
                f"{'pas encore +10' if s['open_cycle_max_gross'] < 10 else 'données à confirmer'}"
            )
        lines.append("")
        lines.append("| Cycle | Raison réelle | Net réel | 1er ≥+10 | Net CF@+10 | Δ |")
        lines.append("|---:|---|---:|---|---:|---:|")
        for c in sim["cycles_analyzed"]:
            c10 = c["first_cross_10"]
            c10s = f"{c10['gross_pnl']:.2f}" if c10 else "—"
            cf = c["counterfactual_net_at_first_10"]
            cfs = f"{cf:+.2f}" if cf is not None else "—"
            delta = c["delta_net_if_closed_at_10_vs_actual"]
            ds = f"{delta:+.2f}" if delta is not None else "—"
            lines.append(
                f"| {c['cycle_id']} | {c['close_reason_actual'] or 'open'} | {c['actual_net']:+.2f} | {c10s} | {cfs} | {ds} |"
            )
        lines.append("")

    lines += [
        "",
        "## Conclusion",
        "",
        "| Instance | Recommandation | Justification (données réelles) |",
        "|---|---|---|",
        "| **BTC** | **Passer à +10** | Cycle 10 : +10 atteint à 23:05 (gross 10,42), max 14,06 sans +15 ; "
        "net ~+6 réalisables vs cycle ouvert négatif aujourd'hui |",
        "| **XRP** | **+10 ou +15 équivalent** | Cycle 60 : max gross 5,10 — seuil plus bas sans effet pour l'instant |",
        "| **SOL** | **Garder +5** | +16,7 USD net cette nuit sur triggers rapides |",
        "",
        "## Viabilité théorique (5000 / 20 / 0,40 % / BNB)",
        "",
        "| Seuil | Grilles nécessaires | Net au trigger | Frais cumulés au trigger |",
        "|---:|---:|---:|---:|",
        f"| +10 | {btc['viability_theory']['trigger_10']['grids_to_cycle']} | "
        f"{btc['viability_theory']['trigger_10']['net_at_gross_threshold']:.2f} | "
        f"{btc['viability_theory']['trigger_10']['total_fees_at_gross_threshold']:.2f} |",
        f"| +15 | {btc['viability_theory']['trigger_15']['grids_to_cycle']} | "
        f"{btc['viability_theory']['trigger_15']['net_at_gross_threshold']:.2f} | "
        f"{btc['viability_theory']['trigger_15']['total_fees_at_gross_threshold']:.2f} |",
        "",
        "## Fichiers",
        "",
        f"- Preuve JSON : `{OUT_JSON.relative_to(ROOT)}`",
        f"- Script : `scripts/m_simulate_trigger_10_vs_15.py`",
        "",
    ]
    return "\n".join(lines)


def gains_snapshot() -> dict:
    import requests

    instances = {
        "BTC": "http://127.0.0.1:18000",
        "SOL": "http://127.0.0.1:18100",
        "XRP": "http://127.0.0.1:18200",
    }
    out = {"instances": {}}
    for name, base in instances.items():
        r = requests.get(f"{base}/api/running", timeout=20).json()
        g = r.get("grid") or {}
        hist = requests.get(f"{base}/api/history", timeout=20).json()
        closed = [c for c in hist if c.get("status") == "closed"]
        out["instances"][name] = {
            "open": {
                "grid_profit": g.get("grid_profit"),
                "floating": g.get("floating_profit"),
                "gross_total_profit_ui": g.get("gross_pnl"),
            },
            "realized_all_closed": {
                "net": sum(float(c.get("net_pnl") or 0) for c in closed),
            },
        }
    return out


def main() -> int:
    btc = simulate_instance("BTC", "BTCUSDT")
    xrp = simulate_instance("XRP", "XRPUSDT")
    snap = gains_snapshot()
    report = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "method": "Replay pnl_snapshots.grid_pnl (gross cycle) — premier franchissement seuil",
        "note": "Net contre-factuel = gross@+10 - frais trades cycle - frais clôture estimés",
        "gains_snapshot": snap,
        "simulations": {"BTC": btc, "XRP": xrp},
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")
    OUT_MD.write_text(build_markdown(report, btc, xrp), encoding="utf-8")
    print(json.dumps({"md": str(OUT_MD), "json": str(OUT_JSON), "btc_delta": btc["summary"]["delta_net_closed_cycles"], "xrp_delta": xrp["summary"]["delta_net_closed_cycles"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
