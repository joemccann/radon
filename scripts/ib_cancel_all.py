#!/usr/bin/env python3
"""Cancel EVERY working order at IB — the kill switch's teeth (REL-004).

Connects as the master client (clientId 0 — the only client IB allows to
see and cancel orders from every other clientId), issues reqGlobalCancel,
then polls the open-order book until it drains or the timeout lapses.
Never claims success while orders remain working.

stdout is reserved for the result JSON (subprocess contract); progress
goes to stderr.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from clients.ib_client import IBClient, DEFAULT_HOST, DEFAULT_GATEWAY_PORT

MASTER_CLIENT_ID = 0
DRAIN_TIMEOUT_SECONDS = 10.0
DRAIN_POLL_STEP_SECONDS = 0.5


def _progress(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def cancel_all(
    port: int = DEFAULT_GATEWAY_PORT,
    drain_timeout: float = DRAIN_TIMEOUT_SECONDS,
    poll_step: float = DRAIN_POLL_STEP_SECONDS,
) -> dict:
    client = IBClient()
    try:
        client.connect(host=DEFAULT_HOST, port=port, client_id=MASTER_CLIENT_ID)
    except Exception as exc:  # noqa: BLE001 — surface, don't crash-trace stdout
        return {"status": "error", "message": f"master connect failed: {exc}"}

    try:
        open_before = list(client.get_open_orders())
        if not open_before:
            _progress("No working orders — nothing to cancel")
            return {"status": "ok", "open_before": 0, "remaining": 0,
                    "remaining_order_ids": []}

        _progress(f"Cancelling {len(open_before)} working order(s) via reqGlobalCancel")
        client.global_cancel()

        deadline = time.monotonic() + drain_timeout
        remaining = open_before
        while True:
            remaining = list(client.get_open_orders())
            if not remaining or time.monotonic() >= deadline:
                break
            time.sleep(poll_step)

        remaining_ids = [t.order.orderId for t in remaining]
        status = "ok" if not remaining else "error"
        if remaining:
            _progress(f"{len(remaining)} order(s) STILL WORKING after global cancel: {remaining_ids}")
        return {
            "status": status,
            "open_before": len(open_before),
            "remaining": len(remaining),
            "remaining_order_ids": remaining_ids,
            **({"message": f"{len(remaining)} order(s) survived global cancel — "
                            f"verify in TWS: {remaining_ids}"} if remaining else {}),
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "message": f"global cancel failed: {exc}"}
    finally:
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001 — teardown
            pass


def main() -> int:
    result = cancel_all()
    print(json.dumps(result))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
