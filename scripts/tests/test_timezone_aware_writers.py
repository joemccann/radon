"""Regression tests for timezone-aware writers in `ib_sync.py` and
`portfolio_performance.py`.

Covers two HIGH-severity correctness bugs:

1. `last_sync` (`scripts/ib_sync.py`) used `datetime.now().isoformat()` — no
   offset. On Hetzner (UTC) the JS consumer parses naive strings as local
   time, shifting the derived ET session date and triggering false-negative
   staleness checks (`web/lib/performanceFreshness.ts`).

2. `entry_date` (`scripts/ib_sync.py`) used `datetime.now().strftime(...)`.
   On Hetzner after 20:00 ET this is already tomorrow in ET, so a fresh
   position lands with the wrong stamp and `web/lib/positionUtils.ts:isSameDay`
   misses it — collapsing same-day P&L back to the broken close-based branch.

3. `portfolio_performance.py` derived `end_date` via `last_sync[:10]`. With
   the new UTC-aware producer the slice is correct only by accident; the
   helper used here picks the ET calendar day explicitly.
"""

import re
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

import ib_sync
import portfolio_performance


ISO_OFFSET_RE = re.compile(r"([+-]\d{2}:\d{2}|Z)$")


class TestIbSyncLastSyncTimezone(unittest.TestCase):
    """`last_sync` must include a timezone offset so JS parses it correctly."""

    def _build_portfolio(self) -> dict:
        with patch.object(ib_sync, "read_journal_entry_date_maps", return_value=({}, {})), \
             patch.object(ib_sync, "read_latest_portfolio_snapshot", return_value=None):
            return ib_sync.convert_to_portfolio_format(
                account={"NetLiquidation": 100000},
                collapsed_positions=[],
                pnl_data=None,
                fill_dates=None,
            )

    def test_last_sync_carries_timezone_offset(self):
        result = self._build_portfolio()
        last_sync = result["last_sync"]
        self.assertIsInstance(last_sync, str)
        self.assertRegex(
            last_sync,
            ISO_OFFSET_RE,
            msg=f"last_sync must include a tz offset (was: {last_sync!r})",
        )

    def test_last_sync_is_utc_aware(self):
        """Producer writes UTC; the offset proves Hetzner's wall clock is preserved."""
        result = self._build_portfolio()
        parsed = datetime.fromisoformat(result["last_sync"])
        self.assertIsNotNone(parsed.tzinfo)
        # UTC offset == zero — equivalent forms `+00:00` from datetime.now(utc).
        self.assertEqual(parsed.utcoffset().total_seconds(), 0)


class TestIbSyncEntryDateInET(unittest.TestCase):
    """`entry_date` must reflect the ET trading day, not the host's local day."""

    def _run_with_now(self, fake_utc_now: datetime, *, expected_et_date: str):
        """Patch `datetime.now` inside `ib_sync` so `today` is computed from a
        controlled instant. Verify the resulting `entry_date` is the ET day.
        """

        class _Now:
            """Pretend-clock: emulates `datetime.now(tz)` precisely."""

            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return fake_utc_now.replace(tzinfo=None)
                return fake_utc_now.astimezone(tz)

            def __getattr__(self, name):  # delegate to real datetime
                return getattr(datetime, name)

        collapsed = [{
            "id": 1, "ticker": "FOO", "structure": "Long Call $50",
            "structure_type": "Long Call", "risk_profile": "defined",
            "expiry": "2026-12-19", "contracts": 1, "direction": "LONG",
            "entry_cost": 100.0, "max_risk": 100.0, "market_value": 110.0,
            "ib_daily_pnl": None,
            "legs": [{
                "direction": "LONG", "contracts": 1, "type": "Call",
                "strike": 50.0, "entry_cost": 100.0, "avg_cost": 100.0,
                "market_price": 1.10, "market_value": 110.0,
            }],
        }]

        with patch.object(ib_sync, "read_journal_entry_date_maps", return_value=({}, {})), \
             patch.object(ib_sync, "read_latest_portfolio_snapshot", return_value=None), \
             patch.object(ib_sync, "datetime", _Now()):
            result = ib_sync.convert_to_portfolio_format(
                account={"NetLiquidation": 100000},
                collapsed_positions=collapsed,
                pnl_data=None,
                fill_dates=None,
            )

        self.assertEqual(result["positions"][0]["entry_date"], expected_et_date)

    def test_entry_date_uses_et_when_utc_host_after_midnight(self):
        # 2026-05-09T01:58 UTC == 2026-05-08T21:58 ET — still the 8th in ET.
        # A naive `datetime.now()` on Hetzner would have written 2026-05-09 here.
        self._run_with_now(
            datetime(2026, 5, 9, 1, 58, tzinfo=timezone.utc),
            expected_et_date="2026-05-08",
        )

    def test_entry_date_during_market_hours(self):
        # 2026-05-08T18:00 UTC == 2026-05-08T14:00 ET — same day in both zones.
        self._run_with_now(
            datetime(2026, 5, 8, 18, 0, tzinfo=timezone.utc),
            expected_et_date="2026-05-08",
        )


class TestPortfolioPerformanceEndDate(unittest.TestCase):
    """`portfolio_performance._last_sync_to_et_date` must derive the ET day
    correctly for naive UTC, tz-aware UTC, and tz-aware ET inputs."""

    def test_naive_utc_after_et_midnight_resolves_to_prior_et_day(self):
        # Legacy producer: naive ISO. Treated as UTC; ET is one day earlier.
        self.assertEqual(
            portfolio_performance._last_sync_to_et_date("2026-05-09T01:58:36.144211"),
            "2026-05-08",
        )

    def test_utc_aware_after_et_midnight_resolves_to_prior_et_day(self):
        # New producer (`datetime.now(timezone.utc).isoformat()`).
        self.assertEqual(
            portfolio_performance._last_sync_to_et_date("2026-05-09T01:58:36.144211+00:00"),
            "2026-05-08",
        )

    def test_zulu_suffix_supported(self):
        self.assertEqual(
            portfolio_performance._last_sync_to_et_date("2026-05-09T01:58:36Z"),
            "2026-05-08",
        )

    def test_et_aware_kept_as_is(self):
        # Laptop in ET writing tz-aware iso → no shift.
        self.assertEqual(
            portfolio_performance._last_sync_to_et_date("2026-05-08T21:58:36-04:00"),
            "2026-05-08",
        )

    def test_empty_returns_none(self):
        self.assertIsNone(portfolio_performance._last_sync_to_et_date(""))


if __name__ == "__main__":
    unittest.main()
