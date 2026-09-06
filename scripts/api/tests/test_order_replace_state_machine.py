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


class TestCancelAlreadyConfirmed:
    def test_ib_202_string(self):
        from scripts.api.server import _cancel_already_confirmed

        assert _cancel_already_confirmed("IB error 202: Order Canceled - reason:")

    def test_ib_202_structured_code(self):
        from scripts.api.server import _cancel_already_confirmed

        assert _cancel_already_confirmed({
            "ib_error_code": 202,
            "ib_error_text": "Order Canceled - reason:",
            "message": "Order Cancelled — IB error 202: Order Canceled - reason:",
        })

    def test_already_cancelled(self):
        from scripts.api.server import _cancel_already_confirmed

        assert _cancel_already_confirmed("Order already Cancelled — cannot cancel")

    def test_filled_is_not_confirmed_cancel(self):
        from scripts.api.server import _cancel_already_confirmed

        assert not _cancel_already_confirmed("Order already Filled — cannot cancel")

    def test_not_found_is_not_confirmed_cancel(self):
        from scripts.api.server import _cancel_already_confirmed

        assert not _cancel_already_confirmed(
            "IB rejected cancel: OrderId that needs to be cancelled is not found"
        )


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


def test_replace_treats_ib_202_cancel_as_confirmed_and_places(trusted_client, monkeypatch):
    """IB 202 during cancel means the original is already gone.

    The 2026-09-04 combo modify toasted REPLACE_PARTIAL / phase=cancellation
    with cancelled=[] and never placed. IB later pushed that the order was
    cancelled. A 202 (or already-Cancelled) from cancel must not abort the
    replacement.
    """
    from scripts.api import server

    monkeypatch.setattr(server, "orders_whatif", AsyncMock(return_value={"status": "ok"}))
    monkeypatch.setattr(
        server,
        "orders_cancel",
        AsyncMock(side_effect=HTTPException(
            status_code=502,
            detail="IB error 202: Order Canceled - reason:",
        )),
    )
    place = AsyncMock(return_value={"status": "ok", "orderId": 99})
    monkeypatch.setattr(server, "_orders_place_after_rate_reservation", place)
    server._order_rate_timestamps.clear()

    try:
        response = trusted_client.post("/orders/replace", json={
            "cancelOrders": [{"orderId": 10}],
            "replaceOrder": _replacement(),
        })
    finally:
        server._order_rate_timestamps.clear()

    assert response.status_code == 200
    place.assert_awaited_once()
    body = response.json()
    assert body["cancelled"] == [{"orderId": 10, "permId": None, "status": "Cancelled"}]
    assert body["orderId"] == 99


def test_replace_treats_already_cancelled_as_confirmed_and_places(trusted_client, monkeypatch):
    from scripts.api import server

    monkeypatch.setattr(server, "orders_whatif", AsyncMock(return_value={"status": "ok"}))
    monkeypatch.setattr(
        server,
        "orders_cancel",
        AsyncMock(side_effect=HTTPException(
            status_code=502,
            detail="Order already Cancelled — cannot cancel",
        )),
    )
    place = AsyncMock(return_value={"status": "ok", "orderId": 8})
    monkeypatch.setattr(server, "_orders_place_after_rate_reservation", place)
    server._order_rate_timestamps.clear()

    try:
        response = trusted_client.post("/orders/replace", json={
            "cancelOrders": [{"orderId": 10, "permId": 44}],
            "replaceOrder": _replacement(),
        })
    finally:
        server._order_rate_timestamps.clear()

    assert response.status_code == 200
    place.assert_awaited_once()
    assert response.json()["cancelled"][0]["status"] == "Cancelled"


def test_replace_still_aborts_on_real_cancel_reject(trusted_client, monkeypatch):
    from scripts.api import server

    monkeypatch.setattr(server, "orders_whatif", AsyncMock(return_value={"status": "ok"}))
    place = AsyncMock(return_value={"status": "ok", "orderId": 1})
    monkeypatch.setattr(
        server,
        "orders_cancel",
        AsyncMock(side_effect=HTTPException(
            status_code=502,
            detail="IB rejected cancel: OrderId that needs to be cancelled is not found",
        )),
    )
    monkeypatch.setattr(server, "_orders_place_after_rate_reservation", place)
    server._order_rate_timestamps.clear()

    try:
        response = trusted_client.post("/orders/replace", json={
            "cancelOrders": [{"orderId": 10}],
            "replaceOrder": _replacement(),
        })
    finally:
        server._order_rate_timestamps.clear()

    assert response.status_code == 502
    place.assert_not_awaited()
    detail = response.json()["detail"]
    assert detail["code"] == "REPLACE_PARTIAL"
    assert detail["phase"] == "cancellation"
    assert detail["cancelled"] == []


def test_replace_does_not_place_when_original_already_filled(trusted_client, monkeypatch):
    from scripts.api import server

    monkeypatch.setattr(server, "orders_whatif", AsyncMock(return_value={"status": "ok"}))
    place = AsyncMock(return_value={"status": "ok", "orderId": 1})
    monkeypatch.setattr(
        server,
        "orders_cancel",
        AsyncMock(side_effect=HTTPException(
            status_code=502,
            detail="Order already Filled — cannot cancel",
        )),
    )
    monkeypatch.setattr(server, "_orders_place_after_rate_reservation", place)
    server._order_rate_timestamps.clear()

    try:
        response = trusted_client.post("/orders/replace", json={
            "cancelOrders": [{"orderId": 10}],
            "replaceOrder": _replacement(),
        })
    finally:
        server._order_rate_timestamps.clear()

    assert response.status_code == 502
    place.assert_not_awaited()
    assert response.json()["detail"]["cancelled"] == []


