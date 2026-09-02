from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


@pytest.fixture
def trusted_client(monkeypatch, tmp_path):
    from scripts.api import auth, server
    import trading_halt

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "test_mode", False)
    monkeypatch.setattr(trading_halt, "HALT_FILE", tmp_path / "halt.json")
    server._order_rate_timestamps.clear()
    try:
        yield TestClient(server.app)
    finally:
        server._order_rate_timestamps.clear()


def _replacement():
    return {
        "type": "combo", "symbol": "SPX", "action": "BUY", "quantity": 1,
        "limitPrice": 1.0,
        "legs": [
            {"expiry": "20260918", "strike": 7000, "right": "C", "action": "BUY", "ratio": 1},
            {"expiry": "20260918", "strike": 7050, "right": "C", "action": "SELL", "ratio": 1},
        ],
    }


def test_replace_preflight_runs_before_any_cancellation(trusted_client, monkeypatch):
    from scripts.api import server

    preflight = AsyncMock(side_effect=HTTPException(status_code=502, detail="what-if rejected"))
    cancel = AsyncMock()
    monkeypatch.setattr(server, "orders_whatif", preflight)
    monkeypatch.setattr(server, "orders_cancel", cancel)

    response = trusted_client.post("/orders/replace", json={
        "cancelOrders": [{"orderId": 10}],
        "replaceOrder": _replacement(),
    })

    assert response.status_code == 502
    cancel.assert_not_awaited()


def test_replace_preserves_indeterminate_status_and_cancel_audit(trusted_client, monkeypatch):
    from scripts.api import server

    monkeypatch.setattr(server, "orders_whatif", AsyncMock(return_value={"status": "ok"}))
    monkeypatch.setattr(server, "orders_cancel", AsyncMock(return_value={"finalStatus": "Cancelled"}))
    monkeypatch.setattr(
        server,
        "_orders_place_after_rate_reservation",
        AsyncMock(side_effect=HTTPException(status_code=504, detail={"code": "ORDER_INDETERMINATE"})),
    )

    response = trusted_client.post("/orders/replace", json={
        "cancelOrders": [{"orderId": 10}],
        "replaceOrder": _replacement(),
    })

    assert response.status_code == 504
    detail = response.json()["detail"]
    assert detail["code"] == "REPLACE_INDETERMINATE"
    assert detail["cancelled"] == [{"orderId": 10, "permId": None, "status": "Cancelled"}]
    assert detail["replacementOrderRef"].startswith("radon-replace-")


# ---------------------------------------------------------------------------
# REL-026 (R-050) — /orders/replace evaluates halt and the order-limit caps up
# front, but never the REL-005 per-minute rate cap: that lives inside
# orders_place, which runs AFTER the cancel loop has already pulled every
# target. An exhausted rate budget (or a halt toggled during the what-if)
# therefore leaves the operator's working orders cancelled and the replacement
# refused — surfaced as REPLACE_PARTIAL with the position unhedged.
# ---------------------------------------------------------------------------


def _exhaust_order_rate_budget(server) -> None:
    from order_limits import max_orders_per_min

    server._order_rate_timestamps.clear()
    for _ in range(max_orders_per_min()):
        server._refuse_if_order_rate_exceeded()


class TestReplaceReservesRateBudgetBeforeCancelling:
    def test_exhausted_rate_budget_refuses_with_zero_cancels(
        self, trusted_client, monkeypatch
    ):
        from scripts.api import server

        cancel = AsyncMock(return_value={"finalStatus": "Cancelled"})
        place = AsyncMock(return_value={"status": "ok"})
        monkeypatch.setattr(server, "orders_whatif", AsyncMock(return_value={"status": "ok"}))
        monkeypatch.setattr(server, "orders_cancel", cancel)
        monkeypatch.setattr(server, "_orders_place_after_rate_reservation", place)
        _exhaust_order_rate_budget(server)

        try:
            response = trusted_client.post("/orders/replace", json={
                "cancelOrders": [{"orderId": 10}],
                "replaceOrder": _replacement(),
            })
        finally:
            server._order_rate_timestamps.clear()

        assert response.status_code == 429
        cancel.assert_not_awaited()
        place.assert_not_awaited()

    def test_halt_set_during_preflight_refuses_with_zero_cancels(
        self, trusted_client, monkeypatch
    ):
        """The what-if is the slow step; a halt fired while it runs must still
        stop the replace before the first cancellation."""
        from scripts.api import server
        import trading_halt

        cancel = AsyncMock(return_value={"finalStatus": "Cancelled"})
        place = AsyncMock(return_value={"status": "ok"})

        async def halt_during_whatif(_request):
            trading_halt.set_halt(reason="kill switch", actor="test")
            return {"status": "ok"}

        monkeypatch.setattr(server, "orders_whatif", halt_during_whatif)
        monkeypatch.setattr(server, "orders_cancel", cancel)
        monkeypatch.setattr(server, "_orders_place_after_rate_reservation", place)
        server._order_rate_timestamps.clear()

        try:
            response = trusted_client.post("/orders/replace", json={
                "cancelOrders": [{"orderId": 10}],
                "replaceOrder": _replacement(),
            })
        finally:
            trading_halt.clear_halt(actor="test")
            server._order_rate_timestamps.clear()

        assert response.status_code == 409
        cancel.assert_not_awaited()
        place.assert_not_awaited()

    def test_replace_consumes_exactly_one_rate_slot(self, trusted_client, monkeypatch):
        """The reservation must not be double-counted: the inner placement
        goes through `_orders_place_after_rate_reservation`, not the public
        `/orders/place` route that would claim a second slot."""
        from scripts.api import server

        monkeypatch.setattr(server, "orders_whatif", AsyncMock(return_value={"status": "ok"}))
        monkeypatch.setattr(server, "orders_cancel", AsyncMock(return_value={"finalStatus": "Cancelled"}))
        monkeypatch.setattr(server, "test_mode", True)
        server._order_rate_timestamps.clear()

        try:
            response = trusted_client.post("/orders/replace", json={
                "cancelOrders": [{"orderId": 10}],
                "replaceOrder": _replacement(),
            })
            consumed = len(server._order_rate_timestamps)
        finally:
            server._order_rate_timestamps.clear()

        assert response.status_code == 200
        assert consumed == 1

    def test_replace_still_succeeds_with_budget_available(
        self, trusted_client, monkeypatch
    ):
        from scripts.api import server

        cancel = AsyncMock(return_value={"finalStatus": "Cancelled"})
        monkeypatch.setattr(server, "orders_whatif", AsyncMock(return_value={"status": "ok"}))
        monkeypatch.setattr(server, "orders_cancel", cancel)
        monkeypatch.setattr(
            server,
            "_orders_place_after_rate_reservation",
            AsyncMock(return_value={"status": "ok", "orderId": 7}),
        )
        server._order_rate_timestamps.clear()

        try:
            response = trusted_client.post("/orders/replace", json={
                "cancelOrders": [{"orderId": 10}],
                "replaceOrder": _replacement(),
            })
        finally:
            server._order_rate_timestamps.clear()

        assert response.status_code == 200
        cancel.assert_awaited_once()
