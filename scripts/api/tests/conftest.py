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
    """Reset the process-wide per-minute placement budget around every test.

    `server._order_rate_timestamps` holds every accepted placement inside a
    rolling 60s window, capped by `RADON_MAX_ORDERS_PER_MIN` (default 10).
    Eight files in this subtree POST `/orders/place`, and under CI's
    `-n auto --dist loadfile` one worker runs several of them back to back
    well inside 60s — so a later test inherited a spent budget and got 429
    where it asserted 200. Two files cleared the deque by hand, which made the
    leak look handled while every other file stayed exposed.
    """
    import server as _server  # imported here: sys.path is set up above

    _server._order_rate_timestamps.clear()
    yield
    _server._order_rate_timestamps.clear()


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