# ---------------------------------------------------------------------------
# REL-233 (R-638) — _cancel_already_confirmed promoted wrapped 5xx failures to
# Cancelled via substring match, and the structured-202 branch returned before
# the already-filled check; replace then placed the replacement at full
# quantity on top of a partial execution. The cancelled entry was fabricated
# from the request with no broker-state verification.
# ---------------------------------------------------------------------------


class TestCancelConfirmationHardening:
    def test_bare_order_canceled_text_is_not_confirmed(self):
        from scripts.api.server import _cancel_already_confirmed

        assert not _cancel_already_confirmed(
            "Upstream 500: Order Canceled appeared in a wrapped traceback"
        )

    def test_structured_202_with_filled_text_is_not_confirmed(self):
        from scripts.api.server import _cancel_already_confirmed

        assert not _cancel_already_confirmed({
            "ib_error_code": 202,
            "message": "Order already Filled — cannot cancel",
        })


def test_replace_refuses_structured_202_when_broker_shows_partial_fill(
    trusted_client, monkeypatch
):
    from unittest.mock import AsyncMock

    from scripts.api import server

    monkeypatch.setattr(server, "orders_whatif", AsyncMock(return_value={"status": "ok"}))
    monkeypatch.setattr(
        server,
        "orders_cancel",
        AsyncMock(side_effect=HTTPException(
            status_code=502,
            detail={
                "ib_error_code": 202,
                "message": "IB error 202: Order Canceled - reason:",
            },
        )),
    )
    monkeypatch.setattr(
        server,
        "_find_working_order",
        AsyncMock(return_value={"orderId": 10, "status": "Submitted", "filled": 3}),
    )
    place = AsyncMock(return_value={"status": "ok", "orderId": 1})
    monkeypatch.setattr(server, "_orders_place_after_rate_reservation", place)
    server._order_rate_timestamps.clear()

    try:
        response = trusted_client.post("/orders/replace", json={
            "cancelOrders": [{"orderId": 10}],
            "replaceOrder": _replacement(),
        })
    finally:
        server._order_rate_timestamps.clear()

    assert response.status_code == 502
    place.assert_not_awaited()
    detail = response.json()["detail"]
    assert detail["code"] == "REPLACE_PARTIAL"
    assert detail["cancelled"] == []


def test_replace_refuses_structured_202_when_broker_shows_order_still_working(
    trusted_client, monkeypatch
):
    """T-463: the status-string arm of _cancel_confirmed_at_broker.

    Broker snapshot shows Submitted with zero filled behind a structured-202
    cancel error — still-working, not confirmed-cancelled. Replace must abort
    with REPLACE_PARTIAL and never place the replacement.
    """
    from unittest.mock import AsyncMock

    from scripts.api import server

    monkeypatch.setattr(server, "orders_whatif", AsyncMock(return_value={"status": "ok"}))
    monkeypatch.setattr(
        server,
        "orders_cancel",
        AsyncMock(side_effect=HTTPException(
            status_code=502,
            detail={
                "ib_error_code": 202,
                "message": "IB error 202: Order Canceled - reason:",
            },
        )),
    )
    monkeypatch.setattr(
        server,
        "_find_working_order",
        AsyncMock(return_value={"orderId": 10, "status": "Submitted", "filled": 0}),
    )
    place = AsyncMock(return_value={"status": "ok", "orderId": 1})
    monkeypatch.setattr(server, "_orders_place_after_rate_reservation", place)
    server._order_rate_timestamps.clear()

    try:
        response = trusted_client.post("/orders/replace", json={
            "cancelOrders": [{"orderId": 10}],
            "replaceOrder": _replacement(),
        })
    finally:
        server._order_rate_timestamps.clear()

    assert response.status_code == 502
    place.assert_not_awaited()
    detail = response.json()["detail"]
    assert detail["code"] == "REPLACE_PARTIAL"
    assert detail["cancelled"] == []


def test_replace_does_not_place_when_cancel_reports_order_still_submitted(
    trusted_client, monkeypatch
):
    from unittest.mock import AsyncMock

    from scripts.api import server

    monkeypatch.setattr(server, "orders_whatif", AsyncMock(return_value={"status": "ok"}))
    monkeypatch.setattr(
        server,
        "orders_cancel",
        AsyncMock(side_effect=HTTPException(
            status_code=502,
            detail="Cancel failed — order still Submitted",
        )),
    )
    place = AsyncMock(return_value={"status": "ok", "orderId": 1})
    monkeypatch.setattr(server, "_orders_place_after_rate_reservation", place)
    server._order_rate_timestamps.clear()

    try:
        response = trusted_client.post("/orders/replace", json={
            "cancelOrders": [{"orderId": 10}],
            "replaceOrder": _replacement(),
        })
    finally:
        server._order_rate_timestamps.clear()

    assert response.status_code == 502
    place.assert_not_awaited()
    assert response.json()["detail"]["cancelled"] == []
