"""The per-minute order brake must not leak across tests.

`server._order_rate_timestamps` is a process-wide deque holding every accepted
placement for a rolling 60 s, capped by RADON_MAX_ORDERS_PER_MIN (default 10).
Nothing reset it between cases, and a placement posted without an `orderRef`
appends `None` — which the dedupe branch never matches — so every placement
test in a worker consumed one slot of a shared budget. Under
`pytest -n auto --dist loadfile` the file-to-worker assignment decides who runs
past the cap, and on 2026-08-29 that was
test_orders_place_safety_contract.py::test_orders_place_subprocess_timeout_fits_inside_next_budget,
which asserted 200 and got 429 — the only red job left on main.

These two cases pin the isolation: the first deliberately saturates the budget,
the second must still be handed a clean one.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def trusted_client(monkeypatch):
    from scripts.api import auth, server

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "test_mode", False)
    return TestClient(server.app), server


def _successful_place_result() -> SimpleNamespace:
    return SimpleNamespace(
        ok=True,
        error=None,
        data={
            "status": "ok",
            "orderId": 42,
            "permId": 420042,
            "initialStatus": "Submitted",
            "message": "BUY 1 AAPL @ $200.00 — Submitted",
        },
    )


_STOCK_ORDER = {
    "type": "stock",
    "symbol": "AAPL",
    "action": "BUY",
    "quantity": 1,
    "limitPrice": 200.0,
    "tif": "DAY",
}


def _place(client, server, monkeypatch):
    async def fake_recovery(script, args, timeout):
        return _successful_place_result()

    monkeypatch.setattr(server, "_run_ib_script_with_recovery", fake_recovery)
    return client.post("/orders/place", json=_STOCK_ORDER)


def test_a_case_may_spend_the_whole_minute_budget(trusted_client, monkeypatch):
    from order_limits import max_orders_per_min

    client, server = trusted_client
    cap = max_orders_per_min()

    accepted = [_place(client, server, monkeypatch).status_code for _ in range(cap)]
    assert accepted == [200] * cap

    refused = _place(client, server, monkeypatch)
    assert refused.status_code == 429
    assert refused.json()["detail"]["code"] == "ORDER_RATE_LIMIT"


def test_the_next_case_starts_from_a_clean_budget(trusted_client, monkeypatch):
    client, server = trusted_client
    assert _place(client, server, monkeypatch).status_code == 200
