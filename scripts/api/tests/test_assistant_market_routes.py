"""Quote + priced UW chain routes for the in-app assistant."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture(autouse=True)
def localhost_bypass(monkeypatch):
    from scripts.api import auth, server

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    yield


@pytest.fixture
def client():
    from scripts.api.server import app

    return TestClient(app)


def test_quote_returns_uw_last(client):
    class _UW:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get_stock_state(self, ticker):
            assert ticker == "ADBE"
            return {"data": {"close": 481.25, "last": 482.1, "nbbo_bid": 482.0, "nbbo_ask": 482.2}}

    with patch("api.routes.assistant_market.UWClient", return_value=_UW()):
        resp = client.get("/quote/ADBE")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ticker"] == "ADBE"
    assert body["last"] == 482.1
    assert body["source"] == "uw"
    assert body["missing"] is False


def test_quote_missing_is_200(client):
    class _UW:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get_stock_state(self, ticker):
            raise RuntimeError("uw down")

    with patch("api.routes.assistant_market.UWClient", return_value=_UW()), patch(
        "api.routes.assistant_market.fetch_yahoo_last", return_value=None
    ):
        resp = client.get("/quote/ADBE")

    assert resp.status_code == 200
    body = resp.json()
    assert body["missing"] is True
    assert body["last"] is None


def test_uw_chain_compacts_around_spot(client):
    rows = []
    for strike in range(400, 601, 10):
        rows.append(
            {
                "option_symbol": f"ADBE260918C{strike:08d}000",
                "strike": float(strike),
                "expiry": "2026-09-18",
                "option_type": "call",
                "nbbo_bid": 5.0,
                "nbbo_ask": 5.4,
                "implied_volatility": 0.3,
                "open_interest": 10,
                "volume": 2,
            }
        )

    class _UW:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get_stock_state(self, ticker):
            return {"data": {"close": 480.0}}

        def get_option_contracts(self, ticker, **kwargs):
            assert ticker == "ADBE"
            assert kwargs.get("expiry") == "2026-09-18"
            return {"data": rows}

    with patch("api.routes.assistant_market.UWClient", return_value=_UW()):
        resp = client.get("/options/uw-chain?symbol=ADBE&expiry=2026-09-18&right=C&wings=3")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ticker"] == "ADBE"
    assert body["spot"] == 480.0
    strikes = [row["strike"] for row in body["contracts"]]
    assert max(strikes) - min(strikes) <= 60
    assert 480.0 in strikes
    assert all(row["right"] == "C" for row in body["contracts"])
    assert "mid" in body["contracts"][0]


def test_uw_chain_without_expiry_returns_breakdown(client):
    class _UW:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get_stock_state(self, ticker):
            return {"data": {"close": 480.0}}

        def get_expiry_breakdown(self, ticker):
            return {"data": [{"expiry": "2026-09-18", "dte": 36}, {"expiry": "2026-10-16", "dte": 64}]}

    with patch("api.routes.assistant_market.UWClient", return_value=_UW()):
        resp = client.get("/options/uw-chain?symbol=ADBE")

    assert resp.status_code == 200
    body = resp.json()
    assert body["expirations"][0]["expiry"] == "2026-09-18"
    assert "contracts" not in body or body.get("contracts") == []
