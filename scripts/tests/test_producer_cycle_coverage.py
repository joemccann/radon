#!/usr/bin/env python3
"""R-294 / R-295 / R-297 (REL-101): a partial cycle is not a healthy one.

Both Equibles fetchers write `ok` and REPLACE the previous snapshot as soon as
ONE ticker returns data. A run that served 3 of 40 tickers and then hit the
daily rate limit is indistinguishable, in `service_health` and on the panel,
from a complete one — and the good snapshot is gone.

Worse, the per-ticker handler catches `EquiblesAPIError`, which is the BASE of
`EquiblesRateLimitError` and `EquiblesAuthError`. Those are cycle-fatal: once
the allowance is exhausted every remaining ticker fails for the same reason,
and the loop patiently records 37 individual "errors" for one condition.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import fetch_equibles_ats_venue_share as ats  # noqa: E402
import fetch_equibles_short_crowding as crowd  # noqa: E402
from clients.equibles_client import (  # noqa: E402
    EquiblesAuthError,
    EquiblesNotFoundError,
    EquiblesRateLimitError,
)

UNIVERSE = [f"T{i:02d}" for i in range(40)]


@pytest.fixture
def health(monkeypatch):
    """Capture every service_health row both modules write."""
    rows: list[dict] = []
    for mod in (ats, crowd):
        monkeypatch.setattr(
            mod,
            "_record_health",
            lambda state, scan_time, error=None, _r=rows: _r.append(
                {"state": state, "error": error}
            ),
        )
        monkeypatch.setattr(mod, "_write_json_cache", lambda *a, **k: None)
    return rows


@pytest.fixture
def written(monkeypatch):
    """Capture snapshot replacements."""
    calls: list = []
    for mod in (ats, crowd):
        monkeypatch.setattr(mod, "_write_db_cache", lambda *a, _c=calls, **k: _c.append(a))
    return calls


def _series(ticker):
    return [{
        "ticker": ticker, "week_start_date": "2026-08-17", "ats_share_pct": 12.0,
        "avg_ats_print_size": 300.0, "total_shares": 1_000_000.0, "ats_shares": 120_000.0,
    }]


class TestAtsVenueShareCoverage:
    def _client_that_dies_after(self, n):
        state = {"served": 0}

        def fetch(client, ticker, start, end):
            if state["served"] >= n:
                raise EquiblesRateLimitError("daily allowance exhausted", code=429)
            state["served"] += 1
            return _series(ticker)

        return fetch

    def test_a_rate_limited_cycle_is_not_ok(self, health, written, monkeypatch):
        monkeypatch.setattr(ats, "watchlist_tickers", lambda: UNIVERSE)
        monkeypatch.setattr(ats, "_fetch_ticker", self._client_that_dies_after(3))
        # Cycle-fatal, so it propagates and systemd marks the unit failed.
        with pytest.raises(EquiblesRateLimitError):
            ats.run(client=object())
        assert health, "no service_health row at all"
        assert health[-1]["state"] != "ok", (
            "3 of 40 tickers then a rate limit was recorded as a healthy cycle"
        )

    def test_a_rate_limited_cycle_does_not_replace_the_snapshot(
        self, health, written, monkeypatch
    ):
        monkeypatch.setattr(ats, "watchlist_tickers", lambda: UNIVERSE)
        monkeypatch.setattr(ats, "_fetch_ticker", self._client_that_dies_after(3))
        with pytest.raises(EquiblesRateLimitError):
            ats.run(client=object())
        assert written == [], "a 3-of-40 payload overwrote the previous snapshot"

    def test_the_dropped_count_is_persisted_not_just_logged(
        self, health, written, monkeypatch
    ):
        monkeypatch.setattr(ats, "watchlist_tickers", lambda: UNIVERSE)
        monkeypatch.setattr(ats, "_fetch_ticker", self._client_that_dies_after(3))
        with pytest.raises(EquiblesRateLimitError):
            ats.run(client=object())
        err = health[-1]["error"] or {}
        assert err.get("covered") == 3 and err.get("requested") == 40, err
        assert any(isinstance(v, int) and v > 0 for v in err.values()), (
            f"the failed-ticker count was lost: {err}"
        )

    def test_an_auth_error_is_cycle_fatal_not_a_ticker_gap(
        self, health, written, monkeypatch
    ):
        monkeypatch.setattr(ats, "watchlist_tickers", lambda: UNIVERSE)

        seen = []

        def fetch(client, ticker, start, end):
            seen.append(ticker)
            raise EquiblesAuthError("key rejected", code=401)

        monkeypatch.setattr(ats, "_fetch_ticker", fetch)
        with pytest.raises(EquiblesAuthError):
            ats.run(client=object())
        assert len(seen) == 1, (
            f"a rejected API key was retried per ticker {len(seen)} times"
        )
        assert health[-1]["state"] == "error"

    def test_a_genuine_per_ticker_gap_still_isolates(self, health, written, monkeypatch):
        monkeypatch.setattr(ats, "watchlist_tickers", lambda: UNIVERSE)

        def fetch(client, ticker, start, end):
            if ticker == "T05":
                raise EquiblesNotFoundError("unknown ticker", code=404)
            return _series(ticker)

        monkeypatch.setattr(ats, "_fetch_ticker", fetch)
        ats.run(client=object())
        assert health[-1]["state"] == "ok", "one unknown ticker failed the whole cycle"
        assert written, "a healthy 39-of-40 cycle did not write its snapshot"


class TestShortCrowdingCoverage:
    def test_a_one_ticker_cycle_is_not_ok(self, health, written, monkeypatch):
        monkeypatch.setattr(crowd, "resolve_universe", lambda t=None: UNIVERSE)
        monkeypatch.setattr(crowd, "_fetch_board", lambda c: [])

        def entry(client, ticker, today):
            if ticker != "T00":
                raise EquiblesRateLimitError("daily allowance exhausted", code=429)
            return {"ticker": ticker, "short_interest_pct_float": 12.0}

        monkeypatch.setattr(crowd, "_fetch_entry", entry)
        try:
            crowd.run(client=object())
        except EquiblesRateLimitError:
            pass
        assert health, "no service_health row at all"
        assert health[-1]["state"] != "ok", (
            "1 of 40 tickers was recorded as a healthy cycle"
        )

    def test_it_accounts_for_errors_at_all(self, health, written, monkeypatch):
        """Short-crowding had no `errors` list in its payload whatsoever."""
        monkeypatch.setattr(crowd, "resolve_universe", lambda t=None: ["AAA", "BBB"])
        monkeypatch.setattr(crowd, "_fetch_board", lambda c: [])

        def entry(client, ticker, today):
            if ticker == "BBB":
                raise EquiblesNotFoundError("unknown", code=404)
            return {"ticker": ticker, "short_interest_pct_float": 12.0}

        monkeypatch.setattr(crowd, "_fetch_entry", entry)
        payload = crowd.run(client=object())
        assert "errors" in payload, "the payload carries no error accounting"
        assert len(payload["errors"]) == 1


class TestMenthorqCtaDegradation:
    """R-297: a vision payload whose percentiles all nulled is not `ok`.

    `fetch_menthorq_cta` wrote NO service_health row at all, and reported a
    wholesale extraction failure as a stderr WARN — invisible to the watchdog.
    The payload looks complete (every row present, every position and z-score
    real) while the one column the surface reads is gone.
    """

    def test_a_mostly_nulled_extraction_is_degraded(self):
        import fetch_menthorq_cta as cta

        assert cta._extraction_degraded(9, 10) is True

    def test_an_entirely_nulled_extraction_is_degraded(self):
        import fetch_menthorq_cta as cta

        assert cta._extraction_degraded(10, 10) is True

    def test_an_empty_extraction_is_degraded(self):
        import fetch_menthorq_cta as cta

        assert cta._extraction_degraded(0, 0) is True

    def test_an_ordinary_row_or_two_is_not(self):
        import fetch_menthorq_cta as cta

        assert cta._extraction_degraded(1, 10) is False

    def test_the_degraded_state_reaches_service_health(self, monkeypatch):
        import fetch_menthorq_cta as cta

        rows: list = []

        class _Writer:
            @staticmethod
            def record_service_health(service, state, finished_at=None, error=None):
                rows.append({"service": service, "state": state, "error": error})

        import db

        monkeypatch.setattr(db, "writer", _Writer, raising=False)
        cta._record_health("error", rows=10, dropped=9)
        assert rows and rows[0]["state"] == "error"
        assert rows[0]["service"] == "menthorq-cta"
        assert rows[0]["error"]["dropped"] == 9
