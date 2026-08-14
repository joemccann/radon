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
  - Pushover is HALF configured (one of PUSHOVER_USER / PUSHOVER_TOKEN
    unset) while a P1 fires — env drift the operator must fix. Both
    unset is the documented no-external-channel mode and stays ``ok``.
  - ``write_service_health_http`` itself raises (DB write failure)

In each case ``last_error`` carries a dispatcher-specific string
(``"pushover 500: …"``, ``"db write failed: …"``) so the banner
surfaces a real, actionable notifier outage — not stale alert history.

Severity routing for the Pushover channel (DUR-14 escalation)
==============================================================

 * P1 → Pushover EMERGENCY (priority=2, retry/expire) — repeats until
   the operator acknowledges the push. A successful P1 delivery also
   inserts a ``watchdog_pages`` row for the laptop Grok responder.
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
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

import fcntl

from .check import CheckOutcome
from . import cooldown as cooldown_mod


log = logging.getLogger("watchdog.notify")

_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"
# Pushover emergency-priority contract: retry >= 30s, expire <= 10800s.
# https://pushover.net/api#priority — the push re-alerts every `retry`
# seconds until acknowledged or `expire` seconds elapse.
# 60s made a single unacknowledged emergency ring near-continuously for its
# whole expire window (2026-07-28 storm); 300s still cuts through DnD.
PUSHOVER_EMERGENCY_RETRY_SECS = 300
PUSHOVER_EMERGENCY_EXPIRE_SECS = 3600


# ── env-driven channel registry ─────────────────────────────────────

PUSHOVER_ENV_VARS = ("PUSHOVER_USER", "PUSHOVER_TOKEN")


def _pushover_creds() -> Optional[tuple[str, str]]:
    user = os.environ.get("PUSHOVER_USER")
    token = os.environ.get("PUSHOVER_TOKEN")
    return (user, token) if user and token else None


def _missing_pushover_vars() -> list[str]:
    return [name for name in PUSHOVER_ENV_VARS if not os.environ.get(name)]


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

PUSHOVER_UNCONFIGURED = "pushover_not_configured"
PUSHOVER_INCOMPLETE = "pushover_credentials_incomplete"


@dataclass(frozen=True)
class ChannelResult:
    """What actually happened when an alert was handed to a channel.

    Three states the dispatcher must tell apart:

     * **delivered** (``attempted`` and no ``error``) — the only state
       that may arm the cooldown. Stamping ``notified`` for a page that
       never left the box mutes the condition for up to
       ``cooldown.REARM_CEILING`` (24h).
     * **error** — the channel was tried and refused (non-2xx /
       transport). Flips the ``watchdog-alerts`` row to ``error``.
     * **skip_reason** — no attempt was made because the channel is not
       configured. Not a dispatcher failure, and not a delivery either.
    """

    attempted: bool
    error: Optional[str] = None
    skip_reason: Optional[str] = None

    @property
    def delivered(self) -> bool:
        return self.attempted and self.error is None


# Non-P1 severities never touch Pushover; the digest owns them.
CHANNEL_NOT_APPLICABLE = ChannelResult(attempted=False)


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
            payload["tags"] = tag
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
    if status == 404:
        # Idempotent recovery: no active receipt with this tag remains.
        log.info("emergency push tag=%s already absent", tag)
        return None
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


def _unconfigured_pushover() -> ChannelResult:
    """Distinguish the two ways credentials can be absent.

    Both vars unset is the documented "service_health is the only
    channel" mode (``log_startup_warning``) — degraded, expected on a
    laptop, and not an error state. Exactly one var set is env drift:
    the operator believes Pushover is wired up and it is not, so it
    surfaces on the dispatcher row. NEITHER is a delivery.
    """
    missing = _missing_pushover_vars()
    if len(missing) == len(PUSHOVER_ENV_VARS):
        return ChannelResult(attempted=False, skip_reason=PUSHOVER_UNCONFIGURED)
    return ChannelResult(
        attempted=False,
        error=f"pushover credentials incomplete: {', '.join(missing)} unset",
        skip_reason=PUSHOVER_INCOMPLETE,
    )


def send_direct_page(*, title: str, message: str, tag: str) -> Optional[str]:
    """DB-free P1 emergency page (REL-010).

    Used when Turso itself is the outage: no cooldown table, no
    service_health write — just Pushover. Returns an error string or
    None on delivery. The caller owns any cooldown (file-based).
    """
    creds = _pushover_creds()
    if not creds:
        return "pushover unconfigured: " + ", ".join(_missing_pushover_vars())
    user, token = creds
    payload = build_pushover_payload(
        user=user,
        token=token,
        title=title,
        message=message,
        severity="P1",
        tag=tag,
    )
    return _post_pushover(payload)


