"""Tests for IB client ID range-based allocation with retry.

Validates that:
1. Persistent services (pool, relay) get fixed IDs from reserved ranges
2. On-demand scripts get IDs from the subprocess range with auto-retry
3. Client ID conflicts trigger automatic rotation to next available ID
4. Range exhaustion raises a clear error
5. The pool's reserved IDs are never allocated to subprocesses
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# Ensure scripts/ is importable
SCRIPTS_DIR = Path(__file__).parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# 1. Range constants are correctly defined and non-overlapping
# ---------------------------------------------------------------------------

class TestClientIdRanges:
    """Verify the ID range constants exist and don't overlap."""

    def test_pool_range_exists(self):
        from clients.ib_client import POOL_ID_RANGE
        assert POOL_ID_RANGE == (0, 9)

    def test_relay_range_exists(self):
        from clients.ib_client import RELAY_ID_RANGE
        assert RELAY_ID_RANGE == (10, 19)

    def test_subprocess_range_exists(self):
        from clients.ib_client import SUBPROCESS_ID_RANGE
        assert SUBPROCESS_ID_RANGE == (20, 49)

    def test_ranges_do_not_overlap(self):
        from clients.ib_client import POOL_ID_RANGE, RELAY_ID_RANGE, SUBPROCESS_ID_RANGE
        pool = set(range(POOL_ID_RANGE[0], POOL_ID_RANGE[1] + 1))
        relay = set(range(RELAY_ID_RANGE[0], RELAY_ID_RANGE[1] + 1))
        subproc = set(range(SUBPROCESS_ID_RANGE[0], SUBPROCESS_ID_RANGE[1] + 1))
        assert pool.isdisjoint(relay), "Pool and relay ranges overlap"
        assert pool.isdisjoint(subproc), "Pool and subprocess ranges overlap"
        assert relay.isdisjoint(subproc), "Relay and subprocess ranges overlap"

    def test_pool_roles_use_pool_range(self):
        from clients.ib_client import POOL_ROLES, POOL_ID_RANGE
        lo, hi = POOL_ID_RANGE
        for role, cid in POOL_ROLES.items():
            assert lo <= cid <= hi, f"Pool role '{role}' uses ID {cid} outside pool range {lo}-{hi}"

    def test_pool_roles_have_unique_ids(self):
        from clients.ib_client import POOL_ROLES
        ids = list(POOL_ROLES.values())
        assert len(ids) == len(set(ids)), f"Duplicate pool role IDs: {ids}"


# ---------------------------------------------------------------------------
# 2. connect() with auto-allocate from subprocess range
# ---------------------------------------------------------------------------

class TestSubprocessAutoAllocate:
    """On-demand scripts should auto-allocate IDs from the subprocess range."""

    def test_connect_with_auto_allocate_uses_subprocess_range(self):
        """When client_id='auto', connect should try IDs from subprocess range."""
        from clients.ib_client import IBClient, SUBPROCESS_ID_RANGE

        client = IBClient()
        mock_ib = MagicMock()
        client._ib = mock_ib
        # First connect succeeds
        mock_ib.connect.return_value = None
        mock_ib.isConnected.return_value = True

        client.connect(client_id="auto", timeout=1)

        # Should have connected with an ID in the subprocess range
        called_id = mock_ib.connect.call_args[1].get("clientId") or mock_ib.connect.call_args[0][2]
        lo, hi = SUBPROCESS_ID_RANGE
        assert lo <= called_id <= hi, f"Auto-allocated ID {called_id} outside subprocess range {lo}-{hi}"

    def test_auto_allocate_retries_on_client_id_conflict(self):
        """When first ID is in use, auto-allocate should try a different one."""
        from clients.ib_client import IBClient, SUBPROCESS_ID_RANGE

        client = IBClient()
        mock_ib = MagicMock()
        client._ib = mock_ib

        call_count = 0

        def mock_connect(host, port, clientId, timeout=3):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("client id is already in use")
            # Second call succeeds

        mock_ib.connect.side_effect = mock_connect

        client.connect(client_id="auto", timeout=1)

        assert call_count == 2
        # The two calls should use different IDs
        first_id = mock_ib.connect.call_args_list[0][1]["clientId"]
        second_id = mock_ib.connect.call_args_list[1][1]["clientId"]
        assert first_id != second_id, "Retry should use a different client ID"
        lo, hi = SUBPROCESS_ID_RANGE
        assert lo <= second_id <= hi

    def test_auto_allocate_wraps_around_range(self):
        """When approaching range end, should wrap to start."""
        from clients.ib_client import IBClient, SUBPROCESS_ID_RANGE

        client = IBClient()
        mock_ib = MagicMock()
        client._ib = mock_ib

        lo, hi = SUBPROCESS_ID_RANGE
        range_size = hi - lo + 1

        # Fail for all IDs in range except the very last attempt
        attempts = 0

        def mock_connect(host, port, clientId, timeout=3):
            nonlocal attempts
            attempts += 1
            if attempts < range_size:
                raise ConnectionError("client id is already in use")
            # Last attempt succeeds

        mock_ib.connect.side_effect = mock_connect

        client.connect(client_id="auto", timeout=1)
        assert attempts == range_size

    def test_auto_allocate_raises_when_range_exhausted(self):
        """When all IDs in range are taken, should raise IBConnectionError."""
        from clients.ib_client import IBClient, IBConnectionError, SUBPROCESS_ID_RANGE

        client = IBClient()
        mock_ib = MagicMock()
        client._ib = mock_ib

        # ALL IDs fail
        mock_ib.connect.side_effect = ConnectionError("client id is already in use")

        with pytest.raises(IBConnectionError, match="all client IDs.*in use"):
            client.connect(client_id="auto", timeout=1)

        lo, hi = SUBPROCESS_ID_RANGE
        expected_attempts = hi - lo + 1
        assert mock_ib.connect.call_count == expected_attempts

    def test_auto_allocate_does_not_retry_non_conflict_errors(self):
        """Connection errors other than 'client id in use' should not trigger rotation."""
        from clients.ib_client import IBClient, IBConnectionError

        client = IBClient()
        mock_ib = MagicMock()
        client._ib = mock_ib

        mock_ib.connect.side_effect = ConnectionError("Connection refused")

        with pytest.raises(IBConnectionError):
            client.connect(client_id="auto", timeout=1, max_retries=1)

        # Should only try once (no ID rotation for non-conflict errors)
        assert mock_ib.connect.call_count == 1


