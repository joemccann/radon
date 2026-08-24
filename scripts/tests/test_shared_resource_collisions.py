"""REL-052 tranche D — R-117, R-121, R-127, R-128.

Four ways the 2026-08-22 delta let two consumers collide on one resource:
an IB historical request slot, a StockCharts scrape budget, a pair of IB
client IDs, and one `scan_snapshots` key.
"""
from __future__ import annotations

import asyncio
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[2]
SERVICES = REPO / "cloud" / "services"


# --------------------------------------------------------------------------
# R-117 — the outer deadline must not pre-empt ib_insync's own cancel path
# --------------------------------------------------------------------------
class _FakeIB:
    """Records the timeout ib_insync itself was handed."""

    def __init__(self, *, inner_timeout_fires: bool = False):
        self.inner_timeout_fires = inner_timeout_fires
        self.hist_timeout = None
        self.cancelled: list = []
        # ib_insync returns a BarDataList (a list subclass), never a mock.
        self.bars: list = []

    def isConnected(self):
        return True

    async def reqHistoricalDataAsync(self, contract, **kwargs):
        self.hist_timeout = kwargs.get("timeout")
        if self.inner_timeout_fires:
            # ib_insync's own path: wait out the deadline, send
            # cancelHistoricalData(reqId), then return an EMPTY container.
            # It never raises, which is exactly why the caller cannot tell
            # a timeout from "this contract has no bars".
            await asyncio.sleep(self.hist_timeout)
            self.cancelled.append("reqId")
        return self.bars

    def cancelHistoricalData(self, bars):
        self.cancelled.append(bars)

    def run(self, coro):
        return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def ib_client():
    from clients.ib_client import IBClient

    client = IBClient.__new__(IBClient)
    client.logger = MagicMock()
    return client


class TestHistoricalCancelReachesIB:
    def test_ib_insync_gets_its_own_timeout_so_it_can_cancel(self, ib_client):
        fake = _FakeIB()
        ib_client._ib = fake
        ib_client.get_historical_data(MagicMock(), timeout=15.0)
        assert fake.hist_timeout == 15.0, (
            "reqHistoricalDataAsync was not given a timeout, so the "
            "cancelHistoricalData path can never run"
        )

    def test_the_outer_deadline_is_strictly_later_than_the_inner_one(self, ib_client):
        from clients import ib_client as mod

        assert mod.HISTORICAL_CANCEL_GRACE_SECS > 0
        fake = _FakeIB()
        ib_client._ib = fake
        captured = {}
        real_wait_for = asyncio.wait_for

        async def spy(aw, timeout):
            captured["outer"] = timeout
            return await real_wait_for(aw, timeout)

        mod.asyncio.wait_for = spy
        try:
            ib_client.get_historical_data(MagicMock(), timeout=15.0)
        finally:
            mod.asyncio.wait_for = real_wait_for
        assert captured["outer"] > fake.hist_timeout

    def test_a_timed_out_keep_up_to_date_request_ends_its_subscription(self, ib_client):
        fake = _FakeIB(inner_timeout_fires=True)
        ib_client._ib = fake
        with pytest.raises(Exception):
            ib_client.get_historical_data(
                MagicMock(), timeout=0.05, keep_up_to_date=True
            )
        assert fake.bars in fake.cancelled, "the subscription leaked"

    def test_an_empty_result_is_reported_as_a_timeout_not_as_no_data(self, ib_client):
        from clients.ib_client import IBTimeoutError

        fake = _FakeIB(inner_timeout_fires=True)
        ib_client._ib = fake
        with pytest.raises(IBTimeoutError):
            ib_client.get_historical_data(MagicMock(), timeout=0.05)


class TestHistoricalRouteHandlesTimeout:
    def test_the_bars_route_maps_a_timeout_to_503(self):
        src = (REPO / "scripts" / "api" / "routes" / "historical.py").read_text()
        assert "IBTimeoutError" in src, (
            "IBTimeoutError subclasses IBError, not ConnectionError — an "
            "unhandled raise is a 500 on a gateway that is merely slow"
        )
        for handler in ("historical_bars", "head_timestamp"):
            body = src.split(f"async def {handler}(")[1].split("\n@router")[0]
            assert "IBTimeoutError" in body, f"{handler} leaves the timeout unhandled"


