#!/usr/bin/env python3
"""Gate 3 must be measurable, bounded, and honest about not running.

R-204: the disk-cache fallback installed a series with no freshness test, so a
stale cache made a ticker "measurable" while `_aligned_returns` (which
intersects DATES) found too few shared dates against a fresh sibling and
returned no correlation. Both tickers passed the per-ticker sufficiency test,
so neither reached `insufficient_data`, and the operator read a Gate 3 report
with zero clusters and zero insufficient tickers — a clean bill of health —
while the concentration the gate exists to surface was structurally invisible.

R-205: `_record_backfill_attempt(due)` stamped the 6-hour blackout for all four
symbols before a byte was fetched, and `_persist_closes` was unguarded, so one
transient Turso write failure on the first symbol discarded its closes, left
the rest unattempted, and blacked all four out for six hours.

R-206: the loader opens a SECOND IB Gateway socket from inside the live
portfolio sync, which the module's own comment says runs every minute during
RTH. A dead gateway is the expensive case — `_ib_reachable` proceeds
optimistically on an unreachable /health, so it is four sequential connect
timeouts before the ladder even reaches UW.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import portfolio_risk  # noqa: E402


def _dated_series(days_ago_start: int, count: int, start: float = 100.0) -> dict:
    base = datetime.now(timezone.utc).date() - timedelta(days=days_ago_start)
    return {
        (base + timedelta(days=i)).isoformat(): start + i
        for i in range(count)
    }


@pytest.fixture
def marker(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_risk, "_BACKFILL_MARKER_PATH", tmp_path / "backfill.json")
    return tmp_path / "backfill.json"


class TestDiskCacheFreshness:
    def test_a_stale_disk_series_is_not_installed_as_measurable(self, monkeypatch, tmp_path):
        """A 2025 cache file must not make a ticker look currently measurable."""
        stale = _dated_series(days_ago_start=400, count=40)
        monkeypatch.setattr(portfolio_risk, "read_price_history_closes", lambda *a, **k: {})
        monkeypatch.setattr(portfolio_risk, "_load_disk_cache_series", lambda t, d: {"OLD": stale})
        monkeypatch.setattr(portfolio_risk, "backfill_price_history", lambda t: {})

        series = portfolio_risk.load_price_series_for_portfolio(
            {"positions": [{"ticker": "OLD", "structure_type": "Stock"}]},
            allow_backfill=False,
        )
        assert "OLD" not in series, (
            "a stale disk series was installed as a current correlation input"
        )

    def test_a_fresh_disk_series_is_still_installed(self, monkeypatch):
        # 40 sessions ending today: deep enough and inside STALE_TOLERANCE_DAYS.
        fresh = _dated_series(days_ago_start=39, count=40)
        monkeypatch.setattr(portfolio_risk, "read_price_history_closes", lambda *a, **k: {})
        monkeypatch.setattr(portfolio_risk, "_load_disk_cache_series", lambda t, d: {"NEW": fresh})
        monkeypatch.setattr(portfolio_risk, "backfill_price_history", lambda t: {})

        series = portfolio_risk.load_price_series_for_portfolio(
            {"positions": [{"ticker": "NEW", "structure_type": "Stock"}]},
            allow_backfill=False,
        )
        assert "NEW" in series


class TestBackfillBlackoutIsPerSymbolAndAfterTheAttempt:
    def test_a_persist_failure_does_not_discard_the_other_symbols(self, marker, monkeypatch):
        attempted: list[str] = []

        def ladder(symbol, deadline=None, clock=None):
            attempted.append(symbol)
            return ({"2026-08-20": 10.0}, "ib")

        def persist(symbol, closes, source):
            if symbol == "AAA":
                raise RuntimeError("turso write hiccup")

        monkeypatch.setattr(portfolio_risk, "_fetch_closes_via_ladder", ladder)
        monkeypatch.setattr(portfolio_risk, "_persist_closes", persist)

        fetched = portfolio_risk.backfill_price_history(["AAA", "BBB", "CCC"])

        assert attempted == ["AAA", "BBB", "CCC"], (
            "one write failure aborted the loop and left the rest unattempted"
        )
        assert "BBB" in fetched and "CCC" in fetched

    def test_a_fetched_series_survives_a_persist_failure(self, marker, monkeypatch):
        monkeypatch.setattr(
            portfolio_risk, "_fetch_closes_via_ladder",
            lambda s, deadline=None, clock=None: ({"2026-08-20": 10.0}, "ib"),
        )
        monkeypatch.setattr(
            portfolio_risk, "_persist_closes",
            lambda *a: (_ for _ in ()).throw(RuntimeError("turso down")),
        )
        fetched = portfolio_risk.backfill_price_history(["AAA"])
        assert fetched.get("AAA"), "closes already in hand were discarded on a write failure"

    def test_the_blackout_is_stamped_per_symbol_after_the_attempt(self, marker, monkeypatch):
        monkeypatch.setattr(
            portfolio_risk, "_fetch_closes_via_ladder",
            lambda s, deadline=None, clock=None: (
                ({"2026-08-20": 10.0}, "ib") if s == "AAA" else ({}, "")
            ),
        )
        monkeypatch.setattr(portfolio_risk, "_persist_closes", lambda *a: None)

        portfolio_risk.backfill_price_history(["AAA", "BBB"])
        # BBB was attempted and produced nothing; it is legitimately blacked
        # out. The defect was blacking out symbols never reached at all.
        assert portfolio_risk._due_for_backfill(["AAA", "BBB"]) == []

    def test_symbols_never_reached_are_not_blacked_out(self, marker, monkeypatch):
        monkeypatch.setattr(portfolio_risk, "BACKFILL_MAX_SYMBOLS_PER_RUN", 2)
        monkeypatch.setattr(
            portfolio_risk, "_fetch_closes_via_ladder",
            lambda s, deadline=None, clock=None: ({"2026-08-20": 10.0}, "ib"),
        )
        monkeypatch.setattr(portfolio_risk, "_persist_closes", lambda *a: None)

        portfolio_risk.backfill_price_history(["AAA", "BBB", "CCC", "DDD"])
        assert portfolio_risk._due_for_backfill(["CCC", "DDD"]) == ["CCC", "DDD"]


class TestLoaderIsTimeBoxed:
    def test_the_backfill_ladder_is_bounded_by_a_total_wall_clock_budget(self, marker, monkeypatch):
        """A dead gateway must not cost four sequential connect timeouts.

        `attach_correlation_risk_report` runs every minute during RTH.
        """
        calls: list[str] = []
        clock = {"t": 0.0}

        def slow_ladder(symbol, deadline=None, ladder_clock=None):
            calls.append(symbol)
            # A dead gateway: this symbol burned the whole budget on connect
            # timeouts before the ladder even reached UW.
            clock["t"] += portfolio_risk.BACKFILL_TOTAL_BUDGET_S
            return ({}, "")

        monkeypatch.setattr(portfolio_risk, "_fetch_closes_via_ladder", slow_ladder)
        monkeypatch.setattr(portfolio_risk, "_persist_closes", lambda *a: None)
        portfolio_risk.backfill_price_history(
            ["AAA", "BBB", "CCC", "DDD"], clock=lambda: clock["t"]
        )
        assert len(calls) == 1, (
            f"the ladder kept going past its wall-clock budget: {calls}"
        )

    def test_the_call_bound_is_stated_rather_than_implied(self):
        """One call is bounded by budget + one symbol's worst-case ladder.

        The budget is checked before a symbol starts, so a symbol already in
        flight runs to its own timeouts. Naming both halves makes the
        guarantee checkable instead of implied, and keeps the whole call
        inside two portfolio-sync cadences.
        """
        bound = (
            portfolio_risk.BACKFILL_TOTAL_BUDGET_S
            + portfolio_risk.BACKFILL_SYMBOL_WORST_CASE_S
        )
        assert bound <= 120, f"a single loader call can block for {bound}s"
        # And the unbounded shape the finding measured — four symbols each
        # running the full three-rung ladder — is no longer reachable.
        unbounded = (
            portfolio_risk.BACKFILL_MAX_SYMBOLS_PER_RUN
            * portfolio_risk.BACKFILL_LADDER_RUNGS
            * portfolio_risk.BACKFILL_SYMBOL_WORST_CASE_S
        )
        assert bound < unbounded
