"""IBClient pacing counters and failed contracts are per IB session.

Audit implicit-state-machines-20261103 §3.4 "IBClient pacing": the per-reqId
pacing counter and the failed-contract set were never pruned. IB reuses
reqIds from 1 on every connection, so a reconnected client inherited the old
session's counts ("max retries exhausted" on its first 162) and the failed set
grew for the life of a pooled client.
"""
from unittest.mock import patch

from clients import ib_client
from clients.ib_client import IBClient


@patch("clients.ib_client.IB")
def test_reconnect_starts_a_fresh_pacing_count(MockIB):
    MockIB.return_value.isConnected.return_value = True
    client = IBClient()
    client.connect(client_id=1)
    for _ in range(ib_client._MAX_PACING_RETRIES):
        client._on_error(1, 162, "pacing violation")
    client.disconnect()
    client.connect(client_id=1)
    client._on_error(1, 162, "pacing violation")
    assert client._pacing_retries == {1: 1}


@patch("clients.ib_client.IB")
def test_disconnect_forgets_the_sessions_failed_contracts(MockIB):
    MockIB.return_value.isConnected.return_value = True
    client = IBClient()
    client.connect(client_id=1)
    client._on_error(3, 200, "No security definition", contract="BADCON")
    assert "BADCON" in client.failed_contracts
    client.disconnect()
    assert client.failed_contracts == set()


@patch("clients.ib_client.IB")
def test_failed_contracts_are_bounded(MockIB):
    MockIB.return_value.isConnected.return_value = True
    client = IBClient()
    client.connect(client_id=1)
    for n in range(ib_client._MAX_FAILED_CONTRACTS + 50):
        client._on_error(n, 200, "No security definition", contract=f"C{n}")
    assert len(client.failed_contracts) <= ib_client._MAX_FAILED_CONTRACTS
    assert f"C{ib_client._MAX_FAILED_CONTRACTS + 49}" in client.failed_contracts
