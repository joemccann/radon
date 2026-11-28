"""PERF-068: strength confirmation must not open one UW session per ticker.

``scan_universe`` submits each ticker with ``client=None``, and
``scan_ticker`` constructed a fresh ``UWClient`` (new TCP/TLS session)
and closed it. The hourly ndx100 refresh (24 workers) paid a handshake
per name. Workers reuse threads, so one session per worker is enough.
"""
from __future__ import annotations

import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import strength_confirmation_scanner as strength  # noqa: E402


_TICKERS = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "AVGO", "TSLA"]
_WORKERS = 2


class _CountingUWClient:
    inits = 0
    closed = 0

    def __init__(self, *args, **kwargs):
        _CountingUWClient.inits += 1

    def close(self):
        _CountingUWClient.closed += 1


@pytest.fixture
def _isolated_ib(monkeypatch):
    @contextmanager
    def _none():
        yield None

    monkeypatch.setattr(strength, "scan_ib_session", _none)


def test_eight_tickers_on_two_workers_open_one_session_each(_isolated_ib, monkeypatch):
    started = threading.Barrier(_WORKERS)
    _CountingUWClient.inits = 0
    _CountingUWClient.closed = 0
    monkeypatch.setattr(strength, "UWClient", _CountingUWClient)
    monkeypatch.setattr(strength, "build_market_context", lambda _client: None)

    def fetch_surface(_client, _ticker, ib=None):
        try:
            started.wait(timeout=5)
        except threading.BrokenBarrierError:
            pass
        return {"ohlc": []}

    monkeypatch.setattr(strength, "fetch_surface", fetch_surface)

    payload = strength.scan_universe(_TICKERS, max_workers=_WORKERS)

    assert payload["coverage"]["tickers"] == len(_TICKERS)
    assert payload["coverage"]["no_setup"] == len(_TICKERS)
    # One context session on the caller thread, plus one per worker.
    assert _CountingUWClient.inits <= 1 + _WORKERS, (
        f"UWClient constructions {_CountingUWClient.inits} "
        f"for {len(_TICKERS)} tickers on {_WORKERS} workers "
        f"(want <= {1 + _WORKERS}, one session per worker plus context)"
    )
    assert _CountingUWClient.closed == _CountingUWClient.inits
