#!/usr/bin/env python3
"""MenthorQ dashboard LIVE login probe — monitor daemon handler.

The metadata-only ``menthorq_session_check`` cannot see a broken re-login
chain: the 2026-08-07 incident (AWS WAF blocking Playwright's
HeadlessChrome UA) left the jar's durable authjs cookies looking healthy
while every actual re-login silently failed for 11 days. This probe
closes that blind spot by exercising the real serving path once a day:
GET /options/exposure on the local FastAPI, whose provider client owns
the credential-based re-login (9a49bad6).

Loopback requests carry no forwarded headers, so the probe rides the
trusted-local auth bypass. Cost on success is one exposure fetch — the
jar is normally >1h past its cognito mint, so the client performs the
full credential re-login (~15-20s on the VPS), which is exactly the
chain being verified.

Verdicts:
- 200 with a payload            -> healthy (DUR-14 auto-ok row).
- 503 naming authentication     -> broken login chain: PERSISTENT ->
                                   error row + latch (the CHECK-handler
                                   exception to writer-state semantics;
                                   never re-launch chromium every 30s
                                   against a broken chain).
- anything else                 -> transient: soft failure (no latch),
                                   retries spaced by a 5-min embargo,
                                   budget-capped per UTC day; exhaustion
                                   writes the error row and burns the
                                   daily slot so the probe can never spin.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import requests

from monitor_daemon.handlers.base import BaseHandler, HandlerSoftFailure

logger = logging.getLogger(__name__)

PROBE_URL = "http://127.0.0.1:8321/options/exposure/SPX"
PROBE_PARAMS = {"frequency": "eod"}
# Connect fast-fails; read covers the full credential login chain
# (~15s measured on the VPS, 60s client-side login timeout) plus the
# failing path's two chromium launches. The daemon loop is sequential,
# so this is also the worst-case stall the probe can inflict on it.
PROBE_TIMEOUT = (5, 90)

CHECK_INTERVAL = 86400  # daily, same slot as menthorq_session_check
RETRY_EMBARGO_SECONDS = 300
MAX_TRANSIENT_ATTEMPTS_PER_DAY = 3


def _now_utc() -> datetime:
    """Indirection to make `datetime.now(tz=UTC)` patchable in tests."""
    return datetime.now(timezone.utc)


def _is_auth_failure(status_code: int, detail: str) -> bool:
    return status_code == 503 and "authentication" in detail.lower()


class MenthorQLoginProbe(BaseHandler):
    """Prove the MenthorQ credential re-login chain works, once per day."""

    name = "menthorq_login_probe"
    interval_seconds = CHECK_INTERVAL
    requires_market_hours = False
    service_name = "menthorq-login-probe"

    def __init__(self) -> None:
        super().__init__()
        self._transient_attempts: int = 0
        self._transient_attempt_date: Optional[str] = None
        self._retry_after: Optional[datetime] = None

    # ------------------------------------------------------------------
    # Cadence — daily interval plus the transient-retry embargo.
    # ------------------------------------------------------------------
    def is_due(self) -> bool:
        if not super().is_due():
            return False
        if self._retry_after is not None and _now_utc() < self._retry_after:
            return False
        return True

    def execute(self) -> Dict[str, Any]:
        started_at = self._utc_now_iso()
        try:
            response = requests.get(
                PROBE_URL, params=PROBE_PARAMS, timeout=PROBE_TIMEOUT
            )
        except requests.RequestException as exc:
            return self._transient(f"probe request failed: {exc}", started_at)

        if response.status_code == 200:
            payload = self._payload_of(response)
            if isinstance(payload, dict) and payload:
                self._reset_transient_bookkeeping()
                return {"status": "healthy", "symbol": payload.get("symbol")}
            return self._transient("probe returned an empty payload", started_at)

        detail = self._detail_of(response)
        if _is_auth_failure(response.status_code, detail):
            return self._broken_login(detail, started_at)
        return self._transient(
            f"probe got HTTP {response.status_code}: {detail}", started_at
        )

    # ------------------------------------------------------------------
    # Verdicts
    # ------------------------------------------------------------------
    def _broken_login(self, detail: str, started_at: str) -> Dict[str, Any]:
        message = (
            "menthorq credential re-login chain is broken: "
            f"{detail} — /options/net-gex re-mints will fail until fixed"
        )
        logger.warning(message)
        self._reset_transient_bookkeeping()
        self.record_cycle_health(
            "error", started_at=started_at, error={"message": message}
        )
        return {"status": "auth_broken", "detail": detail}

    def _transient(self, message: str, started_at: str) -> Dict[str, Any]:
        today = _now_utc().strftime("%Y-%m-%d")
        if self._transient_attempt_date != today:
            self._transient_attempt_date = today
            self._transient_attempts = 0
        self._transient_attempts += 1

        if self._transient_attempts >= MAX_TRANSIENT_ATTEMPTS_PER_DAY:
            exhausted = (
                f"menthorq login probe failed {self._transient_attempts}x today; "
                f"last: {message}"
            )
            logger.warning(exhausted)
            self.record_cycle_health(
                "error", started_at=started_at, error={"message": exhausted}
            )
            return {"status": "probe_failed", "error_detail": message}

        self._retry_after = _now_utc() + timedelta(seconds=RETRY_EMBARGO_SECONDS)
        logger.warning("menthorq login probe soft failure (retrying): %s", message)
        raise HandlerSoftFailure(message)

    def _reset_transient_bookkeeping(self) -> None:
        self._transient_attempts = 0
        self._transient_attempt_date = None
        self._retry_after = None

    # ------------------------------------------------------------------
    # Response parsing — never let a weird body add a new failure mode.
    # ------------------------------------------------------------------
    @staticmethod
    def _payload_of(response: Any) -> Any:
        try:
            return response.json()
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _detail_of(response: Any) -> str:
        try:
            body = response.json()
            if isinstance(body, dict):
                return str(body.get("detail") or body)
            return str(body)
        except Exception:  # noqa: BLE001
            return "<unparseable body>"

    # ------------------------------------------------------------------
    # State persistence — embargo + budget survive daemon restarts.
    # ------------------------------------------------------------------
    def get_state(self) -> Dict[str, Any]:
        state = super().get_state()
        state["transient_attempts"] = self._transient_attempts
        state["transient_attempt_date"] = self._transient_attempt_date
        state["retry_after"] = (
            self._retry_after.isoformat() if self._retry_after else None
        )
        return state

    def set_state(self, state: Dict[str, Any]) -> None:
        super().set_state(state)
        self._transient_attempts = int(state.get("transient_attempts") or 0)
        self._transient_attempt_date = state.get("transient_attempt_date")
        retry_after = state.get("retry_after")
        self._retry_after = (
            datetime.fromisoformat(retry_after) if retry_after else None
        )
