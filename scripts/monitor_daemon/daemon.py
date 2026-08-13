#!/usr/bin/env python3
"""
Monitor Daemon - Main daemon runner.

Manages multiple handlers with different intervals.
Supports state persistence and market hours awareness.
"""

import asyncio
import concurrent.futures
import contextlib
import logging
import os
import socket
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, Any, Iterator, List, Optional

from .handlers.base import BaseHandler
from utils.atomic_io import atomic_save, verified_load

logger = logging.getLogger(__name__)

# Default hard deadline per handler.run() (REL-008 / R-013). ib_insync has
# no request timeouts — an auth-wedged gateway used to hang the current
# handler and, because the loop is single-threaded, every handler after
# it. Handlers may override via a class-level ``max_runtime_seconds``.
DEFAULT_HANDLER_DEADLINE_SECONDS = 120.0


@contextlib.contextmanager
def _thread_event_loop() -> Iterator[None]:
    """Guarantee the calling thread owns an asyncio event loop.

    ib_insync resolves its loop lazily on every call (``util.getLoop``)
    and CPython auto-creates one only on the main thread, so REL-008's
    move of handlers onto worker threads killed every IB connect before
    it reached a socket.

    ``_run_handler_bounded`` spawns a private single-worker executor per
    run, so the calling thread never already owns a loop: install one
    unconditionally and close it on the way out, and no loop outlives the
    cycle that created it. Probing for an existing loop first would be
    worse than useless — ``nest_asyncio`` (a shipped ib_insync dependency)
    patches ``get_event_loop`` to auto-create rather than raise, which
    would turn the probe into a leak.

    Teardown is best-effort: a cleanup failure must never replace the
    handler's result or take down the daemon.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield
    finally:
        try:
            asyncio.set_event_loop(None)
            loop.close()
        except Exception as exc:  # noqa: BLE001 — cleanup must not mask the run
            logger.warning(f"handler event loop teardown failed: {exc}")


def _run_with_thread_event_loop(work: Callable[[], Any]) -> Any:
    """Run ``work`` on a thread guaranteed to own an asyncio event loop."""
    with _thread_event_loop():
        return work()


def sd_notify(state: str) -> None:
    """Best-effort systemd notify (READY=1 / WATCHDOG=1).

    No-op outside systemd (NOTIFY_SOCKET unset). Mirrors the relay's
    WatchdogSec liveness contract so an alive-but-dead daemon gets
    restarted instead of silently idling.
    """
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(addr)
            sock.send(state.encode())
    except Exception as exc:  # noqa: BLE001 — liveness ping must never crash the loop
        logger.debug(f"sd_notify failed: {exc}")


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
        # A handler that outlives its deadline remains registered here until
        # its worker actually finishes. Python cannot kill a running thread;
        # refusing a second worker is the safety barrier that prevents a late
        # live-order mutation from overlapping the next scheduled cycle.
        self._inflight_handlers: Dict[
            str,
            tuple[concurrent.futures.Future, concurrent.futures.ThreadPoolExecutor],
        ] = {}
    
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

        if market_hours or not getattr(handler, "requires_market_hours", True):
            return True

        return self._market_was_open_within_grace(handler)

    def _market_was_open_within_grace(self, handler: BaseHandler) -> bool:
        """True when the handler opted into a post-close grace window and the
        market-calendar source of truth says the session was still open
        ``post_close_grace_minutes`` ago.

        The probe instant (now - grace) routes through
        ``utils.market_calendar.market_state`` rather than a parallel clock
        computation, so weekends and holidays stay closed — grace can only
        extend a session that actually happened. Fails closed: any
        calendar/zoneinfo error reverts to the hard-close gate.
        """
        try:
            grace_minutes = getattr(handler, "post_close_grace_minutes", 0) or 0
            if grace_minutes <= 0:
                return False

            from zoneinfo import ZoneInfo
            from utils.market_calendar import market_state

            probe = datetime.now(ZoneInfo("America/New_York")) - timedelta(minutes=grace_minutes)
            return bool(market_state(probe)["is_open"])
        except Exception:
            return False
    
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
                result = self._run_handler_bounded(handler)
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

    def _run_handler_bounded(self, handler: BaseHandler) -> Dict[str, Any]:
        """Run one handler under a hard deadline (REL-008).

        A timed-out handler's thread is abandoned (Python threads cannot
        be killed) — the loop moves on, the cycle records an error so the
        watchdog pages, and the stray thread either finishes late or dies
        with its wedged socket. The alternative was the whole daemon
        wedging alive-but-dead with open positions unmanaged.

        Every failure mode degrades to one errored handler. ``BaseHandler.run``
        swallows its own exceptions, but the worker also brackets the run with
        an event loop, and that bracketing can fail on its own — an unhandled
        escape here would propagate past ``run_once`` into ``run_loop``, which
        catches only KeyboardInterrupt, killing the daemon REL-008 exists to
        keep alive.
        """
        prior = self._inflight_handlers.get(handler.name)
        if prior is not None:
            prior_future, prior_executor = prior
            if not prior_future.done():
                error = (
                    f"handler {handler.name} is still running after its prior timeout; "
                    "overlapping execution suppressed"
                )
                logger.error(error)
                return {"status": "error", "error": error, "handler": handler.name}
            # Observe the late result only to release executor resources. Its
            # timeout was already reported and must not be rewritten as green.
            try:
                prior_future.result()
            except Exception:
                pass
            prior_executor.shutdown(wait=False, cancel_futures=True)
            del self._inflight_handlers[handler.name]

        deadline = float(getattr(handler, "max_runtime_seconds",
                                 DEFAULT_HANDLER_DEADLINE_SECONDS))
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"handler-{handler.name}"
        )
        retained = False
        try:
            future = executor.submit(_run_with_thread_event_loop, handler.run)
            try:
                return future.result(timeout=deadline)
            except concurrent.futures.TimeoutError:
                self._inflight_handlers[handler.name] = (future, executor)
                retained = True
                error = f"handler {handler.name} timed out after {deadline:.0f}s"
                logger.error(error)
                try:
                    handler.record_cycle_health(
                        "error", error={"timeout_seconds": deadline}
                    )
                except Exception as exc:  # noqa: BLE001 — heartbeat is best-effort here
                    logger.warning(f"timeout heartbeat failed for {handler.name}: {exc}")
                return {"status": "error", "error": error, "handler": handler.name}
            except Exception as exc:  # noqa: BLE001 — one handler must never kill the daemon
                error = f"handler {handler.name} raised outside its own error handling: {exc}"
                logger.exception(error)
                try:
                    handler.record_cycle_health("error", error={"message": str(exc)})
                except Exception as heartbeat_exc:  # noqa: BLE001 — best-effort
                    logger.warning(f"crash heartbeat failed for {handler.name}: {heartbeat_exc}")
                return {"status": "error", "error": error, "handler": handler.name}
        finally:
            if not retained:
                executor.shutdown(wait=False, cancel_futures=True)
    
    def run_loop(self) -> None:
        """
        Run continuously until stopped.
        
        Checks handlers every loop_interval seconds.
        """
        self._running = True
        logger.info(f"Starting daemon loop (interval: {self.loop_interval}s)")
        sd_notify("READY=1")

        try:
            while self._running:
                # Liveness ping keyed to LOOP progress — a wedged loop stops
                # pinging and systemd's WatchdogSec restarts the unit.
                sd_notify("WATCHDOG=1")
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
        atomic_save(str(self.state_file), state)
        logger.debug(f"Saved state to {self.state_file}")

        # Best-effort dual-write to Turso (direct-to-cloud by default). Some
        # native-driver failures inherit directly from BaseException, so guard
        # telemetry without swallowing explicit process termination.
        try:
            from db.writer import upsert_daemon_state
        except BaseException as exc:  # noqa: BLE001 — native driver panics bypass Exception
            if isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                raise
            logger.warning("daemon_state telemetry import failed: %s", exc)
            return

        saved_at = state["saved_at"]
        for handler in self.handlers:
            try:
                handler_state = state["handlers"][handler.name]
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
            except BaseException as exc:  # noqa: BLE001 — native driver panics bypass Exception
                if isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                    raise
                logger.warning(
                    "daemon_state dual-write failed for %s: %s",
                    handler.name,
                    exc,
                )
    
    def load_state(self) -> None:
        """Load handler states from file.

        R-045: a corrupt file is preserved as ``.corrupt-<ts>`` before the
        daemon starts blank — it holds ``fill_monitor.known_orders`` (the
        fill-dedupe baselines) and silently discarding it destroyed the
        only forensic record.
        """
        if not self.state_file or not self.state_file.exists():
            return

        try:
            state = verified_load(str(self.state_file))
            handler_states = state.get("handlers", {})

            for handler in self.handlers:
                if handler.name in handler_states:
                    handler.set_state(handler_states[handler.name])
                    logger.debug(f"Restored state for {handler.name}")

        except Exception as e:
            logger.warning(f"Failed to load state: {e}")
            try:
                backup = self.state_file.with_name(
                    self.state_file.name
                    + f".corrupt-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
                )
                backup.write_text(self.state_file.read_text())
                logger.warning(f"Corrupt state preserved at {backup.name}")
            except Exception as backup_exc:  # noqa: BLE001
                logger.warning(f"Could not back up corrupt state: {backup_exc}")

    def install_signal_handlers(self) -> None:
        """SIGTERM (every deploy) must reach save_state — only
        KeyboardInterrupt did before (R-045), losing up to one cycle of
        handler state on every restart."""
        import signal as _signal

        def _terminate(signum, frame):
            logger.info("SIGTERM received — saving state and exiting")
            self._running = False
            try:
                self.save_state()
            finally:
                raise SystemExit(0)

        _signal.signal(_signal.SIGTERM, _terminate)
    
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
