"""PERF-029: performance-page UW history fetches must share one UWClient
per thread.

Phase B of `_fetch_all_histories` submits every missed stock and every
option to a thread pool. `_fetch_option_history_safe` and
`_fetch_stock_history_fallback` each constructed a new UWClient (new
requests.Session, TCP, TLS) per task, then closed it. Sequential fetches
on one thread — the worker that drains multiple contracts — must reuse
one session the way ivrank (PERF-022) and internals skew (PERF-014) do.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


class _CountingUWClient:
    inits = 0
    closes = 0
    historic_gets = 0
    ohlc_gets = 0

    def __init__(self, *args, **kwargs):
        _CountingUWClient.inits += 1

    def get_option_contract_historic(self, option_id):
        _CountingUWClient.historic_gets += 1
        return {
            "chains": [
                {"date": "2026-01-15", "nbbo_bid": 1.2, "nbbo_ask": 1.3},
            ]
        }

    def get_stock_ohlc(self, symbol, candle_size="1d"):
        _CountingUWClient.ohlc_gets += 1
        return {
            "data": [
                {"date": "2026-01-15", "close": "100.0"},
            ]
        }

    def close(self):
        _CountingUWClient.closes += 1


def test_five_sequential_option_history_fetches_open_one_client(monkeypatch, tmp_path):
    import portfolio_performance as pp
    import utils.price_cache as cache

    _CountingUWClient.inits = 0
    _CountingUWClient.closes = 0
    _CountingUWClient.historic_gets = 0
    monkeypatch.setattr(pp, "UWClient", _CountingUWClient)
    monkeypatch.setattr(pp, "OPTIONS_DIR", tmp_path / "options")
    monkeypatch.setattr(cache, "OPTIONS_DIR", tmp_path / "options")
    (tmp_path / "options").mkdir()
    closer = getattr(pp, "close_thread_uw_client", None)

    try:
        for i in range(5):
            oid, history, warning = pp._fetch_option_history_safe(
                f"AAPL260320C00200000-{i}", "2026-01-01", "2026-01-31"
            )
            assert warning is None
            assert history.get("2026-01-15") is not None
        assert _CountingUWClient.historic_gets == 5
        assert _CountingUWClient.inits == 1, (
            f"UWClient constructions {_CountingUWClient.inits} "
            "(want 1 session for 5 sequential option history GETs, not one per contract)"
        )
    finally:
        if closer is not None:
            closer()


def test_five_sequential_stock_fallback_fetches_open_one_client(monkeypatch, tmp_path):
    import portfolio_performance as pp
    import utils.price_cache as cache

    _CountingUWClient.inits = 0
    _CountingUWClient.closes = 0
    _CountingUWClient.ohlc_gets = 0
    monkeypatch.setattr(pp, "UWClient", _CountingUWClient)
    monkeypatch.setattr(pp, "STOCKS_DIR", tmp_path / "stocks")
    monkeypatch.setattr(cache, "STOCKS_DIR", tmp_path / "stocks")
    (tmp_path / "stocks").mkdir()
    closer = getattr(pp, "close_thread_uw_client", None)

    try:
        for i in range(5):
            sym, history, source = pp._fetch_stock_history_fallback(
                f"T{i}", "2026-01-01", "2026-01-31"
            )
            assert source == "uw"
            assert history.get("2026-01-15") == 100.0
        assert _CountingUWClient.ohlc_gets == 5
        assert _CountingUWClient.inits == 1, (
            f"UWClient constructions {_CountingUWClient.inits} "
            "(want 1 session for 5 sequential stock fallback GETs, not one per symbol)"
        )
    finally:
        if closer is not None:
            closer()
