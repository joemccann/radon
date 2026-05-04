#!/usr/bin/env python3
"""Cash-flow sync — Monitor daemon handler.

Pulls IB CashTransaction rows (deposits, withdrawals, dividends, interest,
fees) from the NAV Flex Query and persists to the `cash_flows` Turso
table once per day. Cash transactions don't change intraday so daily is
sufficient.

Wired into monitor_daemon via scripts/monitor_daemon/run.py:create_daemon().
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

from monitor_daemon.handlers.base import BaseHandler

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# Run once per day (86400s)
CHECK_INTERVAL = 86400


class CashFlowSyncHandler(BaseHandler):
    """Run scripts/cash_flow_sync.py daily to refresh cash transactions."""

    name = "cash_flow_sync"
    interval_seconds = CHECK_INTERVAL
    requires_market_hours = False

    def execute(self) -> Dict[str, Any]:
        if not os.environ.get("IB_FLEX_TOKEN") or not os.environ.get("IB_FLEX_NAV_QUERY_ID"):
            return {"status": "skip", "reason": "IB_FLEX_TOKEN / IB_FLEX_NAV_QUERY_ID not configured"}

        script = PROJECT_ROOT / "scripts" / "cash_flow_sync.py"
        if not script.exists():
            return {"status": "error", "error": f"script not found: {script}"}

        try:
            result = subprocess.run(
                [sys.executable, "-m", "scripts.cash_flow_sync"],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=180,  # Flex Query polling can take ~60s
            )
        except subprocess.TimeoutExpired:
            return {"status": "error", "error": "cash_flow_sync timed out after 180s"}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

        if result.returncode != 0:
            tail = (result.stderr or result.stdout or "").splitlines()[-3:]
            logger.warning("cash_flow_sync failed: %s", " | ".join(tail))
            return {"status": "error", "error": " | ".join(tail), "returncode": result.returncode}

        # Last line of stdout is "Synced N cash flows. Breakdown: {...}"
        last_line = (result.stdout or "").strip().splitlines()[-1] if result.stdout else ""
        return {"status": "ok", "summary": last_line}
