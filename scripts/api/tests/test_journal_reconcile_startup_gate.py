"""Startup warm reconcile must skip non-trading days (weekend / holiday).

Expiration emits no execution, so over a weekend IB shows a residual 0-qty row
for an expired contract while the local snapshot is Friday-frozen. Running
ib_reconcile.py against that pair false-flags a quantity_mismatch and pages
(SPCX 25x $150C, 2026-08-09 P2). The gate uses market_state() as the calendar
SoT and fails OPEN: a calendar error must never silently disable startup
reconciliation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import utils.market_calendar as market_calendar
from scripts.api import server as srv


def _state(reason: str, is_open: bool = False) -> dict:
    return {"state": "open" if is_open else "closed", "is_open": is_open, "reason": reason}


@pytest.fixture
def reconcile_runner(monkeypatch):
    runner = AsyncMock(return_value=srv.ScriptResult(ok=True))
    monkeypatch.setattr(srv, "run_script_raw", runner)
    return runner


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["weekend", "static:holiday", "ibkr:closed"])
async def test_skips_reconcile_on_non_trading_day(monkeypatch, reconcile_runner, reason):
    monkeypatch.setattr(market_calendar, "market_state", lambda: _state(reason))

    await srv._warm_journal_reconciliation_on_startup()

    reconcile_runner.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason, is_open",
    [
        ("weekday_hours", True),
        ("outside_hours", False),
        ("ibkr:open", True),
        ("ibkr:early_close", True),
        ("ibkr:outside_hours", False),
    ],
)
async def test_runs_reconcile_on_trading_day(monkeypatch, reconcile_runner, reason, is_open):
    monkeypatch.setattr(market_calendar, "market_state", lambda: _state(reason, is_open=is_open))

    await srv._warm_journal_reconciliation_on_startup()

    reconcile_runner.assert_awaited_once()


@pytest.mark.asyncio
async def test_calendar_error_fails_open_and_runs_reconcile(monkeypatch, reconcile_runner):
    def _boom():
        raise RuntimeError("calendar unavailable")

    monkeypatch.setattr(market_calendar, "market_state", _boom)

    await srv._warm_journal_reconciliation_on_startup()

    reconcile_runner.assert_awaited_once()


def test_skip_reasons_exist_in_calendar_vocabulary():
    """Pin _NON_TRADING_SESSION_REASONS to strings market_state() can actually
    emit — a calendar rename would otherwise silently turn every skip reason
    into fail-open."""
    import inspect

    calendar_source = inspect.getsource(market_calendar)
    for reason in srv._NON_TRADING_SESSION_REASONS:
        assert f'"{reason}"' in calendar_source, (
            f"gate skip reason {reason!r} is not a reason string "
            "utils/market_calendar.py can emit"
        )