# --------------------------------------------------------------------------
# R-121 — the daily StockCharts scrape is not a 5-minute job
# --------------------------------------------------------------------------
class TestTrinDailyScrapeIsGated:
    def test_a_current_daily_series_skips_the_scrape(self, monkeypatch):
        import fetch_trin

        scraped = []
        monkeypatch.setattr(
            fetch_trin, "fetch_daily", lambda: scraped.append("hit") or []
        )
        monkeypatch.setattr(
            fetch_trin, "last_completed_session_date", lambda: "2026-08-21"
        )
        cached = [("2026-08-20", 1.0), ("2026-08-21", 0.9)]
        assert fetch_trin.daily_needs_refresh(cached) is False
        assert fetch_trin.fetch_daily_if_stale(cached) == []
        assert scraped == []

    def test_a_stale_daily_series_still_scrapes(self, monkeypatch):
        import fetch_trin

        monkeypatch.setattr(fetch_trin, "fetch_daily", lambda: [("2026-08-21", 0.9)])
        monkeypatch.setattr(
            fetch_trin, "last_completed_session_date", lambda: "2026-08-21"
        )
        cached = [("2026-08-20", 1.0)]
        assert fetch_trin.daily_needs_refresh(cached) is True
        assert fetch_trin.fetch_daily_if_stale(cached) == [("2026-08-21", 0.9)]

    def test_an_empty_cache_always_scrapes(self, monkeypatch):
        import fetch_trin

        monkeypatch.setattr(
            fetch_trin, "last_completed_session_date", lambda: "2026-08-21"
        )
        assert fetch_trin.daily_needs_refresh([]) is True

    def test_run_uses_the_gated_fetch(self):
        src = (REPO / "scripts" / "fetch_trin.py").read_text()
        run_body = src.split("def run() -> dict[str, Any]:")[1].split("\ndef ")[0]
        assert "fetch_daily_if_stale" in run_body
        assert re.search(r"=\s*fetch_daily\(\)", run_body) is None


# --------------------------------------------------------------------------
# R-127 — two jobs sharing IB client IDs 56/69 need a real mutex
# --------------------------------------------------------------------------
def _unit_value(unit: str, key: str) -> str:
    for line in (SERVICES / unit).read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1]
    return ""


SHARED_ID_UNITS = ("radon-credit-spread.service", "radon-iei-hyg.service")


class TestSharedIbClientIdsAreSerialized:
    def test_the_two_jobs_still_share_the_same_ids(self):
        from fetch_credit_spread import CREDIT_IB_HISTORY_CLIENT_IDS
        from fetch_iei_hyg import IEI_HYG_IB_HISTORY_CLIENT_IDS

        assert CREDIT_IB_HISTORY_CLIENT_IDS == IEI_HYG_IB_HISTORY_CLIENT_IDS

    @pytest.mark.parametrize("unit", SHARED_ID_UNITS)
    def test_each_takes_the_shared_lock(self, unit):
        exec_start = _unit_value(unit, "ExecStart")
        assert "/usr/bin/flock" in exec_start, (
            f"{unit} shares IB client IDs 56/69 but has no mutex; "
            "RandomizedDelaySec=300 on both makes the 21:45/21:55 gap "
            "insufficient on its own"
        )
        assert "/run/lock/radon-ib-history-5669.lock" in exec_start

    @pytest.mark.parametrize("unit", SHARED_ID_UNITS)
    def test_the_lock_loser_defers_instead_of_failing(self, unit):
        assert _unit_value(unit, "SuccessExitStatus") == "75"
        assert "-E 75" in _unit_value(unit, "ExecStart")

    @pytest.mark.parametrize(
        "unit,peer", list(zip(SHARED_ID_UNITS, tuple(reversed(SHARED_ID_UNITS))))
    )
    def test_the_wait_outlasts_the_peers_whole_run(self, unit, peer):
        # A unit's work ceiling is its start budget minus its own lock wait.
        wait = int(re.search(r"-w (\d+)", _unit_value(unit, "ExecStart")).group(1))
        peer_wait = int(re.search(r"-w (\d+)", _unit_value(peer, "ExecStart")).group(1))
        peer_work = int(_unit_value(peer, "TimeoutStartSec")) - peer_wait
        assert wait >= peer_work, (
            f"{unit} waits {wait}s but {peer} can hold the lock for "
            f"{peer_work}s, so the loser still collides on the next slot"
        )

    @pytest.mark.parametrize("unit", SHARED_ID_UNITS)
    def test_the_start_budget_absorbs_the_wait_plus_the_work(self, unit):
        exec_start = _unit_value(unit, "ExecStart")
        wait = int(re.search(r"-w (\d+)", exec_start).group(1))
        budget = int(_unit_value(unit, "TimeoutStartSec"))
        assert budget > wait, (
            "winning the lock late must not eat the entire work budget"
        )


