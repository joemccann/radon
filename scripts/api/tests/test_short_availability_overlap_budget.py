"""PERF-024: GET /short-availability/{ticker} overlaps IB and UW probes.

The order ticket reads this route for locate fee + shortable shares. IB tick
46/89 and UW get_short_data are independent: fee/rebate overlay does not
wait on the IB result. Sequential awaits kept max in-flight at 1; gather
raises it to 2 without extra UW or IB calls.
"""
from __future__ import annotations

import threading
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi.testclient import TestClient

from scripts.api import auth, server


def test_short_availability_probes_ib_and_uw_concurrently(monkeypatch):
    lock = threading.Lock()
    state = {"in_flight": 0, "max_in_flight": 0}

    def _mark():
        with lock:
            state["in_flight"] += 1
            state["max_in_flight"] = max(state["max_in_flight"], state["in_flight"])
        try:
            time.sleep(0.05)
        finally:
            with lock:
                state["in_flight"] -= 1

    @asynccontextmanager
    async def acquire(role):
        assert role == "data"
        yield "bounded-client"

    def probe_ib(client, ticker):
        _mark()
        return {"difficulty": 3.0, "shortable_shares": 500}

    def fetch_uw(ticker):
        _mark()
        return {"ticker": ticker, "date": "2026-10-03"}

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(
        server,
        "ib_pool",
        SimpleNamespace(is_connected=lambda role: role == "data", acquire=acquire),
    )
    monkeypatch.setattr(server, "uw_available", True)
    monkeypatch.setattr(server, "_probe_short_ticks_in_thread", probe_ib)
    monkeypatch.setattr(server, "_fetch_uw_short_data", fetch_uw)
    monkeypatch.setattr(server, "_uw_short_data_is_fresh", lambda raw, ticker: True)
    monkeypatch.setattr(
        server, "_extract_uw_fee_rebate", lambda raw: (1.25, -0.1, "2026-10-03T11:59:00Z")
    )

    response = TestClient(server.app).get("/short-availability/AAPL")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "ib"
    assert body["fee_rate"] == 1.25
    assert state["max_in_flight"] == 2, (
        f"max in-flight short-availability probes {state['max_in_flight']} (want 2)"
    )
