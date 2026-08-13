#!/usr/bin/env python3
"""Daily keep-latest sweep for append-only Turso snapshot tables (R2).

Standalone oneshot (radon-db-retention.timer). Does NOT touch
portfolio_snapshots (owned by archive_portfolio_snapshots.py) or
service_health_events (owned by flex_token_check).

Heartbeats service_health row ``db-retention`` on every run via the bounded
stdlib Hrana HTTP pipeline so a wedged libsql client cannot block liveness.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SERVICE_NAME = "db-retention"
TURSO_TIMEOUT = 10
SUMMARY_CAP = 300

REPO_ROOT = Path(__file__).resolve().parent.parent


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _hrana_arg(value):
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": str(int(value))}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    return {"type": "text", "value": str(value)}


def write_service_health(state: str, detail: dict | None, started_at: str) -> None:
    """Best-effort heartbeat; never raises into the main path."""
    url = os.environ.get("TURSO_DB_URL", "")
    token = os.environ.get("TURSO_AUTH_TOKEN", "")
    if not url or not token:
        print("  service_health heartbeat skipped: missing TURSO env", file=sys.stderr)
        return
    http_origin = url.replace("libsql://", "https://", 1)
    finished = _now_iso()
    detail_json = json.dumps(detail or {})[:SUMMARY_CAP]
    sql = (
        "INSERT INTO service_health (service, state, last_attempt_started_at, "
        "last_attempt_finished_at, last_error, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(service) DO UPDATE SET "
        "state = excluded.state, "
        "last_attempt_started_at = COALESCE(excluded.last_attempt_started_at, service_health.last_attempt_started_at), "
        "last_attempt_finished_at = COALESCE(excluded.last_attempt_finished_at, service_health.last_attempt_finished_at), "
        "last_error = excluded.last_error, "
        "updated_at = excluded.updated_at"
    )
    args = [
        SERVICE_NAME,
        state,
        started_at,
        finished,
        detail_json if state == "error" else detail_json,
        finished,
    ]
    # last_error column stores JSON detail for both ok and error so the
    # dashboard can show rows_deleted without a separate payload column.
    payload = json.dumps(
        {
            "requests": [
                {
                    "type": "execute",
                    "stmt": {
                        "sql": sql,
                        "args": [_hrana_arg(a) for a in args],
                    },
                },
                {"type": "close"},
            ]
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        http_origin.rstrip("/") + "/v2/pipeline",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TURSO_TIMEOUT) as resp:
            resp.read(1_048_576)
    except Exception as exc:
        print(f"  service_health heartbeat failed: {exc}", file=sys.stderr)


def main() -> int:
    sys.path.insert(0, str(REPO_ROOT))
    started_at = _now_iso()
    t0 = time.monotonic()

    # Ensure writers never open a retired replica even if env is wrong.
    os.environ.setdefault("RADON_DB_NO_REPLICA", "1")

    try:
        from scripts.db.retention import run_retention_sweep_http
    except ImportError:
        from db.retention import run_retention_sweep_http  # type: ignore

    # Bounded Hrana HTTP only — never libsql_experimental (no client timeout;
    # first fleet retention run hung with 74ms CPU for 10+ minutes).
    try:
        results, failed = run_retention_sweep_http()
    except Exception as exc:
        detail = {"error": str(exc)[:SUMMARY_CAP]}
        write_service_health("error", detail, started_at)
        print(json.dumps({"ok": False, **detail}), file=sys.stderr)
        return 1

    elapsed = round(time.monotonic() - t0, 2)
    total = sum(results.values())
    summary = {
        "tables": results,
        "rows_deleted": total,
        "elapsed_s": elapsed,
        "failed_tables": failed,
    }
    # Retention is one required maintenance transaction. A partial sweep is
    # degraded too: otherwise the failed table can grow without bound while
    # systemd and service_health continue to certify the job as healthy.
    if failed:
        write_service_health("error", summary, started_at)
        print(json.dumps({"ok": False, **summary}, indent=2), file=sys.stderr)
        return 1
    write_service_health("ok", summary, started_at)
    print(json.dumps({"ok": True, **summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