# ---------------------------------------------------------------------------
# 3. Explicit client_id still works (backward compat)
# ---------------------------------------------------------------------------

class TestExplicitClientId:
    """Explicit integer client_id should bypass auto-allocation."""

    def test_explicit_int_id_used_directly(self):
        from clients.ib_client import IBClient

        client = IBClient()
        mock_ib = MagicMock()
        client._ib = mock_ib

        client.connect(client_id=99, timeout=1)

        called_id = mock_ib.connect.call_args[1].get("clientId") or mock_ib.connect.call_args[0][2]
        assert called_id == 99

    def test_explicit_id_no_retry_on_conflict(self):
        """Explicit IDs should NOT auto-rotate — the caller chose that ID deliberately."""
        from clients.ib_client import IBClient, IBConnectionError

        client = IBClient()
        mock_ib = MagicMock()
        client._ib = mock_ib
        mock_ib.connect.side_effect = ConnectionError("client id is already in use")

        with pytest.raises(IBConnectionError):
            client.connect(client_id=99, timeout=1, max_retries=2)

        # Should retry max_retries times with the SAME ID (no rotation)
        assert mock_ib.connect.call_count == 2
        for c in mock_ib.connect.call_args_list:
            cid = c[1].get("clientId") or c[0][2]
            assert cid == 99

    def test_client_name_lookup_still_works(self):
        """client_name should resolve to the registry value."""
        from clients.ib_client import IBClient, POOL_ROLES

        client = IBClient()
        mock_ib = MagicMock()
        client._ib = mock_ib

        client.connect(client_name="sync", timeout=1)

        called_id = mock_ib.connect.call_args[1].get("clientId")
        if called_id is None:
            called_id = mock_ib.connect.call_args[0][2]
        assert called_id == POOL_ROLES["sync"]


# ---------------------------------------------------------------------------
# 4. Randomized start offset prevents thundering herd
# ---------------------------------------------------------------------------

class TestRandomizedStart:
    """Auto-allocate should start at a random offset in the range to avoid
    multiple scripts always competing for ID 20 first."""

    def test_auto_allocate_start_varies(self):
        """Multiple auto-allocate calls should not always start at the same ID."""
        from clients.ib_client import IBClient, SUBPROCESS_ID_RANGE

        observed_first_ids = set()
        for _ in range(20):
            client = IBClient()
            mock_ib = MagicMock()
            client._ib = mock_ib
            mock_ib.connect.return_value = None

            client.connect(client_id="auto", timeout=1)

            cid = mock_ib.connect.call_args[1].get("clientId") or mock_ib.connect.call_args[0][2]
            observed_first_ids.add(cid)

        # With 30 slots and 20 attempts, we should see more than 1 unique starting ID
        # (probability of all 20 hitting the same slot is (1/30)^19 ≈ 0)
        assert len(observed_first_ids) > 1, f"All 20 auto-allocations started at the same ID: {observed_first_ids}"


# ---------------------------------------------------------------------------
# 5. Pool uses POOL_ROLES, not CLIENT_IDS
# ---------------------------------------------------------------------------

class TestPoolRoles:
    """The IB pool should use POOL_ROLES for its connections."""

    def test_pool_roles_exported(self):
        from clients.ib_client import POOL_ROLES
        assert "sync" in POOL_ROLES
        assert "orders" in POOL_ROLES
        assert "data" in POOL_ROLES

    def test_pool_roles_in_pool_range(self):
        from clients.ib_client import POOL_ROLES, POOL_ID_RANGE
        lo, hi = POOL_ID_RANGE
        for role, cid in POOL_ROLES.items():
            assert lo <= cid <= hi
