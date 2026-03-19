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


class FlexTokenCheck(BaseHandler):
    """Check IB Flex Web Service token expiry and fire reminder_days alerts."""

    name = "flex_token_check"
    interval_seconds = CHECK_INTERVAL

    def execute(self) -> Dict[str, Any]:
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
