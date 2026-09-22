"""Publication-time shadow research. No predictive validity is implied."""

from datetime import datetime, timedelta

from .model import available_at, canonical, digest, utc


def evaluate_shadow(weeks, as_of=None):
    as_of = utc(as_of)
    # Evidence must have been observed by the evaluation time, not backdated later.
    eligible = [row for row in weeks if available_at(row) <= as_of and row.get("week_end", "9999") <= as_of[:10]]
    latest = {}
    for row in eligible:
        key = row["week_end"]
        if key not in latest or available_at(row) > available_at(latest[key]):
            latest[key] = row
    ordered = sorted(latest.values(), key=lambda row: row["week_end"])
    result = dict(
        status="experimental",
        state="insufficient_evidence",
        reason="Four consecutive fresh weekly evaluations with comparable demand, prices and supply are required; predictive validity is unestablished.",
        evaluated_at=as_of,
        eligible_weeks=0,
    )
    if len(ordered) < 4:
        return result
    window = ordered[-4:]
    dates = [datetime.fromisoformat(row["week_end"][:10]) for row in window]
    if any(b - a != timedelta(days=7) for a, b in zip(dates, dates[1:])):
        return result
    if datetime.fromisoformat(as_of[:10]) - dates[-1] > timedelta(days=8):
        result["reason"] = "Latest weekly evidence is stale; research state suspended."
        return result
    if len({str(row.get("cohort_signature")) for row in window}) != 1:
        result["reason"] = "Evidence cohorts changed during the persistence window."
        return result
    for row in window:
        if (
            row.get("coverage", 0) < 0.8
            or row.get("gpu_providers", 0) < 3
            or not row.get("fresh")
            or not row.get("comparable")
            or not row.get("uncensored")
            or not row.get("independent_supply")
            or row.get("demand_lineage") in (None, "vercel", "portkey")
            or any(
                not isinstance(row.get(key), bool) for key in ("demand_declining", "prices_falling", "supply_rising")
            )
        ):
            result["reason"] = "Required freshness, lineage, matched coverage or source quality gate failed."
            return result
    result["eligible_weeks"] = 4
    watch = all(row["demand_declining"] and row["prices_falling"] and row["supply_rising"] for row in window)
    recovery = all(row.get("recovery_validated") is True and not row["demand_declining"] for row in window)
    if watch:
        result.update(
            state="watch",
            reason="Experimental hypothesis persisted for four completed weeks; research context only, no validated trading edge.",
        )
    elif recovery:
        result.update(
            state="clear",
            reason="Four fresh recovery evaluations satisfy the declared reset rule; no predictive validity established.",
        )
    return result


def walk_forward(weeks):
    """Replay only actually available vintages, preserving insufficient states."""
    times = sorted({available_at(row) for row in weeks})
    evaluations = [evaluate_shadow(weeks, time) for time in times]
    events = sum(
        row["state"] == "watch" and (i == 0 or evaluations[i - 1]["state"] != "watch")
        for i, row in enumerate(evaluations)
    )
    return {
        "version": 1,
        "input_hash": digest(canonical(weeks).encode()),
        "evaluations": evaluations,
        "watch_episodes": events,
        "precision": None,
        "false_alerts": None,
        "lead_time": None,
        "predictive_validity": "unestablished",
        "reason": "Outcome labels and executable returns are not available; performance statistics are not estimable.",
    }
