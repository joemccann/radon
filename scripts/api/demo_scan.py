"""TEST_MODE scan responses — serve seeded Turso snapshots, never spawn scans.

The demo backend (``RADON_API_TEST_MODE=1``, no ``UW_TOKEN``, no IB) must not
run the real scan subprocesses: they fail on missing credentials and the raw
error (``UWAuthError: UW_TOKEN environment variable is not set ...``) leaks
operator guidance to demo users. Instead every scan-trigger endpoint
short-circuits to the latest seeded snapshot in the demo Turso (mirrored from
prod by ``scripts/db/mirror_market_snapshots_to_demo.js``) and falls back to
the endpoint's empty envelope when no snapshot exists.

Reads go through :mod:`api.db_http` (the bounded hrana pipeline) — the sync
libsql client is banned in this process (see Event-Loop Discipline in
``scripts/api/CLAUDE.md``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from api import db_http

logger = logging.getLogger("radon-api")

DEMO_DISABLED_MESSAGE = "Live scans are disabled in the demo environment."

# surface key → SQL returning the newest seeded snapshot payload.
_SNAPSHOT_SQL: dict[str, str] = {
    "scanner": "SELECT payload FROM scanner_snapshots ORDER BY scan_time DESC LIMIT 1",
    "discover": "SELECT payload FROM discover_snapshots ORDER BY scan_time DESC LIMIT 1",
    "flow-analysis": "SELECT payload FROM flow_analysis_snapshots ORDER BY scan_time DESC LIMIT 1",
    "theta-harvester": "SELECT payload FROM theta_harvester_snapshots ORDER BY scan_time DESC LIMIT 1",
    "strength-confirmation": "SELECT payload FROM strength_confirmation_snapshots ORDER BY scan_time DESC LIMIT 1",
    "leap-scan": "SELECT payload FROM scan_snapshots WHERE service = 'leap-scan' ORDER BY scan_time DESC LIMIT 1",
    "garch-scan": "SELECT payload FROM scan_snapshots WHERE service = 'garch-scan' ORDER BY scan_time DESC LIMIT 1",
    "vcg-scan": "SELECT payload FROM vcg_snapshots ORDER BY scan_time DESC LIMIT 1",
    "gex-scan": "SELECT payload FROM gex_snapshots ORDER BY scan_time DESC LIMIT 1",
    "gamma-rotation": "SELECT payload FROM gamma_rotation_snapshots ORDER BY scan_time DESC LIMIT 1",
    "regime": "SELECT payload FROM cri_snapshots ORDER BY date DESC, taken_at DESC LIMIT 1",
    "breadth-scan": "SELECT payload FROM breadth_snapshots ORDER BY taken_at DESC LIMIT 1",
}


def demo_disabled_payload(feature: str) -> dict[str, Any]:
    """Friendly 200 payload for demo-disabled features with no snapshot."""
    return {"error": f"{feature} is disabled in the demo environment."}


def _refuse_real_db_under_pytest() -> bool:
    """Same contract as db.client: tests must never reach a real Turso.

    server.py load_dotenv puts the REAL prod TURSO_DB_URL in os.environ, so
    an unguarded hrana read from a test process would query production.
    """
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) and not os.environ.get(
        "RADON_ALLOW_DB_IN_TESTS"
    )


async def demo_scan_response(surface: str, fallback: dict[str, Any]) -> dict[str, Any]:
    """Latest seeded snapshot for ``surface``, or ``fallback`` when unseeded."""
    if _refuse_real_db_under_pytest():
        return fallback
    sql = _SNAPSHOT_SQL[surface]
    try:
        rows = await asyncio.to_thread(db_http.hrana_execute, sql)
    except db_http.DbHttpError as exc:
        logger.warning("demo snapshot read failed for %s: %s", surface, exc)
        return fallback
    if not rows or not rows[0] or not rows[0][0]:
        return fallback
    try:
        payload = json.loads(rows[0][0])
    except (TypeError, ValueError):
        logger.warning("demo snapshot for %s is not valid JSON", surface)
        return fallback
    return payload if isinstance(payload, dict) else fallback
