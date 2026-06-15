"""Root-cause-aware alert grouping for the watchdog.

When the FastAPI ``/health`` endpoint reports the IB Gateway session
is ``awaiting_2fa`` or ``unreachable``, the IB-dependent services
(vcg-scan, cri-scan, orders-sync, portfolio-sync, fill-monitor,
exit-orders, journal-sync) tend to go stale in the same cycle. Firing
N separate Pushover alerts trains the operator to mute the noise — the
single actionable signal is "approve the 2FA prompt on your phone."

This module groups those into ONE message when:

  1. ``/health.auth_state`` ∈ {``awaiting_2fa``, ``unreachable``}, AND
  2. ≥ 2 IB-dependent services fired in the current cycle.

Threshold-of-2 avoids over-reporting on a single transient blip; one
isolated stale on an IB service is more likely a one-off retry than a
gateway outage.

The ``service_health`` table is still written for each underlying
service's OWN row is the one to consult for downstream truth; the
meta-row ``watchdog-alerts`` reflects DISPATCHER HEALTH ONLY (see
``notify`` module docstring).

Discord is intentionally NOT touched here — a parallel agent is removing
Discord from the watchdog. Only Pushover is grouped.

Cooldown semantics: the grouped alert key is ``ib-gateway-grouped``
with severity ``P1``. ``cooldown.mark_notified`` + ``cooldown_allows_fire``
operate on this synthetic service name so repeated grouped fires
inside the 1h window are suppressed exactly like per-service alerts.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Iterable, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

from . import cooldown as cooldown_mod
from . import notify
from . import services as services_mod
from .check import CheckOutcome


log = logging.getLogger("watchdog.grouping")


GROUPED_ALERT_KEY = "ib-gateway-grouped"
GROUPED_ALERT_SEVERITY = "P1"
GROUPING_AUTH_STATES = frozenset({"awaiting_2fa", "unreachable"})
GROUPING_THRESHOLD = 2
HEALTH_URL = os.environ.get("RADON_HEALTH_URL", "http://127.0.0.1:8321/health")
HEALTH_TIMEOUT_S = 2.0


# ── /health probe ──────────────────────────────────────────────────

def fetch_health() -> dict:
    """Return ``{auth_state: <state>}`` from FastAPI ``/health``.

    Best-effort: any transport, HTTP, or JSON failure degrades to
    ``{auth_state: "unknown"}`` so callers can fall through to the
    per-service path. Tight timeout (2s) because we don't want a slow
    /health to delay alert dispatch on a 5-minute cadence.
    """
    try:
        req = urllib_request.Request(HEALTH_URL, method="GET")
        with urllib_request.urlopen(req, timeout=HEALTH_TIMEOUT_S) as resp:
            body = resp.read()
        data = json.loads(body or b"{}")
    except (urllib_error.URLError, urllib_error.HTTPError, OSError, ValueError, json.JSONDecodeError) as exc:
        log.warning("watchdog /health probe failed: %s", exc)
        return {"auth_state": "unknown"}
    # FastAPI shape: {"ib_gateway": {"auth_state": "..."}}
    ib_gateway = data.get("ib_gateway") if isinstance(data, dict) else None
    if isinstance(ib_gateway, dict):
        return {"auth_state": ib_gateway.get("auth_state") or "unknown"}
    # Some legacy shapes put auth_state at top level.
    if isinstance(data, dict) and "auth_state" in data:
        return {"auth_state": data.get("auth_state") or "unknown"}
    return {"auth_state": "unknown"}


# ── helpers ────────────────────────────────────────────────────────

def _ib_dependent(outcome: CheckOutcome) -> bool:
    return services_mod.requires_ib(outcome.service)


# A radon-api restart (e.g. a deploy) briefly reports auth_state=awaiting_2fa
# during pool warmup, before the recovery heartbeat confirms authentication
# (~seconds). That is NOT a real 2FA prompt — suppress the grouped page when the
# api restarted within this window so a deploy doesn't wake the operator.
API_WARMUP_SUPPRESS_S = 180


def _api_recently_restarted(now: datetime, threshold_s: int = API_WARMUP_SUPPRESS_S) -> bool:
    """True iff radon-api's last (re)start was within ``threshold_s`` seconds.

    Reads ``systemctl show radon-api.service -p ActiveEnterTimestamp``. Returns
    False on any error / non-systemd host (so the only effect is to NOT suppress
    — a genuine awaiting_2fa still pages).
    """
    import subprocess  # noqa: PLC0415 — lazy; only the VPS watchdog path needs it

    try:
        out = subprocess.run(
            ["systemctl", "show", "radon-api.service", "-p", "ActiveEnterTimestamp", "--value"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 — no systemd / not installed → don't suppress
        return False
    if not out:
        return False
    # systemd emits e.g. "Mon 2026-06-15 13:50:04 UTC" (hosts run TZ=UTC).
    try:
        from datetime import timezone

        parts = out.split()
        started = datetime.strptime(f"{parts[1]} {parts[2]}", "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except Exception:  # noqa: BLE001 — unparseable → don't suppress
        return False
    return 0 <= (now - started).total_seconds() <= threshold_s


def _format_grouped_message(*, auth_state: str, services: list[str]) -> str:
    n = len(services)
    listing = ", ".join(sorted(services))
    return (
        f"IB Gateway {auth_state} — {n} services degraded "
        f"({listing}). Approve on phone or POST /ib/reset-backoff."
    )


# ── public entry point ────────────────────────────────────────────

def dispatch_with_grouping(*, outcomes: Iterable[CheckOutcome], now: datetime) -> None:
    """Replacement for the per-outcome dispatch loop in __main__.

    Walks ``outcomes``, partitions IB-dependent vs not, fetches
    /health to confirm the root cause, fires a single grouped Pushover
    if the threshold is met, and falls through to per-service dispatch
    for the rest. service_health rows write through normally for every
    outcome — only the Pushover channel is grouped.
    """
    fired = [o for o in outcomes if o.fired]
    if not fired:
        return

    ib_failing = [o for o in fired if _ib_dependent(o)]
    non_ib_failing = [o for o in fired if not _ib_dependent(o)]

    grouped_handled: set[str] = set()
    grouped_dispatcher_error: Optional[str] = None
    if len(ib_failing) >= GROUPING_THRESHOLD:
        # fetch_health is best-effort but a raised exception (test-double
        # or future bug) must not abort the whole dispatch — fall through
        # to per-service alerts in that case.
        try:
            health_payload = fetch_health() or {}
        except Exception as exc:  # noqa: BLE001 — protect alert dispatch
            log.warning("fetch_health raised: %s — falling back to per-service", exc)
            health_payload = {"auth_state": "unknown"}
        auth_state = health_payload.get("auth_state") or "unknown"
        if auth_state in GROUPING_AUTH_STATES:
            if auth_state == "awaiting_2fa" and _api_recently_restarted(now):
                # Warmup transient from a radon-api restart (a deploy), not a
                # real 2FA prompt — the recovery heartbeat clears it in seconds.
                # Absorb the IB failures so they don't spam per-service, but send
                # NO push: there is nothing for the operator to approve.
                log.info(
                    "IB grouping suppressed — radon-api restarted <%ds ago; "
                    "awaiting_2fa is pool warmup, not a real 2FA prompt",
                    API_WARMUP_SUPPRESS_S,
                )
                grouped_handled = {o.service for o in ib_failing}
            else:
                grouped_handled, grouped_dispatcher_error = _dispatch_grouped(
                    ib_outcomes=ib_failing,
                    auth_state=auth_state,
                    now=now,
                )

    # Everything not absorbed into the grouped alert falls through to the
    # regular per-service path (cooldown gate + Pushover). The per-service
    # service_health row for the FAILING service has already been written
    # by the underlying writer (e.g. ib_sync, vcg_scan). watchdog-alerts is
    # NOT written per-outcome — see notify._write_dispatcher_health.
    for outcome in fired:
        if outcome.service in grouped_handled:
            # Subsumed into the grouped Pushover; nothing more to do.
            notify._log_alert_event(outcome)
            continue
        if outcome.severity and not cooldown_mod.cooldown_allows_fire(
            service=outcome.service, severity=outcome.severity, now=outcome.now
        ):
            log.info("suppressed by cooldown (%s/%s)", outcome.service, outcome.severity)
            continue
        notify.dispatch(outcome)

    # If grouping fired, emit a single dispatcher-health row reflecting
    # whether the grouped push succeeded (notify.dispatch handles its own
    # per-outcome row for the non-grouped fallback path).
    if grouped_handled:
        notify._write_dispatcher_health(now=now, dispatcher_error=grouped_dispatcher_error)

    # No-op for non_ib_failing — they're handled by the loop above.
    _ = non_ib_failing


def _dispatch_grouped(
    *,
    ib_outcomes: list[CheckOutcome],
    auth_state: str,
    now: datetime,
) -> tuple[set[str], Optional[str]]:
    """Send the single grouped Pushover; return ``(suppressed_services,
    dispatcher_error)``.

    Cooldown applies to the grouped key. If we're inside the cooldown
    window we skip the push but STILL return the suppression set —
    the grouped condition is active, we just don't spam the channel.
    """
    services = [o.service for o in ib_outcomes]

    log.info(
        "dispatched alert service=%s severity=%s kind=%s "
        "downstream_services=%s downstream_count=%d auth_state=%s",
        GROUPED_ALERT_KEY,
        GROUPED_ALERT_SEVERITY,
        "grouped",
        ",".join(sorted(services)),
        len(services),
        auth_state,
    )

    if not cooldown_mod.cooldown_allows_fire(
        service=GROUPED_ALERT_KEY,
        severity=GROUPED_ALERT_SEVERITY,
        now=now,
    ):
        log.info("grouped IB alert suppressed by cooldown")
        return set(services), None

    message = _format_grouped_message(auth_state=auth_state, services=services)
    title = f"radon watchdog: IB Gateway {auth_state}"
    dispatcher_error = _emit_grouped_pushover(title=title, message=message)

    cooldown_mod.mark_notified(
        service=GROUPED_ALERT_KEY,
        severity=GROUPED_ALERT_SEVERITY,
        now=now,
    )
    return set(services), dispatcher_error


def _emit_grouped_pushover(*, title: str, message: str) -> Optional[str]:
    """Return a dispatcher error string on failure, ``None`` on success
    (or when Pushover is unconfigured — absence of an external channel
    is not a dispatcher failure).
    """
    creds = notify._pushover_creds()
    if not creds:
        return None
    user, token = creds
    # Grouped IB-outage alerts are P1 — emergency priority with
    # retry/expire, same escalation as the per-service path (DUR-14).
    payload = notify.build_pushover_payload(
        user=user,
        token=token,
        title=title,
        message=message,
        severity=GROUPED_ALERT_SEVERITY,
    )
    try:
        status, body = notify._http_post(notify.PUSHOVER_API_URL, payload)
    except Exception as exc:  # noqa: BLE001 — channel transport failures must surface
        log.warning("grouped pushover transport failure: %s", exc)
        return f"pushover transport failed: {exc}"
    if status >= 400:
        log.warning("grouped pushover non-2xx (%s): %r", status, body[:200])
        return (
            f"pushover {status}: "
            f"{body[:200].decode('utf-8', 'replace').strip() or 'no body'}"
        )
    return None
