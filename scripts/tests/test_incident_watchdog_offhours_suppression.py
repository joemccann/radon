#!/usr/bin/env python3
"""Regression test — incident_watchdog must not page on EXPECTED states.

Production incident (2026-08-04T08:00:00Z — Tuesday 04:00 ET, market
closed; data/incidents_remote/incident-20260804T080000Z-service-health-
degraded.json): the new incident watchdog classified a P2
``service-health-degraded`` from three service_health rows that are all
documented-normal:

    vcg-scan        stale  last run Monday 16:00:51 ET (market close) —
                           an RTH-only scan writer; off-hours staleness
                           is normal (feedback_service_health_staleness)
    cri-scan        stale  same topology, last run Monday 16:30 ET
    cash-flow-sync  error  last_error carries next_attempt_at
                           2026-08-04T21:08:47Z — a FUTURE 24h Flex
                           circuit-breaker embargo engaged BY DESIGN
                           after an upstream IBKR throttle (code 1001).
                           The row cannot heartbeat until the embargo
                           lifts; re-paging it every cycle is noise.

The runbook case itself says to cross-check watchdog cooldowns before
paging ("this class is usually one writer, not an outage"). The pipeline
under test is exactly what production runs each cycle:

    /api/service-health body -> parse_service_health_body()
                             -> findings["nextjs_db"]["failing"]
                             -> classify(findings, now)

``classify`` is pure with an injected ``now``, so every instant below
is derived window-relative (the most recent consecutive trading-day
pair on or before today — hardcoded dates rot in CI,
feedback_window_relative_test_dates) with ZoneInfo arithmetic, never
hand-computed UTC strings or fixed offsets. The SAME pure derivation
anchored at the production dates reproduces the incident JSON's exact
instants (TestTimelineDerivation), so fidelity to the 2026-08-04
evidence is pinned without freezing the red tests to a calendar date.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from incident_watchdog.classify import classify
from incident_watchdog.probes import parse_service_health_body

ET = ZoneInfo("America/New_York")

FLEX_CIRCUIT_BREAKER = timedelta(hours=24)


def _is_trading_day(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    try:
        from utils.market_calendar import load_holidays
    except Exception:
        return True
    return day.strftime("%Y-%m-%d") not in load_holidays(day.year)


def _most_recent_premarket_trading_pair() -> tuple[date, date]:
    """(prior trading day, incident day) — consecutive calendar days that
    are both trading days, most recent pair on or before today. Mirrors
    the production shape: a premarket clock whose previous calendar day
    had a real RTH close (Mon 2026-08-03 -> Tue 2026-08-04)."""
    day = datetime.now(ET).date()
    for _ in range(14):
        prior = day - timedelta(days=1)
        if _is_trading_day(day) and _is_trading_day(prior):
            return prior, day
        day -= timedelta(days=1)
    raise RuntimeError("no consecutive trading-day pair in the last 14 days")


def _at_et(day: date, hour: int, minute: int, second: int) -> datetime:
    local = datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=ET)
    return local.astimezone(timezone.utc)


def _derive_timeline(prior_day: date, incident_day: date) -> dict:
    """The incident's evidence instants, derived from two trading days:

    04:00 ET incident clock; the RTH writers' last runs at the prior
    day's close (as journald recorded them); the Flex breaker engaged at
    the prior day's 17:08:47 ET second SendRequest (throttle code 1001)
    with record_throttle's +24h UTC embargo."""
    breaker_engaged = _at_et(prior_day, 17, 8, 47)
    return {
        "incident_now": _at_et(incident_day, 4, 0, 0),
        "vcg_last_run": _at_et(prior_day, 16, 0, 51),
        "cri_last_run": _at_et(prior_day, 16, 30, 0),
        "breaker_engaged": breaker_engaged,
        "flex_next_attempt": breaker_engaged + FLEX_CIRCUIT_BREAKER,
    }


_PRIOR_DAY, _INCIDENT_DAY = _most_recent_premarket_trading_pair()
_TIMELINE = _derive_timeline(_PRIOR_DAY, _INCIDENT_DAY)

INCIDENT_NOW = _TIMELINE["incident_now"]
VCG_LAST_RUN = _TIMELINE["vcg_last_run"]
CRI_LAST_RUN = _TIMELINE["cri_last_run"]
FLEX_BREAKER_ENGAGED = _TIMELINE["breaker_engaged"]
FLEX_NEXT_ATTEMPT = _TIMELINE["flex_next_attempt"]


