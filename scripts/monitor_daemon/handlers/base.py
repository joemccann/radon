#!/usr/bin/env python3
"""
Base Handler - Abstract base class for all monitor handlers.

Each handler must implement:
- name: Unique identifier
- interval_seconds: How often to run
- execute(): The actual monitoring logic

Contract for ``execute()`` return values (enforced by ``run()``):

    {"status": "error", ...}  → SOFT failure; ``HandlerSoftFailure`` is
                                raised BEFORE ``last_run`` is touched.
                                The exception path keeps ``last_run`` at
                                its previous value so the daemon re-runs
                                the handler on the next cycle. Use
                                ``record_soft_failure`` (or a per-handler
                                analogue) to space retries via a short
                                embargo — never burn a daily slot on a
                                transient error.
    any other dict (or {})    → success / legit skip / domain-specific
                                signal (e.g. ``healthy``, ``healed``,
                                ``throttled``); ``last_run`` IS latched.
                                Handlers are free to use richer status
                                vocabularies for their own bookkeeping;
                                only ``error`` carries the "do not latch"
                                semantics.
    raise <any exception>     → hard failure; ``last_run`` NOT latched
                                (existing behavior preserved).

History: a single 60s IBKR Flex timeout cost ~7 days of cash-flow data on
2026-05-14 because ``cash_flow_sync.execute()`` RETURNED a status=error
dict, ``run()`` latched ``last_run``, and ``is_due`` refused to fire
again for 24h. The contract here makes that bug structurally impossible
for every handler — not just cash_flow_sync. See
``feedback_dont_latch_last_run_on_soft_failure.md``.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class HandlerSoftFailure(RuntimeError):
    """A handler reported a soft failure via ``{"status": "error", ...}``.

    Raised by ``BaseHandler.run()`` BEFORE ``last_run`` is latched so the
    daemon's next cycle re-evaluates ``is_due``. Carries the original
    payload so per-handler bookkeeping (throttle counters, embargo state)
    can be advanced from a single source of truth.
    """

    def __init__(self, message: str, payload: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.payload: Dict[str, Any] = payload or {}


# The single status that means "soft failure; do NOT latch last_run".
# Every other status (or no status at all) is treated as success.
_SOFT_FAILURE_STATUS = "error"


class BaseHandler(ABC):
    """Abstract base class for monitor daemon handlers."""

    # Subclasses must define these
    name: str = "base"
    interval_seconds: int = 60
    requires_market_hours: bool = True

    # Kebab-case service_health row this handler owns. When set, ``run()``
    # guarantees a heartbeat on EVERY cycle (DUR-14): ``ok`` after a
    # successful execute, ``error`` when execute raises / soft-fails /
    # returns a truthy ``result["error"]``. ``None`` opts out for handlers
    # with bespoke row semantics (replica_watchdog's event-driven writer).
    # The registration-completeness CI test collects these names and
    # asserts each has an explicit web/lib/serviceHealthWindows.ts entry.
    service_name: Optional[str] = None

    def __init__(self):
        self.last_run: Optional[datetime] = None
        self._enabled: bool = True
        self._cycle_health_recorded: bool = False
    
    @property
    def enabled(self) -> bool:
        return self._enabled
    
    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value
    
    def is_due(self) -> bool:
        """Check if this handler should run based on its interval."""
        if not self._enabled:
            return False
        
        if self.last_run is None:
            return True
        
        elapsed = datetime.now() - self.last_run
        return elapsed >= timedelta(seconds=self.interval_seconds)
    
    def run(self) -> Dict[str, Any]:
        """
        Execute the handler and wrap result with metadata.

        Enforces the return-value contract documented at module top:
        ``status='error'`` triggers a ``HandlerSoftFailure`` BEFORE
        ``last_run`` is latched so the daemon retries on the next cycle.

        Also guarantees the DUR-14 structural heartbeat: when
        ``service_name`` is declared and the handler hasn't recorded its
        own row this cycle (via ``record_cycle_health``), an ``ok`` or
        ``error`` service_health row is written here — a handler can no
        longer ship without heartbeat discipline.

        Returns:
            Dict with status, timestamp, and data from execute().
        """
        start_time = datetime.now()
        started_at = self._utc_now_iso()
        self._cycle_health_recorded = False

        try:
            result = self.execute()
            self._enforce_return_contract(result)
            self.last_run = datetime.now()
            self._ensure_cycle_heartbeat(result, started_at=started_at)

            elapsed_ms = (self.last_run - start_time).total_seconds() * 1000

            return {
                "status": "ok",
                "timestamp": self.last_run.isoformat(),
                "elapsed_ms": round(elapsed_ms, 2),
                "data": result
            }
        except Exception as e:
            logger.exception(f"Handler {self.name} failed: {e}")
            if not self._cycle_health_recorded:
                self.record_cycle_health(
                    "error", started_at=started_at, error={"message": str(e)}
                )
            return {
                "status": "error",
                "timestamp": datetime.now().isoformat(),
                "error": str(e),
                "data": None
            }

    def record_cycle_health(
        self,
        state: str,
        *,
        started_at: Optional[str] = None,
        finished_at: Optional[str] = None,
        error: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Best-effort service_health write for THIS handler's row.

        Marks the cycle recorded so ``run()`` never overwrites a richer
        row the handler wrote itself (e.g. cash_flow_sync's throttle
        metadata) with a structural ``ok``. No-op when ``service_name``
        is None; a DB failure is logged, never raised.
        """
        self._cycle_health_recorded = True
        if not self.service_name:
            return
        try:
            from db.writer import record_service_health  # noqa: PLC0415 — lazy; libsql optional

            record_service_health(
                self.service_name,
                state,
                started_at=started_at,
                finished_at=finished_at or self._utc_now_iso(),
                error=error,
            )
        except Exception as exc:  # noqa: BLE001 — heartbeat must never kill the cycle
            logger.warning("record_service_health(%s) failed: %s", self.service_name, exc)

    def _ensure_cycle_heartbeat(self, result: Any, *, started_at: str) -> None:
        """Structural heartbeat after a successful execute. ``result["error"]``
        (the swallowed-failure convention from fill_monitor et al) is this
        writer's OWN outcome and surfaces as state=error."""
        if self._cycle_health_recorded:
            return
        swallowed = result.get("error") if isinstance(result, dict) else None
        if swallowed:
            self.record_cycle_health(
                "error", started_at=started_at, error={"message": str(swallowed)}
            )
        else:
            self.record_cycle_health("ok", started_at=started_at)

    @staticmethod
    def _utc_now_iso() -> str:
        from datetime import timezone as _tz

        return datetime.now(_tz.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _enforce_return_contract(result: Any) -> None:
        """Raise ``HandlerSoftFailure`` if ``result`` signals a soft failure.

        Only ``{"status": "error", ...}`` triggers the no-latch path —
        every other shape (no status, ``ok``, ``skip``, or any
        domain-specific string a handler defines for its own bookkeeping,
        like ``healthy`` / ``healed`` / ``throttled``) is treated as
        success and latches ``last_run`` normally.
        """
        if not isinstance(result, dict):
            return
        if result.get("status") != _SOFT_FAILURE_STATUS:
            return
        message = str(result.get("error") or result.get("message") or "soft failure")
        raise HandlerSoftFailure(message, payload=result)
    
    @abstractmethod
    def execute(self) -> Dict[str, Any]:
        """
        Perform the handler's monitoring task.
        
        Subclasses must implement this method.
        
        Returns:
            Dict with handler-specific results
        """
        pass
    
    def get_state(self) -> Dict[str, Any]:
        """
        Get serializable state for persistence.
        
        Override in subclasses to include additional state.
        """
        return {
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "enabled": self._enabled
        }
    
    def set_state(self, state: Dict[str, Any]) -> None:
        """
        Restore handler state from persisted data.
        
        Override in subclasses to restore additional state.
        """
        last_run = state.get("last_run")
        if last_run:
            self.last_run = datetime.fromisoformat(last_run)
        else:
            self.last_run = None
        
        self._enabled = state.get("enabled", True)
