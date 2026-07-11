"""Dead-man's-switch reader for external_probe — pure classification.

The whole risk with a Tier-3 prober is the SILENT DEATH case: if the GitHub
Actions schedule stops firing (workflow disabled, GH incident, secret rotated,
account suspended) the last external_probe row stays frozen with whatever it
last wrote. A consumer that naively reads `ok=1` would report all-green while
the prober is actually dead — the exact failure Tier-3 exists to prevent.

The rule: a row is only trustworthy if its OWN checked_at is fresh relative to
the prober's cadence. GitHub cron is nominally every 5 min but is best-effort
and routinely lags 5-15 min under load, so the staleness window must be
generous — a few missed cycles, not one. We treat a row as STALE (and therefore
the prober as DEAD, NOT the edge as green) once checked_at is older than
STALE_AFTER_SECONDS.

This is pure: callers fetch the row however they like (libsql on-box, the
HTTP API, a dashboard query) and pass it in. The documented equivalent SQL
query is in the module docstring of classify_external_probe.
"""
from __future__ import annotations

from datetime import datetime, timezone

# GitHub requests a five-minute cron, but the latest 99 scheduled runs observed
# on 2026-07-10 had a 41.8-minute median, 78.8-minute p95, and 88.1-minute max
# dispatch interval. Two hours tolerates that platform jitter plus one delayed
# cycle without flapping, while still detecting a silent off-box monitor within
# a bounded window.
STALE_AFTER_SECONDS = 2 * 60 * 60
# GitHub and the VPS may differ by a few seconds. A timestamp materially ahead
# of the reader is corrupt evidence, not a perpetually fresh row.
MAX_FUTURE_SKEW_SECONDS = 5 * 60

# Three-valued verdict, matching the daemon's vocabulary:
#   "healthy"  — fresh row AND ok=1: edge is reachable + aggregate happy
#   "down"     — fresh row AND ok=0: edge confirmed not healthy from off-box
#   "stale"    — checked_at too old: the PROBER is dead, edge status UNKNOWN
VERDICT_HEALTHY = "healthy"
VERDICT_DOWN = "down"
VERDICT_STALE = "stale"


def _parse_iso(value: str) -> datetime:
    """Parse the prober's ISO-8601 'Z' timestamp into an aware UTC datetime."""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_seconds(checked_at: str, now: datetime | None = None) -> float:
    """Seconds between checked_at and now (UTC). Negative clamped to 0."""
    reference = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
    delta = (reference - _parse_iso(checked_at)).total_seconds()
    return delta if delta > 0 else 0.0


def classify_external_probe(
    row: dict | None,
    now: datetime | None = None,
    stale_after_seconds: int = STALE_AFTER_SECONDS,
) -> dict:
    """Apply the dead-man's-switch. Returns {verdict, reason, [age_seconds]}.

    A missing row (prober never ran) is STALE, not green. A row whose own
    checked_at is older than the window is STALE regardless of its ok flag —
    a frozen ok=1 must never read as healthy.

    Documented equivalent SQL (for a dashboard / SWR consumer that prefers a
    query over importing this module):

        SELECT
          CASE
            WHEN checked_at IS NULL
              OR (julianday('now') - julianday(checked_at)) * 86400 > 7200
              THEN 'stale'
            WHEN ok = 1 THEN 'healthy'
            ELSE 'down'
          END AS verdict
        FROM external_probe
        WHERE source = 'github-actions/edge';
    """
    if not row or not row.get("checked_at"):
        return {"verdict": VERDICT_STALE, "reason": "no_probe_row"}

    reference = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
    try:
        checked_at = _parse_iso(row["checked_at"])
    except (AttributeError, TypeError, ValueError):
        return {"verdict": VERDICT_STALE, "reason": "invalid_checked_at"}

    delta_seconds = (reference - checked_at).total_seconds()
    if delta_seconds < -MAX_FUTURE_SKEW_SECONDS:
        return {
            "verdict": VERDICT_STALE,
            "reason": "probe_timestamp_in_future",
            "age_seconds": 0.0,
        }
    seconds_old = max(0.0, delta_seconds)
    if seconds_old > stale_after_seconds:
        return {
            "verdict": VERDICT_STALE,
            "reason": "prober_silent",
            "age_seconds": seconds_old,
        }

    verdict = VERDICT_HEALTHY if int(row.get("ok", 0)) == 1 else VERDICT_DOWN
    return {"verdict": verdict, "reason": str(row.get("detail", "")), "age_seconds": seconds_old}
