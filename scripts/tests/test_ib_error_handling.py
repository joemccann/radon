"""Tests for IBClient pacing & invalid contract error handling.

Red/Green TDD: tests written FIRST (RED phase), then implementation follows.
All tests mock ib_insync.IB — no real IB connection needed.
"""

from unittest.mock import MagicMock, patch

import pytest

from clients.ib_client import IBClient


# ---------------------------------------------------------------------------
# Pacing violations (codes 162, 366)
# ---------------------------------------------------------------------------


class TestPacingViolation:
    """Test exponential backoff for pacing violation errors."""

    @patch("clients.ib_client.IB")
    def test_pacing_162_tracked_in_retries(self, MockIB):
        """Error 162 should be tracked in _pacing_retries."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(client_id=1)

        client._on_error(1, 162, "Historical Market Data pacing violation")

        assert 1 in client._pacing_retries
        assert client._pacing_retries[1] == 1

    @patch("clients.ib_client.IB")
    def test_pacing_366_tracked_in_retries(self, MockIB):
        """Error 366 should be tracked in _pacing_retries."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(client_id=1)

        client._on_error(2, 366, "No historical data query found for ticker id:2")

        assert 2 in client._pacing_retries
        assert client._pacing_retries[2] == 1

    @patch("clients.ib_client.IB")
    def test_pacing_retries_increment_per_reqid(self, MockIB):
        """Multiple pacing errors for same reqId should increment count."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(client_id=1)

        client._on_error(5, 162, "pacing violation")
        client._on_error(5, 162, "pacing violation")
        client._on_error(5, 162, "pacing violation")

        assert client._pacing_retries[5] == 3

    @patch("clients.ib_client.IB")
    def test_pacing_max_retries_respected(self, MockIB):
        """After 3 retries, pacing errors should stop incrementing (capped)."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(client_id=1)

        # Fire 5 pacing errors for same reqId
        for _ in range(5):
            client._on_error(10, 162, "pacing violation")

        # Should cap at 3
        assert client._pacing_retries[10] == 3

    @patch("clients.ib_client.IB")
    def test_pacing_different_reqids_independent(self, MockIB):
        """Pacing retries should be tracked independently per reqId."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(client_id=1)

        client._on_error(1, 162, "pacing violation")
        client._on_error(1, 162, "pacing violation")
        client._on_error(2, 366, "pacing violation")

        assert client._pacing_retries[1] == 2
        assert client._pacing_retries[2] == 1


# ---------------------------------------------------------------------------
# Invalid contracts (codes 200, 354)
# ---------------------------------------------------------------------------


class TestInvalidContract:
    """Test invalid contract handling — no retry, add to failed set."""

    @patch("clients.ib_client.IB")
    def test_invalid_contract_200_added_to_failed(self, MockIB):
        """Error 200 should add contract to _failed_contracts."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(client_id=1)

        contract = MagicMock()
        contract.__str__ = lambda self: "AAPL OPT 20260320 200 C"
        client._on_error(1, 200, "No security definition found", contract)

        assert len(client._failed_contracts) == 1

    @patch("clients.ib_client.IB")
    def test_invalid_contract_354_added_to_failed(self, MockIB):
        """Error 354 should add contract to _failed_contracts."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(client_id=1)

        contract = MagicMock()
        client._on_error(2, 354, "Requested market data not subscribed", contract)

        assert len(client._failed_contracts) == 1

    @patch("clients.ib_client.IB")
    def test_invalid_contract_does_not_retry(self, MockIB):
        """Invalid contract errors should NOT be added to _pacing_retries."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(client_id=1)

        contract = MagicMock()
        client._on_error(1, 200, "No security definition found", contract)

        assert 1 not in client._pacing_retries

    @patch("clients.ib_client.IB")
    def test_failed_contracts_property(self, MockIB):
        """_failed_contracts should be a set accessible as property."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(client_id=1)

        assert isinstance(client.failed_contracts, set)
        assert len(client.failed_contracts) == 0

        contract = MagicMock()
        client._on_error(1, 200, "No security definition found", contract)

        assert len(client.failed_contracts) == 1

    @patch("clients.ib_client.IB")
    def test_multiple_invalid_contracts_all_tracked(self, MockIB):
        """Multiple different invalid contracts should all be in the set."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(client_id=1)

        c1 = MagicMock()
        c1.conId = 111
        c2 = MagicMock()
        c2.conId = 222

        client._on_error(1, 200, "No security definition found", c1)
        client._on_error(2, 354, "Not subscribed", c2)

        assert len(client.failed_contracts) == 2


# ---------------------------------------------------------------------------
# Error 10358 — verify existing handling not broken
# ---------------------------------------------------------------------------


class TestExistingErrorHandling:
    """Verify that existing error handling is not broken by new code."""

    @patch("clients.ib_client.IB")
    def test_error_10358_still_ignored(self, MockIB):
        """Error 10358 (Reuters) should still be silently ignored."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(client_id=1)

        # Should not raise, not add to failed contracts
        client._on_error(1, 10358, "Reuters Fundamentals subscription inactive")

        assert len(client.failed_contracts) == 0
        assert 1 not in client._pacing_retries

    @patch("clients.ib_client.IB")
    def test_info_codes_still_handled(self, MockIB):
        """Info codes (2104, 2106, etc.) should still be handled gracefully."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(client_id=1)

        # These should not raise or add to any tracking
        client._on_error(0, 2104, "Market data farm connection is OK")
        client._on_error(0, 2106, "HMDS data farm connection is OK")

        assert len(client.failed_contracts) == 0
        assert len(client._pacing_retries) == 0

    @patch("clients.ib_client.IB")
    def test_connectivity_codes_still_handled(self, MockIB):
        """Connectivity codes (1100, 1101, 1102) should still be handled."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(client_id=1)

        # Should not raise
        client._on_error(0, 1100, "Connectivity between IB and TWS has been lost")
        client._on_error(0, 1101, "Connectivity restored — data lost")
        client._on_error(0, 1102, "Connectivity restored — data maintained")

    @patch("clients.ib_client.IB")
    def test_last_error_still_stored_for_unknown_codes(self, MockIB):
        """Unknown error codes should still store in _last_error."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(client_id=1)

        client._on_error(99, 999, "Some unknown error")
        assert client._last_error == (999, "Some unknown error")


