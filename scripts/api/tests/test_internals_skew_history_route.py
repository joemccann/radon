"""GET /internals/skew-history: the ticker query params reach the Unusual
Whales URL path. Validate them at the boundary so a path-shaped value is a
400 before any outbound call."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture(autouse=True)
def isolated_route(monkeypatch):
    from scripts.api import auth, server

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "uw_available", True)
    monkeypatch.setattr(server, "_read_internals_skew_cache", lambda path: None)
    monkeypatch.setattr(server, "_write_cache", lambda path, payload: None)
    monkeypatch.setenv("UW_TOKEN", "synthetic-test-token")
    yield


@pytest.fixture
def uw_wire(monkeypatch):
    """Record every UW endpoint path the route would hit; no expiry lookups."""
    from scripts.api import server

    paths: list[str] = []

    async def _candidates(ticker, expiry=None):
        return ([], ["2026-12-18"], "uw")

    def _get(self, endpoint, params=None):
        paths.append(endpoint)
        return {"data": [{"date": "2026-01-02", "risk_reversal": 0.12}]}

    monkeypatch.setattr(server, "_resolve_expiry_candidates", _candidates)
    monkeypatch.setattr(server.UWClient, "_get", _get)
    return paths


@pytest.fixture
def client():
    from scripts.api.server import app

    return TestClient(app)


@pytest.mark.parametrize(
    "bad",
    ["NDX/../option-contracts", "SPX?x=1", "a/b", "NDX%2Fx", "TOOLONGTICKER", "nd x", ""],
)
def test_path_shaped_ticker_is_400_before_any_outbound_call(client, uw_wire, bad):
    for param in ("nq_ticker", "spx_ticker"):
        response = client.get("/internals/skew-history", params={param: bad})
        assert response.status_code == 400, (param, bad, response.text)
    assert uw_wire == []


def test_normal_tickers_hit_the_exact_uw_paths(client, uw_wire):
    response = client.get(
        "/internals/skew-history", params={"nq_ticker": "ndx", "spx_ticker": "spx"}
    )
    assert response.status_code == 200, response.text
    assert uw_wire == [
        "stock/NDX/historical-risk-reversal-skew",
        "stock/SPX/historical-risk-reversal-skew",
    ]
    assert response.json()["nq"]["ticker"] == "NDX"