def _vcg_row() -> dict:
    return {
        "service": "vcg-scan",
        "state": "stale",
        "category": "scheduled",
        "last_attempt_started_at": VCG_LAST_RUN.isoformat(),
        "last_attempt_finished_at": VCG_LAST_RUN.isoformat(),
        "last_error": None,
        "error_summary": None,
        "updated_at": VCG_LAST_RUN.isoformat(),
    }


def _cri_row() -> dict:
    return {
        "service": "cri-scan",
        "state": "stale",
        "category": "scheduled",
        "last_attempt_started_at": CRI_LAST_RUN.isoformat(),
        "last_attempt_finished_at": CRI_LAST_RUN.isoformat(),
        "last_error": None,
        "error_summary": None,
        "updated_at": CRI_LAST_RUN.isoformat(),
    }


def _cash_flow_row(next_attempt_at: str | None) -> dict:
    """cash-flow-sync error row in the shape the route serves. last_error
    matches what CashFlowSyncHandler persists (cash_flow_sync.py:229 —
    ``{"message": ..., "next_attempt_at": ...}``)."""
    message = (
        "ERR: cash flow fetch failed: Flex throttle (code 1001): "
        "Statement could not be generated at this time."
    )
    payload: dict = {"message": message}
    if next_attempt_at is not None:
        payload["next_attempt_at"] = next_attempt_at
    return {
        "service": "cash-flow-sync",
        "state": "error",
        "category": "scheduled",
        "last_attempt_started_at": FLEX_BREAKER_ENGAGED.isoformat(),
        "last_attempt_finished_at": FLEX_BREAKER_ENGAGED.isoformat(),
        "last_error": json.dumps(payload),
        "error_summary": "Flex throttled by IBKR",
        "updated_at": FLEX_BREAKER_ENGAGED.isoformat(),
    }


def _service_health_body(failing_rows: list[dict]) -> dict:
    healthy = {
        "service": "newsfeed-scraper",
        "state": "ok",
        "category": "scheduled",
        "last_attempt_started_at": INCIDENT_NOW.isoformat(),
        "last_attempt_finished_at": INCIDENT_NOW.isoformat(),
        "last_error": None,
        "error_summary": None,
        "updated_at": INCIDENT_NOW.isoformat(),
    }
    services = [healthy, *failing_rows]
    return {
        "services": services,
        "failing": failing_rows,
        "warning": None,
        "summary": {"total": len(services), "failing_count": len(failing_rows)},
    }


def _findings(service_health_body: dict) -> dict:
    """Otherwise-healthy probe cycle with nextjs_db built exactly the way
    probe_nextjs_db builds it (probes.py:120)."""
    return {
        "nextjs_liveness": {"state": "up", "http_status": 200, "detail": ""},
        "nextjs_db": {
            "state": "up",
            **parse_service_health_body(service_health_body),
        },
        "api_lite": {"state": "up", "loop_lag_ms": 12.0},
        "health_status": {"state": "up", "ok": True, "overall_state": "healthy"},
        "freshness": {
            "state": "up",
            # /api/probe/freshness is already market-state aware: no
            # check applies off-hours (probes.py:76-77).
            "all_fresh": None,
            "stale_checks": [],
            "database_ok": True,
        },
        "deploy": {
            "state": "up",
            "ci": {"status": "completed", "conclusion": "success", "head_sha": "abc"},
            "green_marker": "abc",
            "head": "abc",
        },
    }


def _degraded(incidents: list[dict]) -> list[dict]:
    return [i for i in incidents if i["case_id"] == "service-health-degraded"]


class TestTimelineDerivation:
    """Prove the derivation is faithful: anchored at the production
    dates it reproduces the exact instants the incident JSON and VPS
    journald recorded (a pinned date fed to a PURE derivation cannot
    rot), and the window-relative instants the red tests actually use
    keep the production topology's structure."""

    def test_derivation_anchored_at_production_dates_matches_incident_json(self):
        produced = _derive_timeline(date(2026, 8, 3), date(2026, 8, 4))
        assert produced["incident_now"].isoformat() == "2026-08-04T08:00:00+00:00"
        assert produced["vcg_last_run"].isoformat() == "2026-08-03T20:00:51+00:00"
        assert produced["flex_next_attempt"].isoformat() == "2026-08-04T21:08:47+00:00"

    def test_incident_instant_is_off_hours(self):
        local = INCIDENT_NOW.astimezone(ET)
        assert _is_trading_day(local.date())  # a weekday trading day, so the gate
        assert local.hour == 4  # ...is the clock, not the calendar
        assert VCG_LAST_RUN < INCIDENT_NOW < FLEX_NEXT_ATTEMPT  # embargo in force


