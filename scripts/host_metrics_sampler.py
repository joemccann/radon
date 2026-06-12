#!/usr/bin/env python3.13
"""Minute-cadence host/process metrics sampler — DUR-12, stdlib-only.

Samples the whole box (CPU from a 1s /proc/stat delta, memory + swap from
/proc/meminfo, load from /proc/loadavg), every radon-* systemd unit's
ActiveState + NRestarts, and the FastAPI event-loop lag exposed by
/health/lite (read over loopback; covered by the trusted-local bypass).

One row per run into Turso ``host_metrics`` (migration 0012) over the
bounded hrana HTTP path — sync libsql is banned in oneshot daemons (no
timeout, holds the GIL; see scripts/db/hrana_http.py). When the write
fails the row lands in a capped local JSONL fallback instead. Every run
heartbeats ``service_health[host-metrics]`` via the same bounded path so
the freshness banner notices a dead sampler within its 10-min window
(feedback_service_health_heartbeat).

Invoked by radon-cloud radon-host-metrics.timer (oneshot, RuntimeMaxSec=30).
Exit code is 0 whenever sampling succeeded — a Turso outage must not park
the unit as failed (the watchdog unit alarm would page P1 on every blip);
the state=error heartbeat + fallback file carry that signal instead. It
exits non-zero only when the host itself cannot be sampled.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from db.hrana_http import (  # noqa: E402
    HranaHttpError,
    hrana_execute,
    write_service_health_http,
)

SERVICE_NAME = "host-metrics"

CPU_SAMPLE_SECONDS = 1.0
SYSTEMCTL_TIMEOUT_SECONDS = 10
HEALTH_LITE_URL = os.environ.get(
    "RADON_HEALTH_LITE_URL", "http://127.0.0.1:8321/health/lite"
)
HEALTH_LITE_TIMEOUT_SECONDS = 3.0

RETENTION_DAYS = 14

FALLBACK_PATH = _SCRIPTS_DIR.parent / "data" / "host_metrics_fallback.jsonl"
# ~3 days of minutes; a long Turso outage trims oldest-first instead of
# growing without bound.
FALLBACK_MAX_LINES = 4096

HOST_METRICS_INSERT_SQL = (
    "INSERT INTO host_metrics (taken_at, cpu_pct, mem_used_mb, mem_avail_mb, "
    "load1, swap_used_mb, loop_lag_ms, units_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
HOST_METRICS_PRUNE_SQL = "DELETE FROM host_metrics WHERE taken_at < ?"

ROW_COLUMNS = (
    "taken_at",
    "cpu_pct",
    "mem_used_mb",
    "mem_avail_mb",
    "load1",
    "swap_used_mb",
    "loop_lag_ms",
    "units_json",
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --- pure parsers (fixture-tested, no /proc dependency) ---------------------

def parse_proc_stat_cpu(text: str) -> tuple[int, int]:
    """(idle_jiffies, total_jiffies) from the aggregate ``cpu`` line.

    idle counts idle + iowait; total sums the 8 real jiffy fields
    (user nice system idle iowait irq softirq steal) — guest time is
    already folded into user by the kernel.
    """
    for line in text.splitlines():
        if line.startswith("cpu "):
            fields = [int(v) for v in line.split()[1:9]]
            idle = fields[3] + fields[4]
            return idle, sum(fields)
    raise ValueError("no aggregate 'cpu ' line in /proc/stat text")


def cpu_pct_from_samples(
    first: tuple[int, int], second: tuple[int, int]
) -> float | None:
    """Busy % across the interval between two /proc/stat snapshots.
    None when no jiffies elapsed (instantaneous re-read)."""
    idle_delta = second[0] - first[0]
    total_delta = second[1] - first[1]
    if total_delta <= 0:
        return None
    return round(100.0 * (1.0 - idle_delta / total_delta), 2)


def parse_meminfo(text: str) -> dict[str, int]:
    """{field: kB} for every ``Name:   12345 kB`` line."""
    info: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        parts = rest.split()
        if parts and parts[0].isdigit():
            info[name.strip()] = int(parts[0])
    return info


def mem_metrics(meminfo: dict[str, int]) -> tuple[float, float, float]:
    """(mem_used_mb, mem_avail_mb, swap_used_mb) from parsed /proc/meminfo."""
    total_kb = meminfo.get("MemTotal", 0)
    avail_kb = meminfo.get("MemAvailable", 0)
    swap_used_kb = meminfo.get("SwapTotal", 0) - meminfo.get("SwapFree", 0)
    return (
        round((total_kb - avail_kb) / 1024, 1),
        round(avail_kb / 1024, 1),
        round(swap_used_kb / 1024, 1),
    )


def parse_loadavg(text: str) -> float:
    return float(text.split()[0])


def parse_units_blob(text: str) -> list[dict]:
    """Parse ``systemctl show 'radon-*' -p Id,ActiveState,NRestarts`` output
    (blank-line-separated property blocks) into one record per unit."""
    units: list[dict] = []
    for block in text.split("\n\n"):
        props = dict(
            line.split("=", 1) for line in block.splitlines() if "=" in line
        )
        unit = props.get("Id")
        if not unit:
            continue
        try:
            n_restarts = int(props.get("NRestarts", "0"))
        except ValueError:
            n_restarts = 0
        units.append(
            {
                "unit": unit,
                "active_state": props.get("ActiveState", "unknown"),
                "n_restarts": n_restarts,
            }
        )
    return units


def parse_loop_lag(payload) -> float | None:
    if not isinstance(payload, dict):
        return None
    lag = payload.get("loop_lag_ms")
    if isinstance(lag, (int, float)) and not isinstance(lag, bool):
        return float(lag)
    return None


# --- impure collectors (Linux VPS only) --------------------------------------

def read_cpu_pct() -> float | None:
    first = parse_proc_stat_cpu(Path("/proc/stat").read_text())
    time.sleep(CPU_SAMPLE_SECONDS)
    second = parse_proc_stat_cpu(Path("/proc/stat").read_text())
    return cpu_pct_from_samples(first, second)


def read_units() -> list[dict]:
    """Read-only systemctl probe, same shape the watchdog unit alarm uses
    (scripts/watchdog/units.py). Empty list on any failure — metrics for the
    box must still land when systemd is unhappy."""
    try:
        result = subprocess.run(
            ["systemctl", "show", "radon-*", "-p", "Id,ActiveState,NRestarts"],
            capture_output=True,
            text=True,
            timeout=SYSTEMCTL_TIMEOUT_SECONDS,
        )
        return parse_units_blob(result.stdout)
    except Exception:
        return []


def read_loop_lag() -> float | None:
    """FastAPI event-loop lag via /health/lite over loopback (the
    trusted-local bypass authenticates it). None when the api is down or
    slow — NULL in the row is itself a signal."""
    try:
        request = urllib.request.Request(
            HEALTH_LITE_URL, headers={"User-Agent": "radon-host-metrics/1"}
        )
        with urllib.request.urlopen(
            request, timeout=HEALTH_LITE_TIMEOUT_SECONDS
        ) as resp:
            return parse_loop_lag(json.loads(resp.read(65536).decode("utf-8")))
    except Exception:
        return None


# --- bounded writes + fallback ------------------------------------------------

def append_fallback(row: dict) -> None:
    """Append one JSONL line, trimming oldest-first past FALLBACK_MAX_LINES."""
    FALLBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FALLBACK_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    lines = FALLBACK_PATH.read_text(encoding="utf-8").splitlines()
    if len(lines) > FALLBACK_MAX_LINES:
        FALLBACK_PATH.write_text(
            "\n".join(lines[-FALLBACK_MAX_LINES:]) + "\n", encoding="utf-8"
        )


def write_row(row: dict) -> bool:
    """INSERT over the bounded hrana path; JSONL fallback on failure.
    True when the row reached Turso."""
    try:
        hrana_execute(
            HOST_METRICS_INSERT_SQL, tuple(row.get(c) for c in ROW_COLUMNS)
        )
        return True
    except HranaHttpError as exc:
        sys.stderr.write(f"[host-metrics] Turso write failed ({exc}); fallback\n")
        append_fallback(row)
        return False


def should_prune(now: datetime) -> bool:
    """Once an hour is plenty for a 14-day window on a minutely writer."""
    return now.minute == 0


def prune_old_rows(now: datetime) -> None:
    """Mirror of the service_health_events retention sweep
    (scripts/db/writer.py:prune_service_health_events), over hrana because
    this process never holds sync libsql. Best-effort — a failed prune
    retries next hour."""
    cutoff = _iso(now - timedelta(days=RETENTION_DAYS))
    try:
        hrana_execute(HOST_METRICS_PRUNE_SQL, (cutoff,))
    except HranaHttpError as exc:
        sys.stderr.write(f"[host-metrics] prune failed ({exc}); next hour\n")


def heartbeat(
    state: str,
    *,
    started_at: str,
    finished_at: str,
    error: dict | None = None,
) -> None:
    """Best-effort service_health row — when Turso is down this fails with
    the metric write and the freshness window carries the signal."""
    try:
        write_service_health_http(
            SERVICE_NAME,
            state,
            started_at=started_at,
            finished_at=finished_at,
            error=error,
        )
    except HranaHttpError as exc:
        sys.stderr.write(f"[host-metrics] heartbeat failed: {exc}\n")


# --- orchestration -------------------------------------------------------------

def collect_row(now: datetime) -> dict:
    mem_used_mb, mem_avail_mb, swap_used_mb = mem_metrics(
        parse_meminfo(Path("/proc/meminfo").read_text())
    )
    return {
        "taken_at": _iso(now),
        "cpu_pct": read_cpu_pct(),
        "mem_used_mb": mem_used_mb,
        "mem_avail_mb": mem_avail_mb,
        "load1": parse_loadavg(Path("/proc/loadavg").read_text()),
        "swap_used_mb": swap_used_mb,
        "loop_lag_ms": read_loop_lag(),
        "units_json": json.dumps(read_units()),
    }


def main() -> int:
    started = _now_utc()
    try:
        row = collect_row(started)
    except Exception as exc:
        heartbeat(
            "error",
            started_at=_iso(started),
            finished_at=_iso(_now_utc()),
            error={"error": f"sample_failed: {type(exc).__name__}: {exc}"},
        )
        sys.stderr.write(f"[host-metrics] sampling failed: {exc}\n")
        return 1

    wrote = write_row(row)
    if wrote and should_prune(started):
        prune_old_rows(started)

    heartbeat(
        "ok" if wrote else "error",
        started_at=_iso(started),
        finished_at=_iso(_now_utc()),
        error=None if wrote else {"error": "turso_write_failed; row in fallback jsonl"},
    )
    print(json.dumps({"wrote_db": wrote, **row}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
