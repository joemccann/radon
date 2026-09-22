"""Comparable-period arithmetic. Missing evidence raises, never becomes zero."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date, timedelta


class InsufficientEvidence(ValueError):
    pass


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InsufficientEvidence("Finite numeric input required")
    return float(value)


def ratio(numerator, denominator):
    numerator, denominator = finite(numerator), finite(denominator)
    if denominator <= 0:
        raise InsufficientEvidence("Positive denominator required")
    return numerator / denominator


def daily_activity(models, other, *, complete_day, free_model_ids):
    if not complete_day:
        raise InsufficientEvidence("Partial UTC day excluded")
    if other is None:
        raise InsufficientEvidence("Long-tail total not disclosed")
    values = {key: finite(value) for key, value in models.items()}
    other = finite(other)
    if any(value < 0 for value in [other, *values.values()]):
        raise InsufficientEvidence("Negative token count")
    total = sum(values.values()) + other
    return {
        "total_tokens": total,
        "visible_nonfree_tokens": sum(value for key, value in values.items() if key not in free_model_ids),
        "other_share": ratio(other, total),
        "paid_tokens": None,
    }


def complete_daily_windows(points, end):
    """56 consecutive completed dates; no zero-filled gaps or duplicated dates."""
    end = date.fromisoformat(str(end)[:10])
    values = {}
    for point in points:
        day = date.fromisoformat(point["date"][:10])
        if day in values:
            raise InsufficientEvidence("Duplicate daily observation; select a vintage first")
        values[day] = finite(point["value"])
    needed = [end - timedelta(days=offset) for offset in range(56)]
    if any(day not in values for day in needed):
        raise InsufficientEvidence("56 complete consecutive days required")
    recent = sum(values[day] for day in needed[:28])
    previous = sum(values[day] for day in needed[28:])
    return {
        "mean_7d": statistics.mean(values[day] for day in needed[:7]),
        "sum_28d": recent,
        "growth_28d": ratio(recent, previous) - 1,
    }


def share_metrics(token_share, spend_share, previous_token_share=None):
    token_share, spend_share = finite(token_share), finite(spend_share)
    if not (0 <= token_share <= 100 and 0 <= spend_share <= 100):
        raise InsufficientEvidence("Shares must be percentage points in [0,100]")
    result = {"relative_monetization": ratio(spend_share, token_share)}
    if previous_token_share is not None:
        previous = finite(previous_token_share)
        if not 0 <= previous <= 100:
            raise InsufficientEvidence("Invalid prior share")
        result["token_share_change_pp"] = token_share - previous
    return result


GPU_FIELDS = ("provider", "region", "gpu", "vram_gb", "gpu_count", "interconnect", "tenancy", "term")


def matched_gpu_prices(baseline, current, *, min_coverage=0.8, min_providers=3):
    def keyed(rows):
        result = {}
        for row in rows:
            if any(row.get(field) in (None, "") for field in GPU_FIELDS):
                continue
            key = tuple(row[field] for field in GPU_FIELDS)
            if key in result:
                raise InsufficientEvidence("Duplicate comparable GPU offer")
            if finite(row["price_per_gpu_hour"]) <= 0:
                raise InsufficientEvidence("Positive GPU asking price required")
            result[key] = row
        return result

    before, after = keyed(baseline), keyed(current)
    matched = before.keys() & after.keys()
    coverage = ratio(len(matched), len(before))
    if coverage < min_coverage:
        raise InsufficientEvidence("Matched baseline coverage below threshold")
    providers = defaultdict(list)
    prices = []
    for key in matched:
        providers[key[0]].append(math.log(after[key]["price_per_gpu_hour"] / before[key]["price_per_gpu_hour"]))
        prices.append(after[key]["price_per_gpu_hour"])
    if len(providers) < min_providers:
        raise InsufficientEvidence("At least three matched GPU providers required")
    # Each provider receives equal weight regardless of how many offers it lists.
    relative = math.exp(statistics.mean(statistics.mean(values) for values in providers.values()))
    ordered = sorted(prices)

    def quantile(q):
        index = (len(ordered) - 1) * q
        lo, hi = math.floor(index), math.ceil(index)
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)

    return {
        "price_relative": relative,
        "coverage": coverage,
        "provider_count": len(providers),
        "eligible_count": len(matched),
        "p25": quantile(0.25),
        "median": quantile(0.5),
        "p75": quantile(0.75),
    }


def fixed_inference_basket(models, members, *, input_tokens, output_tokens, cohort_version):
    if not members or len(set(members)) != len(members) or not cohort_version:
        raise InsufficientEvidence("Unique fixed membership and cohort version required")
    if any(member not in models for member in members):
        raise InsufficientEvidence("Required model missing; basket suppressed")
    input_tokens, output_tokens = finite(input_tokens), finite(output_tokens)
    if min(input_tokens, output_tokens) < 0 or input_tokens + output_tokens <= 0:
        raise InsufficientEvidence("Invalid fixed token bundle")
    costs = []
    for member in members:
        model = models[member]
        inp, out = finite(model["input_price"]), finite(model["output_price"])
        if min(inp, out) < 0:
            raise InsufficientEvidence("Negative model price")
        costs.append((inp * input_tokens + out * output_tokens) / 1_000_000)
    return {
        "cost_usd": statistics.mean(costs),
        "members": list(members),
        "cohort_version": cohort_version,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def fiscal_quarters(ytd_facts):
    """One selected vintage per cumulative end; only verified cash-flow facts."""
    if not ytd_facts:
        raise InsufficientEvidence("No fiscal facts")
    facts = sorted(ytd_facts, key=lambda row: row["end"])
    starts = {row["start"] for row in facts}
    if len(starts) != 1 or any(not row.get("verified_statement") for row in facts):
        raise InsufficientEvidence("Verified same-fiscal-year cumulative facts required")
    start = date.fromisoformat(facts[0]["start"])
    result, previous_value, previous_end = [], 0.0, start - timedelta(days=1)
    for fact in facts:
        end = date.fromisoformat(fact["end"])
        days = (end - previous_end).days
        if not 70 <= days <= 110:
            raise InsufficientEvidence("Missing, duplicate or irregular fiscal quarter")
        value = finite(fact["value"])
        result.append(
            {
                "start": (previous_end + timedelta(days=1)).isoformat(),
                "end": end.isoformat(),
                "value": value - previous_value,
                "days": days,
            }
        )
        previous_value, previous_end = value, end
    return result


def trailing_four_quarters(quarters):
    if len(quarters) < 4:
        raise InsufficientEvidence("Four complete fiscal quarters required")
    rows = sorted(quarters, key=lambda row: row["end"])[-4:]
    for previous, current in zip(rows, rows[1:]):
        if date.fromisoformat(current["start"]) - date.fromisoformat(previous["end"]) != timedelta(days=1):
            raise InsufficientEvidence("Noncontiguous fiscal quarters")
    days = (date.fromisoformat(rows[-1]["end"]) - date.fromisoformat(rows[0]["start"])).days + 1
    if not 350 <= days <= 380:
        raise InsufficientEvidence("TTM fiscal window is not one year")
    return {"start": rows[0]["start"], "end": rows[-1]["end"], "value": sum(finite(row["value"]) for row in rows)}


def cash_coverage(ocf, capex):
    if (ocf["start"], ocf["end"]) != (capex["start"], capex["end"]):
        raise InsufficientEvidence("Cash flow horizons differ")
    return {
        "coverage": ratio(ocf["value"], capex["value"]),
        "funding_gap": finite(capex["value"]) - finite(ocf["value"]),
        "start": ocf["start"],
        "end": ocf["end"],
    }


def aggregate_cash_coverage(issuers):
    if not issuers or len({(row["start"], row["end"]) for row in issuers}) != 1:
        raise InsufficientEvidence("Synchronized issuer periods required")
    return ratio(sum(finite(row["ocf"]) for row in issuers), sum(finite(row["capex"]) for row in issuers))


def working_capital_days(beginning, ending, quarter_flow, days):
    if not 70 <= finite(days) <= 110 or min(finite(beginning), finite(ending)) < 0:
        raise InsufficientEvidence("Quarter days and nonnegative balances required")
    return ratio((beginning + ending) / 2, quarter_flow) * days


def backlog_residual(beginning, orders, recognized_revenue, ending, *, definitions_match):
    if not definitions_match:
        raise InsufficientEvidence("Backlog definitions do not reconcile")
    return finite(ending) - (finite(beginning) + finite(orders) - finite(recognized_revenue))


def power_residual(
    observed_mw, predicted_mw, *, history_days, weather_coverage, control_validated, interval_mwh_hours=1
):
    if history_days < 365 or weather_coverage < 0.95 or not control_validated:
        raise InsufficientEvidence("Weather/calendar history and control validation required")
    if interval_mwh_hours != 1:
        raise InsufficientEvidence("Only one-hour MWh can be numerically interpreted as average MW")
    return finite(observed_mw) - finite(predicted_mw)
