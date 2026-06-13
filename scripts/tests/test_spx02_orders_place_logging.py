"""SPX-02 — RED/GREEN tests for /orders/place server-side logging and error preservation.

The SPCX investigation showed that when ib_place_order.py returns an error
(status=="error"), server.py raises HTTPException(502) with NO server-side
logging and collapses the structured error detail to a bare string — so the
reason disappears from journald AND radonFetch's coerce contract breaks.

Fix:
  1. Before the 502 raise, emit logger.warning/error with the full detail
     including the new ib_error_code / ib_error_text fields.
  2. Preserve the structured dict in HTTPException(detail=...) so radonFetch's
     coerceRadonErrorDetail can unwrap it (not stringify to "[object Object]").
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _placement_error_result(message: str = "Order Inactive",
                             ib_error_code: int | None = None,
                             ib_error_text: str | None = None) -> SimpleNamespace:
    """ScriptResult that mirrors a failed ib_place_order.py response."""
    data = {
        "status": "error",
        "message": message,
        "orderId": 99,
        "permId": 12345,
        "initialStatus": "Inactive",
    }
    if ib_error_code is not None:
        data["ib_error_code"] = ib_error_code
    if ib_error_text is not None:
        data["ib_error_text"] = ib_error_text
    return SimpleNamespace(ok=True, error=None, data=data)


def _placement_ok_result() -> SimpleNamespace:
    return SimpleNamespace(
        ok=True,
        error=None,
        data={
            "status": "ok",
            "orderId": 99,
            "permId": 12345,
            "initialStatus": "Submitted",
            "message": "SELL 100 SPCX @ $25.00 — Submitted",
        },
    )


def _infra_error_result(error: str = "IB Gateway is not accepting connections") -> SimpleNamespace:
    """ScriptResult with ok=False (infra/connection error)."""
    return SimpleNamespace(ok=False, error=error, data=None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app_client(monkeypatch):
    """FastAPI TestClient reaching routes via the trusted-local bypass."""
    from fastapi.testclient import TestClient
    from api import server
    from api import auth

    # Trusted-local bypass — auth now fails CLOSED when JWKS is unset.
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    return TestClient(server.app), server


# ---------------------------------------------------------------------------
# SPX-02 Tests
# ---------------------------------------------------------------------------

class TestOrdersPlaceLogging:
    """Logger must be called before the 502 raise; detail must stay structured."""

    def test_logger_called_with_detail_on_placement_error(self, app_client, caplog):
        """When ib_place_order returns status==error, logger must fire."""
        client, server = app_client
        result = _placement_error_result(
            message="Order Inactive",
            ib_error_code=201,
            ib_error_text="Order rejected - Shares not available for short sale",
        )

        async def _fake_recovery(*_a, **_kw):
            return result

        with patch.object(server, "_run_ib_script_with_recovery", _fake_recovery), \
             caplog.at_level(logging.WARNING, logger="radon.api"):
            resp = client.post("/orders/place", json={
                "type": "stock", "symbol": "SPCX", "action": "SELL",
                "quantity": 100, "limitPrice": 25.00, "tif": "DAY",
            })

        assert resp.status_code == 502
        # The key requirement: radon.api logger must have logged something
        # that includes the order error detail
        api_records = [r for r in caplog.records if r.name == "radon.api"]
        assert api_records, \
            "radon.api logger was not called before the 502 raise"
        combined = " ".join(r.getMessage() for r in api_records)
        assert "201" in combined or "spcx" in combined.lower() or \
               "inactive" in combined.lower() or "short sale" in combined.lower(), \
               f"Log message should include error detail, got: {combined!r}"

    def test_502_detail_preserves_structured_dict_not_bare_string(self, app_client):
        """HTTPException detail must be a dict (or contain dict fields) for radonFetch."""
        client, server = app_client
        result = _placement_error_result(
            message="Order Inactive",
            ib_error_code=201,
            ib_error_text="Order rejected - Shares not available for short sale",
        )

        async def _fake_recovery(*_a, **_kw):
            return result

        with patch.object(server, "_run_ib_script_with_recovery", _fake_recovery):
            resp = client.post("/orders/place", json={
                "type": "stock", "symbol": "SPCX", "action": "SELL",
                "quantity": 100, "limitPrice": 25.00, "tif": "DAY",
            })

        assert resp.status_code == 502
        body = resp.json()
        # FastAPI wraps HTTPException detail as {"detail": ...}
        detail = body.get("detail")
        # Must NOT be a bare string like "Order Inactive" — must preserve structure
        assert isinstance(detail, dict), \
            f"detail should be dict for radonFetch coercion, got {type(detail).__name__}: {detail!r}"
        assert detail.get("ib_error_code") == 201, \
            f"ib_error_code must survive in detail: {detail}"
        assert detail.get("ib_error_text") == "Order rejected - Shares not available for short sale", \
            f"ib_error_text must survive in detail: {detail}"

    def test_502_detail_has_message_field_for_bare_error(self, app_client):
        """Even without ib_error_code, detail dict must have a 'message' key."""
        client, server = app_client
        result = _placement_error_result(message="Order Inactive")

        async def _fake_recovery(*_a, **_kw):
            return result

        with patch.object(server, "_run_ib_script_with_recovery", _fake_recovery):
            resp = client.post("/orders/place", json={
                "type": "stock", "symbol": "SPCX", "action": "SELL",
                "quantity": 100, "limitPrice": 25.00, "tif": "DAY",
            })

        assert resp.status_code == 502
        body = resp.json()
        detail = body.get("detail")
        assert isinstance(detail, dict), f"detail should be dict: {detail!r}"
        assert "message" in detail, f"detail dict must have 'message' key: {detail}"

    def test_infra_error_still_raises_502(self, app_client):
        """Infrastructure errors (ok=False) still 502 — regression guard."""
        client, server = app_client

        async def _fake_recovery(*_a, **_kw):
            return _infra_error_result("IB Gateway is not accepting connections")

        with patch.object(server, "_run_ib_script_with_recovery", _fake_recovery):
            resp = client.post("/orders/place", json={
                "type": "stock", "symbol": "SPCX", "action": "SELL",
                "quantity": 100, "limitPrice": 25.00, "tif": "DAY",
            })

        assert resp.status_code == 502

    def test_successful_order_returns_200(self, app_client):
        """Successful placement still returns 200 — regression guard."""
        client, server = app_client

        async def _fake_recovery(*_a, **_kw):
            return _placement_ok_result()

        with patch.object(server, "_run_ib_script_with_recovery", _fake_recovery):
            resp = client.post("/orders/place", json={
                "type": "stock", "symbol": "SPCX", "action": "SELL",
                "quantity": 100, "limitPrice": 25.00, "tif": "DAY",
            })

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"

    def test_logger_called_on_infra_error_too(self, app_client, caplog):
        """Infrastructure failures (ok=False) are also logged before the 502."""
        client, server = app_client

        async def _fake_recovery(*_a, **_kw):
            return _infra_error_result("IB Gateway is not accepting connections")

        with patch.object(server, "_run_ib_script_with_recovery", _fake_recovery), \
             caplog.at_level(logging.WARNING, logger="radon.api"):
            resp = client.post("/orders/place", json={
                "type": "stock", "symbol": "SPCX", "action": "SELL",
                "quantity": 100, "limitPrice": 25.00, "tif": "DAY",
            })

        assert resp.status_code == 502
        api_records = [r for r in caplog.records if r.name == "radon.api"]
        assert api_records, "radon.api logger not called on infra error"