class TestOffHoursAndEmbargoSuppression:
    """RED — the production defect. classify() promotes every stale/error
    row in findings["nextjs_db"]["failing"] straight to a P2
    (classify.py:92-103), and parse_service_health_body strips the
    metadata (category, updated_at, last_error.next_attempt_at) any
    suppression rule would need (probes.py:68-71)."""

    def test_full_production_topology_raises_no_incident(self):
        body = _service_health_body([_vcg_row(), _cash_flow_row(FLEX_NEXT_ATTEMPT.isoformat()), _cri_row()])
        incidents = classify(_findings(body), INCIDENT_NOW)
        assert _degraded(incidents) == [], (
            "The 2026-08-04T08:00Z P2 reproduced: classify() paged "
            "service-health-degraded for three EXPECTED states — two "
            "RTH-only scan writers stale at 04:00 ET (market closed; "
            "off-hours staleness is documented-normal) and a "
            "cash-flow-sync error row whose 24h Flex circuit-breaker "
            f"embargo (next_attempt_at {FLEX_NEXT_ATTEMPT.isoformat()}) "
            "is still in the future by design."
        )

    def test_offhours_stale_rth_writers_do_not_classify(self):
        body = _service_health_body([_vcg_row(), _cri_row()])
        incidents = classify(_findings(body), INCIDENT_NOW)
        assert _degraded(incidents) == [], (
            "stale rows from RTH scan writers whose last run was the "
            "prior market close must not page while the market is "
            "closed (feedback_service_health_staleness — the "
            "established scripts/watchdog pager is off-hours aware)."
        )

    def test_future_embargo_error_row_does_not_classify(self):
        body = _service_health_body([_cash_flow_row(FLEX_NEXT_ATTEMPT.isoformat())])
        incidents = classify(_findings(body), INCIDENT_NOW)
        assert _degraded(incidents) == [], (
            "an error row whose last_error.next_attempt_at embargo is "
            "still in the future is a circuit breaker doing its job — "
            "re-classifying it as a new P2 every watchdog cycle until "
            "the embargo lifts is noise."
        )


class TestSuppressionStillFiresWhenItShould:
    """GREEN today — pins the rule so the suppression cannot be faked by
    deleting the degraded-rows classifier."""

    def test_error_row_with_expired_embargo_still_classifies(self):
        after_embargo = FLEX_NEXT_ATTEMPT + timedelta(hours=2)
        body = _service_health_body([_cash_flow_row(FLEX_NEXT_ATTEMPT.isoformat())])
        incidents = _degraded(classify(_findings(body), after_embargo))
        assert len(incidents) == 1
        assert incidents[0]["severity"] == "P2"
        assert incidents[0]["fingerprint"] == "service-health-degraded:cash-flow-sync"

    def test_error_row_without_embargo_still_classifies_off_hours(self):
        # An error with no breaker metadata is writer-health truth at any
        # hour — errors are not market-gated.
        body = _service_health_body([_cash_flow_row(None)])
        incidents = _degraded(classify(_findings(body), INCIDENT_NOW))
        assert len(incidents) == 1
        assert incidents[0]["severity"] == "P2"

    def test_stale_scheduled_row_during_rth_still_classifies(self):
        # 15:00 ET on the prior trading day — inside RTH on a regular
        # trading day; a vcg-scan row hours stale during the open
        # market is real.
        rth_now = _at_et(_PRIOR_DAY, 15, 0, 0)
        stale_since = _at_et(_PRIOR_DAY, 10, 0, 0)
        row = _vcg_row()
        row["last_attempt_started_at"] = stale_since.isoformat()
        row["last_attempt_finished_at"] = stale_since.isoformat()
        row["updated_at"] = stale_since.isoformat()
        incidents = _degraded(classify(_findings(_service_health_body([row])), rth_now))
        assert len(incidents) == 1
        assert incidents[0]["severity"] == "P2"
        assert incidents[0]["fingerprint"] == "service-health-degraded:vcg-scan"
