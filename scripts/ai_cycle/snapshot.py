"""Cache-only snapshot: latest comparable vintages with explicit quality states."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from .model import response_group, utc
from .registry import INDICATORS, SOURCES
from .shadow import evaluate_shadow
from .transforms import InsufficientEvidence, complete_daily_windows

API_SNAPSHOT_MAX_BYTES = 900_000
_MAX_CHART_SERIES = 3
_MAX_CHART_POINTS = 120
_MAX_STRIP_METRICS = 8


def vintage_rank(row):
    return (
        utc(row["fetched_at"]),
        utc(row["published_at"]) if row.get("published_at") else "",
        row["metadata"].get("accession", ""),
    )


def latest_response_members(rows, issues=None):
    """A censored publisher response replaces membership, including removals."""
    groups, ordinary = {}, []
    for row in rows:
        group = response_group(row)
        if group is None:
            ordinary.append(row)
        else:
            groups.setdefault(group, []).append(row)
    for members in groups.values():
        latest_fetch = max(utc(row["fetched_at"]) for row in members)
        current = [row for row in members if utc(row["fetched_at"]) == latest_fetch]
        # Two different payloads with exactly the same fetch timestamp are ambiguous.
        if len({row["raw_hash"] for row in current}) != 1:
            if issues is not None:
                issues.add(current[0]["indicator_id"])
            continue
        expected = {row["metadata"].get("response_row_count") for row in current}
        count = len({(row["series_id"], row["period_start"], row["period_end"]) for row in current})
        if expected != {None} and expected != {count}:
            if issues is not None:
                issues.add(current[0]["indicator_id"])
            continue
        ordinary.extend(current)
    return ordinary


def latest_vintages(rows, response_issues=None):
    latest = {}
    for row in latest_response_members(rows, response_issues):
        key = (
            row["indicator_id"],
            row["series_id"],
            row["source_id"],
            row["period_start"],
            row["period_end"],
            row["methodology_version"],
            row["cohort_version"],
        )
        if key not in latest or vintage_rank(row) > vintage_rank(latest[key]):
            latest[key] = row
    return list(latest.values())


def history_series(row):
    parts = [row["series_id"], row["methodology_version"], row["cohort_version"]]
    if row["indicator_id"] in ("F1", "F2", "H1", "H2") and row["measurement"] != "derived":
        days = (
            datetime.fromisoformat(row["period_end"].replace("Z", "+00:00"))
            - datetime.fromisoformat(row["period_start"].replace("Z", "+00:00"))
        ).days
        parts.append("instant" if days == 0 else f"{round(days / 90)}q-duration")
    return ":".join(parts)


def build_snapshot(store, as_of=None):
    as_of = utc(as_of)
    now = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    response_issues = set()
    rows = latest_vintages(store.read_snapshot_observations(as_of=as_of), response_issues)
    coverage = {}
    for row in rows:
        source = coverage.setdefault(
            row["source_id"],
            {"observation_count": 0, "observed_from": row["period_end"], "observed_through": row["period_end"]},
        )
        source["observation_count"] += 1
        source["observed_from"] = min(source["observed_from"], row["period_end"])
        source["observed_through"] = max(source["observed_through"], row["period_end"])
    rows += derived_observations(rows, as_of)
    statuses = {row["source_id"]: row for row in store.read_source_statuses(as_of=as_of)}
    sources = []
    for id, source in SOURCES.items():
        status = statuses.get(id, {})
        sources.append(
            {
                **source,
                "status": status.get("status", "unavailable"),
                "reason": status.get("reason", "No successful collection has been recorded."),
                "checked_at": status.get("checked_at"),
                "observation_count": coverage.get(id, {}).get("observation_count", 0),
                "observed_from": coverage.get(id, {}).get("observed_from"),
                "observed_through": coverage.get(id, {}).get("observed_through"),
            }
        )
    indicators = []
    for id, definition in INDICATORS.items():
        observations = sorted(
            [row for row in rows if row["indicator_id"] == id], key=lambda row: (row["period_end"], row["fetched_at"])
        )
        # Keep the latest point per explicit method/cohort; never splice methodologies.
        newest = {}
        for row in observations:
            newest[(row["series_id"], row["source_id"], row["methodology_version"], row["cohort_version"])] = row
        if id in ("D1", "D2", "D3", "D5", "D6", "C1", "C2", "C3", "C4") and observations:
            latest_day = max(row["period_end"][:10] for row in observations)
            newest = {key: row for key, row in newest.items() if row["period_end"][:10] == latest_day}
        metrics = []
        for row in newest.values():
            metrics.append(
                {
                    "id": row["series_id"],
                    "label": row["metadata"].get("label", row["series_id"]),
                    **{
                        key: row[key]
                        for key in (
                            "value",
                            "unit",
                            "period_start",
                            "period_end",
                            "published_at",
                            "fetched_at",
                            "source_id",
                            "source_url",
                            "measurement",
                            "methodology_version",
                            "cohort_version",
                            "lineage_group",
                            "raw_hash",
                            "metadata",
                        )
                    },
                }
            )

        def metric_priority(metric, indicator_id=id):
            if indicator_id == "D3":
                metadata = metric["metadata"]
                lab_tokens = metadata.get("dataset") == "labs" and metadata.get("metric") in ("tokens", "token")
                return (0 if lab_tokens else 1, -metric["value"] if lab_tokens else 0, metric["id"])
            if indicator_id == "D2":
                return (0 if metric["methodology_version"] == "openrouter-app-aggregate-v2" else 1, metric["id"])
            if indicator_id == "D6":
                overall_score = metric["id"].startswith("overall.") and metric["id"].endswith(".avg_score")
                return (0 if overall_score else 1, metric["id"])
            return (
                0
                if "ttm_coverage" in metric["id"]
                else 1
                if "ttm_funding_gap" in metric["id"]
                else 2
                if metric["metadata"].get("validated_transform")
                else 3,
                metric["id"],
            )

        metrics.sort(key=metric_priority)
        state, reason = "unavailable", "No verified observations are available."
        if metrics:
            state, reason = "available", "Latest reported measurements; interpretation is experimental."
            for row in newest.values():
                cadence = SOURCES[row["source_id"]]["cadence"]
                end = datetime.fromisoformat(row["period_end"].replace("Z", "+00:00"))
                if cadence in ("daily", "hourly", "live") and now - end > timedelta(
                    hours=72 if cadence == "daily" else 48
                ):
                    state, reason = (
                        "stale",
                        "Observation period is stale; a recent fetch does not refresh measurement age.",
                    )
                    break
                if row["metadata"].get("complete") is False or (
                    row["metadata"].get("verified_statement") is False
                    and not any(metric["metadata"].get("validated_transform") for metric in newest.values())
                ):
                    state, reason = "incomplete", "Measurement coverage or statement reconciliation is incomplete."
                if row["metadata"].get("expected_release_at") and utc(row["metadata"]["expected_release_at"]) < as_of:
                    state, reason = "stale", "Expected reporting update has not been received."
            if (
                state == "available"
                and id in ("C1", "C3", "C4", "F1", "P1")
                and not any(row["metadata"].get("validated_transform") for row in newest.values())
            ):
                state, reason = (
                    "incomplete",
                    "Raw evidence is available; comparable cohort or methodology validation is required before promotion.",
                )
            if state == "available" and id == "D1" and not any(metric["id"] == "growth_28d" for metric in metrics):
                state, reason = (
                    "insufficient_history",
                    "56 consecutive complete UTC days are required for comparable 28-day growth.",
                )
            if id == "D4":
                state, reason = "experimental", "Publisher methodology and traffic independence remain unverified."
            if id == "D6":
                state, reason = (
                    "experimental",
                    "OpenDesign Arena LLM/model-quality stub; design-task scores only, never GPU scarcity.",
                )
            failed = [
                statuses.get(source, {}).get("status") for source in {row["source_id"] for row in newest.values()}
            ]
            if any(status in ("error", "restricted", "unavailable") for status in failed) and state != "stale":
                state, reason = (
                    "incomplete",
                    "Latest source collection failed or is restricted; historical evidence is retained.",
                )
        if id in response_issues:
            state, reason = (
                "incomplete",
                "Latest publisher response membership is incomplete or ambiguous; affected comparisons are suspended.",
            )
        history = [
            {
                "date": row["period_end"],
                "value": row["value"],
                "unit": row["unit"],
                "series_id": history_series(row),
                "label": row["metadata"].get("label", row["series_id"]),
                "source_id": row["source_id"],
            }
            for row in observations
        ]
        indicators.append({**definition, "status": state, "reason": reason, "metrics": metrics, "history": history})
    weekly = weekly_evidence(rows, as_of)
    shadow = evaluate_shadow(weekly, as_of)
    if response_issues & {"D1", "C1", "C2"}:
        shadow.update(
            state="insufficient_evidence",
            reason="Required publisher response membership is incomplete; research state suspended.",
        )
    if shadow["state"] != "insufficient_evidence":
        required_sources = {source for week in weekly[-4:] for source, _ in week["cohort_signature"]}
        healthy = all(
            statuses.get(source, {}).get("status") == "available"
            and now - datetime.fromisoformat(statuses[source]["checked_at"].replace("Z", "+00:00"))
            <= timedelta(hours=72)
            for source in required_sources
        )
        if not healthy:
            shadow.update(
                state="insufficient_evidence",
                reason="Required source checks are missing, failed or stale; research state suspended.",
            )
    return {
        "version": 1,
        "generated_at": utc(),
        "as_of": as_of,
        "indicators": indicators,
        "sources": sources,
        "shadow": shadow,
    }


def derived_observations(rows, as_of):
    """Materialize supported transforms from selected source vintages only."""
    from collections import defaultdict

    from .model import canonical, digest
    from .transforms import (
        cash_coverage,
        daily_activity,
        fixed_inference_basket,
        matched_gpu_prices,
        share_metrics,
        trailing_four_quarters,
    )

    output = []

    def emit(inputs, series, value, unit, metadata=None, start=None, end=None):
        source = max(inputs, key=lambda row: row["fetched_at"])
        latest = max(inputs, key=lambda row: row["period_end"])
        row = {
            **source,
            "series_id": series,
            "value": value,
            "unit": unit,
            "measurement": "derived",
            "period_start": start or min(row["period_start"] for row in inputs),
            "period_end": end or latest["period_end"],
            "published_at": max((row["published_at"] for row in inputs if row.get("published_at")), default=None),
            "raw_hash": digest(canonical(sorted(row["raw_hash"] for row in inputs)).encode()),
            "methodology_version": "ai-cycle-v2",
            "cohort_version": source["cohort_version"] + ":" + source["methodology_version"],
            "metadata": {
                "label": series.replace("_", " "),
                "validated_transform": True,
                "input_hashes": sorted({row["raw_hash"] for row in inputs}),
                **(metadata or {}),
            },
        }
        output.append(row)
        return row

    groups = defaultdict(list)
    for row in rows:
        groups[
            (
                row["indicator_id"],
                row["period_end"][:10],
                row["cohort_version"],
                row["source_id"],
                row["methodology_version"],
            )
        ].append(row)
    totals = []
    for (indicator, day, cohort, _source_id, _method), inputs in groups.items():
        try:
            if indicator == "D1" and day < as_of[:10]:
                models = {row["series_id"]: row["value"] for row in inputs if row["series_id"] != "other"}
                other = next((row["value"] for row in inputs if row["series_id"] == "other"), None)
                free = {
                    row["series_id"] for row in inputs if row["metadata"].get("paid_classification") == "free_variant"
                }
                values = daily_activity(models, other, complete_day=True, free_model_ids=free)
                for key in ("total_tokens", "visible_nonfree_tokens", "other_share"):
                    row = emit(
                        inputs,
                        key,
                        values[key],
                        "ratio" if key == "other_share" else "tokens",
                        {"paid_composition": "unknown", "complete": True},
                    )
                    if key == "total_tokens":
                        totals.append(row)
            elif indicator == "D3":
                entities = defaultdict(dict)
                for row in inputs:
                    entities[(row["metadata"].get("dataset"), row["metadata"].get("entity"))][
                        row["metadata"].get("metric")
                    ] = row
                for (dataset, entity), measures in entities.items():
                    token = measures.get("tokens") or measures.get("token")
                    spend = measures.get("spend")
                    if token and spend and token["value"] > 0:
                        emit(
                            [token, spend],
                            f"{dataset}.{entity}.relative_monetization",
                            share_metrics(token["value"], spend["value"])["relative_monetization"],
                            "ratio",
                            {
                                "definition": "Spend share divided by token share; not dollar ASP",
                                "label": f"{str(entity).replace('-', ' ').title()} · spend share / token share",
                                "dataset": dataset,
                                "entity": entity,
                                "metric": "relative_monetization",
                            },
                        )
            elif indicator == "C3":
                required = inputs[0]["metadata"].get("cohort_members") or inputs[0]["metadata"].get("required_members")
                if not required:
                    continue
                assumptions = {
                    (row["metadata"].get("input_tokens"), row["metadata"].get("output_tokens")) for row in inputs
                }
                if len(assumptions) != 1:
                    continue
                inp, out = next(iter(assumptions))
                result = fixed_inference_basket(
                    {row["series_id"]: row["metadata"] for row in inputs},
                    required,
                    input_tokens=inp,
                    output_tokens=out,
                    cohort_version=cohort,
                )
                emit(inputs, "fixed_inference_basket", result["cost_usd"], "USD", result)
        except (InsufficientEvidence, KeyError, TypeError):
            continue
    # Demand comparisons retain cohort boundaries.
    for cohort in {row["cohort_version"] for row in totals}:
        daily = sorted([row for row in totals if row["cohort_version"] == cohort], key=lambda row: row["period_end"])
        for index in range(55, len(daily)):
            window = daily[index - 55 : index + 1]
            try:
                values = complete_daily_windows(
                    [dict(date=row["period_end"][:10], value=row["value"]) for row in window],
                    window[-1]["period_end"][:10],
                )
                for key, value in values.items():
                    emit(window, key, value, "ratio" if key == "growth_28d" else "tokens", {"complete": True})
            except InsufficientEvidence:
                pass
    # Fixed GPU cohort comparisons use exactly 28 calendar days, never nearest date.
    gpu_days = defaultdict(list)
    for (id, day, _, _, _), inputs in groups.items():
        if id == "C1":
            gpu_days[day].extend(inputs)
    for day, inputs in gpu_days.items():
        baseline_day = (datetime.fromisoformat(day) - timedelta(days=28)).date().isoformat()
        if baseline_day not in gpu_days:
            continue

        def offers(values):
            return [
                {**row["metadata"], "price_per_gpu_hour": row["value"]}
                for row in values
                if row["metadata"].get("cohort_eligible", True)
            ]

        cohorts = {(row["metadata"].get("gpu"), row["metadata"].get("term")) for row in inputs}
        for gpu, term in cohorts:
            current_cohort = [
                row for row in inputs if (row["metadata"].get("gpu"), row["metadata"].get("term")) == (gpu, term)
            ]
            prior_cohort = [
                row
                for row in gpu_days[baseline_day]
                if (row["metadata"].get("gpu"), row["metadata"].get("term")) == (gpu, term)
            ]
            try:
                result = matched_gpu_prices(offers(prior_cohort), offers(current_cohort))
                series = (
                    "matched_gpu_price_relative_28d"
                    if gpu in ("H100 SXM", "H100_SXM") and term == "on-demand"
                    else f"matched_gpu_price_relative_28d.{gpu}.{term}"
                )
                emit(
                    prior_cohort + current_cohort,
                    series,
                    result["price_relative"],
                    "ratio",
                    {**result, "gpu": gpu, "term": term},
                )
            except InsufficientEvidence:
                pass
    supply_series = defaultdict(dict)
    for row in rows:
        if row["indicator_id"] == "C2" and row["series_id"].endswith(".gpus") and not row["metadata"].get("truncated"):
            supply_series[(row["series_id"], row["cohort_version"], row["source_id"], row["methodology_version"])][
                row["period_end"][:10]
            ] = row
    for series in supply_series.values():
        for day, current in series.items():
            previous = series.get((datetime.fromisoformat(day) - timedelta(days=28)).date().isoformat())
            if previous and previous["value"] > 0:
                emit(
                    [previous, current],
                    "rentable_gpu_growth_28d",
                    current["value"] / previous["value"] - 1,
                    "ratio",
                    {"comparable": True, "definition": "Fixed-filter rentable GPU count, not fleet utilization"},
                )
    # SEC cumulative cash flows are eligible only after statement reconciliation.
    finances = defaultdict(list)
    for row in rows:
        if row["indicator_id"] == "F1" and row["metadata"].get("verified_statement"):
            finances[
                (
                    row["metadata"].get("entity"),
                    row["metadata"].get("tag"),
                    row["source_id"],
                    row["methodology_version"],
                    row["cohort_version"],
                )
            ].append(row)
    ttm = {}
    for (entity, tag, source_id, method, cohort), inputs in finances.items():
        years = defaultdict(dict)
        for row in inputs:
            previous = years[row["period_start"][:10]].get(row["period_end"][:10])
            if previous is None or vintage_rank(row) > vintage_rank(previous):
                years[row["period_start"][:10]][row["period_end"][:10]] = row
        quarters = []
        fiscal_month = 7 if entity == "MSFT" else 6 if entity == "ORCL" else 1
        for start, ends in years.items():
            if datetime.fromisoformat(start).month != fiscal_month:
                continue
            previous = None
            for end, row in sorted(ends.items()):
                beginning = (
                    (datetime.fromisoformat(previous["period_end"][:10]) + timedelta(days=1)).date().isoformat()
                    if previous
                    else start
                )
                days = (datetime.fromisoformat(end) - datetime.fromisoformat(beginning)).days + 1
                if 70 <= days <= 110:
                    quarters.append(
                        {
                            "start": beginning,
                            "end": end,
                            "value": row["value"] - (previous["value"] if previous else 0),
                            "inputs": [previous, row] if previous else [row],
                        }
                    )
                previous = row
        quarters.sort(key=lambda quarter: quarter["end"])
        for index in range(3, len(quarters)):
            window = quarters[index - 3 : index + 1]
            try:
                total = trailing_four_quarters(window)
            except InsufficientEvidence:
                continue
            # Include exactly the current/prior YTD facts needed for these four quarter differences.
            provenance = {}
            for quarter in window:
                for row in quarter["inputs"]:
                    provenance[(row["period_start"], row["period_end"], row["fetched_at"], row["raw_hash"])] = row
            window_inputs = list(provenance.values())
            ttm[(entity, tag, source_id, method, cohort, total["start"], total["end"])] = (total, window_inputs)
            emit(
                window_inputs,
                f"{entity}.{tag}.ttm",
                total["value"],
                "USD",
                {
                    "verified_statement": True,
                    "entity": entity,
                    "label": f"{entity} · {'Operating cash flow' if tag == 'NetCashProvidedByUsedInOperatingActivities' else 'Cash capex'} (TTM)",
                },
                start=total["start"],
                end=total["end"],
            )
    horizons = {(key[0], key[2], key[3], key[4], key[5], key[6]) for key in ttm}
    for entity, source_id, method, cohort, start, end in sorted(horizons):
        ocf = ttm.get((entity, "NetCashProvidedByUsedInOperatingActivities", source_id, method, cohort, start, end))
        capex_tag = (
            "PaymentsToAcquireProductiveAssets" if entity == "AMZN" else "PaymentsToAcquirePropertyPlantAndEquipment"
        )
        capex = ttm.get((entity, capex_tag, source_id, method, cohort, start, end))
        if not ocf or not capex:
            continue
        try:
            result = cash_coverage(ocf[0], capex[0])
            for key in ("coverage", "funding_gap"):
                emit(
                    ocf[1] + capex[1],
                    f"{entity}.ttm_{key}",
                    result[key],
                    "ratio" if key == "coverage" else "USD",
                    {
                        "entity": entity,
                        "verified_statement": True,
                        "label": f"{entity} · {'OCF / cash capex' if key == 'coverage' else 'Cash funding gap'} (TTM)",
                    },
                    start=result["start"],
                    end=result["end"],
                )
        except InsufficientEvidence:
            pass
    return output


def weekly_evidence(rows, as_of):
    """Join already-transformed weekly demand/price evidence to verified supply."""
    from collections import defaultdict

    weeks = defaultdict(lambda: defaultdict(list))
    for row in rows:
        day = row["period_end"][:10]
        if datetime.fromisoformat(day).weekday() != 6 or day >= as_of[:10]:
            continue
        if row["series_id"] == "growth_28d":
            weeks[day]["demand"].append(row)
        elif row["series_id"] == "matched_gpu_price_relative_28d":
            weeks[day]["price"].append(row)
        elif (
            row["indicator_id"] == "C2"
            and row["metadata"].get("validated_transform")
            and row["metadata"].get("comparable")
            and row["series_id"] == "rentable_gpu_growth_28d"
        ):
            weeks[day]["supply"].append(row)
    result = []
    for day, evidence in weeks.items():
        if set(evidence) != {"demand", "price", "supply"} or any(len(value) != 1 for value in evidence.values()):
            continue
        evidence = {key: values[0] for key, values in evidence.items()}
        demand, price, supply = (evidence[key] for key in ("demand", "price", "supply"))
        result.append(
            dict(
                week_end=day,
                cohort_signature=tuple((row["source_id"], row["cohort_version"]) for row in (demand, price, supply)),
                fetched_at=max(row["fetched_at"] for row in evidence.values()),
                published_at=max(
                    (row["published_at"] for row in evidence.values() if row.get("published_at")), default=None
                ),
                coverage=price["metadata"].get("coverage", 0),
                gpu_providers=price["metadata"].get("provider_count", 0),
                fresh=all(
                    row["fetched_at"][:10] <= (datetime.fromisoformat(day) + timedelta(days=2)).date().isoformat()
                    for row in evidence.values()
                ),
                comparable=True,
                uncensored=demand["metadata"].get("complete", False),
                independent_supply=supply["lineage_group"] != price["lineage_group"],
                demand_lineage=demand["lineage_group"],
                demand_declining=demand["value"] < 0,
                prices_falling=price["value"] < 1,
                supply_rising=supply["value"] > 0,
                recovery_validated=demand["value"] >= 0,
            )
        )
    return result


def _downsample(points, limit):
    if len(points) <= limit:
        return points
    last = len(points) - 1
    indexes = sorted({round(i * last / (limit - 1)) for i in range(limit)})
    return [points[i] for i in indexes]


def compact_snapshot(snapshot, max_bytes=API_SNAPSHOT_MAX_BYTES):
    """Bound GET payload to the Hrana response ceiling; never invent zeros."""
    compact = json.loads(json.dumps(snapshot, allow_nan=False))
    for indicator in compact["indicators"]:
        metrics = indicator.get("metrics") or []
        indicator["metrics"] = metrics[:_MAX_STRIP_METRICS]
        groups = {}
        for point in indicator.get("history") or []:
            groups.setdefault((point.get("source_id"), point.get("series_id"), point.get("unit")), []).append(point)
        metric_ids = {metric["id"] for metric in indicator["metrics"]}
        ranked = sorted(
            groups.items(),
            key=lambda item: (0 if item[0][1] in metric_ids else 1, -len(item[1]), item[0][1] or ""),
        )
        history = []
        for _key, points in ranked[:_MAX_CHART_SERIES]:
            history.extend(_downsample(sorted(points, key=lambda point: point["date"]), _MAX_CHART_POINTS))
        indicator["history"] = history
    def encoded():
        return json.dumps(compact, sort_keys=True, separators=(",", ":"), allow_nan=False)

    payload = encoded()
    series_limit, point_limit = _MAX_CHART_SERIES, _MAX_CHART_POINTS
    while len(payload.encode()) > max_bytes and (series_limit > 1 or point_limit > 24):
        if point_limit > 24:
            point_limit = max(24, point_limit // 2)
        else:
            series_limit = max(1, series_limit - 1)
        for indicator in compact["indicators"]:
            groups = {}
            for point in indicator.get("history") or []:
                groups.setdefault((point.get("source_id"), point.get("series_id"), point.get("unit")), []).append(point)
            history = []
            for _key, points in list(groups.items())[:series_limit]:
                history.extend(_downsample(sorted(points, key=lambda point: point["date"]), point_limit))
            indicator["history"] = history
        payload = encoded()
    if len(payload.encode()) > max_bytes:
        raise ValueError("Compact snapshot exceeds API read budget")
    return json.loads(payload)


def persist_api_snapshot(store):
    snapshot = compact_snapshot(build_snapshot(store))
    store.write_api_snapshot(snapshot)
    return snapshot


def registry_snapshot(store):
    class StatusOnly:
        def read_snapshot_observations(self, as_of=None):
            return []

        def read_source_statuses(self, as_of=None):
            return store.read_source_statuses(as_of=as_of)

    return build_snapshot(StatusOnly())


def load_api_snapshot(store):
    payload = store.read_api_snapshot()
    if payload is not None:
        return payload
    return registry_snapshot(store)
