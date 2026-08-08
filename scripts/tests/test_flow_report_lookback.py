"""flow_report defaults to a 20-session dark-pool history window."""

from unittest.mock import patch


def test_build_report_default_lookback_is_20():
    import flow_report

    assert flow_report.DEFAULT_LOOKBACK_DAYS == 20

    fake_flow = {
        "dark_pool": {"aggregate": {}, "daily": []},
        "options_flow": {"bias": "NO_DATA"},
        "combined_signal": "NO_SIGNAL",
        "market_status": "closed",
        "trading_day_progress": 1.0,
        "trading_days_checked": [],
    }
    with patch.object(flow_report, "fetch_flow", return_value=fake_flow) as mock_fetch, \
         patch.object(flow_report, "analyze_signal", return_value={"direction": "NEUTRAL", "strength": 0}):
        report = flow_report.build_report("GLD")

    mock_fetch.assert_called_once_with("GLD", lookback_days=20)
    assert report["lookback_days"] == 20
