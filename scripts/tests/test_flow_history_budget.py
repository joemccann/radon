"""A flow report always lands inside its time budget.

2026-10-27: every QQQ flow report since the 20-session window shipped
(2026-08-07) was killed at FastAPI's 225s deadline. QQQ prior days walk the
40-page cap (~18k prints, ~13s each), so 20 uncached days cannot finish, the
report is never written, and /flow-analysis/QQQ kept serving a 2026-06-23 cache.

``fetch_flow(history_budget_s=...)`` bounds the UNCACHED history walk: today and
cache hits are always served; uncached prior days are walked newest-first until
the budget is spent, and the rest are skipped. Each walked day lands in the
per-day cache, so the next scan starts further back and the window fills.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import fetch_flow

DAYS = ["2026-10-27", "2026-10-26", "2026-10-23", "2026-10-22", "2026-10-21"]
TRADE = {"size": "100", "price": "10", "premium": "1000", "nbbo_bid": "9", "nbbo_ask": "10"}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _run(cached: set[str], *, budget, per_fetch_s: float):
    clock = Clock()
    fetched: list[str] = []

    def fake_fetch(ticker, date, **_kw):
        fetched.append(date)
        clock.now += per_fetch_s
        return [dict(TRADE)]

    def fake_cached(ticker, date):
        return [dict(TRADE)] if date in cached else None

    client = MagicMock()
    with patch.object(fetch_flow, "get_last_n_trading_days", return_value=DAYS[1:]), \
         patch.object(fetch_flow, "_session_now_et", return_value=fetch_flow._ET.localize(fetch_flow.datetime(2026, 10, 27, 15, 0))), \
         patch.object(fetch_flow, "_is_trading_day", return_value=True), \
         patch.object(fetch_flow, "get_trading_day_progress", return_value=(0.8, True, "open")), \
         patch.object(fetch_flow, "get_cached_darkpool", side_effect=fake_cached), \
         patch.object(fetch_flow, "set_cached_darkpool") as set_cached, \
         patch.object(fetch_flow, "fetch_darkpool", side_effect=fake_fetch), \
         patch.object(fetch_flow, "fetch_flow_alerts", return_value=[]), \
         patch.object(fetch_flow, "is_immutable", side_effect=lambda d: d < "2026-10-27"), \
         patch.object(fetch_flow, "_monotonic", clock):
        result = fetch_flow.fetch_flow("QQQ", lookback_days=4, _client=client, history_budget_s=budget)
    return result, fetched, set_cached


def test_without_a_budget_every_day_is_fetched():
    result, fetched, _ = _run(set(), budget=None, per_fetch_s=13)
    assert fetched == DAYS
    assert result["history_backfill_skipped"] == []


def test_budget_skips_the_oldest_uncached_days_and_keeps_today():
    # Today + two prior days fit in 30s at 13s per walk; the rest are skipped.
    result, fetched, set_cached = _run(set(), budget=30, per_fetch_s=13)
    assert fetched == ["2026-10-27", "2026-10-26", "2026-10-23"]
    assert result["history_backfill_skipped"] == ["2026-10-22", "2026-10-21"]
    assert [row["date"] for row in result["dark_pool"]["daily"]] == ["2026-10-27", "2026-10-26", "2026-10-23"]
    # Every walked day is persisted so the next scan resumes further back.
    assert [c.args[1] for c in set_cached.call_args_list] == fetched


def test_cached_days_are_served_after_the_budget_is_spent():
    result, fetched, _ = _run({"2026-10-22", "2026-10-21"}, budget=10, per_fetch_s=13)
    # Today always fetched and spends the budget; the uncached 10-26 and
    # 10-23 are skipped, but the cached older days still count.
    assert fetched == ["2026-10-27"]
    assert result["history_backfill_skipped"] == ["2026-10-26", "2026-10-23"]
    dates = [row["date"] for row in result["dark_pool"]["daily"]]
    assert dates == ["2026-10-27", "2026-10-22", "2026-10-21"]


def test_today_is_fetched_even_with_a_zero_budget():
    result, fetched, _ = _run(set(), budget=0, per_fetch_s=13)
    assert fetched == ["2026-10-27"]
    assert result["history_backfill_skipped"] == DAYS[1:]


def test_flow_report_passes_the_budget_and_reports_coverage():
    import flow_report

    fake_flow = {
        "dark_pool": {"aggregate": {}, "daily": [{"date": "2026-10-27"}, {"date": "2026-10-26"}]},
        "options_flow": {"bias": "NO_DATA"},
        "combined_signal": "NO_SIGNAL",
        "market_status": "open",
        "trading_day_progress": 0.5,
        "trading_days_checked": DAYS,
        "history_backfill_skipped": ["2026-10-23", "2026-10-22", "2026-10-21"],
    }
    with patch.object(flow_report, "fetch_flow", return_value=fake_flow) as mock_fetch, \
         patch.object(flow_report, "analyze_signal", return_value={"direction": "NEUTRAL", "strength": 0}), \
         patch.object(flow_report, "fetch_ticker_skew", return_value={}):
        report = flow_report.build_report("QQQ", lookback_days=20, history_budget_s=60)

    mock_fetch.assert_called_once_with("QQQ", lookback_days=20, history_budget_s=60)
    assert report["history_backfill_skipped"] == ["2026-10-23", "2026-10-22", "2026-10-21"]
    assert report["sessions_covered"] == 2


def test_flow_report_cli_accepts_a_history_budget():
    import flow_report

    with patch.object(flow_report, "build_report", return_value={"ok": True}) as build, \
         patch("sys.argv", ["flow_report.py", "QQQ", "--days", "20", "--history-budget-secs", "60"]):
        flow_report.main()
    build.assert_called_once_with("QQQ", lookback_days=20, history_budget_s=60.0)
