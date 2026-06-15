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
from datetime import datetime, timezone
from pathlib import Path

# ── paths ──────────────────────────────────────────────────────────────
_SCRIPTS_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPTS_DIR.parent
_DATA_DIR = _PROJECT_DIR / "data"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts}: {msg}", file=sys.stderr, flush=True)


def _is_trading_day() -> bool:
    try:
        from utils.market_calendar import _is_trading_day as _check
        return _check(datetime.now())
    except Exception:
        return True  # fail-open: run the scan if calendar unavailable


def _run_scan(script: str, args: list[str], out_path: Path, timeout: int = 120) -> bool:
    """Run *script* (relative to scripts/) with *args*, write JSON to *out_path*.

    Returns True on success, False on failure (existing file preserved).
    """
    script_path = _SCRIPTS_DIR / script
    tmp_path = out_path.with_suffix(".json.tmp")
    cmd = [sys.executable, str(script_path)] + args

    _log(f"Running {script} ...")
    t0 = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(_PROJECT_DIR),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _log(f"{script} timed out after {timeout}s — keeping existing {out_path.name}")
        return False
    except Exception as exc:
        _log(f"{script} failed to start: {exc} — keeping existing {out_path.name}")
        return False

    elapsed = time.monotonic() - t0

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip().splitlines()
        last_err = err[-1] if err else f"exit {result.returncode}"
        _log(f"{script} failed ({last_err}) — keeping existing {out_path.name}")
        return False

    # Extract JSON from stdout (scripts may emit progress lines before the payload)
    stdout = result.stdout or ""
    json_start = stdout.find("{")
    if json_start == -1:
        _log(f"{script} produced no JSON output — keeping existing {out_path.name}")
        return False

    try:
        data = json.loads(stdout[json_start:])
    except json.JSONDecodeError as exc:
        _log(f"{script} returned invalid JSON: {exc} — keeping existing {out_path.name}")
        return False

    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path.write_text(json.dumps(data, indent=2))
    tmp_path.rename(out_path)
    _log(f"{script} complete ({elapsed:.1f}s) → {out_path.name}")
    return True


def main() -> int:
    if not _is_trading_day():
        _log("Market holiday or weekend — skipping data refresh")
        return 0

    cri_ok = _run_scan("cri_scan.py", ["--json"], _DATA_DIR / "cri.json", timeout=120)
    vcg_ok = _run_scan("vcg_scan.py", ["--json"], _DATA_DIR / "vcg.json", timeout=120)

    statuses = f"cri: {'OK' if cri_ok else 'FAIL'}, vcg: {'OK' if vcg_ok else 'FAIL'}"
    _log(f"Data refresh complete ({statuses})")

    # A scan that soft-fails (timeout / bad output) keeps the existing JSON and
    # retries on the next 15-min run — graceful, transient degradation, NOT a
    # process crash. Exiting non-zero would mark the systemd unit "failed" and
    # page the operator for self-healing noise (cri_scan timed out once
    # post-close on 2026-06-15, fine on every neighbouring run). Data freshness
    # is monitored separately by the cri-scan/vcg-scan staleness watchers, which
    # catch a PERSISTENT failure (stale JSON / no service_health heartbeat). So
    # the unit succeeds whenever the refresh RAN; only an unhandled crash
    # (Python's own non-zero exit) trips the unit-failed alarm.
    return 0


if __name__ == "__main__":
    sys.exit(main())
