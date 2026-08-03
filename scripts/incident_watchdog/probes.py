"""Bounded stdlib probes + pure body parsers.

Every probe is three-valued: ``up`` / ``down`` / ``unknown``. A bounded-probe
TIMEOUT is ``unknown``, never ``down`` (the health_service convention); a
connection refusal is ``down``. HTTP status codes are transport truth only —
the parsers below judge bodies, because /api/service-health and
/api/probe/freshness both return 200 during real outages.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

PROBE_TIMEOUT_SECS = 6.0
DEPLOY_CMD_TIMEOUT_SECS = 12.0

DEFAULT_NEXTJS_BASE = "http://127.0.0.1:3000"
DEFAULT_API_BASE = "http://127.0.0.1:8321"
DEFAULT_HEALTH_BASE = "http://127.0.0.1:8330"
DEFAULT_GREEN_MARKER = "/home/radon/.radon-last-green-deploy"

UNKNOWN = {"state": "unknown"}


def _http_get(url: str, headers: dict[str, str] | None = None) -> dict:
    """Fetch ``url`` and return {state, http_status, body} (body json-decoded
    when possible). Never raises."""
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT_SECS) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as err:
        raw = err.read()
        status = err.code
    except (TimeoutError, socket.timeout):
        return {"state": "unknown", "http_status": None, "body": None,
                "detail": f"timeout after {PROBE_TIMEOUT_SECS}s"}
    except (urllib.error.URLError, OSError) as err:
        return {"state": "down", "http_status": None, "body": None,
                "detail": str(getattr(err, "reason", err))}
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        body = None
    return {"state": "up", "http_status": status, "body": body, "detail": ""}


def parse_service_health_body(body: dict | None) -> dict:
    """Judge the /api/service-health BODY. The route always returns 200; a DB
    outage surfaces as a single synthetic ``turso-db`` error row + warning."""
    if not isinstance(body, dict):
        return {"synthetic_turso_row": False, "failing": [], "warning": None}
    services = body.get("services") or []
    warning = body.get("warning")
    synthetic = bool(
        warning
        and len(services) == 1
        and services[0].get("service") == "turso-db"
        and services[0].get("state") == "error"
    )
    failing = [
        {"service": row.get("service"), "state": row.get("state")}
        for row in (body.get("failing") or [])
    ]
    return {"synthetic_turso_row": synthetic, "failing": failing, "warning": warning}


def parse_freshness_body(body: dict | None) -> dict:
    """Judge /api/probe/freshness. ``all_fresh`` is already market-state
    aware (null when no check applies — off-hours quiet is normal)."""
    if not isinstance(body, dict):
        return {"all_fresh": None, "stale_checks": [], "database_ok": None}
    checks = body.get("checks") or {}
    stale = sorted(
        name
        for name, check in checks.items()
        if isinstance(check, dict)
        and check.get("applicable")
        and check.get("fresh") is False
    )
    return {
        "all_fresh": body.get("all_fresh"),
        "stale_checks": stale,
        "database_ok": body.get("database_ok"),
    }


def parse_health_status_body(body: dict | None) -> dict:
    if not isinstance(body, dict):
        return {"ok": None, "overall_state": None}
    return {"ok": body.get("ok"), "overall_state": body.get("overall_state")}


def probe_nextjs_liveness(base: str) -> dict:
    """Public-route liveness. /sign-in is the ONLY correct liveness URL: an
    anonymous 401/404 on a protected route is the auth perimeter, not an
    outage. A 500 here means the app itself cannot render."""
    result = _http_get(f"{base}/sign-in")
    if result["state"] != "up":
        return {"state": result["state"], "http_status": None,
                "detail": result["detail"]}
    status = result["http_status"]
    state = "up" if status == 200 else "down"
    return {"state": state, "http_status": status,
            "detail": f"/sign-in returned {status}"}


def probe_nextjs_db(base: str) -> dict:
    result = _http_get(f"{base}/api/service-health")
    if result["state"] != "up":
        return {"state": result["state"], "synthetic_turso_row": False,
                "failing": [], "warning": result.get("detail")}
    return {"state": "up", **parse_service_health_body(result["body"])}


def probe_api_lite(base: str) -> dict:
    result = _http_get(f"{base}/health/lite")
    if result["state"] != "up":
        return {"state": result["state"], "loop_lag_ms": None}
    body = result["body"] if isinstance(result["body"], dict) else {}
    return {"state": "up", "loop_lag_ms": body.get("loop_lag_ms")}


def probe_health_status(base: str) -> dict:
    result = _http_get(f"{base}/status")
    if result["state"] != "up":
        return {"state": result["state"], "ok": None, "overall_state": None}
    return {"state": "up", **parse_health_status_body(result["body"])}


def probe_freshness(base: str, token: str | None) -> dict:
    if not token:
        return {"state": "unknown", "all_fresh": None, "stale_checks": [],
                "database_ok": None}
    result = _http_get(
        f"{base}/api/probe/freshness",
        headers={"Authorization": f"Bearer {token}"},
    )
    if result["state"] != "up" or result["http_status"] != 200:
        return {"state": result["state"] if result["state"] != "up" else "unknown",
                "all_fresh": None, "stale_checks": [], "database_ok": None}
    return {"state": "up", **parse_freshness_body(result["body"])}


def _read_ci_run() -> dict | None:
    try:
        proc = subprocess.run(
            ["gh", "run", "list", "--workflow=ci.yml", "--limit", "1",
             "--json", "status,conclusion,headSha"],
            capture_output=True, text=True, timeout=DEPLOY_CMD_TIMEOUT_SECS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        runs = json.loads(proc.stdout)
    except ValueError:
        return None
    if not runs:
        return None
    run = runs[0]
    return {"status": run.get("status"), "conclusion": run.get("conclusion"),
            "head_sha": run.get("headSha")}


def _read_head(repo_dir: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", repo_dir, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=DEPLOY_CMD_TIMEOUT_SECS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def probe_deploy(repo_dir: str, green_marker_path: str) -> dict:
    """Deploy status: last CI run via gh, plus green-marker vs HEAD on hosts
    where the marker exists (the VPS). HEAD == target SHA with a mismatched
    green marker is the cancelled-deploy signature — Git HEAD equality alone
    is never success."""
    ci = _read_ci_run()
    marker_file = Path(green_marker_path)
    green_marker = None
    head = None
    if marker_file.is_file():
        green_marker = marker_file.read_text().strip() or None
        head = _read_head(repo_dir)
    state = "up" if (ci or green_marker) else "unknown"
    return {"state": state, "ci": ci, "green_marker": green_marker, "head": head}


def gather_findings() -> dict:
    nextjs = os.environ.get("INCIDENT_WATCHDOG_NEXTJS_BASE", DEFAULT_NEXTJS_BASE)
    api = os.environ.get("INCIDENT_WATCHDOG_API_BASE", DEFAULT_API_BASE)
    health = os.environ.get("INCIDENT_WATCHDOG_HEALTH_BASE", DEFAULT_HEALTH_BASE)
    token = os.environ.get("RADON_PROBE_FRESHNESS_TOKEN")
    repo_dir = os.environ.get("INCIDENT_WATCHDOG_REPO_DIR", str(Path.cwd()))
    marker = os.environ.get("INCIDENT_WATCHDOG_GREEN_MARKER", DEFAULT_GREEN_MARKER)
    return {
        "nextjs_liveness": probe_nextjs_liveness(nextjs),
        "nextjs_db": probe_nextjs_db(nextjs),
        "api_lite": probe_api_lite(api),
        "health_status": probe_health_status(health),
        "freshness": probe_freshness(nextjs, token),
        "deploy": probe_deploy(repo_dir, marker),
    }
