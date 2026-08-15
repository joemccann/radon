"""UW daily-cap circuit breaker, shared by every writer that calls UW.

Unusual Whales enforces a 40k requests/day cap that resets at 20:00 ET. A
writer that lets `UWRateLimitError` escape exits non-zero, the systemd unit
alarm pages a P1, and the operator is woken for a condition that clears
itself at the reset. The contract instead is: keep the last snapshot, write a
`next_attempt_at` deadline the watchdog's error bucket embargoes on, and stop
calling UW until it lapses.

That logic was written for `skew`, copy-pasted into `oi-changes`, and read by
`skew2d` through a private accessor before it landed here. One copy now.

Each writer owns its own sidecar file, so an embargoed `skew` never mutes
`oi-changes`. Derived indicators read a parent's deadline with `deadline()`,
which never clears — only the owner consumes its own sidecar.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from clients.uw_client import UWRateLimitError

_ET = ZoneInfo("America/New_York")
DAILY_LIMIT_MARKER = "daily request limit"
DAILY_RESET_HOUR_ET = 20
BURST_RETRY = timedelta(minutes=5)

PathSource = Callable[[], Path]


def _utc_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def is_daily_quota(exc: BaseException) -> bool:
    """True only for the 24h cap — a burst 429 clears in minutes, not a day."""
    return isinstance(exc, UWRateLimitError) and DAILY_LIMIT_MARKER in str(exc).lower()


def retry_at(exc: BaseException, now: datetime) -> str:
    """When this writer may call UW again. Daily cap → next 20:00 ET reset."""
    if not is_daily_quota(exc):
        return _utc_iso(now + BURST_RETRY)
    local = now.astimezone(_ET)
    reset = local.replace(hour=DAILY_RESET_HOUR_ET, minute=0, second=0, microsecond=0)
    if local >= reset:
        reset += timedelta(days=1)
    return _utc_iso(reset)


def _load_writer() -> Any:
    from db import writer

    return writer


class UwEmbargo:
    """One writer's sidecar breaker.

    ``path_source`` is called on every access rather than resolved once, so a
    writer whose sidecar hangs off a module constant (``fetch_skew.SKEW_JSON``)
    stays relocatable by tests and by callers that repoint that constant.
    """

    def __init__(self, service: str, path_source: PathSource) -> None:
        self._service = service
        self._path_source = path_source

    @property
    def service(self) -> str:
        return self._service

    def path(self) -> Path:
        return self._path_source()

    # ── sidecar ───────────────────────────────────────────────────

    def deadline(self, now: datetime) -> Optional[datetime]:
        """Live deadline, or None. Read-only — never clears the sidecar."""
        stored = self._read()
        return stored if stored is not None and now < stored else None

    def active_until(self, now: datetime) -> Optional[str]:
        """Live deadline as an ISO string, consuming a lapsed sidecar."""
        stored = self._read()
        if stored is None:
            return None
        if now >= stored:
            self.clear()
            return None
        return _utc_iso(stored)

    def arm(self, next_attempt_at: str) -> None:
        path = self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"next_attempt_at": next_attempt_at}))

    def clear(self) -> None:
        self.path().unlink(missing_ok=True)

    def _read(self) -> Optional[datetime]:
        try:
            raw = json.loads(self.path().read_text()).get("next_attempt_at")
        except (OSError, ValueError, TypeError, AttributeError):
            return None
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    # ── telemetry ─────────────────────────────────────────────────

    def record_error(
        self, exc: BaseException, *, now: datetime, next_attempt_at: str
    ) -> None:
        """Heartbeat the embargo so the watchdog quiets after one alert."""
        message = str(exc).splitlines()[0].strip()[:300]
        try:
            writer = _load_writer()
            writer.ensure_no_replica_for_writers()
            writer.record_service_health(
                self._service,
                "error",
                finished_at=_utc_iso(now),
                error={"message": message, "next_attempt_at": next_attempt_at},
            )
        except Exception as exc_write:  # noqa: BLE001 — telemetry never masks the cycle
            print(
                f"[{self._service}] error heartbeat non-fatal: {exc_write}",
                file=sys.stderr,
            )
