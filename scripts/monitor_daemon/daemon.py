#!/usr/bin/env python3
"""
Monitor Daemon - Main daemon runner.

Manages multiple handlers with different intervals.
Supports state persistence and market hours awareness.
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

from .handlers.base import BaseHandler

logger = logging.getLogger(__name__)


class MonitorDaemon:
    """
    Main daemon that orchestrates multiple monitoring handlers.
    
    Features:
    - Plugin architecture: register any handler implementing BaseHandler
    - Per-handler intervals: each handler runs on its own schedule
    - State persistence: handlers can save/restore state across restarts
    - Market hours awareness: can skip runs outside trading hours
    """
    
    # US market hours in ET
    MARKET_OPEN_HOUR = 9
    MARKET_OPEN_MINUTE = 30
    MARKET_CLOSE_HOUR = 16
    MARKET_CLOSE_MINUTE = 0
    
    def __init__(
        self,
        state_file: Optional[Path] = None,
        respect_market_hours: bool = True,
        loop_interval: int = 30  # seconds between run_once calls in loop
    ):
        self.handlers: List[BaseHandler] = []
        self.state_file = state_file
        self.respect_market_hours = respect_market_hours
        self.loop_interval = loop_interval
        self._running = False
    
    def register(self, handler: BaseHandler) -> None:
        """Register a handler with the daemon."""
        self.handlers.append(handler)
        logger.info(f"Registered handler: {handler.name} (interval: {handler.interval_seconds}s)")
    
    def _is_market_hours_time(self, hour: int, minute: int, weekday: int) -> bool:
        """
        Check if given time is within market hours.
        
        Args:
            hour: Hour (0-23)
            minute: Minute (0-59)
            weekday: Day of week (0=Monday, 6=Sunday)
        
        Returns:
            True if within market hours
        """
        # Weekend check
        if weekday >= 5:  # Saturday=5, Sunday=6
            return False
        
        # Convert to minutes since midnight for easier comparison
        current_mins = hour * 60 + minute
        open_mins = self.MARKET_OPEN_HOUR * 60 + self.MARKET_OPEN_MINUTE
        close_mins = self.MARKET_CLOSE_HOUR * 60 + self.MARKET_CLOSE_MINUTE
        
        return open_mins <= current_mins < close_mins
    
    def is_market_hours(self) -> bool:
        """Check if current time is within US market hours.

        Uses zoneinfo for proper DST handling. The previous implementation
        hardcoded a UTC-5 offset which silently shifted the market window
        by one hour every DST season — market-hours handlers (fill_monitor,
        journal_sync, exit_orders) would skip the first hour of EDT trading
        and run for an extra hour after the real close.
        """
        try:
            from zoneinfo import ZoneInfo
            et_now = datetime.now(ZoneInfo("America/New_York"))
        except Exception:
            # Fail-open: if zoneinfo / tzdata is unavailable on the host,
            # fall back to UTC-5 so handlers still run rather than going
            # silent. Better to run an hour late than not at all.
            from datetime import timezone, timedelta
            et_now = datetime.now(timezone.utc) + timedelta(hours=-5)

        return self._is_market_hours_time(
            et_now.hour,
            et_now.minute,
            et_now.weekday()
        )

    def _handler_can_run_now(self, handler: BaseHandler, market_hours: Optional[bool] = None) -> bool:
        """Return True when the handler is eligible to run in the current window."""
        if not handler.is_due():
            return False

        if not self.respect_market_hours:
            return True

        if market_hours is None:
            market_hours = self.is_market_hours()

        return market_hours or not getattr(handler, "requires_market_hours", True)
    
    def run_once(self, market_hours: Optional[bool] = None) -> Dict[str, Any]:
        """
        Run all due handlers once.
        
        Returns:
            Dict mapping handler names to their results
        """
        results = {}
        if market_hours is None and self.respect_market_hours:
            market_hours = self.is_market_hours()
        
        for handler in self.handlers:
            if self._handler_can_run_now(handler, market_hours=market_hours):
                logger.info(f"Running handler: {handler.name}")
                result = handler.run()
                results[handler.name] = result
                
                if result["status"] == "error":
                    logger.error(f"Handler {handler.name} error: {result.get('error')}")
            else:
                if handler.is_due():
                    logger.debug(f"Skipping handler outside market hours: {handler.name}")
                else:
                    logger.debug(f"Handler {handler.name} not due yet")
        
        # Save state after each run
        if self.state_file and results:
            self.save_state()
        
        return results
    
    def run_loop(self) -> None:
        """
        Run continuously until stopped.
        
        Checks handlers every loop_interval seconds.
        """
        self._running = True
        logger.info(f"Starting daemon loop (interval: {self.loop_interval}s)")
        
        try:
            while self._running:
                market_hours = self.is_market_hours() if self.respect_market_hours else None
                # Run handlers
                results = self.run_once(market_hours=market_hours)
                
                if results:
                    logger.info(f"Completed run: {list(results.keys())}")
                
                # Poll less aggressively when only off-hours handlers are eligible.
                if self.respect_market_hours and market_hours is False:
                    logger.debug("Outside market hours, sleeping...")
                    time.sleep(60)
                else:
                    time.sleep(self.loop_interval)
                
        except KeyboardInterrupt:
            logger.info("Daemon stopped by user")
            self._running = False
    
    def stop(self) -> None:
        """Stop the daemon loop."""
        self._running = False
    
    def save_state(self) -> None:
        """Save all handler states to file + dual-write to Turso daemon_state.

        Phase 4 of the Turso source-of-truth migration. Per-handler row
        in the daemon_state table replaces the monolithic JSON blob; the
        JSON file is still written for disaster-recovery fallback.
        """
        if not self.state_file:
            return

        state = {
            "saved_at": datetime.now().isoformat(),
            "handlers": {}
        }

        for handler in self.handlers:
            state["handlers"][handler.name] = handler.get_state()

        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state, indent=2))
        logger.debug(f"Saved state to {self.state_file}")

        # Best-effort dual-write to Turso (direct-to-cloud by default).
        try:
            from db.writer import upsert_daemon_state
            saved_at = state["saved_at"]
            for handler in self.handlers:
                handler_state = handler.get_state()
                # `last_run` shape varies per handler; flatten common fields.
                last_run = handler_state.get("last_run") or saved_at
                last_status = handler_state.get("last_status") or handler_state.get("status")
                last_error = handler_state.get("last_error") or handler_state.get("error")
                upsert_daemon_state(
                    handler.name,
                    last_run=last_run,
                    last_status=last_status,
                    last_error=last_error,
                )
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning(f"daemon_state dual-write failed: {exc}")
    
    def load_state(self) -> None:
        """Load handler states from file."""
        if not self.state_file or not self.state_file.exists():
            return
        
        try:
            state = json.loads(self.state_file.read_text())
            handler_states = state.get("handlers", {})
            
            for handler in self.handlers:
                if handler.name in handler_states:
                    handler.set_state(handler_states[handler.name])
                    logger.debug(f"Restored state for {handler.name}")
                    
        except Exception as e:
            logger.warning(f"Failed to load state: {e}")
    
    def status(self) -> Dict[str, Any]:
        """Get daemon status summary."""
        return {
            "handlers": [
                {
                    "name": h.name,
                    "enabled": h.enabled,
                    "interval": h.interval_seconds,
                    "last_run": h.last_run.isoformat() if h.last_run else None,
                    "is_due": h.is_due()
                }
                for h in self.handlers
            ],
            "market_hours": self.is_market_hours() if self.respect_market_hours else "N/A",
            "running": self._running
        }