def _emit_pushover(outcome: CheckOutcome) -> ChannelResult:
    """P1 only — emergency priority cuts through iOS DnD and repeats
    until acknowledged. Non-P1 outcomes batch into the daily digest
    instead (see ``flush_daily_digest``).

    Returns a :class:`ChannelResult` rather than a bare error string:
    ``None`` used to mean both "Pushover accepted it" and "there is no
    Pushover to accept it", and ``dispatch`` read that ``None`` as
    delivery — so a P1 fired while the credentials were missing armed
    the cooldown and muted the condition for up to 24h.
    """
    if outcome.severity != "P1":
        return CHANNEL_NOT_APPLICABLE
    creds = _pushover_creds()
    if not creds:
        return _unconfigured_pushover()
    user, token = creds
    payload = build_pushover_payload(
        user=user,
        token=token,
        title=f"radon watchdog: {outcome.service}",
        message=outcome.message,
        severity="P1",
        tag=outcome.service,  # so cancel_emergency(service) clears it on recovery
    )
    return ChannelResult(attempted=True, error=_post_pushover(payload))


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
        # Always bounded hrana (same as prod). Tests patch
        # ``db.hrana_http.hrana_execute`` onto in-memory sqlite (see
        # test_watchdog/conftest.py) — never fall back to sync libsql.
        from db.hrana_http import write_service_health_http

        write_service_health_http(
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
_DIGEST_THREAD_LOCK = threading.Lock()


@contextmanager
def _digest_state_lock():
    DIGEST_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_path = DIGEST_STATE_PATH.with_name(f"{DIGEST_STATE_PATH.name}.lock")
    with _DIGEST_THREAD_LOCK, lock_path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _load_digest_state() -> dict:
    try:
        return json.loads(DIGEST_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_digest_state(state: dict) -> None:
    DIGEST_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=DIGEST_STATE_PATH.parent,
        prefix=f".{DIGEST_STATE_PATH.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            json.dump(state, tmp, indent=1)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, DIGEST_STATE_PATH)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _enqueue_digest(outcome: CheckOutcome) -> None:
    """Best-effort: append a non-P1 outcome to the pending digest."""
    try:
        with _digest_state_lock():
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
    with _digest_state_lock():
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
            # No external channel: drop this locked batch so the file cannot grow
            # unbounded. A concurrent enqueue waits and is written afterwards.
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

def _mark_notified_best_effort(*, service: str, severity: str, now) -> None:
    """Persist cooldown state without putting storage on the paging path."""
    try:
        cooldown_mod.mark_notified(service=service, severity=severity, now=now)
    except Exception as exc:  # noqa: BLE001 - duplicate delivery beats no delivery
        log.warning(
            "cooldown mark failed for %s/%s; later alerts may repeat: %s",
            service,
            severity,
            exc,
        )


def _reached_a_channel(outcome: CheckOutcome, pushover: ChannelResult) -> bool:
    """True only when the alert actually left the dispatcher.

    P1 pages through Pushover, so nothing short of an accepted POST
    counts. P2/P3 batch into the on-disk daily digest, which always
    accepts the hand-off — their cooldown arms as before.
    """
    if outcome.severity == "P1":
        return pushover.delivered
    return True


def dispatch(
    outcome: CheckOutcome,
    *,
    record_health: bool = True,
) -> Optional[str]:
    """Route ``outcome`` to every enabled channel matching its
    severity, then stamp the cooldown row only after successful delivery.

    "Successful delivery" means the channel took it: for a P1 that is
    credentials present AND a 2xx from Pushover. An unpaged P1 leaves
    the cooldown un-armed so the condition is still fireable on the next
    cycle instead of being muted for the 24h re-arm ceiling.

    Callers should pre-check ``cooldown_allows_fire()``; ``dispatch``
    does NOT skip on cooldown so end-to-end tests can verify channel
    dispatch directly.

    By default, writes a dispatcher-health row reflecting whether the dispatch
    itself succeeded. Bucket dispatch passes ``record_health=False`` and writes
    one aggregate row after every outcome. Downstream alert content goes to
    journalctl via ``_log_alert_event``, never into ``service_health.last_error``.
    P2/P3 outcomes additionally batch into the once-daily digest.
    """
    if not outcome.fired:
        return None

    _log_alert_event(outcome)
    pushover = _emit_pushover(outcome)
    if outcome.severity and outcome.severity != "P1":
        _enqueue_digest(outcome)
    if pushover.skip_reason:
        log.warning(
            "P1 for %s was NOT paged (%s) — cooldown left un-armed so the "
            "next cycle re-fires once the channel is configured",
            outcome.service,
            pushover.skip_reason,
        )
    if record_health:
        _write_dispatcher_health(now=outcome.now, dispatcher_error=pushover.error)

    if outcome.severity and _reached_a_channel(outcome, pushover):
        _mark_notified_best_effort(
            service=outcome.service,
            severity=outcome.severity,
            now=outcome.now,
        )
        if outcome.severity == "P1":
            _enqueue_grok_page(outcome)
    return pushover.error


def _enqueue_grok_page(outcome: CheckOutcome) -> None:
    """Best-effort ticket for the laptop Grok responder. Never raise."""
    try:
        from . import pages
        pages.enqueue_delivered_page(
            service=outcome.service,
            severity=outcome.severity or "P1",
            kind=outcome.kind,
            message=outcome.message,
            now=outcome.now,
        )
    except Exception as exc:  # noqa: BLE001 — paging path stays up
        log.warning("grok page enqueue failed for %s: %s", outcome.service, exc)
