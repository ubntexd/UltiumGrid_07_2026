"""Module 10bis — superviseur indépendant (tests réels)."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dotenv import load_dotenv
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))
sys.path.insert(0, str(ROOT / "supervisor"))
load_dotenv(ROOT / ".env", override=True)

from ultiumgrid.bot_runner import BotRunner, build_client_from_env  # noqa: E402
from ultiumgrid.db.models import BotState, make_session_factory, utcnow  # noqa: E402
from ultiumgrid.engine.config import StrategyConfig  # noqa: E402
from ultium_supervisor.models import SupervisorAlert, SupervisorMetric  # noqa: E402
from ultium_supervisor.watchdog import Watchdog  # noqa: E402

PROOFS = ROOT / "docs" / "proofs"


@pytest.fixture
def db_url(tmp_path):
    # Use shared sqlite file for bot+supervisor tables
    p = ROOT / "data" / "test_supervisor.db"
    p.parent.mkdir(exist_ok=True)
    if p.exists():
        p.unlink()
    url = f"sqlite:///{p}"
    # create bot tables
    make_session_factory(url)
    return url


@pytest.mark.integration
def test_supervisor_normal_cycle_and_latency_sample(db_url):
    """Cycle normal : pas d'alerte critique, métriques latence enregistrées."""
    os.environ["SUPERVISOR_BOT_HEALTH_URL"] = "http://localhost:8000/health"
    os.environ["SUPERVISOR_HEARTBEAT_TIMEOUT_S"] = "90"
    os.environ["SUPERVISOR_RECON_THRESHOLD_USDT"] = "1.0"
    os.environ["SUPERVISOR_MARKET_ANOMALY_PCT"] = "0.5"

    SessionLocal, _ = make_session_factory(db_url)
    session = SessionLocal()
    client = build_client_from_env()
    bot = BotRunner(client, session, StrategyConfig(capital_usdt=100, num_levels=4))
    # heartbeat
    session.add(
        BotState(
            key="heartbeat",
            value_json={"ts": utcnow().isoformat(), "running": False, "pid": 1},
        )
    )
    session.add(
        BotState(
            key="main",
            value_json={
                "running": False,
                "config": StrategyConfig().to_dict(),
                "grid": {"position_qty": 0.0, "entry_avg": 0.0},
                "guards": {"daily_pnl": 0.0, "panic": False},
            },
        )
    )
    session.commit()

    wd = Watchdog(db_url)
    # échantillons latence
    samples = []
    for _ in range(5):
        wd.check_exchange_connectivity()
        wd.check_market_anomaly()
        wd.check_heartbeat()
        wd.check_reconciliation()
        wd.check_guardrails()
        time.sleep(0.2)

    session2 = wd.SessionLocal()
    metrics = (
        session2.query(SupervisorMetric)
        .filter(SupervisorMetric.kind == "exchange_latency_ms")
        .all()
    )
    alerts_active = (
        session2.query(SupervisorAlert)
        .filter(SupervisorAlert.status == "active")
        .all()
    )
    samples = [m.value for m in metrics]
    proof = {
        "latency_samples_ms": samples,
        "latency_avg_ms": sum(samples) / len(samples) if samples else None,
        "latency_max_ms": max(samples) if samples else None,
        "active_alerts": [{"kind": a.kind, "message": a.message} for a in alerts_active],
        "market_state": session2.execute(
            text("SELECT value_json FROM supervisor_state WHERE key='market'")
        ).scalar(),
    }
    assert len(samples) >= 5
    assert proof["latency_avg_ms"] is not None
    # en fonctionnement normal, pas de bot_unresponsive
    assert not any(a.kind == "bot_unresponsive" for a in alerts_active)
    (PROOFS / "m10bis_normal_cycle.json").write_text(json.dumps(proof, indent=2, default=str))
    print(json.dumps(proof, indent=2, default=str))
    session.close()
    session2.close()


@pytest.mark.integration
def test_supervisor_detects_bot_unresponsive(db_url):
    os.environ["SUPERVISOR_BOT_HEALTH_URL"] = "http://127.0.0.1:9/health"  # port mort
    os.environ["SUPERVISOR_HEARTBEAT_TIMEOUT_S"] = "5"

    SessionLocal, _ = make_session_factory(db_url)
    session = SessionLocal()
    # heartbeat ancien
    old = datetime.now(timezone.utc) - timedelta(seconds=120)
    session.add(
        BotState(
            key="heartbeat",
            value_json={"ts": old.isoformat(), "running": True},
        )
    )
    # forcer updated_at ancien via SQL
    session.commit()
    session.execute(
        text("UPDATE bot_state SET updated_at=:t WHERE key='heartbeat'"),
        {"t": old},
    )
    session.commit()

    wd = Watchdog(db_url)
    wd.check_heartbeat()

    alerts = (
        wd.SessionLocal()
        .query(SupervisorAlert)
        .filter(
            SupervisorAlert.kind == "bot_unresponsive",
            SupervisorAlert.status == "active",
        )
        .all()
    )
    proof = {
        "alerts": [{"kind": a.kind, "message": a.message, "severity": a.severity} for a in alerts],
        "heartbeat_state": wd.SessionLocal()
        .execute(text("SELECT value_json FROM supervisor_state WHERE key='heartbeat'"))
        .scalar(),
    }
    assert len(alerts) >= 1
    (PROOFS / "m10bis_bot_unresponsive.json").write_text(json.dumps(proof, indent=2, default=str))
    print(json.dumps(proof, indent=2, default=str))
    session.close()


@pytest.mark.integration
def test_supervisor_detects_reconciliation_mismatch(db_url):
    os.environ["SUPERVISOR_BOT_HEALTH_URL"] = "http://localhost:8000/health"
    os.environ["SUPERVISOR_RECON_THRESHOLD_USDT"] = "1.0"

    SessionLocal, _ = make_session_factory(db_url)
    session = SessionLocal()
    # Injecter un écart volontaire : DB déclare 1 BTC en grille alors que le compte n'en a pas
    session.add(
        BotState(
            key="main",
            value_json={
                "running": False,
                "config": {"symbol": "BTCUSDT"},
                "grid": {"position_qty": 1.0, "entry_avg": 60000.0},
                "guards": {},
            },
        )
    )
    session.add(
        BotState(
            key="heartbeat",
            value_json={"ts": utcnow().isoformat(), "running": False},
        )
    )
    session.commit()

    wd = Watchdog(db_url)
    wd.check_reconciliation()

    alerts = (
        wd.SessionLocal()
        .query(SupervisorAlert)
        .filter(
            SupervisorAlert.kind == "reconciliation_mismatch_watchdog",
            SupervisorAlert.status == "active",
        )
        .all()
    )
    proof = {
        "alerts": [
            {"kind": a.kind, "message": a.message, "payload": a.payload_json} for a in alerts
        ]
    }
    assert len(alerts) >= 1
    assert proof["alerts"][0]["payload"]["delta_usdt"] > 1.0
    (PROOFS / "m10bis_recon_mismatch.json").write_text(json.dumps(proof, indent=2, default=str))
    print(json.dumps(proof, indent=2, default=str))
    session.close()
