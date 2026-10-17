"""PERF-014: GET /internals/skew-history reuses one UW session per ticker.

A cache miss used to construct a new UWClient (requests.Session, TLS) for
the expiry-breakdown call and again for every expiry skew fetch. Two
tickers x (1 breakdown + 3 expiries) was 8 sessions. One client per ticker
covers both legs.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

_EXPIRIES = ["2026-12-18", "2027-01-15", "2027-03-19"]
_SKEW_ROWS = [
    {"date": "2020-01-02", "risk_reversal": 0.12},
    {"date": "2024-01-02", "risk_reversal": 0.11},
]


class _CountingUWClient:
    inits = 0

    def __init__(self, *args, **kwargs):
        type(self).inits += 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        return None

    def get_expiry_breakdown(self, ticker):
        return {"data": [{"expiry": expiry} for expiry in _EXPIRIES]}

    def get_historical_risk_reversal_skew(self, ticker, **kwargs):
        return {"data": list(_SKEW_ROWS)}


@pytest.fixture
def client(monkeypatch):
    from scripts.api import auth, server

    _CountingUWClient.inits = 0
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "uw_available", True)
    monkeypatch.setattr(server, "_read_internals_skew_cache", lambda path: None)
    monkeypatch.setattr(server, "_write_cache", lambda path, payload: None)
    monkeypatch.setattr(server, "UWClient", _CountingUWClient)
    monkeypatch.setenv("UW_TOKEN", "synthetic-test-token")
    return TestClient(server.app)


def test_skew_history_uses_one_uw_client_per_ticker(client):
    response = client.get("/internals/skew-history")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["nq"]["ticker"] == "NDX"
    assert body["spx"]["ticker"] == "SPX"
    assert _CountingUWClient.inits == 2, (
        f"UWClient constructions {_CountingUWClient.inits} "
        "(want 2: one session per ticker, not one per expiry)"
    )
