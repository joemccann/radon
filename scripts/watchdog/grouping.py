"""Root-cause-aware alert grouping for the watchdog.

When the FastAPI ``/health`` endpoint reports the IB Gateway session
is ``awaiting_2fa`` or ``unreachable``, IB-dependent services
(vcg-scan, cri-scan, orders-sync, portfolio-sync, fill-monitor,
exit-orders, journal-sync) tend to go stale in the same cycle. Firing
N separate Pushover alerts trains the operator to mute the noise.

Only failures that look IB-caused join the group: an IB-shaped
``last_error`` / check reason (connection, 2FA, gateway, timeout), or
a stale heartbeat with no latched writer error. A latched non-IB
error (execution fact conflict, reconcile drift) falls through to the
per-service path even when ``requires_ib`` is true.

This module groups those into ONE message when:

  1. ``/health.auth_state`` ∈ {``awaiting_2fa``, ``unreachable``}, AND
  2. ≥ 2 IB-caused services fired in the current cycle.

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

A delivered grouped page also stamps the cooldown row of every service
it NAMED. That roster is the armed set: while the grouped key is muted,
a service whose own row still allows firing was never in a delivered
page, so it is NOT absorbed — it falls through to the per-service path
and pages once. Without that, a service joining the outage after the
grouped page (exit-orders going stale 40 min into a 2FA window) was
returned as ``grouped_handled`` every cycle and never paged at all.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

from . import cooldown as cooldown_mod
from . import notify
from . import services as services_mod
from .check import CheckOutcome, error_text


log = logging.getLogger("watchdog.grouping")


GROUPED_ALERT_KEY = "ib-gateway-grouped"
GROUPED_ALERT_SEVERITY = "P1"
GROUPING_AUTH_STATES = frozenset({"awaiting_2fa", "unreachable"})
GROUPING_THRESHOLD = 2
HEALTH_URL = os.environ.get("RADON_HEALTH_URL", "http://127.0.0.1:8321/health")
HEALTH_TIMEOUT_S = 2.0


@dataclass(frozen=True)
class DispatchSummary:
    health_recorded: bool = False
    delivery_failed: bool = False
    suppressed_count: int = 0


def _cooldown_allows_fire(*, service: str, severity: str, now: datetime) -> bool:
    """Fail open for delivery when cooldown storage is unavailable."""
    try:
        return cooldown_mod.cooldown_allows_fire(
            service=service, severity=severity, now=now
        )
    except Exception as exc:  # noqa: BLE001 - storage must not block paging
        log.error(
            "cooldown read failed for %s/%s; delivering without suppression: %s",
            service,
            severity,
            exc,
        )
        return True


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


# Same production substrings ib_gateway uses to decide a last_error is
# "caused by IB Gateway being unreachable", plus 2FA wording writers emit.
# Kept local so watchdog does not import the FastAPI gateway module.
_IB_SHAPED_PATTERNS = (
    "failed to connect to ib",
    "127.0.0.1:4001",
    "timeouterror",
    "connection refused",
    "econnrefused",
    "connect call failed",
    "api connection failed",
    "ibconnectionerror",
    "make sure api port",
    "request timed out",
    "awaiting_2fa",
    "awaiting 2fa",
    "2fa",
)


def _parse_error_text(raw: object) -> str:
    if raw is None:
        return ""
    if isinstance(raw, dict):
        return error_text(raw)
    text = str(raw).strip()
    if not text:
        return ""
    if text[0] in "{[":
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text
        if isinstance(parsed, dict):
            return error_text(parsed) or text
    return text


def _failure_reason(outcome: CheckOutcome) -> str:
    reason = _parse_error_text(getattr(outcome, "last_error", None))
    if reason:
        return reason
    msg = outcome.message or ""
    prefix = "in error state:"
    if msg.lower().startswith(prefix):
        return msg[len(prefix):].strip()
    return ""


def _looks_like_ib_outage(reason: str) -> bool:
    hay = reason.lower()
    return any(pattern in hay for pattern in _IB_SHAPED_PATTERNS)


def _ib_caused_failure(outcome: CheckOutcome) -> bool:
    """True when this fired outcome should join an IB-outage page.

    A latched non-IB last_error (writer integrity, drift) is never
    sold as a gateway outage. Stale silence with no last_error still
    joins: that is the classic IB-down symptom.
    """
    if not _ib_dependent(outcome):
        return False
    reason = _failure_reason(outcome)
    if _looks_like_ib_outage(reason):
        return True
    if reason:
        return False
    return outcome.kind == "stale"


# A radon-api restart (e.g. a deploy) briefly reports auth_state=awaiting_2fa
# during pool warmup, before the recovery heartbeat confirms authentication
# (~seconds). That is NOT a real 2FA prompt — suppress the grouped page when the
# api restarted within this window so a deploy doesn't wake the operator.
API_WARMUP_SUPPRESS_S = 180

# R-056: a restart loop keeps ActiveEnterTimestamp perpetually <180s old (the
# pool-recovery os._exit(1) ladder cycles every ~185-200s), so the timestamp
# read alone suppresses every IB-outage page indefinitely. Bound it: after this
# many CONSECUTIVE warmup-suppressed cycles the grouped page fires anyway.
# A real warmup clears in seconds — one 5-min cycle; three in a row is a loop.
WARMUP_SUPPRESS_MAX_CONSECUTIVE = 3
_WARMUP_SUPPRESS_COUNTER_SERVICE = "ib-gateway-warmup-suppress"
_WARMUP_SUPPRESS_COUNTER_KIND = "warmup-suppression"


def _register_warmup_suppression(*, now: datetime) -> bool:
    """Count one warmup-suppressed cycle; True while under the ceiling.

    Counter storage failure fails toward paging (False) — a broken
    counter must never extend the suppression.
    """
    try:
        decision = cooldown_mod.record_failure_and_decide(
            service=_WARMUP_SUPPRESS_COUNTER_SERVICE,
            kind=_WARMUP_SUPPRESS_COUNTER_KIND,
            now=now,
        )
    except Exception as exc:  # noqa: BLE001 — storage must not extend suppression
        log.error("warmup suppression counter unavailable; not suppressing: %s", exc)
        return False
    return decision.consecutive_failures <= WARMUP_SUPPRESS_MAX_CONSECUTIVE


def _reset_warmup_suppression() -> None:
    """The ceiling counts CONSECUTIVE cycles: any cycle that does not
    warmup-suppress (healthy, or a real page fired) restarts the count,
    so ordinary deploys days apart never accumulate to a spurious page.
    """
    try:
        cooldown_mod.record_success(
            service=_WARMUP_SUPPRESS_COUNTER_SERVICE,
            kind=_WARMUP_SUPPRESS_COUNTER_KIND,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort reset
        log.warning("warmup suppression counter reset failed: %s", exc)


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
    head = f"IB Gateway {auth_state}: {n} services degraded ({listing})."
    if auth_state == "awaiting_2fa":
        return f"{head} Approve on phone or POST /ib/reset-backoff."
    return f"{head} Gateway is down. Recover with radon restart."


# ── public entry point ────────────────────────────────────────────

def dispatch_with_grouping(*, outcomes: Iterable[CheckOutcome], now: datetime) -> DispatchSummary:
    """Replacement for the per-outcome dispatch loop in __main__.

    Walks ``outcomes``, partitions IB-caused vs not, fetches
    /health to confirm the root cause, fires a single grouped Pushover
    if the threshold is met, and falls through to per-service dispatch
    for the rest. service_health rows write through normally for every
    outcome — only the Pushover channel is grouped.
    """
    fired = [o for o in outcomes if o.fired]
    if not fired:
        _reset_warmup_suppression()
        return DispatchSummary()

    ib_failing = [o for o in fired if _ib_caused_failure(o)]
    grouped_handled: set[str] = set()
    grouped_dispatcher_error: Optional[str] = None
    grouped_attempted = False
    delivery_attempted = False
    dispatcher_errors: list[str] = []
    suppressed_count = 0
    warmup_suppressed = False
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
            if (
                auth_state == "awaiting_2fa"
                and _api_recently_restarted(now)
                and _register_warmup_suppression(now=now)
            ):
                # Warmup transient from a radon-api restart (a deploy), not a
                # real 2FA prompt — the recovery heartbeat clears it in seconds.
                # Absorb the IB failures so they don't spam per-service, but send
                # NO push: there is nothing for the operator to approve. Past the
                # consecutive ceiling the api is restart-LOOPING, not warming up,
                # and the grouped page fires through the else branch.
                log.info(
                    "IB grouping suppressed — radon-api restarted <%ds ago; "
                    "awaiting_2fa is pool warmup, not a real 2FA prompt",
                    API_WARMUP_SUPPRESS_S,
                )
                grouped_handled = {o.service for o in ib_failing}
                warmup_suppressed = True
            else:
                grouped_handled, grouped_dispatcher_error, grouped_attempted = _dispatch_grouped(
                    ib_outcomes=ib_failing,
                    auth_state=auth_state,
                    now=now,
                )
                delivery_attempted = grouped_attempted
                if grouped_dispatcher_error:
                    dispatcher_errors.append(grouped_dispatcher_error)

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
        if outcome.severity and not _cooldown_allows_fire(
            service=outcome.service, severity=outcome.severity, now=outcome.now
        ):
            log.info("suppressed by cooldown (%s/%s)", outcome.service, outcome.severity)
            suppressed_count += 1
            continue
        dispatcher_error = notify.dispatch(outcome, record_health=False)
        delivery_attempted = True
        if dispatcher_error:
            dispatcher_errors.append(dispatcher_error)

    if not warmup_suppressed:
        _reset_warmup_suppression()

    # Suppressed outcomes need no dispatcher row. Attempted deliveries share
    # one aggregate write so a later success cannot erase an earlier failure.
    if not grouped_attempted and grouped_handled:
        suppressed_count += len(grouped_handled)

    if delivery_attempted:
        notify._write_dispatcher_health(
            now=now,
            dispatcher_error="; ".join(dispatcher_errors) or None,
        )

    return DispatchSummary(
        health_recorded=delivery_attempted,
        delivery_failed=bool(dispatcher_errors),
        suppressed_count=suppressed_count,
    )


def _covered_by_armed_page(outcome: CheckOutcome, *, now: datetime) -> bool:
    """True when this service's own cooldown row still suppresses it.

    That row is what a delivered grouped page stamps, so "suppressed"
    means "an already-delivered page named this service". An outcome
    with no severity has no per-service page to fall through to, so it
    stays absorbed.
    """
    if not outcome.severity:
        return True
    return not _cooldown_allows_fire(
        service=outcome.service, severity=outcome.severity, now=now
    )


def _absorb_only_services_already_paged(
    *, ib_outcomes: list[CheckOutcome], now: datetime
) -> tuple[set[str], Optional[str], bool]:
    """The grouped key is muted: absorb the cohort it already covers and
    let anything newer through.

    Returning every currently-failing IB service here swallowed the
    alert for services that STARTED failing after the grouped page was
    armed — for the whole muted window, up to the 24h re-arm ceiling.
    """
    covered = {o.service for o in ib_outcomes if _covered_by_armed_page(o, now=now)}
    newcomers = sorted({o.service for o in ib_outcomes} - covered)
    if newcomers:
        log.info(
            "grouped IB alert muted; %d service(s) began failing after it was "
            "armed — falling through to per-service: %s",
            len(newcomers),
            ",".join(newcomers),
        )
    else:
        log.info("grouped IB alert suppressed by cooldown")
    return covered, None, False


def _mark_grouped_page_delivered(
    *, ib_outcomes: list[CheckOutcome], now: datetime
) -> None:
    """Stamp the grouped key and every service the page NAMED.

    Those per-service rows are the armed roster. Keeping it in the rows
    the per-service gate already reads means one state model: no second
    store to migrate, no roster to lose on a deploy, and recovery
    (``cooldown.record_success``) re-arms each service exactly as it
    does for a per-service page.
    """
    notify._mark_notified_best_effort(
        service=GROUPED_ALERT_KEY,
        severity=GROUPED_ALERT_SEVERITY,
        now=now,
    )
    for outcome in ib_outcomes:
        if outcome.severity:
            notify._mark_notified_best_effort(
                service=outcome.service,
                severity=outcome.severity,
                now=now,
            )


def _dispatch_grouped(
    *,
    ib_outcomes: list[CheckOutcome],
    auth_state: str,
    now: datetime,
) -> tuple[set[str], Optional[str], bool]:
    """Send one grouped Pushover.

    Returns ``(suppressed_services, dispatcher_error, attempted)``.

    Cooldown applies to the grouped key. Inside the cooldown window we
    skip the push and return only the services the armed page already
    covers — newcomers are left for the per-service path.
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

    if not _cooldown_allows_fire(
        service=GROUPED_ALERT_KEY,
        severity=GROUPED_ALERT_SEVERITY,
        now=now,
    ):
        return _absorb_only_services_already_paged(ib_outcomes=ib_outcomes, now=now)

    message = _format_grouped_message(auth_state=auth_state, services=services)
    title = f"radon watchdog: IB Gateway {auth_state}"
    dispatcher_error = _emit_grouped_pushover(title=title, message=message)

    if dispatcher_error is None:
        _mark_grouped_page_delivered(ib_outcomes=ib_outcomes, now=now)
        if notify._pushover_creds():
            try:
                from . import pages
                pages.enqueue_delivered_page(
                    service=GROUPED_ALERT_KEY,
                    severity=GROUPED_ALERT_SEVERITY,
                    kind="grouped",
                    message=message,
                    now=now,
                )
            except Exception as exc:  # noqa: BLE001 — paging path stays up
                log.warning("grok page enqueue failed for grouped IB: %s", exc)
    return set(services), dispatcher_error, True


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
        tag=GROUPED_ALERT_KEY,  # so cancel_emergency clears it when IB recovers
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
