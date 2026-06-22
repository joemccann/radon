"""Alert-rule evaluation + dispatch.

``evaluate_rules(scan_row, rules, now)`` returns the subset of rules whose
``(metric op threshold)`` predicate is crossed by the scan row AND that are not
inside their throttle window. ``evaluate_and_dispatch`` additionally routes each
match through the EXISTING watchdog notification helper (Pushover P1 +
service_health heartbeat) and stamps ``last_fired_at`` so the rule cannot
re-fire until the throttle window elapses.

Reuse, not duplication: dispatch builds a ``watchdog.check.CheckOutcome`` and
hands it to ``watchdog.notify.dispatch`` — the same path the service-health
monitor uses. No new transport code.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from watchdog import notify
from watchdog.check import CheckOutcome


# One alert per (rule) per hour by default — matches the watchdog cooldown.
DEFAULT_THROTTLE_SECONDS = 3600

# Severity used for dispatched alerts. P3 keeps signal alerts in the once-daily
# digest rather than firing an emergency Pushover push every time a scan crosses
# a threshold (an operator can raise individual rules later if desired).
ALERT_SEVERITY = "P3"

_OPS = {
    ">": lambda value, threshold: value > threshold,
    "<": lambda value, threshold: value < threshold,
    ">=": lambda value, threshold: value >= threshold,
    "<=": lambda value, threshold: value <= threshold,
}


@dataclass
class AlertRule:
    id: str
    user_id: str
    ticker: str
    metric: str
    op: str
    threshold: float
    channel: str
    last_fired_at: Optional[str] = None


@dataclass
class RuleMatch:
    rule: AlertRule
    value: float


# ── pure predicate ──────────────────────────────────────────────────

def rule_matches(rule: AlertRule, value: Optional[float]) -> bool:
    """True when ``value op rule.threshold`` holds. Guard clauses keep an
    unknown operator or a missing value from ever firing."""
    if value is None:
        return False
    comparator = _OPS.get(rule.op)
    if comparator is None:
        return False
    return bool(comparator(value, rule.threshold))


# ── throttle ────────────────────────────────────────────────────────

def _parse_iso(value: str) -> Optional[datetime]:
    try:
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _within_throttle(rule: AlertRule, now: datetime) -> bool:
    if not rule.last_fired_at:
        return False
    last = _parse_iso(rule.last_fired_at)
    if last is None:
        return False
    return (now - last).total_seconds() < DEFAULT_THROTTLE_SECONDS


# ── metric resolution ───────────────────────────────────────────────

def _metric_value(scan_row: dict[str, Any], metric: str) -> Optional[float]:
    raw = scan_row.get(metric)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _row_ticker(scan_row: dict[str, Any]) -> Optional[str]:
    ticker = scan_row.get("ticker") or scan_row.get("symbol")
    return str(ticker).strip().upper() if ticker else None


# ── evaluation ──────────────────────────────────────────────────────

def evaluate_rules(
    scan_row: dict[str, Any],
    rules: list[AlertRule],
    *,
    now: datetime,
) -> list[RuleMatch]:
    """Return the rules a fresh scan row fires, excluding throttled rules."""
    ticker = _row_ticker(scan_row)
    if not ticker:
        return []

    matches: list[RuleMatch] = []
    for rule in rules:
        if rule.ticker.strip().upper() != ticker:
            continue
        if _within_throttle(rule, now):
            continue
        value = _metric_value(scan_row, rule.metric)
        if rule_matches(rule, value):
            matches.append(RuleMatch(rule=rule, value=float(value)))
    return matches


# ── dispatch ────────────────────────────────────────────────────────

def _build_outcome(match: RuleMatch, now: datetime) -> CheckOutcome:
    rule = match.rule
    message = (
        f"{rule.ticker} {rule.metric} {rule.op} {rule.threshold:g} "
        f"(now {match.value:g})"
    )
    return CheckOutcome(
        service=f"alert:{rule.ticker}:{rule.metric}",
        kind="alert",
        status="error",
        severity=ALERT_SEVERITY,
        fired=True,
        message=message,
        consecutive_failures=1,
        now=now,
    )


def _mark_rule_fired(rule: AlertRule, now: datetime) -> None:
    """Stamp ``last_fired_at`` so the rule respects its throttle window.
    Best-effort; a DB hiccup must never break the scan tail."""
    try:
        from db.client import get_db

        db = get_db()
        db.execute(
            "UPDATE alert_rules SET last_fired_at = ? WHERE id = ?",
            (now.isoformat().replace("+00:00", "Z"), rule.id),
        )
        db.commit()
    except Exception:  # noqa: BLE001 — telemetry must not kill the scan
        pass


def evaluate_and_dispatch(
    scan_row: dict[str, Any],
    rules: list[AlertRule],
    *,
    now: Optional[datetime] = None,
) -> int:
    """Evaluate ``scan_row`` against ``rules``, dispatch each match through the
    watchdog notifier, and stamp ``last_fired_at``. Returns the match count."""
    now = now or datetime.now(timezone.utc)
    matches = evaluate_rules(scan_row, rules, now=now)
    for match in matches:
        notify.dispatch(_build_outcome(match, now))
        _mark_rule_fired(match.rule, now)
    return len(matches)


# ── rule loading (DB) ───────────────────────────────────────────────

def _row_to_rule(row: Any) -> AlertRule:
    return AlertRule(
        id=row[0],
        user_id=row[1],
        ticker=row[2],
        metric=row[3],
        op=row[4],
        threshold=float(row[5]),
        channel=row[6],
        last_fired_at=row[8] if len(row) > 8 else None,
    )


def run_alerts_for_results(rows: list[dict[str, Any]]) -> int:
    """Best-effort scan-tail hook: for each result row, load that ticker's
    rules and dispatch any matches. Wrapped so a DB/notify failure never
    breaks the scan that produced ``rows``. Returns total alerts dispatched."""
    now = datetime.now(timezone.utc)
    dispatched = 0
    for row in rows or []:
        try:
            ticker = _row_ticker(row)
            if not ticker:
                continue
            rules = load_rules_for_ticker(ticker)
            if not rules:
                continue
            dispatched += evaluate_and_dispatch(row, rules, now=now)
        except Exception:  # noqa: BLE001 — scan tail must never break on alerts
            continue
    return dispatched


def load_rules_for_ticker(ticker: str) -> list[AlertRule]:
    """Load every alert rule targeting ``ticker`` across all users.
    Best-effort: returns ``[]`` if the table or DB is unavailable."""
    try:
        from db.client import get_db

        db = get_db()
        cursor = db.execute(
            """
            SELECT id, user_id, ticker, metric, op, threshold, channel,
                   created_at, last_fired_at
            FROM alert_rules
            WHERE UPPER(ticker) = ?
            """,
            (ticker.strip().upper(),),
        )
        rows = cursor.fetchall() if hasattr(cursor, "fetchall") else list(cursor)
        return [_row_to_rule(r) for r in rows]
    except Exception:  # noqa: BLE001 — scan tail must never break on alerts
        return []
