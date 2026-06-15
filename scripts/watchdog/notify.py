"""Notification dispatcher.

This module routes ``CheckOutcome`` alerts to enabled channels
(Pushover today; service_health table always-on heartbeat).

Service-health row contract (dispatcher health, not alert content)
==================================================================

The ``service_health`` row named ``watchdog-alerts`` reflects DISPATCHER
HEALTH ONLY — i.e. "can the notifier reach its channels and persist
its own bookkeeping." It does NOT mirror the kind or severity of the
last alert dispatched.

Why: a banner row whose state mirrors the last alert latches at
``error`` until another alert with different severity fires. If the
downstream service recovers between watchdog cycles (the common case),
no ``healed`` event fires and the row stays ``error`` indefinitely.
The dashboard reads ``last_error`` and surfaces stale alert detail as
a current outage — long after recovery. Same anti-pattern as
``feedback_banner_only_actionable.md``.

Where to find the alert event itself: ``logging.getLogger("watchdog.notify")``
emits an INFO line on every dispatch with structured fields
(service, severity, kind, message). On Hetzner that lands in
``journalctl -u radon-watchdog-*.service`` and on the laptop in stderr.

The row only flips to ``state=error`` when:
  - Pushover returns a non-2xx code (channel transport failure)
  - ``record_service_health`` itself raises (DB write failure)

In both cases ``last_error`` carries a dispatcher-specific string
(``"pushover 500: …"``, ``"db write failed: …"``) so the banner
surfaces a real, actionable notifier outage — not stale alert history.

Severity routing for the Pushover channel (DUR-14 escalation)
==============================================================

 * P1 → Pushover EMERGENCY (priority=2, retry/expire) — repeats until
   the operator acknowledges the push.
 * P2 / P3 → service_health heartbeat + once-daily digest push (batched
   in ``DIGEST_STATE_PATH``, flushed by the daily watchdog bucket via
   :func:`flush_daily_digest`).

The Resend email channel was DELETED 2026-06-12: ``enabled_channels()``
registered it but no emitter ever existed (the startup log claimed a
channel that did not exist), and no RESEND_API_KEY is present in any
environment (VPS radon-cloud/.env, unit files, laptop .env / web/.env /
~/.zshrc — all checked, zero matches).

If no external channel is configured a one-line warning prints on
startup so the operator notices alerts will only land in the table.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

from .check import CheckOutcome
from . import cooldown as cooldown_mod


log = logging.getLogger("watchdog.notify")

_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"
# Pushover emergency-priority contract: retry >= 30s, expire <= 10800s.
# https://pushover.net/api#priority — the push re-alerts every `retry`
# seconds until acknowledged or `expire` seconds elapse.
PUSHOVER_EMERGENCY_RETRY_SECS = 60
PUSHOVER_EMERGENCY_EXPIRE_SECS = 3600


# ── env-driven channel registry ─────────────────────────────────────

def _pushover_creds() -> Optional[tuple[str, str]]:
    user = os.environ.get("PUSHOVER_USER")
    token = os.environ.get("PUSHOVER_TOKEN")
    return (user, token) if user and token else None


def enabled_channels() -> set[str]:
    channels = {"service_health"}
    if _pushover_creds():
        channels.add("pushover")
    return channels


def log_startup_warning() -> None:
    channels = enabled_channels()
    external = channels - {"service_health"}
    if not external:
        sys.stderr.write(
            "[watchdog] warning: no external notification channel configured "
            "(set PUSHOVER_USER+PUSHOVER_TOKEN). "
            "Alerts will only land in the service_health table.\n"
        )
    else:
        sys.stderr.write(f"[watchdog] channels enabled: {sorted(channels)}\n")


# ── HTTP seam (mocked in tests) ─────────────────────────────────────

def _http_post(url: str, payload: dict, headers: Optional[dict] = None) -> tuple[int, bytes]:
    """Thin urllib wrapper so tests can monkeypatch a single function.
    Returns (status_code, body). Raises on transport-level failure.
    """
    data = json.dumps(payload).encode("utf-8")
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib_request.Request(url, data=data, headers=req_headers, method="POST")
    try:
        with urllib_request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib_error.HTTPError as exc:
        return exc.code, exc.read() if hasattr(exc, "read") else b""


# ── per-channel emitters ────────────────────────────────────────────

_EMOJI = {"P1": "🚨", "P2": "❌", "P3": "⚠️"}


def _format_summary(outcome: CheckOutcome) -> str:
    emoji = _EMOJI.get(outcome.severity or "", "")
    sev = outcome.severity or ""
    return f"{emoji} [{sev}] `{outcome.service}` — {outcome.message}"


def _log_alert_event(outcome: CheckOutcome) -> None:
    """Emit a structured INFO line to journalctl/stderr so the alert
    event remains discoverable after we stopped recording it in
    ``service_health.last_error``. Keep this terse — operators grep
    journalctl with ``service`` + ``severity`` keys.
    """
    log.info(
        "dispatched alert service=%s severity=%s kind=%s consecutive_failures=%d message=%s",
        outcome.service,
        outcome.severity or "",
        outcome.kind,
        outcome.consecutive_failures,
        outcome.message,
    )


def build_pushover_payload(*, user: str, token: str, title: str, message: str,
                           severity: Optional[str], tag: Optional[str] = None) -> dict:
    """Single source for severity → Pushover priority mapping. P1 is
    EMERGENCY (priority=2 + retry/expire, repeats until acknowledged);
    everything else (digest pushes) is normal priority. Shared with the
    grouped IB-outage path in ``grouping.py``.

    ``tag`` stamps emergency pushes so they can be cancelled by tag once the
    condition recovers (``cancel_emergency``) — otherwise an emergency keeps
    re-alerting every 60s for the full hour even after the outage clears."""
    payload = {"token": token, "user": user, "title": title, "message": message}
    if severity == "P1":
        payload["priority"] = 2
        payload["retry"] = PUSHOVER_EMERGENCY_RETRY_SECS
        payload["expire"] = PUSHOVER_EMERGENCY_EXPIRE_SECS
        if tag:
            payload["tag"] = tag
    else:
        payload["priority"] = 0
    return payload


PUSHOVER_CANCEL_BY_TAG_URL = "https://api.pushover.net/1/receipts/cancel_by_tag/{tag}.json"


def cancel_emergency(tag: str) -> Optional[str]:
    """Cancel any unacknowledged P1 emergency pushes carrying ``tag`` so a
    recovered/transient alert stops re-alerting before its 1h expire. Returns a
    dispatcher-error string on failure, ``None`` on success / no creds."""
    creds = _pushover_creds()
    if not creds:
        return None
    _user, token = creds
    from urllib.parse import quote

    url = PUSHOVER_CANCEL_BY_TAG_URL.format(tag=quote(str(tag), safe=""))
    try:
        status, body = _http_post(url, {"token": token})
    except Exception as exc:  # noqa: BLE001
        log.warning("pushover cancel_by_tag transport failure: %s", exc)
        return f"pushover cancel transport failed: {exc}"
    if status >= 400:
        log.warning("pushover cancel non-2xx (%s): %r", status, body[:200])
        return f"pushover cancel {status}"
    log.info("cancelled emergency push(es) tag=%s", tag)
    return None


def _post_pushover(payload: dict) -> Optional[str]:
    """POST to Pushover; return a dispatcher error string on failure."""
    try:
        status, body = _http_post(PUSHOVER_API_URL, payload)
    except Exception as exc:  # noqa: BLE001 — channel transport failures must surface
        log.warning("pushover transport failure: %s", exc)
        return f"pushover transport failed: {exc}"
    if status >= 400:
        log.warning("pushover non-2xx (%s): %r", status, body[:200])
        return f"pushover {status}: {body[:200].decode('utf-8', 'replace').strip() or 'no body'}"
    return None


def _emit_pushover(outcome: CheckOutcome) -> Optional[str]:
    """P1 only — emergency priority cuts through iOS DnD and repeats
    until acknowledged. Returns a dispatcher error string on failure,
    ``None`` on success or when this channel is not applicable
    (non-P1, no creds). Non-P1 outcomes batch into the daily digest
    instead (see ``flush_daily_digest``).
    """
    if outcome.severity != "P1":
        return None
    creds = _pushover_creds()
    if not creds:
        return None
    user, token = creds
    payload = build_pushover_payload(
        user=user,
        token=token,
        title=f"radon watchdog: {outcome.service}",
        message=outcome.message,
        severity="P1",
        tag=outcome.service,  # so cancel_emergency(service) clears it on recovery
    )
    return _post_pushover(payload)


def _write_dispatcher_health(
    *,
    now,
    dispatcher_error: Optional[str],
    bucket: Optional[str] = None,
) -> None:
    """Single source of truth for the ``watchdog-alerts`` row.

    Writes ``state=ok`` with empty ``last_error`` when the dispatcher
    succeeded; writes ``state=error`` with the dispatcher's failure
    string when something the notifier itself controls broke (channel
    5xx, DB write, etc).

    Never writes downstream alert content. Best-effort: a DB failure
    while recording dispatcher health is logged but does not raise —
    the bucket cycle must complete.
    """
    from db.writer import record_service_health  # local import — fixture reloads

    finished_at = now.isoformat().replace("+00:00", "Z") if hasattr(now, "isoformat") else None
    state = "error" if dispatcher_error else "ok"
    error_payload: Optional[dict[str, Any]] = None
    if dispatcher_error:
        error_payload = {"dispatcher_error": dispatcher_error}
    elif bucket:
        # Heartbeat-only payload is structurally distinct from the legacy
        # alert payload (no service/severity/kind keys) so the dashboard
        # can ignore it without a regex check.
        error_payload = {"heartbeat_at": finished_at, "bucket": bucket}

    try:
        record_service_health(
            "watchdog-alerts",
            state,
            finished_at=finished_at,
            error=error_payload,
        )
    except Exception as exc:  # noqa: BLE001 — telemetry must not kill the cycle
        log.warning("watchdog-alerts row write failed: %s", exc)


def heartbeat_ok(*, bucket: str, now) -> None:
    """Write ``watchdog-alerts=ok`` on a quiet cycle (no alerts fired).

    Kept as a public seam — ``__main__._cmd_bucket`` calls this when
    the bucket dispatched nothing. The error path lives in
    ``_write_dispatcher_health`` and is reached via ``dispatch`` /
    ``dispatch_with_grouping``.
    """
    _write_dispatcher_health(now=now, dispatcher_error=None, bucket=bucket)


# ── once-daily P2/P3 digest (DUR-14) ───────────────────────────────
#
# Non-P1 outcomes used to land in journalctl + service_health only —
# operationally invisible unless the operator went looking. They now
# batch into a small JSON state file and the DAILY watchdog bucket
# (hourly timer) flushes at most one normal-priority push per UTC day.

DIGEST_STATE_PATH = _PROJECT_DIR / "data" / "watchdog_digest_state.json"
DIGEST_MAX_PENDING = 200
DIGEST_MESSAGE_CHAR_BUDGET = 1000  # Pushover message limit is 1024


def _load_digest_state() -> dict:
    try:
        return json.loads(DIGEST_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_digest_state(state: dict) -> None:
    DIGEST_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    DIGEST_STATE_PATH.write_text(json.dumps(state, indent=1), encoding="utf-8")


def _enqueue_digest(outcome: CheckOutcome) -> None:
    """Best-effort: append a non-P1 outcome to the pending digest."""
    try:
        state = _load_digest_state()
        pending = state.get("pending") or []
        pending.append({
            "service": outcome.service,
            "severity": outcome.severity,
            "kind": outcome.kind,
            "message": outcome.message,
            "at": outcome.now.isoformat() if hasattr(outcome.now, "isoformat") else None,
        })
        state["pending"] = pending[-DIGEST_MAX_PENDING:]
        _save_digest_state(state)
    except Exception as exc:  # noqa: BLE001 — digest bookkeeping must not kill dispatch
        log.warning("digest enqueue failed: %s", exc)


def _format_digest(pending: list[dict]) -> str:
    """Group by (severity, service); one line per group with a count and
    the most recent message."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for entry in pending:
        key = (entry.get("severity") or "P3", entry.get("service") or "?")
        groups.setdefault(key, []).append(entry)
    lines = []
    for (severity, service), entries in sorted(groups.items()):
        emoji = _EMOJI.get(severity, "")
        suffix = f" ×{len(entries)}" if len(entries) > 1 else ""
        lines.append(f"{emoji} [{severity}] {service}{suffix} — {entries[-1].get('message', '')}")
    text = "\n".join(lines)
    if len(text) > DIGEST_MESSAGE_CHAR_BUDGET:
        text = text[: DIGEST_MESSAGE_CHAR_BUDGET - 1] + "…"
    return text


