"""LEAP must wait through an account minute window without skipping its universe.

Synthetic HTTP and a virtual clock exercise the real shared admission ledger.
"""
from contextlib import nullcontext
from types import SimpleNamespace
import json
import time

import pytest
import requests

from clients import uw_client
from utils import uw_governor as gov
import leap_scanner_uw as leap

TOKEN = "synthetic-leap-token"


@pytest.fixture
def clock(monkeypatch):
    now = [time.time()]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(gov, "time", SimpleNamespace(
        time=lambda: now[0], monotonic=lambda: now[0], sleep=sleep,
    ))
    monkeypatch.setattr(uw_client, "_USE_CACHE", False)
    return now, sleeps


def wire(monkeypatch):
    calls = []

    def get(_session, url, **kwargs):
        calls.append(url)
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({"data": []}).encode()
        return response

    monkeypatch.setattr(requests.Session, "get", get)
    return calls


def test_explicit_scanner_wait_crosses_minute_window(monkeypatch, clock):
    monkeypatch.setenv("RADON_UW_REQUESTS_PER_MINUTE", "1")
    gov.reserve(TOKEN, timeout=0)
    gov.reserve(TOKEN, timeout=120)
    assert clock[1] == [pytest.approx(60)]
    assert gov.snapshot()["reserved_or_observed"] == 2


def test_explicit_wait_is_still_bounded(monkeypatch, clock):
    now, sleeps = clock
    with gov._db() as conn:
        conn.execute("UPDATE policy SET embargo=? WHERE id=1", (now[0] + 121,))
    with pytest.raises(gov.AdmissionError, match="busy"):
        gov.reserve(TOKEN, timeout=3600)
    assert sleeps == []


def test_typed_scanner_calls_survive_embargo_and_minute_window(monkeypatch, clock):
    monkeypatch.setenv("RADON_UW_REQUESTS_PER_MINUTE", "1")
    with gov._db() as conn:
        conn.execute("UPDATE policy SET embargo=? WHERE id=1", (clock[0][0] + 60,))
    calls = wire(monkeypatch)
    with uw_client.UWClient(token=TOKEN, admission_timeout=120) as client:
        assert client.get_iv_rank("AAPL") == {"data": []}
        assert client.get_option_contracts("AAPL") == {"data": []}
    assert len(calls) == 2
    assert sum(clock[1]) == pytest.approx(120)


def test_default_client_still_refuses_long_window_before_http(monkeypatch, clock):
    monkeypatch.setenv("RADON_UW_REQUESTS_PER_MINUTE", "1")
    gov.reserve(TOKEN, timeout=0)
    calls = wire(monkeypatch)
    with uw_client.UWClient(token=TOKEN) as client:
        with pytest.raises(uw_client.UWRateLimitError, match="busy"):
            client.get_iv_rank("AAPL")
    assert calls == []
    assert clock[1] == []


def test_explicit_request_deadline_overrides_scanner_default(monkeypatch, clock):
    monkeypatch.setenv("RADON_UW_REQUESTS_PER_MINUTE", "1")
    gov.reserve(TOKEN, timeout=0)
    calls = wire(monkeypatch)
    with uw_client.UWClient(token=TOKEN, admission_timeout=120) as client:
        with pytest.raises(uw_client.UWRateLimitError, match="busy"):
            client.get("stock/AAPL/iv-rank", wait_timeout=0, use_cache=False)
    assert calls == []
    assert clock[1] == []


@pytest.mark.parametrize("state", ["daily", "provider", "embargo"])
def test_long_wait_does_not_bypass_quota_or_embargo(monkeypatch, clock, state):
    if state == "daily":
        monkeypatch.setenv("RADON_UW_DAILY_LIMIT", "1")
        gov.reserve(TOKEN, timeout=0)
    elif state == "provider":
        gov.observe(TOKEN, 200, {"x-uw-token-req-limit": "10", "x-uw-daily-req-count": "10"})
    else:
        with gov._db() as conn:
            conn.execute("UPDATE policy SET embargo=? WHERE id=1", (clock[0][0] + 3600,))
    calls = wire(monkeypatch)
    with uw_client.UWClient(token=TOKEN, admission_timeout=120) as client:
        with pytest.raises(uw_client.UWRateLimitError):
            client.get_iv_rank("AAPL")
    assert calls == []
    assert clock[1] == []


