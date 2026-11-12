"""A preflight-embargo exit adopts the shared embargo, never a soft retry.

The subprocess exits EXIT_FLEX_PREFLIGHT_EMBARGO when the token-wide Flex
sidecar is already armed. `_failure_from_exit` maps that to
flex_class="preflight_embargo" and `_record_failure` has a branch that mirrors
the sidecar deadline, but `_classify_failure` folded the class into "soft", so
that branch never ran and each embargoed cycle spent a soft attempt instead.

No Flex requests, no Turso: the health write and the sidecar read are stubbed.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from monitor_daemon.handlers.cash_flow_sync import CashFlowSyncHandler, SyncFailure  # noqa: E402


def test_preflight_embargo_adopts_the_shared_deadline(monkeypatch):
    handler = CashFlowSyncHandler()
    deadline = (datetime.now(timezone.utc) + timedelta(days=6)).isoformat()
    monkeypatch.setattr(handler, "_shared_embargo_until", lambda: deadline)
    written: list[dict] = []
    monkeypatch.setattr(
        handler, "record_cycle_health", lambda state, **kw: written.append(kw["error"])
    )

    handler._record_failure(
        datetime.now(timezone.utc).isoformat(),
        "Flex token embargoed until the sidecar deadline",
        exc=SyncFailure("embargoed", flex_class="preflight_embargo", code="1025"),
    )

    assert written[-1]["class"] == "preflight_embargo"
    assert handler._backoff_state["blocked_until"] == deadline
    assert not handler._backoff_state.get("soft_attempts")
