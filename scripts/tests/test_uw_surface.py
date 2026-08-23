"""Shared UW surface fetch used by theta and strength scanners."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from clients.uw_client import UWRateLimitError
from utils.uw_surface import MIN_DAILY_CLOSES, fetch_daily_closes, fetch_surface


class RecordingClient:
    def __init__(self, **payloads):
        self.calls: list[tuple] = []
        self.payloads = payloads

    def get_stock_ohlc(self, ticker: str, candle_size: str = "1d"):
        self.calls.append(("ohlc", ticker, candle_size))
        return self.payloads["ohlc"]

    def get_iv_rank(self, ticker: str):
        self.calls.append(("iv_rank", ticker))
        return self.payloads["iv_rank"]

    def get_option_contracts(self, ticker: str, **kwargs):
        self.calls.append(("contracts", ticker, kwargs))
        return self.payloads["contracts"]

    def get_greek_exposure_by_strike(self, ticker: str):
        self.calls.append(("gex_strike", ticker))
        return self.payloads["gex_strike"]


def _payloads() -> dict:
    return {
        "ohlc": {"data": [{"close": 100.0}]},
        "iv_rank": {"data": [{"date": "2026-06-24", "iv_rank_1y": 40}]},
        "contracts": {"data": [{"option_symbol": "AAPL260717C00100000"}]},
        "gex_strike": {"data": [{"strike": 100, "call_gex": 1}]},
    }


def test_fetch_surface_issues_four_gets_and_returns_raw_payloads() -> None:
    payloads = _payloads()
    client = RecordingClient(**payloads)

    surface = fetch_surface(client, "aapl")

    assert surface == {
        "ohlc": payloads["ohlc"],
        "iv_rank": payloads["iv_rank"],
        "contracts": payloads["contracts"],
        "gex_strike": payloads["gex_strike"],
    }
    assert [name for name, *_rest in client.calls] == [
        "ohlc",
        "iv_rank",
        "contracts",
        "gex_strike",
    ]
    assert client.calls[0] == ("ohlc", "AAPL", "1d")
    assert client.calls[1] == ("iv_rank", "AAPL")
    assert client.calls[2][0] == "contracts"
    assert client.calls[2][1] == "AAPL"
    assert client.calls[2][2] == {
        "exclude_zero_vol_chains": True,
        "maybe_otm_only": True,
    }
    assert client.calls[3] == ("gex_strike", "AAPL")


def test_fetch_surface_propagates_rate_limit() -> None:
    class Boom:
        def get_stock_ohlc(self, ticker: str, candle_size: str = "1d"):
            raise UWRateLimitError("daily request limit")

    with pytest.raises(UWRateLimitError, match="daily request limit"):
        fetch_surface(Boom(), "AAPL")


class RecordingIB:
    def __init__(self, bars=None, error: Exception | None = None):
        self.calls: list[tuple] = []
        self.bars = bars
        self.error = error

    def get_historical_data(self, contract, **kwargs):
        self.calls.append((contract, kwargs))
        if self.error is not None:
            raise self.error
        return self.bars


def _ib_bars(count: int, start: float = 100.0) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(date=f"2026-01-{i + 1:02d}", close=start + i)
        for i in range(count)
    ]


def test_fetch_daily_closes_ib_hit_skips_uw() -> None:
    payloads = _payloads()
    uw = RecordingClient(**payloads)
    ib = RecordingIB(bars=_ib_bars(MIN_DAILY_CLOSES))

    ohlc = fetch_daily_closes("aapl", ib=ib, uw=uw)

    assert [row["close"] for row in ohlc["data"]] == list(range(100, 100 + MIN_DAILY_CLOSES))
    assert uw.calls == []
    assert len(ib.calls) == 1
    assert ib.calls[0][1]["duration"] == "1 Y"
    assert ib.calls[0][1]["bar_size"] == "1 day"


def test_fetch_daily_closes_ib_miss_calls_uw() -> None:
    payloads = _payloads()
    uw = RecordingClient(**payloads)
    ib = RecordingIB(bars=_ib_bars(3))

    ohlc = fetch_daily_closes("MSFT", ib=ib, uw=uw)

    assert ohlc == payloads["ohlc"]
    assert uw.calls == [("ohlc", "MSFT", "1d")]
    assert len(ib.calls) == 1


def test_fetch_daily_closes_ib_error_falls_through_to_uw() -> None:
    payloads = _payloads()
    uw = RecordingClient(**payloads)
    ib = RecordingIB(error=RuntimeError("gateway down"))

    ohlc = fetch_daily_closes("NVDA", ib=ib, uw=uw)

    assert ohlc == payloads["ohlc"]
    assert uw.calls == [("ohlc", "NVDA", "1d")]


def test_fetch_surface_ib_hit_skips_uw_ohlc() -> None:
    payloads = _payloads()
    uw = RecordingClient(**payloads)
    ib = RecordingIB(bars=_ib_bars(MIN_DAILY_CLOSES))

    surface = fetch_surface(uw, "aapl", ib=ib)

    assert [name for name, *_rest in uw.calls] == ["iv_rank", "contracts", "gex_strike"]
    assert len(surface["ohlc"]["data"]) == MIN_DAILY_CLOSES
    assert surface["iv_rank"] == payloads["iv_rank"]


def test_scan_ib_session_connect_failure_yields_none(monkeypatch) -> None:
    from utils.uw_surface import scan_ib_session

    class BoomClient:
        def connect(self, **kwargs):
            raise RuntimeError("gateway down")

        def disconnect(self):
            raise AssertionError("disconnect unconnected client")

    monkeypatch.setattr("clients.ib_client.IBClient", BoomClient)
    with scan_ib_session() as ib:
        assert ib is None


def test_scan_ib_session_uses_auto_id_and_disconnects(monkeypatch) -> None:
    from utils.uw_surface import scan_ib_session

    calls: list = []

    class FakeClient:
        def connect(self, **kwargs):
            calls.append(("connect", kwargs))

        def disconnect(self):
            calls.append("disconnect")

        def get_historical_data(self, contract, **kwargs):
            calls.append("hist")
            return []

    monkeypatch.setattr("clients.ib_client.IBClient", FakeClient)
    with scan_ib_session() as ib:
        assert ib is not None
        ib.get_historical_data("AAPL")
    assert calls[0][0] == "connect"
    assert calls[0][1]["client_id"] == "auto"
    assert calls[0][1]["timeout"] == 8
    assert calls[0][1]["max_retries"] == 1
    assert "hist" in calls
    assert calls[-1] == "disconnect"


def test_fetch_surface_ib_miss_calls_uw_ohlc() -> None:
    payloads = _payloads()
    uw = RecordingClient(**payloads)
    ib = RecordingIB(bars=[])

    surface = fetch_surface(uw, "aapl", ib=ib)

    assert surface["ohlc"] == payloads["ohlc"]
    assert [name for name, *_rest in uw.calls] == [
        "ohlc",
        "iv_rank",
        "contracts",
        "gex_strike",
    ]
