#!/usr/bin/env python3
"""Replica WAL watchdog — Monitor daemon handler.

Detects when the Next.js embedded libsql replica (`data/replica.db`) has
fallen into a `WalConflict` death spiral after an unclean shutdown of
`radon-nextjs.service` and self-heals by stopping the service, deleting
the replica + its sidecar files, and restarting the service.

Symptom we look for: repeated `Error syncing database: WAL frame insert
conflict` lines in `journalctl -u radon-nextjs.service`. Once the replica
is corrupt, the same line repeats every sync cycle (60s) and the
dashboard silently serves stale data until someone notices.

Manual recipe (mirrored here):
    sudo systemctl stop radon-nextjs.service
    rm -f data/replica.db data/replica.db-wal data/replica.db-shm data/replica.db-info
    sudo systemctl start radon-nextjs.service

Wired into monitor_daemon via scripts/monitor_daemon/run.py:create_daemon().
"""
from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from monitor_daemon.handlers.base import BaseHandler

logger = logging.getLogger(__name__)


class ReplicaWatchdogHandler(BaseHandler):
    """Detect WalConflict death spirals on the Next.js replica and reset it."""

    name = "replica-watchdog"
    interval_seconds = 60
    requires_market_hours = False
    # Deliberate opt-OUT of the DUR-14 structural heartbeat: this writer
    # has bespoke ok/syncing/error row semantics (event-driven heal cycle)
    # and records its own row every cycle — a structural ok after execute
    # would clobber the syncing/error states it just wrote.
    service_name = None

    # Tunables (class constants so tests can override).
    SERVICE_NAME = "radon-nextjs.service"
    DATA_DIR = "/home/radon/radon/data"
    REPLICA_FILES = (
        "replica.db",
        "replica.db-wal",
        "replica.db-shm",
        "replica.db-info",
    )
    WAL_CONFLICT_TOKEN = "WalConflict"
    CONFLICT_THRESHOLD = 3  # >= this many in a 5-min window → heal
    JOURNAL_WINDOW = "5 minutes ago"
    THROTTLE_SECONDS = 30 * 60  # 30 minutes between heals
    SYSTEMCTL_BIN = "/usr/bin/systemctl"
    SUBPROCESS_TIMEOUT = 30
    JOURNALCTL_TIMEOUT = 10

    def __init__(self) -> None:
        super().__init__()
        self._last_heal_at: Optional[datetime] = None

    # ------------------------------------------------------------------
    # State persistence — survive daemon restarts so throttle is durable.
    # ------------------------------------------------------------------
    def get_state(self) -> Dict[str, Any]:
        state = super().get_state()
        state["last_heal_at"] = (
            self._last_heal_at.isoformat() if self._last_heal_at else None
        )
        return state

    def set_state(self, state: Dict[str, Any]) -> None:
        super().set_state(state)
        last_heal_at = state.get("last_heal_at")
        if last_heal_at:
            parsed = datetime.fromisoformat(last_heal_at)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            self._last_heal_at = parsed
        else:
            self._last_heal_at = None

    # ------------------------------------------------------------------
    # Entry point.
    # ------------------------------------------------------------------
    def execute(self) -> Dict[str, Any]:
        conflicts = self._count_recent_wal_conflicts()
        if conflicts < self.CONFLICT_THRESHOLD:
            # Heartbeat ok on every healthy cycle. The watchdog runs every
            # 60s but only writes service_health on heal events, so a
            # healthy steady state used to age out and flip the row to
            # stale at the 24h window. Heartbeat keeps the row fresh so
            # the banner only flags us when something is actually wrong.
            # See feedback_service_health_heartbeat.md.
            self._record_heartbeat(conflicts)
            return {"status": "healthy", "wal_conflicts_5m": conflicts}

        throttle_status = self._throttle_status()
        if throttle_status is not None:
            self._record_heartbeat(conflicts, throttle=throttle_status)
            return throttle_status

        return self._self_heal(conflicts)

    def _record_heartbeat(
        self,
        conflicts: int,
        *,
        throttle: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Best-effort `service_health=ok` write on healthy + throttled cycles.

        Failures are logged but never propagated — telemetry must not
        crash the watchdog itself.
        """
        from db.writer import _now_iso, record_service_health

        finished_at = _now_iso()
        try:
            record_service_health(
                "replica-watchdog",
                "ok",
                finished_at=finished_at,
                error={
                    "heartbeat_at": finished_at,
                    "wal_conflicts_5m": conflicts,
                    **({"throttle": throttle} if throttle else {}),
                },
            )
        except Exception as exc:  # noqa: BLE001 — best-effort telemetry
            logger.warning("record_service_health(heartbeat) failed: %s", exc)

    # ------------------------------------------------------------------
    # Helpers — single responsibility per method.
    # ------------------------------------------------------------------
    def _count_recent_wal_conflicts(self) -> int:
        try:
            result = subprocess.run(
                [
                    "journalctl",
                    "-u",
                    self.SERVICE_NAME,
                    "--since",
                    self.JOURNAL_WINDOW,
                    "--no-pager",
                ],
                capture_output=True,
                text=True,
                timeout=self.JOURNALCTL_TIMEOUT,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            logger.warning("journalctl unavailable for replica watchdog: %s", exc)
            return 0

        output = result.stdout or ""
        return output.count(self.WAL_CONFLICT_TOKEN)

    def _throttle_status(self) -> Optional[Dict[str, Any]]:
        """Return throttle payload if we're inside the cool-down, else None."""
        if self._last_heal_at is None:
            return None

        elapsed = datetime.now(timezone.utc) - self._last_heal_at
        if elapsed >= timedelta(seconds=self.THROTTLE_SECONDS):
            return None

        return {
            "status": "throttled",
            "since_last_heal_s": int(elapsed.total_seconds()),
        }

    def _self_heal(self, conflicts: int) -> Dict[str, Any]:
        from db.writer import _now_iso, record_service_health  # local import — keeps daemon import light

        started_at = _now_iso()
        try:
            record_service_health(
                "replica-watchdog", "syncing", started_at=started_at
            )
        except Exception as exc:  # noqa: BLE001 — best-effort telemetry
            logger.warning("record_service_health(syncing) failed: %s", exc)

        try:
            self._stop_service()
            self._delete_replica_files()
            self._start_service()
        except subprocess.CalledProcessError as exc:
            return self._record_heal_failure(exc, conflicts)
        except subprocess.TimeoutExpired as exc:
            return self._record_heal_failure(exc, conflicts)

        finished_at = _now_iso()
        self._last_heal_at = datetime.now(timezone.utc)

        try:
            record_service_health(
                "replica-watchdog",
                "ok",
                finished_at=finished_at,
                error={
                    "healed_at": finished_at,
                    "wal_conflicts_observed": conflicts,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("record_service_health(ok) failed: %s", exc)

        logger.warning(
            "Replica watchdog healed %s after %d WalConflicts in 5m",
            self.SERVICE_NAME,
            conflicts,
        )
        return {"status": "healed", "wal_conflicts_5m": conflicts}

    def _stop_service(self) -> None:
        subprocess.run(
            ["sudo", self.SYSTEMCTL_BIN, "stop", self.SERVICE_NAME],
            check=True,
            timeout=self.SUBPROCESS_TIMEOUT,
        )

    def _start_service(self) -> None:
        subprocess.run(
            ["sudo", self.SYSTEMCTL_BIN, "start", self.SERVICE_NAME],
            check=True,
            timeout=self.SUBPROCESS_TIMEOUT,
        )

    def _delete_replica_files(self) -> None:
        for filename in self.REPLICA_FILES:
            path = os.path.join(self.DATA_DIR, filename)
            try:
                os.unlink(path)
                logger.info("Removed corrupt replica file: %s", path)
            except FileNotFoundError:
                continue

    def _record_heal_failure(
        self, exc: BaseException, conflicts: int
    ) -> Dict[str, Any]:
        from db.writer import record_service_health

        message = str(exc)
        logger.error("Replica watchdog heal failed: %s", message)

        try:
            record_service_health(
                "replica-watchdog",
                "error",
                error={"error": message, "wal_conflicts_observed": conflicts},
            )
        except Exception as inner:  # noqa: BLE001
            logger.warning("record_service_health(error) failed: %s", inner)

        # Throttle deliberately NOT advanced — next cycle should retry.
        return {"status": "heal_failed", "error": message}