# --------------------------------------------------------------------------
# R-128 — one `vol-cone` snapshot key, two writers, one shared minute
# --------------------------------------------------------------------------
def _oncalendar(unit: str) -> list[str]:
    return [
        line.split("=", 1)[1]
        for line in (SERVICES / unit).read_text().splitlines()
        if line.startswith("OnCalendar=")
    ]


class TestVolConeWritersDoNotCollide:
    def test_the_intraday_timer_no_longer_fires_at_the_eod_minute(self):
        intraday = " ".join(_oncalendar("radon-vol-cone-intraday.timer"))
        # 16:45 America/New_York IS 20:45 UTC in EDT — the EOD slot.
        assert "16:45" not in intraday.replace(" ", "")
        assert not re.search(r"16[^\n]*:0*0,15,30,45", intraday), (
            "the 16:45 ET slot is the EOD writer's own minute"
        )

    def test_the_intraday_timer_still_covers_the_session(self):
        intraday = " ".join(_oncalendar("radon-vol-cone-intraday.timer"))
        for slot in ("09", "15", "16"):
            assert slot in intraday

    def test_a_market_closed_hold_does_not_republish_the_shared_snapshot(
        self, monkeypatch
    ):
        import fetch_vol_cone

        published: list = []
        monkeypatch.setattr(
            fetch_vol_cone, "_read_history_rows",
            lambda: [
                {
                    "ticker": "SPY", "expiry": "2026-09-18", "date": "2026-08-21",
                    "spot": 500.0, "atm_iv": 0.2, "call_10_iv": 0.22,
                    "put_10_iv": 0.25, "call_10_strike": 550.0,
                    "put_10_strike": 450.0,
                }
            ],
        )
        monkeypatch.setattr(
            fetch_vol_cone, "select_target_expiries",
            lambda d: [__import__("datetime").date(2026, 9, 18)],
        )
        monkeypatch.setattr(fetch_vol_cone, "market_state", lambda now: {"is_open": False})
        monkeypatch.setattr(
            fetch_vol_cone, "_mirror_snapshot",
            lambda payload, scan_time, rows_changed: published.append(scan_time),
        )
        monkeypatch.setattr(fetch_vol_cone, "_write_json_cache", lambda p: published.append("json"))
        recorded: list = []
        monkeypatch.setattr(
            fetch_vol_cone, "_write_intraday_db_cache",
            lambda payload, scan_time, hold_reason=None: recorded.append(hold_reason),
        )

        now = datetime(2026, 8, 21, 20, 45, tzinfo=timezone.utc)
        payload = fetch_vol_cone.run_intraday(now=now)

        assert payload["hold_reason"] == "market closed"
        assert published == [], (
            "a held intraday run has nothing new to publish, but it "
            "overwrote the EOD writer's snapshot and JSON file"
        )
        assert recorded == [None], "the heartbeat must still be stamped"

    def test_a_live_intraday_pass_still_publishes(self):
        src = (REPO / "scripts" / "fetch_vol_cone.py").read_text()
        body = src.split("def run_intraday(")[1].split("\n# ── CLI")[0]
        assert "_write_json_cache" in body and "_write_intraday_db_cache" in body
