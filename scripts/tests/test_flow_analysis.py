"""flow_analysis must not live-backfill immutable darkpool history."""
from __future__ import annotations

import io
import json
import re
import time
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock

import flow_analysis

_NEUTRAL = {
    "score": 0,
    "signal": "NONE",
    "direction": "UNKNOWN",
    "strength": 0,
    "buy_ratio": None,
    "sustained_days": 0,
    "recent_direction": "UNKNOWN",
    "recent_strength": 0,
}

_REPO = Path(__file__).resolve().parents[2]


def _positions(*tickers: str) -> list[dict]:
    return [
        {"ticker": ticker, "direction": "LONG", "structure": "Shares"}
        for ticker in tickers
    ]


def _patch_analysis(monkeypatch, fetch=None):
    mock_fetch = fetch or MagicMock(
        return_value={"dark_pool": {"aggregate": {}, "daily": []}}
    )
    monkeypatch.setattr(flow_analysis, "fetch_flow_module", mock_fetch)
    monkeypatch.setattr(flow_analysis, "analyze_signal", lambda _flow: dict(_NEUTRAL))
    snapshots: list = []
    monkeypatch.setattr(
        flow_analysis,
        "mirror_scan_snapshot",
        lambda *args, **kwargs: snapshots.append((args, kwargs)),
    )
    return mock_fetch, snapshots


def _run_stdout(monkeypatch, tickers, fetch=None) -> tuple[dict, object, list]:
    monkeypatch.setattr(flow_analysis, "load_portfolio", lambda: _positions(*tickers))
    mock_fetch, snapshots = _patch_analysis(monkeypatch, fetch=fetch)
    buf = io.StringIO()
    with redirect_stdout(buf):
        flow_analysis.run_analysis()
    return json.loads(buf.getvalue()), mock_fetch, snapshots


def test_run_analysis_skips_closed_day_history_backfill(monkeypatch):
    payload, mock_fetch, _snapshots = _run_stdout(monkeypatch, ["GLD"])
    mock_fetch.assert_called_once()
    _args, kwargs = mock_fetch.call_args
    assert kwargs.get("fetch_missing_history") is False
    assert payload["positions_scanned"] == 1


class TestSweepBudget:
    """2026-09-11 20:00Z page 24ac3520: 16:00 ET close walk of 10 positions
    ran 121s, FastAPI SIGKILL'd flow_analysis.py at timeout=120, wrapper
    logged indeterminate HTTP 502 (not capacity-exhausted), oneshot exit 1,
    next timer Monday 13:00 UTC."""

    def test_tarpitted_fetch_stops_inside_the_wall_clock_budget(self, monkeypatch):
        monkeypatch.setattr(flow_analysis, "SWEEP_BUDGET_S", 0.15, raising=False)

        calls = []

        def hang(_ticker, fetch_missing_history=False):
            calls.append(_ticker)
            time.sleep(0.4)
            return {"dark_pool": {"aggregate": {}, "daily": []}}

        started = time.monotonic()
        payload, _mock_fetch, snapshots = _run_stdout(
            monkeypatch,
            [f"T{i:02d}" for i in range(8)],
            fetch=hang,
        )
        elapsed = time.monotonic() - started

        # 8 sequential 0.4s fetches would take ~3.2s and still be running
        # when FastAPI SIGKILLs at 120s. The budget must cut this off and
        # still reach a snapshot write so the oneshot exits 0.
        assert elapsed < 1.0
        assert len(calls) < 8
        assert payload["positions_scanned"] == len(calls)
        assert payload["positions_deferred"] == 8 - len(calls)
        assert snapshots, "truncated walk must still mirror the snapshot"

    def test_tickers_finished_before_the_deadline_are_kept(self, monkeypatch):
        monkeypatch.setattr(flow_analysis, "SWEEP_BUDGET_S", 30.0, raising=False)
        payload, mock_fetch, snapshots = _run_stdout(
            monkeypatch, ["EWY", "SPY", "MSFT"]
        )
        assert mock_fetch.call_count == 3
        assert payload["positions_scanned"] == 3
        assert payload.get("positions_deferred", 0) == 0
        assert snapshots

    def test_sweep_budget_fits_inside_fastapi_timeout(self):
        server = (_REPO / "scripts" / "api" / "server.py").read_text(encoding="utf-8")
        start = server.index('@app.post("/flow-analysis")\n')
        end = server.index("\n@", start + 1)
        block = server[start:end]
        timeout = int(re.search(r"timeout\s*=\s*(\d+)", block).group(1))
        wrapper = (_REPO / "scripts" / "run_flow_refresh.sh").read_text(
            encoding="utf-8"
        )
        scan_timeout = int(
            re.search(r"RADON_FLOW_REFRESH_SCAN_TIMEOUT:-(\d+)", wrapper).group(1)
        )
        unit = next(
            line
            for line in (_REPO / "cloud" / "services" / "radon-flow-refresh.service")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.startswith("TimeoutStartSec=")
        )
        unit_timeout = int(unit.split("=", 1)[1])

        # 2026-09-11: wrapper waited 180s, FastAPI killed at 120s, 121s 502.
        assert timeout == scan_timeout
        assert timeout >= 180
        assert flow_analysis.SWEEP_BUDGET_S + 30 <= timeout
        assert 3 * scan_timeout <= unit_timeout
