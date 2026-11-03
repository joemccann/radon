"""gex-scan must wait through a short shared UW admission window.

radon-refresh fires gex_scan.py on the same :00/:15/:30/:45 marks as
radon-vol-cone-intraday, which fills the 96/min standard window in seconds,
and a provider 429 sets a 60s embargo. The UWClient 5s default turned either
into "UW admission is busy" and an error row (2026-10-30 20:00Z).

Synthetic HTTP and a virtual clock exercise the real shared admission ledger.
"""
from types import SimpleNamespace
import json
import time

import pytest
import requests

from clients import uw_client
from utils import uw_governor as gov
import data_refresh
import gex_scan

TOKEN = "synthetic-gex-token"


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
    monkeypatch.setenv("UW_TOKEN", TOKEN)
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


def test_gex_client_waits_out_provider_retry_after_embargo(monkeypatch, clock):
    now, sleeps = clock
    with gov._db() as conn:
        conn.execute("UPDATE policy SET embargo=? WHERE id=1", (now[0] + 60,))
    calls = wire(monkeypatch)
    client = gex_scan._get_uw_client()
    try:
        assert gex_scan.fetch_strike_gex(client, "SPX") == []
    finally:
        client.close()
    assert len(calls) == 1
    assert sum(sleeps) == pytest.approx(60)


def test_gex_client_waits_out_a_saturated_minute(monkeypatch, clock):
    now, sleeps = clock
    monkeypatch.setenv("RADON_UW_REQUESTS_PER_MINUTE", "1")
    gov.reserve(TOKEN, timeout=0)  # another writer took the whole minute
    calls = wire(monkeypatch)
    client = gex_scan._get_uw_client()
    try:
        assert gex_scan.fetch_strike_gex(client, "SPX") == []
    finally:
        client.close()
    assert len(calls) == 1
    assert sum(sleeps) == pytest.approx(60)


def test_gex_admission_wait_fits_the_refresh_child_budget():
    assert gex_scan.GEX_ADMISSION_WAIT_S >= 60  # default Retry-After / one minute window
    assert gex_scan.GEX_ADMISSION_WAIT_S <= gov.MAX_WAIT_SECONDS
    # data_refresh kills gex_scan.py at DEFAULT_SCAN_TIMEOUT_SECS; leave room
    # for the remaining UW calls and the persist after one full wait.
    assert gex_scan.GEX_ADMISSION_WAIT_S <= data_refresh.DEFAULT_SCAN_TIMEOUT_SECS - 30
