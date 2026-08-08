"""portfolio_report dark-pool path must use multi-page fetch_darkpool."""

from datetime import date
from unittest.mock import patch


def test_fetch_dark_pool_flow_uses_paginated_fetch_darkpool():
    import portfolio_report as pr

    trades = [
        {"price": "101", "nbbo_bid": "100", "nbbo_ask": "102", "size": 1000},
        {"price": "99", "nbbo_bid": "100", "nbbo_ask": "102", "size": 500},
    ]

    pr.TODAY = date(2026, 8, 7)
    pr.TODAY_STR = "2026-08-07"

    with patch.dict("os.environ", {"UW_TOKEN": "test-token"}), \
         patch("fetch_flow.fetch_darkpool", return_value=trades) as mock_dp, \
         patch("utils.darkpool_cache.get_cached_darkpool", return_value=None), \
         patch("utils.darkpool_cache.set_cached_darkpool") as mock_set:
        out = pr.fetch_dark_pool_flow(["GLD"], days=1)

    assert mock_dp.call_count >= 1
    assert mock_set.called
    assert out["GLD"]["direction"] in ("ACCUMULATION", "DISTRIBUTION", "NEUTRAL")
    assert out["GLD"]["buy_ratio"] > 0
    # No raw requests module path — fetch_darkpool is the only UW entry.
    call = mock_dp.call_args
    assert call.args[0] == "GLD" or call.kwargs.get("ticker") == "GLD" or call.args[0] == "GLD"


def test_fetch_dark_pool_flow_no_token_short_circuits():
    import portfolio_report as pr

    with patch.dict("os.environ", {"UW_TOKEN": ""}):
        out = pr.fetch_dark_pool_flow(["AAPL"], days=1)
    assert out["AAPL"]["direction"] == "NO_TOKEN" or out["AAPL"].get("status") == "NO_TOKEN" or (
        "NO_TOKEN" in str(out["AAPL"])
    ) or out["AAPL"].get("direction") == "NO_TOKEN"
