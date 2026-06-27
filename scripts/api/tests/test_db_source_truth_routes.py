"""FastAPI source-of-truth routes must avoid stale JSON mirrors."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def trusted_client(monkeypatch):
    from scripts.api import auth, server

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    return TestClient(server.app)


def test_portfolio_sync_returns_live_payload_without_turso_reread(trusted_client, monkeypatch):
    from scripts.api import server
    from utils import atomic_io

    async def fake_run(*args, **kwargs):
        return SimpleNamespace(
            ok=True,
            error=None,
            data={
                "bankroll": 125000,
                "position_count": 1,
                "positions": [{"ticker": "SPY", "structure": "Stock"}],
            },
        )

    def fake_hrana_execute(sql, args=(), timeout=None):
        raise AssertionError("/portfolio/sync must not require a Turso read-back after live IB sync")

    def fail_disk_read(*args, **kwargs):
        raise AssertionError("portfolio/sync must not read data/portfolio.json")

    monkeypatch.setattr(server, "_run_ib_script_with_recovery", fake_run)
    monkeypatch.setattr(server.db_http, "hrana_execute", fake_hrana_execute)
    monkeypatch.setattr(atomic_io, "verified_load", fail_disk_read)

    response = trusted_client.post("/portfolio/sync")

    assert response.status_code == 200
    body = response.json()
    assert body["bankroll"] == 125000
    assert body["positions"][0]["ticker"] == "SPY"


def test_portfolio_sync_falls_back_to_turso_snapshot_for_legacy_raw_sync(trusted_client, monkeypatch):
    from scripts.api import server

    async def fake_run(*args, **kwargs):
        return SimpleNamespace(ok=True, error=None, data={})

    def fake_hrana_execute(sql, args=(), timeout=None):
        if "FROM portfolio_snapshots" in sql:
            return [
                (
                    json.dumps(
                        {
                            "bankroll": 125000,
                            "position_count": 1,
                            "positions": [{"ticker": "SPY", "structure": "Stock"}],
                        }
                    ),
                )
            ]
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(server, "_run_ib_script_with_recovery", fake_run)
    monkeypatch.setattr(server.db_http, "hrana_execute", fake_hrana_execute)

    response = trusted_client.post("/portfolio/sync")

    assert response.status_code == 200
    body = response.json()
    assert body["bankroll"] == 125000
    assert body["positions"][0]["ticker"] == "SPY"


def test_orders_refresh_returns_turso_snapshot_not_orders_json(trusted_client, monkeypatch):
    from scripts.api import server

    async def fake_run(*args, **kwargs):
        return SimpleNamespace(ok=True, error=None)

    def fake_hrana_execute(sql, args=(), timeout=None):
        if "FROM open_orders" in sql:
            return [
                (
                    json.dumps({"permId": 101, "symbol": "AAPL", "status": "Submitted"}),
                    "2026-06-23T15:00:00Z",
                )
            ]
        if "FROM executed_orders" in sql:
            return [
                (
                    json.dumps({"execId": "E1", "symbol": "MSFT", "quantity": 1}),
                    "2026-06-23T14:55:00Z",
                )
            ]
        raise AssertionError(f"unexpected SQL: {sql}")

    def fail_disk_read(*args, **kwargs):
        raise AssertionError("orders/refresh must not read data/orders.json")

    monkeypatch.setattr(server, "test_mode", False)
    monkeypatch.setattr(server, "_run_ib_script_with_recovery", fake_run)
    monkeypatch.setattr(server.db_http, "hrana_execute", fake_hrana_execute)
    monkeypatch.setattr(server, "_read_cache", fail_disk_read)

    response = trusted_client.post("/orders/refresh")

    assert response.status_code == 200
    body = response.json()
    assert body["last_sync"] == "2026-06-23T15:00:00Z"
    assert body["open_count"] == 1
    assert body["executed_count"] == 1
    assert body["open_orders"][0]["permId"] == 101
    assert body["executed_orders"][0]["execId"] == "E1"


def test_orders_refresh_allows_empty_turso_snapshot(trusted_client, monkeypatch):
    from scripts.api import server

    async def fake_run(*args, **kwargs):
        return SimpleNamespace(ok=True, error=None)

    def fake_hrana_execute(sql, args=(), timeout=None):
        if "FROM open_orders" in sql or "FROM executed_orders" in sql:
            return []
        raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(server, "test_mode", False)
    monkeypatch.setattr(server, "_run_ib_script_with_recovery", fake_run)
    monkeypatch.setattr(server.db_http, "hrana_execute", fake_hrana_execute)
    monkeypatch.setattr(
        server,
        "_read_cache",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("orders/refresh must not read data/orders.json")
        ),
    )

    response = trusted_client.post("/orders/refresh")

    assert response.status_code == 200
    assert response.json() == {
        "last_sync": "",
        "open_orders": [],
        "executed_orders": [],
        "open_count": 0,
        "executed_count": 0,
    }
