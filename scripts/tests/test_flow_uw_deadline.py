"""The flow report's UW budget bounds every page, not only which days are walked.

2026-10-27 (prod, after the history budget shipped): QQQ still timed out at
196s. A host profile showed 70 of 83s inside ``uw_governor.reserve`` sleeps:
the account-wide governor admits ~96 standard requests a minute, and today's
40-page walk plus the 25-page alerts walk waited it out page by page with a
120s admission wait each. The budget now bounds paging itself: walks stop at
the deadline (today keeps what it fetched, a truncated prior day is dropped and
never cached), and each page waits for admission no longer than the budget.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import fetch_flow

PAGE = fetch_flow.DARKPOOL_PAGE_LIMIT


class Clock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self):
        return self.now


def _trade(i: int, ts: str) -> dict:
    return {"size": 100, "price": 50.0, "premium": 5000.0, "nbbo_bid": 49.9, "nbbo_ask": 50.1,
            "executed_at": ts, "tracking_id": i}


def _full_page(base: int, hour: int) -> list:
    return [_trade(base + i, f"2026-10-26T{hour:02d}:{59 - (i // 60):02d}:{59 - (i % 60):02d}Z") for i in range(PAGE)]


def test_walk_stops_paging_at_the_deadline_and_is_not_cacheable():
    clock = Clock()
    pages = [_full_page(0, 19), _full_page(PAGE, 18), _full_page(2 * PAGE, 17)]
    client = MagicMock()

    def page(*_a, **_kw):
        clock.now += 20
        return {"data": pages.pop(0)}

    client.get_darkpool_flow.side_effect = page
    with patch.object(fetch_flow, "_monotonic", clock):
        walk = fetch_flow.walk_darkpool("QQQ", date="2026-10-26", _client=client, deadline=clock.now + 30)
    # Page 1 at t+20, page 2 at t+40 crossed the deadline; no third page.
    assert client.get_darkpool_flow.call_count == 2
    assert len(walk.trades) == 2 * PAGE
    assert walk.cacheable is False


def test_walk_always_fetches_its_first_page():
    clock = Clock()
    client = MagicMock()
    client.get_darkpool_flow.return_value = {"data": _full_page(0, 19)[:10]}
    with patch.object(fetch_flow, "_monotonic", clock):
        walk = fetch_flow.walk_darkpool("QQQ", date="2026-10-26", _client=client, deadline=clock.now - 1)
    assert client.get_darkpool_flow.call_count == 1
    assert len(walk.trades) == 10


def test_alerts_walk_stops_at_the_deadline_after_its_first_page():
    clock = Clock()
    full = [{"premium": "60000", "is_call": True, "created_at": f"2026-10-27T15:{59 - (i // 60):02d}:{59 - (i % 60):02d}Z",
             "id": f"a{i}"} for i in range(fetch_flow.FLOW_ALERTS_PAGE_LIMIT)]
    client = MagicMock()

    def page(*_a, **_kw):
        clock.now += 20
        return {"data": list(full)}

    client.get_flow_alerts.side_effect = page
    with patch.object(fetch_flow, "_monotonic", clock):
        alerts = fetch_flow.fetch_flow_alerts("QQQ", _client=client, deadline=clock.now - 1)
    assert client.get_flow_alerts.call_count == 1
    assert len(alerts) == len(full)


DAYS = ["2026-10-27", "2026-10-26", "2026-10-23"]


def test_a_prior_day_cut_off_by_the_deadline_is_dropped_and_not_cached():
    clock = Clock()

    def walk(ticker, date=None, **kwargs):
        clock.now += 25
        # Today finishes; 10-26 is cut off mid-walk by the deadline.
        complete = date == "2026-10-27"
        return fetch_flow.DarkpoolWalk([_trade(1, f"{date}T15:00:00Z")], complete)

    client = MagicMock()
    with patch.object(fetch_flow, "get_last_n_trading_days", return_value=DAYS[1:]), \
         patch.object(fetch_flow, "_session_now_et", return_value=fetch_flow._ET.localize(fetch_flow.datetime(2026, 10, 27, 15, 0))), \
         patch.object(fetch_flow, "_is_trading_day", return_value=True), \
         patch.object(fetch_flow, "get_trading_day_progress", return_value=(0.8, True, "open")), \
         patch.object(fetch_flow, "get_cached_darkpool", return_value=None), \
         patch.object(fetch_flow, "set_cached_darkpool") as set_cached, \
         patch.object(fetch_flow, "walk_darkpool", side_effect=walk) as walker, \
         patch.object(fetch_flow, "fetch_flow_alerts", return_value=[]) as alerts, \
         patch.object(fetch_flow, "is_immutable", side_effect=lambda d: d < "2026-10-27"), \
         patch.object(fetch_flow, "_monotonic", clock):
        result = fetch_flow.fetch_flow("QQQ", lookback_days=2, _client=client, history_budget_s=30)

    dates = [row["date"] for row in result["dark_pool"]["daily"]]
    assert dates == ["2026-10-27"]
    assert result["history_backfill_skipped"] == ["2026-10-26", "2026-10-23"]
    # Today may be handed to the cache (which ignores mutable days); the
    # truncated closed day never is.
    assert "2026-10-26" not in [c.args[1] for c in set_cached.call_args_list]
    deadline = clock.now  # all walks got the same absolute deadline
    assert {c.kwargs["deadline"] for c in walker.call_args_list} == {1030.0}
    assert alerts.call_args.kwargs["deadline"] == 1030.0
    assert deadline >= 1030.0


def test_a_budgeted_client_waits_one_governor_minute_per_page():
    # Capping the wait at a short budget made reserve() refuse at once whenever
    # the minute window needed ~60s (prod host run, 2026-10-27): one governor
    # minute is the wait; the caller's finish margin covers that last page.
    assert fetch_flow._flow_client_kwargs(True, history_budget_s=30) == {
        "admission_timeout": fetch_flow.FLOW_BUDGETED_ADMISSION_WAIT_S
    }
    assert fetch_flow.FLOW_BUDGETED_ADMISSION_WAIT_S == 60.0
    assert fetch_flow._flow_client_kwargs(True) == {"admission_timeout": fetch_flow.FLOW_ADMISSION_WAIT_S}


def test_a_prior_day_refused_by_the_governor_is_skipped_in_a_budgeted_run():
    from clients.uw_client import UWRateLimitError

    clock = Clock()

    def walk(ticker, date=None, **kwargs):
        clock.now += 5
        if date == "2026-10-26":
            raise UWRateLimitError("UW admission is busy; retry after the current window", status_code=429)
        return fetch_flow.DarkpoolWalk([_trade(1, f"{date}T15:00:00Z")], True)

    client = MagicMock()
    with patch.object(fetch_flow, "get_last_n_trading_days", return_value=DAYS[1:]), \
         patch.object(fetch_flow, "_session_now_et", return_value=fetch_flow._ET.localize(fetch_flow.datetime(2026, 10, 27, 15, 0))), \
         patch.object(fetch_flow, "_is_trading_day", return_value=True), \
         patch.object(fetch_flow, "get_trading_day_progress", return_value=(0.8, True, "open")), \
         patch.object(fetch_flow, "get_cached_darkpool", return_value=None), \
         patch.object(fetch_flow, "set_cached_darkpool"), \
         patch.object(fetch_flow, "walk_darkpool", side_effect=walk), \
         patch.object(fetch_flow, "fetch_flow_alerts", return_value=[]), \
         patch.object(fetch_flow, "is_immutable", side_effect=lambda d: d < "2026-10-27"), \
         patch.object(fetch_flow, "_monotonic", clock):
        result = fetch_flow.fetch_flow("QQQ", lookback_days=2, _client=client, history_budget_s=300)

    assert [row["date"] for row in result["dark_pool"]["daily"]] == ["2026-10-27", "2026-10-23"]
    assert result["history_backfill_skipped"] == ["2026-10-26"]


def test_today_refused_by_the_governor_still_fails_the_report():
    import pytest
    from clients.uw_client import UWRateLimitError

    def walk(ticker, date=None, **kwargs):
        raise UWRateLimitError("UW admission is busy; retry after the current window", status_code=429)

    client = MagicMock()
    with patch.object(fetch_flow, "get_last_n_trading_days", return_value=DAYS[1:]), \
         patch.object(fetch_flow, "_session_now_et", return_value=fetch_flow._ET.localize(fetch_flow.datetime(2026, 10, 27, 15, 0))), \
         patch.object(fetch_flow, "_is_trading_day", return_value=True), \
         patch.object(fetch_flow, "get_trading_day_progress", return_value=(0.8, True, "open")), \
         patch.object(fetch_flow, "get_cached_darkpool", return_value=None), \
         patch.object(fetch_flow, "walk_darkpool", side_effect=walk), \
         patch.object(fetch_flow, "is_immutable", side_effect=lambda d: d < "2026-10-27"):
        with pytest.raises(UWRateLimitError):
            fetch_flow.fetch_flow("QQQ", lookback_days=2, _client=client, history_budget_s=300)


def test_flow_report_turns_an_absolute_deadline_into_the_budget():
    import flow_report

    with patch.object(flow_report, "build_report", return_value={"ok": True}) as build, \
         patch.object(flow_report.time, "time", return_value=1_000.0), \
         patch("sys.argv", ["flow_report.py", "QQQ", "--days", "20", "--deadline-epoch", "1140"]):
        flow_report.main()
    build.assert_called_once_with("QQQ", lookback_days=20, history_budget_s=140.0)

    with patch.object(flow_report, "build_report", return_value={"ok": True}) as build, \
         patch.object(flow_report.time, "time", return_value=2_000.0), \
         patch("sys.argv", ["flow_report.py", "QQQ", "--deadline-epoch", "1140"]):
        flow_report.main()
    # Already past: page nothing beyond the first page of today.
    assert build.call_args.kwargs["history_budget_s"] == 0.0
