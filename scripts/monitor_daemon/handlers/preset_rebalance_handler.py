#!/usr/bin/env python3
"""
Preset Rebalance Handler — BaseHandler wrapper for the daemon.

Runs weekly (every 604,800 seconds). Checks S&P 500, NASDAQ 100,
and Russell 2000 for constituent changes and updates presets.

Does NOT require market hours — runs on Sundays.
"""

from typing import Dict, Any
from .base import BaseHandler

# 7 days in seconds
WEEKLY = 7 * 24 * 60 * 60


class PresetRebalanceHandler(BaseHandler):
    """Monitor index constituents and update presets on changes."""

    name = "preset_rebalance"
    interval_seconds = WEEKLY  # Run weekly
    requires_market_hours = False
    # Shipped for months with NO service_health row — exactly the silent-
    # writer class DUR-14 closes. BaseHandler.run() heartbeats this now.
    service_name = "preset-rebalance"

    def execute(self) -> Dict[str, Any]:
        # Import here to avoid circular / slow imports at daemon startup
        from monitor_daemon.handlers.preset_rebalance import execute as run_rebalance
        return run_rebalance()
