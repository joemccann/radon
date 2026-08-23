"""flow_analysis must not live-backfill immutable darkpool history."""
import io
from contextlib import redirect_stdout
from unittest.mock import MagicMock

import flow_analysis


def test_run_analysis_skips_closed_day_history_backfill(monkeypatch):
    monkeypatch.setattr(
        flow_analysis,
        "load_portfolio",
        lambda: [{"ticker": "GLD", "direction": "LONG", "structure": "Shares"}],
    )
    mock_fetch = MagicMock(return_value={"dark_pool": {"aggregate": {}, "daily": []}})
    monkeypatch.setattr(flow_analysis, "fetch_flow_module", mock_fetch)
    monkeypatch.setattr(flow_analysis, "analyze_signal", lambda _flow: {
        "score": 0,
        "signal": "NONE",
        "direction": "UNKNOWN",
        "strength": 0,
        "buy_ratio": None,
        "sustained_days": 0,
        "recent_direction": "UNKNOWN",
        "recent_strength": 0,
    })
    monkeypatch.setattr(flow_analysis, "mirror_scan_snapshot", lambda *a, **k: None)

    with redirect_stdout(io.StringIO()):
        flow_analysis.run_analysis()

    mock_fetch.assert_called_once()
    _args, kwargs = mock_fetch.call_args
    assert kwargs.get("fetch_missing_history") is False
