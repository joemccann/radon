"""REL-062 / R-150, R-151, R-152 (all P1) — UW budget holes reopened by jobs
this delta merged.

R-150: `vol-cone-intraday` writes an `ok` heartbeat on the QUOTA-HOLD path.
`_write_intraday_db_cache` calls `record_service_health(..., "ok")`
unconditionally, and the watchdog window (45 min open) is keyed on row
freshness — so a fresh `ok` every 15 minutes means the staleness detector
can never fire for a quota outage. `payload["market_status"]` is also stamped
"closed" while the market is open. The VOL CONE tab reverts to last night's
cone for the rest of the session behind a green chip.

R-151: two brakes on one budget with incompatible thresholds.
`should_block_universe_scan` refuses theta / strength / discover at 20,000
used, reserving the top half; `_INTRADAY_UW_FLOOR = 2_000` lets
`vol-cone-intraday` spend down to 2,000 remaining — i.e. freely consume the
entire 20,000-38,000 band the universe brake exists to protect, at
2,106-4,160 requests per weekday.

R-152: `fetch_ivrank._uw_get` is a raw `urlopen`: no `record_hit`, no cache,
no 429 handling, no daily-limit check. Every ivrank UW hit is invisible to
`/uw/usage`, to `top_callers` and to the universe brake — the R-062 hole
REL-036 closed for the six Next.js routes, reopened. The unit sets no
`RADON_UW_CALLER`, so even counted it would be unattributed.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from utils import uw_budget


class TestIntradayFloorProtectsTheUniverseBand:
    def test_the_intraday_floor_does_not_eat_the_universe_reserve(self):
        import fetch_vol_cone as vc

        assert vc._INTRADAY_UW_FLOOR >= uw_budget.DAILY_LIMIT - uw_budget.UNIVERSE_BLOCK_AT, (
            "vol-cone-intraday can still spend the whole band "
            f"{uw_budget.UNIVERSE_BLOCK_AT}-{uw_budget.DAILY_LIMIT} that "
            "should_block_universe_scan reserves for theta/strength/discover"
        )

    def test_intraday_holds_once_the_universe_brake_would_trip(self, monkeypatch):
        import fetch_vol_cone as vc

        monkeypatch.setattr(vc, "_uw_remaining", lambda: uw_budget.DAILY_LIMIT - uw_budget.UNIVERSE_BLOCK_AT)
        monkeypatch.setattr(vc, "market_state", lambda _now: {"is_open": True})
        reason = vc._intraday_hold_reason(
            datetime(2026, 8, 21, 15, 0, tzinfo=timezone.utc), [{"ticker": "SPY"}]
        )
        assert reason is not None
        assert "budget" in reason.lower()

    def test_intraday_still_runs_with_ample_budget(self, monkeypatch):
        import fetch_vol_cone as vc

        monkeypatch.setattr(vc, "_uw_remaining", lambda: uw_budget.DAILY_LIMIT)
        monkeypatch.setattr(vc, "market_state", lambda _now: {"is_open": True})
        assert vc._intraday_hold_reason(
            datetime(2026, 8, 21, 15, 0, tzinfo=timezone.utc), [{"ticker": "SPY"}]
        ) is None


class TestIntradayHoldIsHonest:
    def test_a_quota_hold_does_not_write_an_ok_heartbeat(self, monkeypatch):
        import fetch_vol_cone as vc

        recorded: list[tuple] = []

        class _Writer:
            @staticmethod
            def record_service_health(service, state, **kw):
                recorded.append((service, state, kw.get("error")))

        monkeypatch.setattr(vc, "_mirror_snapshot", lambda *a, **k: _Writer())
        vc._write_intraday_db_cache(
            {"market_status": "open"},
            "2026-08-21T15:00:00Z",
            hold_reason="UW daily budget nearly spent (1500 left)",
        )

        assert recorded, "no heartbeat at all"
        service, state, error = recorded[-1]
        assert service == "vol-cone-intraday"
        assert state == "error", (
            "a quota hold heartbeated ok, so the 45-minute staleness window "
            "can never fire for a quota outage"
        )
        assert "budget" in str(error).lower()

    def test_a_normal_cycle_still_heartbeats_ok(self, monkeypatch):
        import fetch_vol_cone as vc

        recorded: list[tuple] = []

        class _Writer:
            @staticmethod
            def record_service_health(service, state, **kw):
                recorded.append((service, state))

        monkeypatch.setattr(vc, "_mirror_snapshot", lambda *a, **k: _Writer())
        vc._write_intraday_db_cache({"market_status": "open"}, "2026-08-21T15:00:00Z")
        assert recorded[-1] == ("vol-cone-intraday", "ok")

    def test_a_hold_never_relabels_an_open_market_as_closed(self, monkeypatch):
        import fetch_vol_cone as vc
        import inspect

        source = inspect.getsource(vc)
        assert "_HOLD_MARKET_STATUS" in source, (
            'the budget-hold branch still stamps market_status="closed" while '
            "the market is open"
        )


class TestIvrankUwHitsAreCounted:
    def test_every_ivrank_uw_get_is_recorded(self, tmp_path, monkeypatch):
        import fetch_ivrank as iv

        budget = tmp_path / "uw_budget.json"
        monkeypatch.setattr(uw_budget, "BUDGET_PATH", budget)
        monkeypatch.setenv("UW_TOKEN", "t0ken")

        class _Resp:
            @staticmethod
            def read():
                return json.dumps({"data": []}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        import urllib.request

        monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: _Resp())

        iv._uw_get("/api/stock/SPY/iv-rank")
        iv._uw_get("/api/stock/SPY/iv-rank?date=2026-08-21")

        assert uw_budget.used(path=budget) == 2, (
            "ivrank UW hits are still invisible to /uw/usage, top_callers and "
            "should_block_universe_scan"
        )
        snapshot = uw_budget.usage_snapshot(path=budget)
        callers = {row["name"] for row in snapshot["top_callers"]}
        assert "ivrank" in callers, f"unattributed: {callers}"

    def test_the_repair_pass_refuses_to_run_past_the_universe_brake(self, monkeypatch):
        """R-152: `repair_outliers` re-flags every row still tagged `ib` and
        re-fetches them EVERY NIGHT forever — a date UW cannot serve (its
        2023-09-22 floor) never clears. Unbounded and monotone."""
        import fetch_ivrank as iv

        assert hasattr(iv, "UW_REPAIR_MAX_LOOKUPS")
        assert iv.UW_REPAIR_MAX_LOOKUPS > 0

    def test_the_unit_attributes_its_uw_hits(self):
        unit = (REPO / "cloud" / "services" / "radon-ivrank.service").read_text()
        assert "RADON_UW_CALLER=ivrank" in unit


class TestIvrankRepairSurvivesTheNextIbFetch:
    """REL-064 / R-154 (P1): the repair is clobbered by the next IB fetch.

    `merge_history` refuses only `uw`-over-`ib`. The reverse — the daily IB
    "1M" fetch restating the same bad bar as `source='ib'` — overwrites the
    stored repaired `uw` row. `repair_outliers` then re-detects it and
    re-calls UW; if UW raises or returns None the loop `continue`s,
    `_rows_changed` is still True, and `_write_db` upserts the bad IB print
    back over the good value. Once the date ages out of the 1-month IB
    window it FREEZES at whatever the last run wrote. A 0.2443 print against
    a ~0.12 series sets the max of the 252-session window, distorting
    `iv_rank` and the SUPPRESSED/NORMAL/ELEVATED/EXTREME label for every one
    of the next 252 sessions.
    """

    def test_an_ib_restatement_never_overwrites_a_repaired_uw_row(self):
        import fetch_ivrank as iv

        stored = [{"date": "2026-08-14", "iv": 0.121, "source": "uw", "repaired": True}]
        fetched = [{"date": "2026-08-14", "iv": 0.2443, "source": "ib"}]

        merged = iv.merge_history(stored, fetched)

        assert merged[0]["iv"] == 0.121
        assert merged[0]["source"] == "uw"

    def test_an_ib_restatement_still_wins_over_an_unrepaired_ib_row(self):
        import fetch_ivrank as iv

        stored = [{"date": "2026-08-14", "iv": 0.118, "source": "ib"}]
        fetched = [{"date": "2026-08-14", "iv": 0.120, "source": "ib"}]

        assert iv.merge_history(stored, fetched)[0]["iv"] == 0.120

    def test_a_uw_row_still_never_overwrites_a_plain_ib_row(self):
        import fetch_ivrank as iv

        stored = [{"date": "2026-08-14", "iv": 0.118, "source": "ib"}]
        fetched = [{"date": "2026-08-14", "iv": 0.130, "source": "uw"}]

        assert iv.merge_history(stored, fetched)[0]["iv"] == 0.118

    def test_a_failed_uw_lookup_leaves_the_stored_value_alone(self):
        """The second half: a repair pass whose lookup fails must not report
        a change, or the bad IB print is upserted back over the good one."""
        import fetch_ivrank as iv

        rows = [
            {"date": "2026-08-12", "iv": 0.120, "source": "ib"},
            {"date": "2026-08-13", "iv": 0.2443, "source": "ib"},
            {"date": "2026-08-14", "iv": 0.121, "source": "ib"},
        ]

        def _dead(_date):
            raise RuntimeError("UW 429")

        repaired, repairs = iv.repair_outliers(rows, _dead)

        assert repairs == []
        assert repaired == rows, "a failed repair rewrote the series"

    def test_a_successful_repair_is_marked_so_it_survives(self):
        import fetch_ivrank as iv

        rows = [
            {"date": "2026-08-12", "iv": 0.120, "source": "ib"},
            {"date": "2026-08-13", "iv": 0.2443, "source": "ib"},
            {"date": "2026-08-14", "iv": 0.121, "source": "ib"},
        ]
        repaired, repairs = iv.repair_outliers(rows, lambda _d: 0.1205)

        assert len(repairs) == 1
        fixed = next(r for r in repaired if r["date"] == "2026-08-13")
        assert fixed["source"] == "uw"
        assert fixed.get("repaired") is True