# ---------------------------------------------------------------------------
# Auto-recovery: connection error pattern matching
# ---------------------------------------------------------------------------


class TestConnectionErrorPatterns:
    """Test that _is_ib_connection_error detects all failure modes."""

    def test_econnrefused_detected(self):
        from api.server import _is_ib_connection_error
        assert _is_ib_connection_error("Connect call failed: ECONNREFUSED")

    def test_connection_refused_detected(self):
        from api.server import _is_ib_connection_error
        assert _is_ib_connection_error("Connection refused on 127.0.0.1:4001")

    def test_timeout_error_detected(self):
        from api.server import _is_ib_connection_error
        assert _is_ib_connection_error("API connection failed: TimeoutError()")

    def test_api_connection_failed_detected(self):
        from api.server import _is_ib_connection_error
        assert _is_ib_connection_error("API connection failed: some reason")

    def test_failed_to_connect_detected(self):
        from api.server import _is_ib_connection_error
        assert _is_ib_connection_error(
            "Failed to connect to IB on 127.0.0.1:4001 after 1 attempt(s): "
        )

    def test_ib_connection_error_class_detected(self):
        from api.server import _is_ib_connection_error
        assert _is_ib_connection_error(
            "clients.ib_client.IBConnectionError: Failed to connect"
        )

    def test_unrelated_error_not_detected(self):
        from api.server import _is_ib_connection_error
        assert not _is_ib_connection_error("No security definition found")

    def test_none_safe(self):
        from api.server import _is_ib_connection_error
        assert not _is_ib_connection_error(None)

    def test_empty_safe(self):
        from api.server import _is_ib_connection_error
        assert not _is_ib_connection_error("")

    def test_make_sure_api_port_detected(self):
        from api.server import _is_ib_connection_error
        assert _is_ib_connection_error("Make sure API port on TWS/IBG is open")

    def test_connectivity_lost_detected(self):
        from api.server import _is_ib_connection_error
        assert _is_ib_connection_error(
            "Connectivity between IBKR and Trader Workstation has been lost."
        )

    def test_request_timed_out_detected(self):
        from api.server import _is_ib_connection_error
        assert _is_ib_connection_error("account updates for U4698258 request timed out")


# ---------------------------------------------------------------------------
# CLOSE_WAIT detection
# ---------------------------------------------------------------------------


class TestCloseWaitDetection:
    """Test _has_close_wait detection of dead upstream IB sessions."""

    @patch("api.ib_gateway.subprocess.check_output")
    def test_close_wait_detected(self, mock_lsof):
        from api.ib_gateway import _has_close_wait
        mock_lsof.return_value = (
            "java 62381 user 50u IPv6 0xce TCP 10.0.0.215:52097->8.17.22.31:4001 (CLOSE_WAIT)\n"
            "java 62381 user 41u IPv6 0xa9 TCP *:4001 (LISTEN)\n"
        )
        assert _has_close_wait() is True

    @patch("api.ib_gateway.subprocess.check_output")
    def test_healthy_no_close_wait(self, mock_lsof):
        from api.ib_gateway import _has_close_wait
        mock_lsof.return_value = (
            "java 62381 user 41u IPv6 0xa9 TCP *:4001 (LISTEN)\n"
            "java 62381 user 56u IPv6 0xf8 TCP localhost:4001->localhost:52132 (ESTABLISHED)\n"
        )
        assert _has_close_wait() is False

    @patch("api.ib_gateway.subprocess.check_output")
    def test_lsof_failure_returns_false(self, mock_lsof):
        import subprocess
        from api.ib_gateway import _has_close_wait
        mock_lsof.side_effect = subprocess.SubprocessError("lsof not found")
        assert _has_close_wait() is False


# ---------------------------------------------------------------------------
# Pre-flight pool check
# ---------------------------------------------------------------------------


class TestPoolPreflightCheck:
    """Test _pool_has_any_connection fast-fail logic."""

    def test_returns_false_when_no_pool(self):
        from api.server import _pool_has_any_connection
        import api.server as srv
        original = srv.ib_pool
        srv.ib_pool = None
        try:
            assert _pool_has_any_connection() is False
        finally:
            srv.ib_pool = original

    def test_returns_true_when_any_role_connected(self):
        from api.server import _pool_has_any_connection
        import api.server as srv
        mock_pool = MagicMock()
        mock_pool.is_connected.side_effect = lambda r: r == "sync"
        original = srv.ib_pool
        srv.ib_pool = mock_pool
        try:
            assert _pool_has_any_connection() is True
        finally:
            srv.ib_pool = original

    def test_returns_false_when_all_disconnected(self):
        from api.server import _pool_has_any_connection
        import api.server as srv
        mock_pool = MagicMock()
        mock_pool.is_connected.return_value = False
        original = srv.ib_pool
        srv.ib_pool = mock_pool
        try:
            assert _pool_has_any_connection() is False
        finally:
            srv.ib_pool = original
