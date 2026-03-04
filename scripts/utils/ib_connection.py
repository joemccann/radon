"""Shared Interactive Brokers connection utilities.

Centralises client-ID registry, default host/port constants, and a
convenience ``connect_ib`` helper used by every IB script.
"""

from ib_insync import IB

# Client-ID registry for IB connections.
#
# STRATEGY: Use clientId=0 (master) by default for full order control.
# Only use unique IDs when concurrent connections are required.
#
# clientId=0 (MASTER):
#   - Can cancel/modify ANY order (including TWS-placed orders)
#   - Full account visibility
#   - Only ONE connection can use clientId=0 at a time
#
# clientId=1-999:
#   - Can only manage orders placed with same clientId
#   - Multiple concurrent connections allowed
#   - Use for long-running services (streaming, monitoring)
#
CLIENT_IDS: dict = {
    "ib_order_manage": 0,   # Master — cancel/modify any order
    "ib_sync": 0,           # Master — needs full position visibility
    "ib_orders": 11,        # Dedicated — concurrent with ib_order_manage
    "ib_reconcile": 0,      # Master — needs full account access
    "ib_order": 2,          # Unique — order placement (tags orders)
    "ib_fill_monitor": 52,  # Unique — long-running monitor service
    "exit_order_service": 60,  # Unique — background daemon
    "fetch_analyst_ratings": 99,  # Unique — may run concurrently
    "ib_realtime_server": 100,    # Unique — persistent streaming
}

DEFAULT_HOST = "127.0.0.1"
DEFAULT_GATEWAY_PORT = 4001
DEFAULT_TWS_PORT = 7497


def connect_ib(
    client_name: str,
    host: str = None,
    port: int = None,
    client_id: int = None,
    timeout: int = 10,
) -> IB:
    """Connect to TWS / IB Gateway and return an ``IB`` instance.

    Args:
        client_name: Key in ``CLIENT_IDS`` (e.g. ``"ib_sync"``).
        host: Override host (default ``DEFAULT_HOST``).
        port: Override port (default ``DEFAULT_GATEWAY_PORT``).
        client_id: Override the registry client-ID.
        timeout: Connection timeout in seconds.

    Returns:
        Connected ``IB`` instance.

    Raises:
        ValueError: If *client_name* is not in the registry
            **and** no *client_id* override is given.
        ConnectionRefusedError (or similar): If IB is not reachable.
    """
    if client_id is None:
        if client_name not in CLIENT_IDS:
            raise ValueError(
                f"Unknown client name '{client_name}'. "
                f"Known names: {sorted(CLIENT_IDS.keys())}"
            )
        client_id = CLIENT_IDS[client_name]

    ib = IB()
    ib.connect(
        host or DEFAULT_HOST,
        port or DEFAULT_GATEWAY_PORT,
        clientId=client_id,
        timeout=timeout,
    )
    return ib
