"""flow_analysis must not live-backfill immutable darkpool history."""
import io
import re
from contextlib import redirect_stdout
from pathlib import Path
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


class _RecordingUWClient:
    """Stand-in that records constructor kwargs and returns an empty book."""

    constructed: list[dict] = []

    def __init__(self, **kwargs):
        type(self).constructed.append(kwargs)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get_darkpool_flow(self, *args, **kwargs):
        return {"data": []}

    def get_flow_alerts(self, *args, **kwargs):
        return {"data": []}


def test_portfolio_scan_does_not_use_the_report_admission_wait(monkeypatch):
    """The hourly book must not take the single-ticker 120s UW wait.

    radon-flow-refresh.service paged P1 after POST /flow-analysis held the
    connection for the whole wrapper budget and curl exited 28 with http
    000 and 0 bytes. fetch_flow defaults retry_transient=True, which builds
    UWClient(admission_timeout=FLOW_ADMISSION_WAIT_S). That wait is the
    flow report path (300s). Two of those waits are 240s, past the wrapper
    SCAN_TIMEOUT of 180s, so the client disconnects and the oneshot exits 1.
    Scanner and discover already pass retry_transient=False.
    """
    wrapper = Path(__file__).resolve().parents[1] / "run_flow_refresh.sh"
    match = re.search(
        r'SCAN_TIMEOUT="\$\{RADON_FLOW_REFRESH_SCAN_TIMEOUT:-(\d+)\}"',
        wrapper.read_text(),
    )
    assert match, "wrapper SCAN_TIMEOUT default missing"
    scan_budget = int(match.group(1))

    from fetch_flow import FLOW_ADMISSION_WAIT_S, _flow_client_kwargs

    assert FLOW_ADMISSION_WAIT_S * 2 > scan_budget, (
        f"two report admission waits ({FLOW_ADMISSION_WAIT_S}s) fit inside "
        f"the {scan_budget}s oneshot curl budget; the page mechanism changed"
    )
    assert _flow_client_kwargs(True) == {"admission_timeout": FLOW_ADMISSION_WAIT_S}
    assert "admission_timeout" not in _flow_client_kwargs(False)

    _RecordingUWClient.constructed = []
    monkeypatch.setattr("fetch_flow.UWClient", _RecordingUWClient)
    monkeypatch.setattr(
        flow_analysis,
        "load_portfolio",
        lambda: [
            {"ticker": "AAA", "direction": "LONG", "structure": "Shares"},
            {"ticker": "BBB", "direction": "SHORT", "structure": "Shares"},
        ],
    )
    monkeypatch.setattr(flow_analysis, "mirror_scan_snapshot", lambda *a, **k: None)

    with redirect_stdout(io.StringIO()):
        flow_analysis.run_analysis()

    assert len(_RecordingUWClient.constructed) == 2, _RecordingUWClient.constructed
    assert _RecordingUWClient.constructed == [
        {"max_retries": 0, "backoff_factor": 0},
        {"max_retries": 0, "backoff_factor": 0},
    ], _RecordingUWClient.constructed
