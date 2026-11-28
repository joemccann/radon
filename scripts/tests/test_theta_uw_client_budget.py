"""PERF-069: theta harvester must not open one UW session per ticker.

``scan_universe`` submits each ticker without a client, and
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

import theta_harvester_scanner as theta  # noqa: E402


_TICKERS = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "AVGO", "TSLA"]
_WORKERS = 2


class _CountingUWClient:
    inits = 0
    closed = 0

    def __init__(self, *args, **kwargs):
        _CountingUWClient.inits += 1

    def close(self):
        _CountingUWClient.closed += 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


@pytest.fixture
def _isolated_ib(monkeypatch):
    @contextmanager
    def _none():
        yield None

    monkeypatch.setattr(theta, "scan_ib_session", _none)


def test_eight_tickers_on_two_workers_open_one_session_each(_isolated_ib, monkeypatch):
    started = threading.Barrier(_WORKERS)
    _CountingUWClient.inits = 0
    _CountingUWClient.closed = 0
    monkeypatch.setattr(theta, "UWClient", _CountingUWClient)

    def fetch_surface(_client, _ticker, ib=None):
        try:
            started.wait(timeout=5)
        except threading.BrokenBarrierError:
            pass
        return {"ohlc": []}

    monkeypatch.setattr(theta, "fetch_surface", fetch_surface)

    payload = theta.scan_universe(_TICKERS, max_workers=_WORKERS)

    assert payload["coverage"]["tickers"] == len(_TICKERS)
    assert payload["coverage"]["no_setup"] == len(_TICKERS)
    assert _CountingUWClient.inits <= _WORKERS, (
        f"UWClient constructions {_CountingUWClient.inits} "
        f"for {len(_TICKERS)} tickers on {_WORKERS} workers "
        f"(want <= {_WORKERS}, one session per worker)"
    )
    assert _CountingUWClient.closed == _CountingUWClient.inits
