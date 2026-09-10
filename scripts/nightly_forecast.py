#!/usr/bin/env python3
"""Thin CLI for the nightly forecasting runner.

Orchestrates backfill -> flow-surprise -> calibration and prints the summary
as JSON on stdout. See ``forecasting.nightly_forecast`` for the logic and
``docs/forecasting-deploy.md`` for the systemd timer that drives it.

R-402: this shim now owns the `forecast-nightly` service_health row. The unit
contained no `service_health` reference on any path and appears in neither
watchdog catalog, and zero units in `cloud/services/*.service` carry an
`OnFailure=` backstop — so a throwing Chronos-2 backfill left the unit `failed`,
nothing wrote a row, the watchdog had no key to age-check, and the forecast
tables silently stopped advancing. The heartbeat lives HERE rather than in
`forecasting.nightly_forecast` because the unit runs under
`/home/radon/forecasting-venv`, which does not carry the repo's DB extras; the
bounded-stdlib Hrana writer is imported lazily so a missing dependency degrades
to a logged skip instead of taking the run down.

Usage:
    python3 scripts/nightly_forecast.py --metric flow_strength --top 25 \
        --lookback 250 --backfill-days 20
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SERVICE_NAME = "forecast-nightly"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _record_health(state: str, detail: dict | None = None) -> None:
    """Best-effort row. Never raises: telemetry must not mask the job's result."""
    try:
        from db.hrana_http import write_service_health_http  # noqa: PLC0415

        write_service_health_http(
            SERVICE_NAME,
            state,
            error=detail if state != "ok" else None,
        )
    except Exception as exc:  # noqa: BLE001 — a dead Turso is not a failed forecast
        print(f"[forecast-nightly] heartbeat skipped: {exc}", file=sys.stderr)


def _run(argv):
    from forecasting.nightly_forecast import main as _main  # noqa: PLC0415

    return _main(argv)


def main(argv=None) -> int:
    started_at = _now_iso()
    try:
        code = _run(argv)
    except BaseException as exc:  # noqa: BLE001 — the row is the point
        _record_health(
            "error",
            {"message": f"{type(exc).__name__}: {exc}", "started_at": started_at},
        )
        raise
    if code:
        _record_health("error", {"message": f"exit {code}", "started_at": started_at})
    else:
        _record_health("ok")
    return code


if __name__ == "__main__":
    sys.exit(main())
