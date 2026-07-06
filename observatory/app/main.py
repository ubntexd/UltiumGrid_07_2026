"""API Observatory — collecte horaire, historique, métriques VPS."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.collector import collect_full_report
from app.vps_metrics import collect_vps_metrics

DATA_DIR = Path(os.getenv("OBSERVATORY_DATA_DIR", "/data"))
HISTORY_DIR = DATA_DIR / "hourly"
HISTORY_DIR.mkdir(parents=True, exist_ok=True)
LATEST_FILE = DATA_DIR / "latest.json"

# URLs UltiumGrid depuis le conteneur (host gateway)
HOST_API = os.getenv("ULTIUM_HOST_API", "http://127.0.0.1")
API_MAP = {
    "btc": f"{HOST_API}:18000",
    "sol": f"{HOST_API}:18100",
    "xrp": f"{HOST_API}:18200",
}

app = FastAPI(title="UltiumGrid Observatory", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _save_report(report: dict) -> Path:
    LATEST_FILE.write_text(json.dumps(report, indent=2), encoding="utf-8")
    hour_file = HISTORY_DIR / f"{report['hour_key'].replace(':', '-')}.json"
    if not hour_file.exists():
        hour_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return hour_file


@app.get("/health")
def health():
    return {"ok": True, "ts": datetime.now(timezone.utc).isoformat()}


@app.get("/api/vps")
def api_vps():
    return collect_vps_metrics()


@app.post("/api/collect")
def api_collect():
    """Déclenché par n8n chaque heure — collecte + sauvegarde."""
    report = collect_full_report(API_MAP)
    report["vps"] = collect_vps_metrics()
    path = _save_report(report)
    return {"ok": True, "saved": str(path), "hour_key": report["hour_key"], "summary": report["summary"]}


@app.get("/api/collect")
def api_collect_get():
    """Collecte à la demande (UI ou test)."""
    return api_collect()


@app.get("/api/latest")
def api_latest():
    if not LATEST_FILE.exists():
        raise HTTPException(404, "Aucun rapport — lancer POST /api/collect ou attendre n8n")
    return json.loads(LATEST_FILE.read_text(encoding="utf-8"))


@app.get("/api/history")
def api_history(limit: int = 48):
    files = sorted(HISTORY_DIR.glob("*.json"), reverse=True)[:limit]
    items = []
    for f in reversed(files):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            items.append(
                {
                    "hour_key": d.get("hour_key"),
                    "ts_utc": d.get("ts_utc"),
                    "summary": d.get("summary"),
                    "instances": [
                        {
                            "id": i.get("instance_id"),
                            "label": i.get("label"),
                            "gross_open": (i.get("pnl_open") or {}).get("gross_total"),
                            "net_realized": (i.get("realized") or {}).get("sum_net"),
                            "net_today": (i.get("realized") or {}).get("net_today"),
                        }
                        for i in d.get("instances", [])
                    ],
                }
            )
        except (json.JSONDecodeError, OSError):
            continue
    return {"points": items, "count": len(items)}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
