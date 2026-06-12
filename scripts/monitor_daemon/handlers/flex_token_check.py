#!/usr/bin/env python3
"""
Flex Token Expiry Check — Monitor daemon handler.

Checks the IB Flex Web Service token TTL daily.
Fires reminders at configurable thresholds (default: 30, 14, 7, 1 days).
Writes reminder state to flex_token_config.json to avoid repeats.

Reads: data/flex_token_config.json
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from monitor_daemon.handlers.base import BaseHandler

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "data" / "flex_token_config.json"

# Run once per day (86400s)
CHECK_INTERVAL = 86400


def _dual_write_flex_state_to_app_config(config: Dict[str, Any], days_remaining: int) -> None:
    """Phase 4 — store flex token expiry telemetry in app_config k/v.

    Three keys: expires_at (ISO date), days_remaining (int), reminders_sent
    (JSON blob of which thresholds have already fired). Disk JSON remains
    canonical; this gives the UI a fast key lookup path.
    """
    try:
        from db.writer import upsert_app_config
    except ImportError:
        return
    try:
        if config.get("expires_at"):
            upsert_app_config("flex_token_expires_at", str(config["expires_at"]))
        upsert_app_config("flex_token_days_remaining", str(days_remaining))
        if config.get("reminders_sent"):
            upsert_app_config("flex_token_reminders_sent", json.dumps(config["reminders_sent"]))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"flex_token app_config dual-write failed: {exc}")


class FlexTokenCheck(BaseHandler):
    """Check IB Flex Web Service token expiry and fire reminder_days alerts."""

    name = "flex_token_check"
    interval_seconds = CHECK_INTERVAL
    requires_market_hours = False
    _SERVICE_NAME = "flex-token-check"

    def execute(self) -> Dict[str, Any]:
        """Wrap inner logic with service_health heartbeat (success+error)."""
        try:
            from db.writer import _now_iso, record_service_health  # type: ignore
        except Exception as exc:  # pragma: no cover — hosts without libsql
            logger.warning("service_health heartbeat unavailable: %s", exc)
            return self._execute_inner()

        started_at = _now_iso()
        try:
            result = self._execute_inner()
            result["events_pruned"] = self._prune_service_health_events()
        except Exception as exc:
            try:
                record_service_health(
                    self._SERVICE_NAME, "error",
                    started_at=started_at, finished_at=_now_iso(),
                    error={"message": str(exc)},
                )
            except Exception as inner:
                logger.warning("record_service_health(error) failed: %s", inner)
            raise

        try:
            record_service_health(
                self._SERVICE_NAME, "ok",
                started_at=started_at, finished_at=_now_iso(),
            )
        except Exception as exc:
            logger.warning("record_service_health failed: %s", exc)

        return result

    @staticmethod
    def _prune_service_health_events() -> int | None:
        """DUR-11: daily retention sweep of the append-only history table.

        Piggybacks on this handler because it is the existing daily,
        24/7 monitor-daemon slot. Import failures (hosts without libsql
        or an older db.writer) skip gracefully; DB errors propagate so
        BaseHandler retries next cycle instead of latching last_run.
        """
        try:
            from db.writer import prune_service_health_events
        except Exception as exc:  # pragma: no cover — hosts without libsql
            logger.warning("service_health_events prune unavailable: %s", exc)
            return None
        return prune_service_health_events()

    def _execute_inner(self) -> Dict[str, Any]:
        if not CONFIG_PATH.exists():
            return {"status": "skip", "reason": "flex_token_config.json not found"}

        with open(CONFIG_PATH) as f:
            config = json.load(f)

        expires_str = config.get("expires_at")
        if not expires_str:
            return {"status": "skip", "reason": "no expires_at in config"}

        # Parse expiry — handle both offset-aware and naive
        expires_at = datetime.fromisoformat(expires_str)
        now = datetime.now(timezone.utc)
        if expires_at.tzinfo is None:
            # Treat as UTC if no TZ
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        else:
            expires_at = expires_at.astimezone(timezone.utc)

        days_remaining = (expires_at - now).days
        reminder_days = config.get("reminder_days", [30, 14, 7, 1])
        reminders_sent = config.get("reminders_sent", {})
        renewal_url = config.get("renewal_url", "")
        breadcrumb = config.get("breadcrumb", "")

        # Determine if we should fire a reminder
        should_warn = False
        fired_reminder = None
        for threshold in sorted(reminder_days, reverse=True):
            key = str(threshold)
            if days_remaining <= threshold and key not in reminders_sent:
                should_warn = True
                fired_reminder = threshold
                # Record that we sent this reminder
                reminders_sent[key] = datetime.now(timezone.utc).isoformat()
                break

        # Persist updated reminders_sent
        if fired_reminder is not None:
            config["reminders_sent"] = reminders_sent
            with open(CONFIG_PATH, "w") as f:
                json.dump(config, f, indent=2)
                f.write("\n")

            logger.warning(
                f"⚠️ IB Flex Token expires in {days_remaining} days "
                f"(threshold: {fired_reminder}d). Renew at: {renewal_url}"
            )

        # Phase 4 dual-write — mirror config + computed days_remaining
        # into the app_config k/v store. Best-effort.
        _dual_write_flex_state_to_app_config(config, days_remaining)

        expired = days_remaining <= 0

        return {
            "days_remaining": days_remaining,
            "expires_at": expires_str,
            "should_warn": should_warn,
            "fired_reminder": fired_reminder,
            "expired": expired,
            "renewal_url": renewal_url,
            "breadcrumb": breadcrumb,
            "reminder_days": reminder_days,
        }