def flush_daily_digest(*, now: datetime) -> Optional[str]:
    """Send at most one P2/P3 digest push per UTC day. Carried by the
    daily watchdog bucket (hourly timer), so a failed send retries
    within the hour — pending entries are only cleared on success.

    Returns a dispatcher error string on send failure (also recorded on
    the ``watchdog-alerts`` row), ``None`` otherwise.
    """
    state = _load_digest_state()
    pending = state.get("pending") or []
    if not pending:
        return None

    last_sent = state.get("last_sent_at") or ""
    today = now.date().isoformat()
    if last_sent[:10] == today:
        return None

    creds = _pushover_creds()
    if not creds:
        # No external channel: drop the batch so the file can't grow
        # unbounded — the rows already live in service_health(+events).
        _save_digest_state({"pending": [], "last_sent_at": state.get("last_sent_at")})
        log.info("digest skipped (no Pushover creds) — %d entries dropped", len(pending))
        return None

    user, token = creds
    payload = build_pushover_payload(
        user=user,
        token=token,
        title=f"radon watchdog: daily digest ({len(pending)} alerts)",
        message=_format_digest(pending),
        severity=None,  # normal priority — never emergency
    )
    dispatcher_error = _post_pushover(payload)
    if dispatcher_error:
        _write_dispatcher_health(now=now, dispatcher_error=f"digest: {dispatcher_error}")
        return dispatcher_error

    _save_digest_state({"pending": [], "last_sent_at": now.isoformat()})
    log.info("daily digest dispatched (%d entries)", len(pending))
    return None


# ── public entry point ─────────────────────────────────────────────

def dispatch(outcome: CheckOutcome) -> None:
    """Route ``outcome`` to every enabled channel matching its
    severity, then stamp the cooldown row.

    Callers should pre-check ``cooldown_allows_fire()``; ``dispatch``
    does NOT skip on cooldown so end-to-end tests can verify channel
    dispatch directly.

    Writes a dispatcher-health row reflecting whether the dispatch
    itself succeeded. Downstream alert content goes to journalctl via
    ``_log_alert_event``, never into ``service_health.last_error``.
    P2/P3 outcomes additionally batch into the once-daily digest.
    """
    if not outcome.fired:
        return

    _log_alert_event(outcome)
    dispatcher_error = _emit_pushover(outcome)
    if outcome.severity and outcome.severity != "P1":
        _enqueue_digest(outcome)
    _write_dispatcher_health(now=outcome.now, dispatcher_error=dispatcher_error)

    if outcome.severity:
        cooldown_mod.mark_notified(
            service=outcome.service,
            severity=outcome.severity,
            now=outcome.now,
        )