def test_scan_universe_uses_bounded_waiting_client(monkeypatch):
    settings = []

    def client(**kwargs):
        settings.append(kwargs)
        return nullcontext(object())

    monkeypatch.setattr(leap, "UWClient", client)
    monkeypatch.setattr(leap, "scan_ib_session", lambda: nullcontext(None))
    monkeypatch.setattr(leap, "scan_ticker", lambda *a, **kw: object())
    assert len(leap.scan_universe(["AAPL", "MSFT"], 10, workers=2)) == 2
    assert settings == [{"admission_timeout": 120}]


def test_full_scan_keeps_tickers_across_admission_windows(monkeypatch, clock):
    monkeypatch.setenv("UW_TOKEN", TOKEN)
    monkeypatch.setenv("RADON_UW_REQUESTS_PER_MINUTE", "1")
    monkeypatch.setattr(leap, "scan_ib_session", lambda: nullcontext(None))
    monkeypatch.setattr(leap, "get_vol_data", lambda ticker, **kw: leap.VolData(
        ticker=ticker, price=100, hv_20=40, hv_60=35, hv_252=30, avg_hv=35,
    ))
    calls = []

    def get(_session, url, **kwargs):
        calls.append(url)
        ticker = url.split("/")[-2]
        rows = [{"option_symbol": f"{ticker}290119C00100000",
                 "implied_volatility": 0.25, "volume": 10, "open_interest": 100}]
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({"data": rows if url.endswith("option-contracts") else []}).encode()
        return response

    monkeypatch.setattr(requests.Session, "get", get)
    with gov._db() as conn:
        conn.execute("UPDATE policy SET embargo=? WHERE id=1", (clock[0][0] + 60,))
    failures = []
    results = leap.scan_universe(["AAPL", "MSFT"], 10, workers=1, provider_failures=failures)
    assert sorted(row.ticker for row in results) == ["AAPL", "MSFT"]
    assert failures == []
    assert len(calls) == 4
    assert sum(clock[1]) == pytest.approx(240)


@pytest.mark.parametrize("admission_timeout,release_after,succeeds", [
    (120, 60, True), (5, 60, False), (3600, 121, False),
])
def test_cached_scanner_waits_for_inflight_request(
    monkeypatch, clock, admission_timeout, release_after, succeeds,
):
    """The real cache path must not truncate the scanner wait to ten seconds."""
    from utils import uw_cache
    monkeypatch.setattr(uw_client, "_USE_CACHE", True)
    monkeypatch.setattr(uw_cache, "time", gov.time)
    release_at = clock[0][0] + release_after
    actual_fcntl = uw_cache.fcntl

    def flock(fd, flags):
        if flags != actual_fcntl.LOCK_UN and clock[0][0] < release_at:
            raise BlockingIOError()
        return actual_fcntl.flock(fd, flags)

    monkeypatch.setattr(uw_cache, "fcntl", SimpleNamespace(
        flock=flock, LOCK_EX=actual_fcntl.LOCK_EX,
        LOCK_NB=actual_fcntl.LOCK_NB, LOCK_UN=actual_fcntl.LOCK_UN,
    ))
    calls = wire(monkeypatch)
    with uw_client.UWClient(token=TOKEN, admission_timeout=admission_timeout) as client:
        if succeeds:
            assert client.get_iv_rank("INFLIGHT") == {"data": []}
        else:
            with pytest.raises(uw_client.UWRateLimitError, match="already in progress"):
                client.get_iv_rank("INFLIGHT")
    assert len(calls) == int(succeeds)
    expected_wait = release_after if succeeds else min(admission_timeout, 120)
    assert sum(clock[1]) == pytest.approx(expected_wait, abs=0.03)
