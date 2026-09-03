"""Shared pytest fixtures for the FastAPI tests.

This package is a separate collection subtree from scripts/tests, so the
orphan-state isolation contract has to be declared here too.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add scripts/ to sys.path so tests can import `utils.*` the way the app does.
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from utils import ib_2fa_lock  # noqa: E402


def _credential_route_modules():
    """Return both import-path instances used by the FastAPI test tree."""
    # server imports the route through the production ``api.*`` path; load it
    # before collecting aliases so fixture ordering cannot hide that instance.
    from scripts.api import server as _server  # noqa: F401
    from scripts.api.routes import credentials

    modules = [credentials]
    doppelganger = sys.modules.get("api.routes.credentials")
    if doppelganger is not None and doppelganger is not credentials:
        modules.append(doppelganger)
    return modules


@pytest.fixture(autouse=True)
def _isolate_credential_validator_throttle():
    """Keep validator cooldown and semaphore state local to each test."""
    modules = _credential_route_modules()
    saved = [
        (module, module._validator_last_run, module._validator_slots)
        for module in modules
    ]
    for module, _, _ in saved:
        module._validator_last_run = {}
        module._validator_slots = None
    yield
    for module, last_run, slots in saved:
        module._validator_last_run = last_run
        module._validator_slots = slots


@pytest.fixture(autouse=True)
def _isolate_ib_2fa_lock_orphan_state(monkeypatch):
    """Reset the process-wide orphan-confirmation memory around every test.

    Same contract as scripts/tests/conftest.py: R-210's `_orphan_reported` /
    `_orphan_seen_up` globals and the real inter-probe `time.sleep` are
    process-wide, and the api restart suites never reset them. T-226.
    """
    ib_2fa_lock.reset_orphan_state()
    monkeypatch.setattr(ib_2fa_lock, "ORPHAN_CONFIRM_INTERVAL_SECS", 0.0)
    yield
    ib_2fa_lock.reset_orphan_state()


@pytest.fixture(autouse=True)
def _isolate_order_rate_budget():
    """Hand every test a fresh per-minute order budget.

    `server._order_rate_timestamps` is process-wide and holds every accepted
    placement for a rolling 60 s, capped at RADON_MAX_ORDERS_PER_MIN (10). A
    placement posted without an `orderRef` appends `None`, which the dedupe
    branch never matches, so cases spent a SHARED budget and the eleventh
    placement in a worker got a 429 it never asked for. Under
    `pytest -n auto --dist loadfile` the victim depends on file-to-worker
    assignment; on 2026-08-29 it was the /orders/place timeout contract, the
    last red job on main. Contract: test_order_rate_limit_isolation.py.
    """
    from scripts.api import server

    server._order_rate_timestamps.clear()
    yield
    server._order_rate_timestamps.clear()


@pytest.fixture(autouse=True)
def _isolate_flow_reports_dir(tmp_path, monkeypatch):
    """Point the flow-report cache at tmp_path for every test in this subtree.

    `server._FLOW_REPORTS_DIR` is the REAL `data/flow_reports`, so any case
    that reaches POST /flow-analysis/{ticker} writes a live cache entry into
    the working tree. That dirties the tree the weekend loops require clean,
    trips deploy.sh's tracked-drift guard, and seeds a stub report that both
    the FastAPI GET handler and web/app/api/flow-analysis/[ticker]/route.ts
    then serve for a real ticker. scripts/tests/test_api_flow_cache.py already
    patches this per-case; this makes it the default for the api subtree. T-275.
    """
    from scripts.api import server

    monkeypatch.setattr(server, "_FLOW_REPORTS_DIR", tmp_path / "flow_reports")
    yield
