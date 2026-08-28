#!/usr/bin/env python3
"""Standalone helper for combined CRI + VCG refresh.

NOT wired into any scheduler. The autonomous entry points are:

  - radon-cri-scan.timer        (CRI: every 30 minutes, ET trading hours)
  - radon-data-refresh.timer    (scanner / flow_analysis / discover)
  - radon-vcg-refresh.timer     (VCG: every 5 minutes, ET trading hours)
  - com.radon.cri-scan          (laptop launchd)
  - com.radon.data-refresh      (laptop launchd)
  - com.radon.vcg-refresh       (laptop launchd)

Kept for ad-hoc invocation (``python -m scripts.data_refresh``) — runs
both scans sequentially, writes atomically, and exits 0 on success or
skipped holiday/weekend. Useful when reconciling discrepancies between
the two cache files at the same instant.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# ── paths ──────────────────────────────────────────────────────────────
_SCRIPTS_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPTS_DIR.parent
_DATA_DIR = _PROJECT_DIR / "data"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Child budgets. cri regularly needs 60-103s on a live IB path (2026-08-28);
# the old uniform 120s ceiling killed two consecutive runs and left
# service_health[cri-scan] silent past the 35m open window. vcg/gex stay at
# 120s (they finish in seconds under --no-mq). Sum must fit TimeoutStartSec=480.
DEFAULT_SCAN_TIMEOUT_SECS = 120
CRI_SCAN_TIMEOUT_SECS = 180

# Map child script → service_health name. Soft-fail heartbeats use these so a
# parent-killed child cannot go silent (children only heartbeat on success).
_SCRIPT_SERVICES = {
    "cri_scan.py": "cri-scan",
    "vcg_scan.py": "vcg-scan",
    "gex_scan.py": "gex-scan",
}

# Align with service_cycle.SOFT_RETRY_EMBARGO_SECS — long enough to cover the
# next 15-min timer fire without re-paging the error bucket every cycle.
_SOFT_FAIL_EMBARGO_SECS = 15 * 60


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts}: {msg}", file=sys.stderr, flush=True)


def _is_trading_day() -> bool:
    try:
        from utils.market_calendar import _is_trading_day as _check
        return _check(datetime.now())
    except Exception:
        return True  # fail-open: run the scan if calendar unavailable


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _heartbeat_soft_fail(script: str, reason: str) -> None:
    """Refresh service_health for a parent-killed / soft-failed child.

    Best-effort: never raises. Success path is owned by the child writer.
    """
    service = _SCRIPT_SERVICES.get(script)
    if not service:
        return
    next_attempt = (
        datetime.now(timezone.utc) + timedelta(seconds=_SOFT_FAIL_EMBARGO_SECS)
    ).isoformat().replace("+00:00", "Z")
    try:
        from db import writer  # noqa: PLC0415 — optional on stripped hosts
        ensure = getattr(writer, "ensure_no_replica_for_writers", None)
        if ensure is not None:
            ensure()
        writer.record_service_health(
            service,
            "error",
            started_at=_iso_now(),
            finished_at=_iso_now(),
            error={"message": reason, "next_attempt_at": next_attempt},
        )
    except Exception as exc:  # noqa: BLE001 — telemetry must not mask soft-fail
        _log(f"{service} soft-fail heartbeat failed: {exc}")


def _run_scan(
    script: str,
    args: list[str],
    out_path: Path,
    timeout: int = DEFAULT_SCAN_TIMEOUT_SECS,
) -> bool:
    """Run *script* (relative to scripts/) with *args*, write JSON to *out_path*.

    Returns True on success, False on failure (existing file preserved).
    Soft-fail paths heartbeat the child service so the 35m open window cannot
    page stale silence after a parent kill (cri-scan 2026-08-28 15:15Z).
    """
    script_path = _SCRIPTS_DIR / script
    tmp_path = out_path.with_suffix(".json.tmp")
    cmd = [sys.executable, str(script_path)] + args

    _log(f"Running {script} ...")
    t0 = time.monotonic()
    fail_reason: Optional[str] = None
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(_PROJECT_DIR),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        fail_reason = f"timed out after {timeout}s"
        _log(f"{script} {fail_reason} — keeping existing {out_path.name}")
        _heartbeat_soft_fail(script, fail_reason)
        return False
    except Exception as exc:
        fail_reason = f"failed to start: {exc}"
        _log(f"{script} {fail_reason} — keeping existing {out_path.name}")
        _heartbeat_soft_fail(script, fail_reason)
        return False

    elapsed = time.monotonic() - t0

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip().splitlines()
        last_err = err[-1] if err else f"exit {result.returncode}"
        fail_reason = f"failed ({last_err})"
        _log(f"{script} {fail_reason} — keeping existing {out_path.name}")
        _heartbeat_soft_fail(script, fail_reason)
        return False

    # Extract JSON from stdout (scripts may emit progress lines before the payload)
    stdout = result.stdout or ""
    json_start = stdout.find("{")
    if json_start == -1:
        fail_reason = "produced no JSON output"
        _log(f"{script} {fail_reason} — keeping existing {out_path.name}")
        _heartbeat_soft_fail(script, fail_reason)
        return False

    try:
        data = json.loads(stdout[json_start:])
    except json.JSONDecodeError as exc:
        fail_reason = f"returned invalid JSON: {exc}"
        _log(f"{script} {fail_reason} — keeping existing {out_path.name}")
        _heartbeat_soft_fail(script, fail_reason)
        return False

    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path.write_text(json.dumps(data, indent=2))
    tmp_path.rename(out_path)
    _log(f"{script} complete ({elapsed:.1f}s) → {out_path.name}")
    return True


SERVICE_NAME = "data-refresh"


def main() -> int:
    if not _is_trading_day():
        _log("Market holiday or weekend — skipping data refresh")
        return 0

    # R-325: this unit fires every 15 min through RTH and wrote NO
    # service_health row, so it sat in neither watchdog catalog and a refresh
    # loop that stopped firing was invisible to check.py. The individual scans
    # heartbeat their own names, but nothing observed the DRIVER.
    from db.service_cycle import service_cycle  # noqa: PLC0415 — optional dep

    with service_cycle(SERVICE_NAME, market_hours_class="intraday"):
        return _refresh()


def _refresh() -> int:
    cri_ok = _run_scan(
        "cri_scan.py", ["--json"], _DATA_DIR / "cri.json",
        timeout=CRI_SCAN_TIMEOUT_SECS,
    )
    vcg_ok = _run_scan(
        "vcg_scan.py", ["--json"], _DATA_DIR / "vcg.json",
        timeout=DEFAULT_SCAN_TIMEOUT_SECS,
    )
    # GEX had NO autonomous scheduler (unlike vcg's radon-vcg-refresh.timer), so
    # it only refreshed when a browser opened /regime/gex — leaving it days stale
    # between sessions. Fold it into this same RTH cadence. `--no-mq` skips the
    # headless-Playwright MenthorQ enrichment (the long pole that blows the
    # DEFAULT_SCAN_TIMEOUT_SECS budget on a cold run and 502s the on-demand
    # POST); the UW-only snapshot still dual-writes disk + Turso + heartbeats
    # service_health[gex-scan] via persist_snapshot. MQ enrichment stays
    # best-effort on the on-demand path.
    gex_ok = _run_scan(
        "gex_scan.py", ["--json", "--no-mq"], _DATA_DIR / "gex.json",
        timeout=DEFAULT_SCAN_TIMEOUT_SECS,
    )

    statuses = f"cri: {'OK' if cri_ok else 'FAIL'}, vcg: {'OK' if vcg_ok else 'FAIL'}, gex: {'OK' if gex_ok else 'FAIL'}"
    _log(f"Data refresh complete ({statuses})")

    # A scan that soft-fails (timeout / bad output) keeps the existing JSON and
    # retries on the next 15-min run — graceful, transient degradation, NOT a
    # process crash. Exiting non-zero would mark the systemd unit "failed" and
    # page the operator for self-healing noise (cri_scan timed out once
    # post-close on 2026-06-15, fine on every neighbouring run). Soft-fail now
    # heartbeats the child service (error + next_attempt_at) so the cri-scan /
    # vcg-scan staleness watchers see activity rather than silence. The unit
    # still succeeds whenever the refresh RAN; only an unhandled crash
    # (Python's own non-zero exit) trips the unit-failed alarm.
    return 0


if __name__ == "__main__":
    sys.exit(main())
