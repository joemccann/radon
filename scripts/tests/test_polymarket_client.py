"""Tests for the F5 Polymarket REST client (scripts/clients/polymarket_client.py).

The client hits the public Polymarket Gamma + CLOB endpoints (no auth):
  - GET {gamma}/markets               -> markets list
  - GET {clob}/midpoint?token_id=...  -> {"mid": "0.42"}
  - GET {clob}/prices-history?...     -> {"history": [{t, p}, ...]}

Every test injects a fake ``requests.Session`` so nothing touches the network.
The session double records the URLs it was called with and returns canned
``Response``-like objects, mirroring the UWClient test style.
"""
from __future__ import annotations

import json
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from clients.polymarket_client import (
    PolymarketClient,
    PolymarketAPIError,
    PolymarketNotFoundError,
    midpoint_to_probability,
)


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: Any = None, headers: Optional[dict] = None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.reason = "OK" if status_code == 200 else "ERR"

    def json(self) -> Any:
        return self._payload


class _FakeSession:
    """Records GET calls and returns queued responses (FIFO)."""

    def __init__(self, responses: list[_FakeResponse]):
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.headers: dict[str, str] = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if not self._responses:
            return _FakeResponse(200, {})
        return self._responses.pop(0)

    def close(self):
        pass


def _client_with(responses: list[_FakeResponse]) -> tuple[PolymarketClient, _FakeSession]:
    client = PolymarketClient(max_retries=0)
    session = _FakeSession(responses)
    client._session = session  # type: ignore[attr-defined]
    return client, session


# ── midpoint -> probability conversion ─────────────────────────────

def test_midpoint_to_probability_passthrough():
    # Binary market midpoint is already a 0..1 probability.
    assert midpoint_to_probability(0.42) == pytest.approx(0.42)
    assert midpoint_to_probability("0.42") == pytest.approx(0.42)


def test_midpoint_to_probability_clamps_range():
    assert midpoint_to_probability(1.5) == pytest.approx(1.0)
    assert midpoint_to_probability(-0.2) == pytest.approx(0.0)


def test_midpoint_to_probability_none_on_garbage():
    assert midpoint_to_probability(None) is None
    assert midpoint_to_probability("not-a-number") is None


# ── markets list ───────────────────────────────────────────────────

def test_get_markets_returns_list_and_hits_gamma():
    payload = [
        {"question": "Will the Fed cut rates in June?", "active": True},
        {"question": "Will CPI come in hot?", "active": True},
    ]
    client, session = _client_with([_FakeResponse(200, payload)])
    markets = client.get_markets(active=True, limit=2)
    assert isinstance(markets, list)
    assert len(markets) == 2
    assert "gamma" in session.calls[0]["url"]
    assert session.calls[0]["url"].endswith("/markets")
    assert session.calls[0]["params"]["active"] is True
    assert session.calls[0]["params"]["limit"] == 2


def test_get_markets_unwraps_data_envelope():
    payload = {"data": [{"question": "X"}]}
    client, _ = _client_with([_FakeResponse(200, payload)])
    markets = client.get_markets()
    assert markets == [{"question": "X"}]


# ── midpoint ───────────────────────────────────────────────────────

def test_get_midpoint_parses_mid_field_and_hits_clob():
    client, session = _client_with([_FakeResponse(200, {"mid": "0.37"})])
    mid = client.get_midpoint("0xTOKEN")
    assert mid == pytest.approx(0.37)
    assert "clob" in session.calls[0]["url"]
    assert session.calls[0]["params"]["token_id"] == "0xTOKEN"


def test_get_midpoint_none_when_field_absent():
    client, _ = _client_with([_FakeResponse(200, {})])
    assert client.get_midpoint("0xTOKEN") is None


# ── price history ──────────────────────────────────────────────────

def test_get_price_history_returns_history_rows():
    payload = {"history": [{"t": 1, "p": 0.4}, {"t": 2, "p": 0.5}]}
    client, session = _client_with([_FakeResponse(200, payload)])
    rows = client.get_price_history("0xTOKEN", interval="1d")
    assert rows == payload["history"]
    assert session.calls[0]["params"]["market"] == "0xTOKEN"
    assert session.calls[0]["params"]["interval"] == "1d"


# ── error handling ─────────────────────────────────────────────────

def test_404_raises_not_found():
    client, _ = _client_with([_FakeResponse(404, {"error": "nope"})])
    with pytest.raises(PolymarketNotFoundError):
        client.get_midpoint("missing")


def test_500_raises_api_error_when_not_retried():
    client, _ = _client_with([_FakeResponse(500, {"error": "boom"})])
    with pytest.raises(PolymarketAPIError):
        client.get_markets()


def test_missing_token_raises_value_error():
    client, _ = _client_with([_FakeResponse(200, {"mid": "0.5"})])
    with pytest.raises(ValueError):
        client.get_midpoint("")
