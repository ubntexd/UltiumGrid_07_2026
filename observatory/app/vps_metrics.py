"""Métriques VPS temps réel — CPU, RAM, disque, Docker."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read_mem() -> dict[str, float]:
    info: dict[str, float] = {}
    with open("/proc/meminfo", encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2 and parts[0].rstrip(":") in (
                "MemTotal",
                "MemAvailable",
                "MemFree",
                "Buffers",
                "Cached",
                "SwapTotal",
                "SwapFree",
            ):
                info[parts[0].rstrip(":")] = float(parts[1]) * 1024
    total = info.get("MemTotal", 1)
    avail = info.get("MemAvailable", 0)
    return {
        "total_bytes": total,
        "available_bytes": avail,
        "used_bytes": total - avail,
        "used_pct": round((total - avail) / total * 100, 1) if total else 0,
        "swap_total_bytes": info.get("SwapTotal", 0),
        "swap_free_bytes": info.get("SwapFree", 0),
    }


def _read_load() -> dict[str, float]:
    load1, load5, load15 = os.getloadavg()
    return {"load_1m": load1, "load_5m": load5, "load_15m": load15}


def _read_disk(path: str = "/") -> dict[str, Any]:
    st = os.statvfs(path)
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    used = total - free
    return {
        "path": path,
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": free,
        "used_pct": round(used / total * 100, 1) if total else 0,
    }


def _read_cpu_count() -> int:
    return os.cpu_count() or 1


def _docker_stats() -> list[dict[str, Any]]:
    try:
        out = subprocess.check_output(
            [
                "/usr/bin/docker",
                "stats",
                "--no-stream",
                "--format",
                "{{json .}}",
            ],
            text=True,
            timeout=20,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return []
    rows = []
    for line in out.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = d.get("Name", "")
        stack = "other"
        if "ultiumgrid_07_2026" in name or name.startswith("ultiumgrid_07_2026"):
            stack = "btc"
        elif "ultiumgrid_sol" in name:
            stack = "sol"
        elif "ultiumgrid_hyper" in name:
            stack = "xrp"
        elif "n8n" in name.lower() or "observatory" in name.lower():
            stack = "observatory"
        cpu = d.get("CPUPerc", "0%").replace("%", "")
        mem = d.get("MemUsage", "0B / 0B").split("/")
        mem_used = mem[0].strip() if mem else "0B"
        rows.append(
            {
                "name": name,
                "stack": stack,
                "cpu_pct": float(cpu) if cpu else 0.0,
                "mem_usage": mem_used,
                "mem_pct": d.get("MemPerc", "").replace("%", ""),
                "net_io": d.get("NetIO", ""),
                "block_io": d.get("BlockIO", ""),
            }
        )
    return rows


def _uptime_sec() -> float:
    with open("/proc/uptime", encoding="utf-8") as f:
        return float(f.read().split()[0])


def collect_vps_metrics() -> dict[str, Any]:
    stacks = {"btc": 0.0, "sol": 0.0, "xrp": 0.0, "observatory": 0.0, "other": 0.0}
    docker_rows = _docker_stats()
    for row in docker_rows:
        stacks[row["stack"]] = stacks.get(row["stack"], 0) + row["cpu_pct"]

    return {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "hostname": os.uname().nodename,
        "cpu_cores": _read_cpu_count(),
        "load": _read_load(),
        "memory": _read_mem(),
        "disk_root": _read_disk("/"),
        "uptime_sec": _uptime_sec(),
        "docker": {
            "container_count": len(docker_rows),
            "containers": docker_rows,
            "cpu_pct_by_stack": stacks,
        },
    }
