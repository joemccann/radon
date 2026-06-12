"""Shared fixtures for the monitor_daemon test suite.

Why this file exists: `fill_monitor`, `journal_sync`, `replica_watchdog`,
`cash_flow_sync`, and `exit_orders` all import `db.writer` writers and
call them inline. The handlers wrap writes in try/except (so a write
failure never crashes the cycle) which means tests that DON'T patch the
writers silently let real writes through to whatever Turso URL the env
points at — including production.

This surfaced on 2026-05-14: a full `pytest scripts/tests/` run with
production env vars in scope wrote two `MagicMock`-stringified journal
rows into the production journal table, which then surfaced as phantom
"AAOI STK" trades on the /orders Historical Trades panel.

The autouse fixture below replaces every writer with a Mock so test
runs are guaranteed not to touch any database, regardless of which env
vars the runner has loaded. Tests that intentionally want to verify
writer behaviour can override per-test via `monkeypatch.setattr(...)`
or by importing the real writer themselves; the fixture is non-strict.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


_WRITER_NAMES = (
    "upsert_journal_entry",
    "record_service_health",
    "upsert_cash_flow",
    "prune_service_health_events",
)


@pytest.fixture(autouse=True)
def _block_writes_in_monitor_daemon_tests(monkeypatch: pytest.MonkeyPatch):
    """Replace every `db.writer` function with a no-op Mock for the
    duration of every test in this directory. Prevents the
    test-pollution incident from recurring.
    """
    import db.writer as writer_mod  # noqa: WPS433 — late import keeps the module fresh

    for name in _WRITER_NAMES:
        if hasattr(writer_mod, name):
            monkeypatch.setattr(writer_mod, name, MagicMock(name=f"writer.{name}"))

    # Also patch the writers as imported into handler modules so the
    # handlers' `from db.writer import upsert_journal_entry` references
    # resolve to the mocks.
    for module_name in (
        "monitor_daemon.handlers.fill_monitor",
        "monitor_daemon.handlers.journal_sync",
        "monitor_daemon.handlers.cash_flow_sync",
        "monitor_daemon.handlers.replica_watchdog",
    ):
        try:
            module = __import__(module_name, fromlist=["*"])
        except ImportError:
            continue
        for name in _WRITER_NAMES:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, MagicMock(name=f"{module_name}.{name}"))

    yield
