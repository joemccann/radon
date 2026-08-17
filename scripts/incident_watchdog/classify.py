"""Pure classification: probe findings -> incidents mapped to runbook cases.

No clock reads — the cycle passes ``now`` so every rule is testable with a
pinned clock (the only I/O is a once-per-year cached holiday-config read).
``unknown`` findings never classify as the probed condition (a probe that
could not observe is not evidence of an outage) — but a probe whose unknown
is DEFINITIVE (missing/rotated token, non-200 auth response) cannot self-heal
and is its own ``watchdog-probe-dead`` incident. EXPECTED states never
classify: stale rows while
the market is closed (RTH-only writers quiet off-hours are documented-normal)
and error rows whose own ``next_attempt_at`` circuit-breaker embargo is still
in the future (2026-08-04T08:00Z false-P2 incident).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

RUNBOOK = "docs/incident-runbook.md"

ET = ZoneInfo("America/New_York")
RTH_OPEN_MINUTES = 9 * 60 + 30
RTH_CLOSE_MINUTES = 16 * 60


@lru_cache(maxsize=8)
def _holidays(year: int) -> frozenset[str]:
    try:
        from utils.market_calendar import load_holidays
    except ImportError:
        try:
            from scripts.utils.market_calendar import load_holidays
        except ImportError:
            return frozenset()
    try:
        return frozenset(load_holidays(year))
    except Exception:
        return frozenset()


def _is_market_open(now: datetime) -> bool:
    et = now.astimezone(ET)
    if et.weekday() >= 5:
        return False
    if et.strftime("%Y-%m-%d") in _holidays(et.year):
        return False
    minutes = et.hour * 60 + et.minute
    return RTH_OPEN_MINUTES <= minutes < RTH_CLOSE_MINUTES


def _embargo_in_future(last_error: str | None, now: datetime) -> bool:
    if not last_error:
        return False
    try:
        payload = json.loads(last_error)
    except (ValueError, TypeError):
        return False
    raw = payload.get("next_attempt_at") if isinstance(payload, dict) else None
    if not raw:
        return False
    try:
        until = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > now


def _is_expected_quiet(row: dict, now: datetime) -> bool:
    """True for degraded rows that are documented-normal right now."""
    state = row.get("state")
    if state == "stale":
        return not _is_market_open(now)
    if state == "error":
        return _embargo_in_future(row.get("last_error"), now)
    return False


# Which probes bear on each case: resolution scope for the store. An
# indeterminate probe only latches the incidents it actually observes.
CASE_PROBES = {
    "cancelled-deploy-corrupt-next-build": ("nextjs_liveness", "deploy"),
    "turso-destroy-storm": ("nextjs_db", "nextjs_liveness"),
    "stale-market-data-freshness": ("freshness",),
    "service-health-degraded": ("nextjs_db",),
    "service-down": ("api_lite",),
    "watchdog-probe-dead": ("freshness",),
}


def _incident(case_id: str, severity: str, title: str, fingerprint: str,
              evidence: dict) -> dict:
    return {
        "case_id": case_id,
        "severity": severity,
        "title": title,
        "fingerprint": fingerprint,
        "evidence": evidence,
        "probes": list(CASE_PROBES[case_id]),
    }


def _classify_corrupt_build(findings: dict, now: datetime) -> dict | None:
    liveness = findings.get("nextjs_liveness", {})
    deploy = findings.get("deploy", {})
    ci = deploy.get("ci") or {}
    # ci=None is unknown, never settled. The VPS has no gh, so CI is
    # always None; the transition journal only covers promote-verify,
    # not the multi-minute staged build. P2 needs a positively observed
    # completed CI plus marker!=head plus not in_flight.
    deploy_settled = (
        ci.get("status") == "completed" and not deploy.get("in_flight")
    )

    if liveness.get("state") == "down" and liveness.get("http_status") == 500:
        return _incident(
            "cancelled-deploy-corrupt-next-build", "P1",
            "Public route /sign-in returns 500 — app cannot render any page",
            "corrupt-next-build",
            {
                "liveness": liveness,
                "last_ci_run": ci or None,
                "green_marker": deploy.get("green_marker"),
                "head": deploy.get("head"),
            },
        )

    marker = deploy.get("green_marker")
    head = deploy.get("head")
    if marker and head and marker != head and deploy_settled:
        return _incident(
            "cancelled-deploy-corrupt-next-build", "P2",
            "Git HEAD does not match the last green-deploy marker",
            "deploy-marker-mismatch",
            {"green_marker": marker, "head": head, "last_ci_run": ci or None},
        )
    return None


def _classify_turso_wedge(findings: dict, now: datetime) -> dict | None:
    db = findings.get("nextjs_db", {})
    liveness = findings.get("nextjs_liveness", {})
    if db.get("state") == "up" and db.get("synthetic_turso_row") \
            and liveness.get("state") == "up":
        return _incident(
            "turso-destroy-storm", "P1",
            "Next.js Turso reads failing while the process is up",
            "turso-destroy-storm",
            {
                "warning": db.get("warning"),
                "next_diagnostic": (
                    "Run the Python Turso canary from the same host in the same "
                    "minute; canary succeeds => Node-local wedge (restart "
                    "radon-nextjs helps), canary fails => upstream Turso "
                    "(do NOT restart-flap)."
                ),
            },
        )
    return None


def _classify_stale_freshness(findings: dict, now: datetime) -> dict | None:
    fresh = findings.get("freshness", {})
    if fresh.get("state") == "up" and fresh.get("all_fresh") is False:
        stale = fresh.get("stale_checks") or []
        return _incident(
            "stale-market-data-freshness", "P2",
            f"Market-data freshness failing during RTH: {', '.join(stale)}",
            "stale-freshness:" + ",".join(stale),
            {"stale_checks": stale, "database_ok": fresh.get("database_ok")},
        )
    return None


def _classify_degraded_rows(findings: dict, now: datetime) -> dict | None:
    db = findings.get("nextjs_db", {})
    failing = db.get("failing") or []
    if db.get("state") != "up" or db.get("synthetic_turso_row") or not failing:
        return None
    actionable = [row for row in failing if not _is_expected_quiet(row, now)]
    suppressed = [row for row in failing if _is_expected_quiet(row, now)]
    if not actionable:
        return None
    names = sorted(str(row.get("service")) for row in actionable)
    return _incident(
        "service-health-degraded", "P2",
        f"{len(names)} service_health row(s) degraded: {', '.join(names)}",
        "service-health-degraded:" + ",".join(names),
        {
            "failing": actionable,
            "suppressed_expected": [
                {"service": row.get("service"), "state": row.get("state")}
                for row in suppressed
            ],
        },
    )


def _classify_service_down(findings: dict, now: datetime) -> dict | None:
    if findings.get("api_lite", {}).get("state") == "down":
        return _incident(
            "service-down", "P1",
            "FastAPI /health/lite unreachable (connection refused)",
            "service-down:fastapi",
            {"health_status": findings.get("health_status")},
        )
    return None


def _classify_dead_freshness_probe(findings: dict, now: datetime) -> dict | None:
    """A freshness probe that answered definitively wrong — token missing or
    rotated, 401/403, route removed — will never self-heal, and while it
    persists the watchdog is blind to market-data staleness. Unlike a timeout
    (transient, indeterminate) it is an alarm in its own right."""
    fresh = findings.get("freshness", {})
    if fresh.get("state") != "unknown":
        return None
    if fresh.get("http_status") is None and not fresh.get("token_missing"):
        return None
    detail = fresh.get("detail") or "freshness probe cannot observe"
    return _incident(
        "watchdog-probe-dead", "P2",
        f"Freshness probe is dead, not transiently unknown: {detail}",
        "watchdog-probe-dead:freshness",
        {
            "http_status": fresh.get("http_status"),
            "token_missing": bool(fresh.get("token_missing")),
            "detail": fresh.get("detail"),
        },
    )


_RULES = (
    _classify_corrupt_build,
    _classify_turso_wedge,
    _classify_stale_freshness,
    _classify_degraded_rows,
    _classify_service_down,
    _classify_dead_freshness_probe,
)


def classify(findings: dict, now: datetime) -> list[dict]:
    incidents = []
    for rule in _RULES:
        incident = rule(findings, now)
        if incident:
            incident["classified_at"] = now.isoformat()
            incidents.append(incident)
    return incidents
