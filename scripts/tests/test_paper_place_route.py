"""FU6 (B) — POST /paper/place shadow-placement route.

The route runs the paper matcher + fills engine against current quotes and
persists a simulated fill via ``scripts/paper/store.py`` into ``paper_fills``.
All DB / subprocess I/O is mocked — nothing live, no real Turso write (the DB
client refuses real connections under PYTEST_CURRENT_TEST anyway).

Two layers are tested:

  1. The subprocess script ``paper_place.py`` (pure matcher+fills core, store
     patched) computes a fill and hands a ``PaperFill`` to the store.
  2. The FastAPI route ``POST /paper/place`` runs that script off-loop via
     ``run_script`` and returns its JSON result, never blocking the uvicorn loop
     on a synchronous libsql write.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ── subprocess script: matcher + fills + store ──────────────────────

class TestPaperPlaceScript:
    def test_marketable_limit_buy_fills_and_persists(self):
        import paper_place

        recorded = []
        with patch.object(paper_place, "record_paper_fill", lambda fill: recorded.append(fill)):
            result = paper_place.place_paper_order(
                {
                    "ticker": "WULF",
                    "side": "BUY",
                    "order_type": "LIMIT",
                    "limit_price": 5.70,
                    "quantity": 10,
                    "bid": 5.60,
                    "ask": 5.62,
                }
            )

        assert result["status"] == "filled"
        assert len(recorded) == 1
        fill = recorded[0]
        assert fill.ticker == "WULF"
        assert fill.side == "BUY"
        assert fill.quantity == 10
        # BUY pays the mid + half-spread → above the 5.61 mid, but at/under limit.
        assert 5.61 <= fill.fill_price <= 5.70
        assert result["fill_price"] == pytest.approx(fill.fill_price)

    def test_non_marketable_limit_does_not_fill_or_persist(self):
        import paper_place

        recorded = []
        with patch.object(paper_place, "record_paper_fill", lambda fill: recorded.append(fill)):
            result = paper_place.place_paper_order(
                {
                    "ticker": "WULF",
                    "side": "BUY",
                    "order_type": "LIMIT",
                    "limit_price": 5.00,  # below the ask → cannot fill
                    "quantity": 10,
                    "bid": 5.60,
                    "ask": 5.62,
                }
            )

        assert result["status"] == "working"
        assert recorded == []

    def test_fill_id_is_idempotency_key(self):
        import paper_place

        recorded = []
        with patch.object(paper_place, "record_paper_fill", lambda fill: recorded.append(fill)):
            paper_place.place_paper_order(
                {
                    "ticker": "WULF",
                    "side": "SELL",
                    "order_type": "LIMIT",
                    "limit_price": 5.50,
                    "quantity": 4,
                    "bid": 5.60,
                    "ask": 5.62,
                    "fill_id": "abc-123",
                }
            )

        assert recorded[0].fill_id == "abc-123"


# ── FastAPI route ───────────────────────────────────────────────────

@pytest.fixture
def app_client(monkeypatch):
    from fastapi.testclient import TestClient
    from api import server
    from api import auth

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    return TestClient(server.app), server


class TestPaperPlaceRoute:
    def test_route_runs_script_offloop_and_returns_fill(self, app_client):
        client, server = app_client

        fill_payload = {
            "status": "filled",
            "ticker": "WULF",
            "side": "BUY",
            "fill_price": 5.61,
            "quantity": 10,
            "fill_id": "deadbeef",
        }

        async def _fake_run_script(name, args, timeout=None):
            assert name == "paper_place.py"
            return SimpleNamespace(ok=True, error=None, data=fill_payload)

        with patch.object(server, "run_script", _fake_run_script):
            resp = client.post(
                "/paper/place",
                json={
                    "ticker": "WULF",
                    "side": "BUY",
                    "order_type": "LIMIT",
                    "limit_price": 5.70,
                    "quantity": 10,
                    "bid": 5.60,
                    "ask": 5.62,
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "filled"
        assert body["ticker"] == "WULF"
        assert body["fill_price"] == 5.61

    def test_route_surfaces_script_failure(self, app_client):
        client, server = app_client

        async def _fake_run_script(name, args, timeout=None):
            return SimpleNamespace(ok=False, error="matcher exploded", data=None)

        with patch.object(server, "run_script", _fake_run_script):
            resp = client.post(
                "/paper/place",
                json={
                    "ticker": "WULF",
                    "side": "BUY",
                    "order_type": "LIMIT",
                    "limit_price": 5.70,
                    "quantity": 10,
                },
            )

        assert resp.status_code == 502

    def test_route_validates_required_fields(self, app_client):
        client, server = app_client
        resp = client.post("/paper/place", json={"ticker": "WULF"})
        assert resp.status_code == 400
