"""Tests for IBClient subscription tracking + the REL-014 no-auto-reconnect contract.

REL-014 (finding R-015): the old ``_on_disconnect`` auto-reconnect path was never
registered on ``disconnectedEvent`` — documented resilience was dead code that
only these tests invoked directly. It was DELETED rather than registered: every
production consumer already recovers at a higher layer (FastAPI pool acquire-time
reconnect, monitor-daemon per-cycle reconnects, relay stale-tick ladder), and a
``time.sleep`` backoff inside an ib_insync event callback would block the event
loop. These tests pin the new contract:

- IBClient registers NO handler on ``disconnectedEvent`` and has no
  ``_on_disconnect`` attribute.
- ``_subscriptions`` does not leak: ``cancel_market_data`` removes the entry.

All tests mock ib_insync.IB where a connection would be needed.
"""

from unittest.mock import MagicMock, patch

from clients.ib_client import IBClient


# ---------------------------------------------------------------------------
# Subscription tracking
# ---------------------------------------------------------------------------


class TestSubscriptionTracking:
    """Track active streaming market data subscriptions."""

    @patch("clients.ib_client.IB")
    def test_get_quote_streaming_adds_subscription(self, MockIB):
        """get_quote with snapshot=False should record the subscription."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        mock_ib.reqMktData.return_value = MagicMock()

        client = IBClient()
        client.connect(client_id=1)

        contract = MagicMock()
        client.get_quote(contract, snapshot=False, generic_ticks="233")

        assert len(client._subscriptions) == 1
        sub = client._subscriptions[0]
        assert sub["contract"] is contract
        assert sub["generic_ticks"] == "233"

    @patch("clients.ib_client.IB")
    def test_get_quote_snapshot_does_not_add_subscription(self, MockIB):
        """get_quote with snapshot=True should NOT record a subscription."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        mock_ib.reqMktData.return_value = MagicMock()

        client = IBClient()
        client.connect(client_id=1)

        contract = MagicMock()
        client.get_quote(contract, snapshot=True)

        assert len(client._subscriptions) == 0

    @patch("clients.ib_client.IB")
    def test_multiple_subscriptions_tracked(self, MockIB):
        """Multiple streaming subscriptions should all be tracked."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        mock_ib.reqMktData.return_value = MagicMock()

        client = IBClient()
        client.connect(client_id=1)

        c1, c2, c3 = MagicMock(), MagicMock(), MagicMock()
        client.get_quote(c1, snapshot=False)
        client.get_quote(c2, snapshot=False, generic_ticks="100,101")
        client.get_quote(c3, snapshot=True)  # snapshot — not tracked

        assert len(client._subscriptions) == 2

    @patch("clients.ib_client.IB")
    def test_cancel_market_data_removes_subscription(self, MockIB):
        """cancel_market_data must remove the tracked entry (no leak)."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        mock_ib.reqMktData.return_value = MagicMock()

        client = IBClient()
        client.connect(client_id=1)

        c1, c2 = MagicMock(), MagicMock()
        client.get_quote(c1, snapshot=False)
        client.get_quote(c2, snapshot=False)
        assert len(client._subscriptions) == 2

        client.cancel_market_data(c1)

        mock_ib.cancelMktData.assert_called_once_with(c1)
        assert len(client._subscriptions) == 1
        assert client._subscriptions[0]["contract"] is c2

    @patch("clients.ib_client.IB")
    def test_cancel_market_data_untracked_contract_is_noop_on_tracking(self, MockIB):
        """Cancelling an untracked contract must not disturb other entries."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        mock_ib.reqMktData.return_value = MagicMock()

        client = IBClient()
        client.connect(client_id=1)

        tracked = MagicMock()
        client.get_quote(tracked, snapshot=False)

        client.cancel_market_data(MagicMock())

        assert len(client._subscriptions) == 1
        assert client._subscriptions[0]["contract"] is tracked

    @patch("clients.ib_client.IB")
    def test_clear_subscriptions(self, MockIB):
        """clear_subscriptions should empty the tracking list."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        mock_ib.reqMktData.return_value = MagicMock()

        client = IBClient()
        client.connect(client_id=1)

        client.get_quote(MagicMock(), snapshot=False)
        assert len(client._subscriptions) == 1

        client.clear_subscriptions()
        assert len(client._subscriptions) == 0


# ---------------------------------------------------------------------------
# No in-client auto-reconnect (REL-014)
# ---------------------------------------------------------------------------


class TestNoAutoReconnectDeadPath:
    """IBClient must not carry an unregistered auto-reconnect path."""

    def test_on_disconnect_handler_deleted(self):
        """The dead _on_disconnect path must not exist on IBClient."""
        assert not hasattr(IBClient, "_on_disconnect")

    def test_no_handler_registered_on_disconnected_event(self):
        """IBClient registers nothing on disconnectedEvent — recovery is
        owned by higher layers (pool acquire, daemon cycles, relay ladder)."""
        client = IBClient()
        assert len(client.ib.disconnectedEvent) == 0

    def test_no_reconnect_constants_exported(self):
        """The backoff constants of the deleted path must be gone."""
        import clients.ib_client as mod

        assert not hasattr(mod, "MAX_RECONNECT_ATTEMPTS")
        assert not hasattr(mod, "MAX_RECONNECT_BACKOFF")
