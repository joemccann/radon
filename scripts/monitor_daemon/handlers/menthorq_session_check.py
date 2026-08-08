#!/usr/bin/env python3
"""MenthorQ dashboard session liveness — monitor daemon handler.

The options-exposure surface (/options/net-gex) authenticates against
dashboard.menthorq.io with a dedicated Playwright cookie jar. On
2026-08-07 that jar's ``cognito`` cookie was found to have expired on
07-27: Net GEX had been dark for EVERY ticker for 11 days and nothing
noticed, because the exposure path is on-demand and had no service_health
row, no freshness window, and no watchdog entry. Unit tests could not
catch it — they mock the provider; only a live-credential monitor can.

This is the monitoring twin of ``flex-token-check``: a cheap daily read of
the jar's own expiry metadata. No browser, no network — the failing
exposure path already proves how expensive discovery-by-request is
(~28s, two chromium launches, before it gives up).

Reads: data/menthorq_dashboard/menthorq_dashboard_storage_state.json
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from monitor_daemon.handlers.base import BaseHandler

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_STORAGE_STATE_PATH = (
    PROJECT_ROOT / "data" / "menthorq_dashboard" / "menthorq_dashboard_storage_state.json"
)

CHECK_INTERVAL = 86400  # daily, same slot as flex-token-check
WARN_DAYS = 3

# Only these cookies decide whether the dashboard still authenticates.
# The jar is full of analytics cookies (_hjSession, _uetsid, MR, __cf_bm)
# that expire constantly and mean nothing.
#
# The DURABLE ``__Secure-authjs.session-token`` (~30d, dashboard.menthorq.io)
# governs: it is the credential actually sent with dashboard requests. The
# ``cognito`` cookie is EPHEMERAL (~1h TTL, lives on the amazoncognito
# domain, never sent to the dashboard) and self-heals — the client's
# credential-based re-login (9a49bad6) re-mints it on next use. Governing
# on min(all auth cookies) false-alarmed daily: a jar minted minutes ago
# scored days_remaining=0 and any jar >1h old scored expired.
DURABLE_COOKIE_PREFIX = "__Secure-authjs.session-token"
AUTH_COOKIE_PREFIXES = ("cognito", DURABLE_COOKIE_PREFIX)


def _is_auth_cookie(name: str) -> bool:
    return any(name.startswith(prefix) for prefix in AUTH_COOKIE_PREFIXES)


def evaluate_session(
    jar_path: Path, now: datetime
) -> Dict[str, Any]:
    """Pure core: jar on disk -> session verdict. No browser, no network."""
    if not jar_path.is_file():
        return _dead("no dashboard session jar on disk", None)

    try:
        payload = json.loads(jar_path.read_text())
    except (OSError, ValueError) as exc:
        return _dead(f"dashboard session jar unreadable: {exc}", None)

    cookies = payload.get("cookies") if isinstance(payload, dict) else None
    if not isinstance(cookies, list):
        return _dead("dashboard session jar has no cookies", None)

    auth_cookies = [
        (cookie["name"], cookie["expires"])
        for cookie in cookies
        if isinstance(cookie, dict)
        and isinstance(cookie.get("name"), str)
        and _is_auth_cookie(cookie["name"])
        and isinstance(cookie.get("expires"), (int, float))
        and cookie["expires"] > 0
    ]
    if not auth_cookies:
        return _dead("dashboard session jar carries no auth cookie", None)

    # The DURABLE authjs credential governs when present: the ephemeral
    # cognito cookie self-heals via credential re-login and would false-alarm
    # daily (see AUTH_COOKIE_PREFIXES). Cognito-only jars fall back to the
    # cognito expiry — with no durable credential it is all the jar has.
    durable = [
        expires for name, expires in auth_cookies
        if name.startswith(DURABLE_COOKIE_PREFIX)
    ]
    governing = min(durable) if durable else min(
        expires for _, expires in auth_cookies
    )
    seconds_remaining = governing - now.timestamp()
    days_remaining = int(seconds_remaining // 86400)
    expires_at = datetime.fromtimestamp(governing, timezone.utc).isoformat()

    if seconds_remaining <= 0:
        return _dead(
            f"dashboard session expired {expires_at} — /options/net-gex is dark "
            "for every ticker until it is re-minted",
            days_remaining,
            expires_at=expires_at,
        )

    return {
        "expired": False,
        "should_warn": days_remaining <= WARN_DAYS,
        "days_remaining": days_remaining,
        "expires_at": expires_at,
        "message": (
            f"menthorq dashboard session expires in {days_remaining}d ({expires_at})"
        ),
    }


def _dead(
    message: str, days_remaining: Optional[int], *, expires_at: Optional[str] = None
) -> Dict[str, Any]:
    return {
        "expired": True,
        "should_warn": True,
        "days_remaining": days_remaining,
        "expires_at": expires_at,
        "message": f"menthorq dashboard session unavailable: {message}",
    }


class MenthorQSessionCheck(BaseHandler):
    """Publish the MenthorQ dashboard session's health once per day."""

    name = "menthorq_session_check"
    interval_seconds = CHECK_INTERVAL
    requires_market_hours = False
    service_name = "menthorq-session"

    def __init__(self) -> None:
        super().__init__()
        self._storage_state_path = DEFAULT_STORAGE_STATE_PATH

    def execute(self) -> Dict[str, Any]:
        started_at = self._utc_now_iso()
        verdict = evaluate_session(self._storage_state_path, datetime.now(timezone.utc))

        # A dead or dying session is a PERSISTENT condition, not a transient
        # failure: returning status="error" would trip HandlerSoftFailure,
        # skip the last_run latch, and re-run every 30s forever. Report the
        # finding through the row instead — the explicitly-allowed
        # CHECK-handler exception to writer-state-not-event-content.
        if verdict["expired"] or verdict["should_warn"]:
            logger.warning(verdict["message"])
            self.record_cycle_health(
                "error",
                started_at=started_at,
                error={
                    "message": verdict["message"],
                    "days_remaining": verdict["days_remaining"],
                    "expires_at": verdict["expires_at"],
                },
            )

        return verdict
