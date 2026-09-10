"""Numerical and point-in-time contracts for AI infrastructure evidence."""

import json
import math
from datetime import date, timedelta

import pytest

from scripts.ai_cycle.model import validate_observation
from scripts.ai_cycle.shadow import evaluate_shadow, walk_forward
from scripts.ai_cycle.snapshot import build_snapshot
from scripts.ai_cycle.store import ObservationStore
from scripts.ai_cycle.transforms import (
    InsufficientEvidence,
    aggregate_cash_coverage,
    backlog_residual,
    cash_coverage,
    complete_daily_windows,
    daily_activity,
    fiscal_quarters,
    fixed_inference_basket,
    matched_gpu_prices,
    power_residual,
    share_metrics,
    trailing_four_quarters,
    working_capital_days,
)


def observation(**updates):
    return dict(
        indicator_id="D1",
        series_id="total_tokens",
        source_id="openrouter",
        value=100,
        unit="tokens",
        period_start="2026-08-01",
        period_end="2026-08-02",
        published_at=None,
        fetched_at="2026-08-03T00:00:00Z",
        source_url="https://openrouter.ai/",
        raw_hash="a" * 64,
        methodology_version="v1",
        cohort_version="v1",
        lineage_group="openrouter",
        measurement="observed",
        metadata={},
        **updates,
    )


@pytest.mark.parametrize("value", [None, math.nan, math.inf, -math.inf, True, "1"])
def test_invalid_values(value):
    row = observation()
    row["value"] = value
    with pytest.raises(ValueError):
        validate_observation(row)


def test_store_vintages_and_unknown_publication():
    store = ObservationStore(":memory:")
    assert len(build_snapshot(store)["indicators"]) == 16
    first = observation()
    store.append_observations([first, first])
    revised = {**first, "value": 120, "fetched_at": "2026-08-05T00:00:00Z", "published_at": "2026-08-02T00:00:00Z"}
    store.append_observations([revised])
    assert store.read_observations("2026-08-02T00:00:00Z") == []
    assert len(store.read_observations("2026-08-04T00:00:00Z")) == 1
    assert len(store.read_observations("2026-08-06T00:00:00Z")) == 2
    old = build_snapshot(store, "2026-08-04T00:00:00Z")
    new = build_snapshot(store, "2026-08-06T00:00:00Z")
    assert old["indicators"][0]["metrics"][0]["value"] == 100
    assert new["indicators"][0]["metrics"][0]["value"] == 120
    assert new["indicators"][0]["status"] == "stale"
    assert new["shadow"]["state"] == "insufficient_evidence"


def test_batch_validation_before_writes():
    store = ObservationStore(":memory:")
    with pytest.raises(ValueError):
        store.append_observations([observation(), {**observation(), "value": None}])
    assert store.read_observations() == []


def test_cloud_writes_retry_transient_hrana_timeouts(monkeypatch):
    from scripts.db import hrana_http

    calls = []

    def flaky(sql, args):
        calls.append((sql, args))
        if len(calls) < 3:
            raise hrana_http.HranaHttpError("TimeoutError: read operation timed out")

    monkeypatch.setattr(hrana_http, "hrana_execute", flaky)
    monkeypatch.setattr("scripts.ai_cycle.store.time.sleep", lambda _delay: None)
    ObservationStore()._execute("INSERT OR IGNORE INTO test VALUES (?)", (1,))
    assert len(calls) == 3


def test_cloud_writes_fail_fast_on_statement_errors(monkeypatch):
    from scripts.db import hrana_http

    calls = []

    def broken(sql, args):
        calls.append((sql, args))
        raise hrana_http.HranaHttpError("SQLITE_CONSTRAINT: invalid row")

    monkeypatch.setattr(hrana_http, "hrana_execute", broken)
    with pytest.raises(hrana_http.HranaHttpError):
        ObservationStore()._execute("INSERT INTO test VALUES (?)", (1,))
    assert len(calls) == 1


def test_source_status_cloud_write_is_retry_idempotent(monkeypatch):
    from scripts.db import hrana_http

    calls = []
    monkeypatch.setattr(hrana_http, "hrana_execute", lambda sql, args: calls.append((sql, args)))
    store = ObservationStore()
    store._initialized = True
    store.record_source_status(
        dict(source_id="openrouter", status="available", reason="fixture", checked_at="2026-09-08T00:00:00Z")
    )
    assert "WHERE NOT EXISTS" in calls[0][0]
    assert calls[0][1][:2] == calls[0][1][2:]


def test_snapshot_reads_cloud_history_in_safe_large_pages(monkeypatch):
    store = ObservationStore()
    payload = json.dumps(observation())
    calls = []

    def query(sql, args):
        calls.append((sql, args))
        if len(calls) == 1:
            return [(row_id, payload) for row_id in range(1, 501)]
        return [(501, payload)]

    monkeypatch.setattr(store, "_query", query)
    assert len(store.read_snapshot_observations("2026-09-08T00:00:00Z")) == 501
    assert all("LIMIT 500" in sql for sql, _args in calls)
    assert calls[1][1][0] == 500


def test_snapshot_persist_reads_past_one_hundred_thousand_rows(monkeypatch):
    store = ObservationStore()
    payload = json.dumps(observation())
    calls = []

    def query(sql, args):
        calls.append(args[0])
        if len(calls) <= 201:
            start = (len(calls) - 1) * 500 + 1
            return [(row_id, payload) for row_id in range(start, start + 500)]
        return []

    monkeypatch.setattr(store, "_query", query)
    assert len(store.read_snapshot_observations("2026-09-08T00:00:00Z")) == 100_500
    assert len(calls) == 202


@pytest.mark.parametrize(
    "update",
    [
        {"fetched_at": "2026-08-02T12:00:00"},
        {"source_url": "https://x.test/?key=secret"},
        {"raw_hash": "x"},
        {"lineage_group": "independent"},
        {"period_end": "2026-01-01"},
    ],
)
def test_lineage_and_provenance_validation(update):
    with pytest.raises(ValueError):
        validate_observation({**observation(), **update})


def test_tokens_include_other_without_inventing_paid():
    value = daily_activity({"free": 10, "priced": 30}, 60, complete_day=True, free_model_ids={"free"})
    assert value == dict(total_tokens=100, visible_nonfree_tokens=30, other_share=0.6, paid_tokens=None)
    with pytest.raises(InsufficientEvidence):
        daily_activity({}, None, complete_day=True, free_model_ids=set())
    with pytest.raises(InsufficientEvidence):
        daily_activity({}, 1, complete_day=False, free_model_ids=set())


def test_windows_require_every_day():
    end = date(2026, 8, 31)
    points = [dict(date=(end - timedelta(days=i)).isoformat(), value=2 if i < 28 else 1) for i in range(56)]
    assert complete_daily_windows(points, end) == dict(mean_7d=2, sum_28d=56, growth_28d=1)
    with pytest.raises(InsufficientEvidence):
        complete_daily_windows(points[:-1], end)


def offer(provider, price, region="us"):
    return dict(
        provider=provider,
        region=region,
        gpu="H100 SXM",
        vram_gb=80,
        gpu_count=8,
        interconnect="NVLink",
        tenancy="dedicated",
        term="on-demand",
        price_per_gpu_hour=price,
    )


def test_provider_balanced_geometric_prices():
    before = [offer("a", 2), offer("a", 2, "eu"), offer("b", 4), offer("c", 8)]
    after = [offer("a", 1), offer("a", 1, "eu"), offer("b", 4), offer("c", 16)]
    result = matched_gpu_prices(before, after)
    assert result["price_relative"] == pytest.approx(1)
    assert result["provider_count"] == 3
    with pytest.raises(InsufficientEvidence):
        matched_gpu_prices(before, after[:-1])
    with pytest.raises(InsufficientEvidence):
        matched_gpu_prices(before, [{**row, "gpu_count": 1} for row in after])


def test_fixed_model_basket_cannot_drop_expensive_member():
    models = {"a": dict(input_price=1, output_price=3), "b": dict(input_price=10, output_price=30)}
    result = fixed_inference_basket(
        models, ["a", "b"], input_tokens=1_000_000, output_tokens=1_000_000, cohort_version="fixed-v2"
    )
    assert result["cost_usd"] == 22
    with pytest.raises(InsufficientEvidence):
        fixed_inference_basket({"a": models["a"]}, ["a", "b"], input_tokens=1, output_tokens=1, cohort_version="v2")


def test_fiscal_ytd_is_differenced_before_ttm():
    facts = [
        dict(start="2025-07-01", end=end, value=value, verified_statement=True)
        for end, value in [("2025-09-30", 10), ("2025-12-31", 25), ("2026-03-31", 45), ("2026-06-30", 80)]
    ]
    quarters = fiscal_quarters(facts)
    assert [row["value"] for row in quarters] == [10, 15, 20, 35]
    ttm = trailing_four_quarters(quarters)
    assert ttm["value"] == 80
    assert cash_coverage(ttm, {**ttm, "value": 100})["funding_gap"] == 20
    with pytest.raises(InsufficientEvidence):
        fiscal_quarters(facts[1:])
    with pytest.raises(InsufficientEvidence):
        fiscal_quarters([{**facts[0], "verified_statement": False}])
    with pytest.raises(InsufficientEvidence):
        cash_coverage(ttm, {**ttm, "start": "2025-01-01"})


def test_remaining_financial_and_power_contracts():
    assert (
        aggregate_cash_coverage(
            [dict(start="a", end="b", ocf=10, capex=20), dict(start="a", end="b", ocf=90, capex=30)]
        )
        == 2
    )
    assert working_capital_days(100, 200, 300, 90) == 45
    assert backlog_residual(100, 50, 30, 125, definitions_match=True) == 5
    assert share_metrics(20, 30, 10) == dict(relative_monetization=1.5, token_share_change_pp=10)
    with pytest.raises(InsufficientEvidence):
        power_residual(100, 90, history_days=65, weather_coverage=1, control_validated=True)


def weeks():
    return [
        dict(
            week_end=f"2026-08-{day:02d}",
            fetched_at=f"2026-08-{day + 1:02d}T00:00:00Z",
            published_at=None,
            coverage=0.8,
            gpu_providers=3,
            fresh=True,
            comparable=True,
            uncensored=True,
            independent_supply=True,
            demand_lineage="openrouter",
            demand_declining=True,
            prices_falling=True,
            supply_rising=True,
        )
        for day in [2, 9, 16, 23]
    ]


def test_shadow_warmup_staleness_and_vintage():
    rows = weeks()
    assert evaluate_shadow(rows, "2026-08-24T00:00:00Z")["state"] == "watch"
    assert evaluate_shadow(rows, "2026-08-20T00:00:00Z")["state"] == "insufficient_evidence"
    assert evaluate_shadow(rows, "2026-09-20T00:00:00Z")["state"] == "insufficient_evidence"
    rows[-1]["coverage"] = 0.79
    assert evaluate_shadow(rows, "2026-08-24T00:00:00Z")["state"] == "insufficient_evidence"
    report = walk_forward(weeks())
    assert report["watch_episodes"] == 1 and report["precision"] is None


def test_fractional_vintage_order_is_chronological():
    store = ObservationStore(":memory:")
    store.append_observations([{**observation(), "fetched_at": "2026-08-03T00:00:00.123Z"}])
    assert store.read_observations("2026-08-03T00:00:00Z") == []
    assert len(store.read_observations("2026-08-03T00:00:00.124Z")) == 1


def test_snapshot_keeps_fixed_historical_floor_and_reports_source_coverage():
    store = ObservationStore(":memory:")
    rows = [
        {
            **observation(),
            "indicator_id": "H2",
            "source_id": "sec",
            "lineage_group": "sec",
            "series_id": "NVDA.InventoryNet",
            "period_start": "2010-01-01",
            "period_end": "2010-01-01",
            "published_at": "2010-02-01T00:00:00Z",
            "fetched_at": "2026-08-03T00:00:00Z",
        },
        {**observation(), "period_start": "2025-01-01", "period_end": "2025-01-01"},
    ]
    store.append_observations(rows)
    snapshot = build_snapshot(store, "2026-08-04T00:00:00Z")
    sources = {source["id"]: source for source in snapshot["sources"]}
    assert sources["sec"]["observed_from"] == "2010-01-01T00:00:00.000000Z"
    assert sources["sec"]["observed_through"] == "2010-01-01T00:00:00.000000Z"
    assert sources["sec"]["observation_count"] == 1
    assert sources["openrouter"]["observed_from"] == "2025-01-01T00:00:00.000000Z"
    hardware = next(item for item in snapshot["indicators"] if item["id"] == "H2")
    assert hardware["history"][0]["date"] == "2010-01-01T00:00:00.000000Z"


def test_snapshot_derives_complete_demand_and_fixed_basket():
    store = ObservationStore(":memory:")
    rows = []
    end = date(2026, 8, 31)
    for i in range(56):
        day = (end - timedelta(days=i)).isoformat()
        for model, value in [("paid", 20 if i < 28 else 10), ("other", 20 if i < 28 else 10)]:
            rows.append(
                {
                    **observation(),
                    "period_start": day,
                    "period_end": day,
                    "series_id": model,
                    "value": value,
                    "fetched_at": day + "T23:59:59Z",
                }
            )
    for model, price in [("a", 1), ("b", 3)]:
        rows.append(
            {
                **observation(),
                "indicator_id": "C3",
                "source_id": "artificial-analysis",
                "lineage_group": "artificial-analysis",
                "series_id": model,
                "period_start": "2026-08-31",
                "period_end": "2026-08-31",
                "fetched_at": "2026-08-31T23:59:59Z",
                "metadata": dict(
                    required_members=["a", "b"],
                    input_price=price,
                    output_price=price,
                    input_tokens=1_000_000,
                    output_tokens=1_000_000,
                ),
            }
        )
    store.append_observations(rows)
    snapshot = build_snapshot(store, "2026-09-01T00:00:00Z")
    demand = next(row for row in snapshot["indicators"] if row["id"] == "D1")
    assert next(metric["value"] for metric in demand["metrics"] if metric["id"] == "growth_28d") == 1
    compute = next(row for row in snapshot["indicators"] if row["id"] == "C3")
    assert next(metric["value"] for metric in compute["metrics"] if metric["id"] == "fixed_inference_basket") == 4
    assert compute["status"] == "available"


def test_complete_observation_pipeline_reaches_experimental_watch():
    store = ObservationStore(":memory:")
    end = date(2026, 8, 30)  # completed Sunday
    rows = []
    for offset in range(90):
        day = end - timedelta(days=offset)
        fetched = (day + timedelta(days=1)).isoformat() + "T00:00:00Z"
        for model in ("paid", "other"):
            rows.append(
                {
                    **observation(),
                    "period_start": day.isoformat(),
                    "period_end": day.isoformat(),
                    "series_id": model,
                    "value": 100 + offset,
                    "fetched_at": fetched,
                }
            )
        for provider in ("a", "b", "c"):
            rows.append(
                {
                    **observation(),
                    "indicator_id": "C1",
                    "source_id": "gpu-rental",
                    "lineage_group": "gpu-aggregator",
                    "series_id": provider,
                    "value": 2 + offset / 100,
                    "period_start": day.isoformat(),
                    "period_end": day.isoformat(),
                    "fetched_at": fetched,
                    "metadata": offer(provider, 1),
                }
            )
        rows.append(
            {
                **observation(),
                "indicator_id": "C2",
                "source_id": "vast",
                "lineage_group": "vast",
                "series_id": "US.H100_SXM.gpus",
                "value": 200 - offset,
                "period_start": day.isoformat(),
                "period_end": day.isoformat(),
                "fetched_at": fetched,
                "metadata": {"truncated": False},
            }
        )
    store.append_observations(rows)
    for source in ("openrouter", "gpu-rental", "vast"):
        store.record_source_status(
            dict(source_id=source, status="available", reason="fixture validated", checked_at="2026-08-31T00:00:00Z")
        )
    snapshot = build_snapshot(store, "2026-08-31T12:00:00Z")
    assert snapshot["shadow"]["state"] == "watch"
    assert snapshot["shadow"]["eligible_weeks"] == 4
    store.record_source_status(
        dict(source_id="vast", status="error", reason="provider unavailable", checked_at="2026-08-31T01:00:00Z")
    )
    assert build_snapshot(store, "2026-08-31T12:00:00Z")["shadow"]["state"] == "insufficient_evidence"
    # Simulated time before four complete weekly comparisons must never see future rows.
    assert build_snapshot(store, "2026-08-03T12:00:00Z")["shadow"]["state"] == "insufficient_evidence"


def test_f1_snapshot_uses_ttm_and_separate_amazon_definition():
    store = ObservationStore(":memory:")
    rows = []
    for tag, multiple in [("NetCashProvidedByUsedInOperatingActivities", 2), ("PaymentsToAcquireProductiveAssets", 1)]:
        for end, value in [("2025-03-31", 10), ("2025-06-30", 30), ("2025-09-30", 60), ("2025-12-31", 100)]:
            rows.append(
                {
                    **observation(),
                    "indicator_id": "F1",
                    "source_id": "sec",
                    "lineage_group": "sec",
                    "series_id": f"AMZN.{tag}",
                    "period_start": "2025-01-01",
                    "period_end": end,
                    "fetched_at": "2026-02-01T00:00:00Z",
                    "value": value * multiple,
                    "metadata": dict(entity="AMZN", tag=tag, verified_statement=True),
                }
            )
    store.append_observations(rows)
    panel = next(row for row in build_snapshot(store, "2026-02-02T00:00:00Z")["indicators"] if row["id"] == "F1")
    assert next(row["value"] for row in panel["metrics"] if row["id"] == "AMZN.ttm_coverage") == 2
    assert next(row["value"] for row in panel["metrics"] if row["id"] == "AMZN.ttm_funding_gap") == -100


def test_latest_gateway_cohort_does_not_mark_retired_models_stale():
    store = ObservationStore(":memory:")
    rows = []
    for entity, day in [("retired", "2026-01-01"), ("current", "2026-08-31")]:
        rows.append(
            {
                **observation(),
                "indicator_id": "D3",
                "source_id": "vercel",
                "lineage_group": "vercel",
                "series_id": entity,
                "value": 100,
                "period_start": day,
                "period_end": day,
                "fetched_at": "2026-09-01T00:00:00Z",
                "metadata": {"entity": entity},
            }
        )
    store.append_observations(rows)
    panel = next(row for row in build_snapshot(store, "2026-09-01T00:00:00Z")["indicators"] if row["id"] == "D3")
    assert panel["status"] == "available"
    assert [row["id"] for row in panel["metrics"]] == ["current"]
    assert len(panel["history"]) == 2


def test_compact_application_metrics_lead_legacy_per_app_rows():
    store = ObservationStore(":memory:")
    rows = []
    for series, method in [
        ("app.legacy.total_tokens", "1"),
        ("returned_requests", "openrouter-app-aggregate-v2"),
        ("tokens_per_request", "openrouter-app-aggregate-v2"),
        ("top10_token_concentration", "openrouter-app-aggregate-v2"),
    ]:
        rows.append(
            {
                **observation(),
                "indicator_id": "D2",
                "series_id": series,
                "methodology_version": method,
                "period_start": "2026-08-31",
                "period_end": "2026-08-31",
                "fetched_at": "2026-09-01T00:00:00Z",
            }
        )
    store.append_observations(rows)
    panel = next(row for row in build_snapshot(store, "2026-09-01T01:00:00Z")["indicators"] if row["id"] == "D2")
    assert [metric["id"] for metric in panel["metrics"][:3]] == [
        "returned_requests",
        "tokens_per_request",
        "top10_token_concentration",
    ]


def test_same_fetch_sec_revisions_choose_latest_publication_not_insert_order():
    store = ObservationStore(":memory:")
    base = {
        **observation(),
        "indicator_id": "F1",
        "source_id": "sec",
        "lineage_group": "sec",
        "fetched_at": "2026-08-10T00:00:00Z",
    }
    newest = {**base, "value": 120, "published_at": "2026-08-08T00:00:00Z", "metadata": {"accession": "0002"}}
    older = {**base, "value": 100, "published_at": "2026-08-04T00:00:00Z", "metadata": {"accession": "0001"}}
    store.append_observations([newest, older])
    assert store.read_snapshot_observations("2026-08-11T00:00:00Z")[0]["value"] == 120
    assert len(store.read_observations("2026-08-11T00:00:00Z")) == 2


def test_financial_ttm_does_not_adopt_unusable_later_period():
    from scripts.ai_cycle.snapshot import derived_observations

    rows = []
    for end, value in [("2025-03-31", 10), ("2025-06-30", 30), ("2025-09-30", 60), ("2025-12-31", 100)]:
        rows.append(
            validate_observation(
                {
                    **observation(),
                    "indicator_id": "F1",
                    "source_id": "sec",
                    "lineage_group": "sec",
                    "series_id": "MSFT.tag",
                    "period_start": "2025-01-01",
                    "period_end": end,
                    "fetched_at": "2026-03-01T00:00:00Z",
                    "value": value,
                    "metadata": {
                        "entity": "META",
                        "tag": "NetCashProvidedByUsedInOperatingActivities",
                        "verified_statement": True,
                    },
                }
            )
        )
    rows.append({**rows[0], "period_start": "2026-01-01T00:00:00.000000Z", "period_end": "2026-02-01T00:00:00.000000Z"})
    result = derived_observations(rows, "2026-03-02T00:00:00.000000Z")
    assert result[0]["period_end"] == "2025-12-31"
    assert result[0]["value"] == 100


def test_shadow_rejects_cohort_switch_and_ambiguous_weekly_inputs():
    from scripts.ai_cycle.snapshot import weekly_evidence

    rows = weeks()
    rows[0]["cohort_signature"] = "old"
    rows[1]["cohort_signature"] = "new"
    assert evaluate_shadow(rows, "2026-08-24T00:00:00Z")["state"] == "insufficient_evidence"
    demand = {**observation(), "series_id": "growth_28d", "period_end": "2026-08-23", "metadata": {"complete": True}}
    assert weekly_evidence([demand, {**demand, "cohort_version": "second"}], "2026-08-24T00:00:00Z") == []


def test_gateway_leads_with_largest_lab_token_shares_before_price_ratios():
    store = ObservationStore(":memory:")
    rows = []
    for dataset, entity, metric, value in [
        ("labs", "small", "tokens", 1),
        ("labs", "anthropic", "tokens", 60),
        ("labs", "anthropic", "spend", 90),
        ("labs", "google", "tokens", 39),
        ("models", "large-model", "tokens", 100),
    ]:
        rows.append(
            {
                **observation(),
                "indicator_id": "D3",
                "source_id": "vercel",
                "lineage_group": "vercel",
                "series_id": f"{dataset}.{metric}.{entity}",
                "value": value,
                "period_start": "2026-08-31",
                "period_end": "2026-08-31",
                "fetched_at": "2026-09-01T00:00:00Z",
                "metadata": dict(dataset=dataset, entity=entity, metric=metric),
            }
        )
    store.append_observations(rows)
    panel = next(row for row in build_snapshot(store, "2026-09-01T00:00:00Z")["indicators"] if row["id"] == "D3")
    assert [metric["id"] for metric in panel["metrics"][:3]] == [
        "labs.tokens.anthropic",
        "labs.tokens.google",
        "labs.tokens.small",
    ]
    derived = next(metric for metric in panel["metrics"] if metric["id"] == "labs.anthropic.relative_monetization")
    assert derived["label"] == "Anthropic · spend share / token share"
    assert derived["value"] == 1.5


def test_same_day_token_revision_replaces_membership_including_dropped_model():
    store = ObservationStore(":memory:")
    for fetched, members in [
        ("2026-08-03T00:00:00Z", {"a": 10, "b": 20, "other": 30}),
        ("2026-08-03T01:00:00Z", {"a": 15, "c": 25, "other": 40}),
    ]:
        store.append_observations(
            [
                {**observation(), "series_id": name, "value": value, "fetched_at": fetched}
                for name, value in members.items()
            ]
        )
    panel = build_snapshot(store, "2026-08-03T02:00:00Z")["indicators"][0]
    assert "b" not in {row["id"] for row in panel["metrics"]}
    assert next(row["value"] for row in panel["metrics"] if row["id"] == "total_tokens") == 80
    assert not any(row["series_id"].startswith("b:") for row in panel["history"])
    old = build_snapshot(store, "2026-08-03T00:30:00Z")["indicators"][0]
    assert next(row["value"] for row in old["metrics"] if row["id"] == "total_tokens") == 60


def test_same_day_gateway_revision_drops_old_entity_and_retains_other_metric_response():
    store = ObservationStore(":memory:")
    for fetched, metric, shares in [
        ("2026-08-03T00:00:00Z", "tokens", {"a": 60, "b": 40}),
        ("2026-08-03T00:30:00Z", "spend", {"a": 100}),
        ("2026-08-03T01:00:00Z", "tokens", {"a": 70, "c": 30}),
    ]:
        store.append_observations(
            [
                {
                    **observation(),
                    "indicator_id": "D3",
                    "source_id": "vercel",
                    "lineage_group": "vercel",
                    "series_id": f"labs.{metric}.{name}",
                    "value": value,
                    "fetched_at": fetched,
                    "metadata": dict(dataset="labs", metric=metric, entity=name),
                }
                for name, value in shares.items()
            ]
        )
    panel = next(row for row in build_snapshot(store, "2026-08-03T02:00:00Z")["indicators"] if row["id"] == "D3")
    tokens = [row for row in panel["metrics"] if row["metadata"].get("metric") == "tokens"]
    assert sum(row["value"] for row in tokens) == 100
    assert {row["id"] for row in tokens} == {"labs.tokens.a", "labs.tokens.c"}
    assert any(row["id"] == "labs.spend.a" for row in panel["metrics"])


def test_same_day_gpu_response_does_not_resurrect_removed_offer():
    store = ObservationStore(":memory:")
    for fetched, providers in [("2026-08-03T00:00:00Z", ("a", "b", "c")), ("2026-08-03T01:00:00Z", ("a", "c"))]:
        store.append_observations(
            [
                {
                    **observation(),
                    "indicator_id": "C1",
                    "source_id": "gpu-rental",
                    "lineage_group": "gpu-aggregator",
                    "series_id": provider,
                    "cohort_version": provider,
                    "value": 3,
                    "fetched_at": fetched,
                    "metadata": offer(provider, 3),
                }
                for provider in providers
            ]
        )
    panel = next(row for row in build_snapshot(store, "2026-08-03T02:00:00Z")["indicators"] if row["id"] == "C1")
    assert {row["id"] for row in panel["metrics"]} == {"a", "c"}


def test_interrupted_response_insert_is_incomplete_and_not_promoted(monkeypatch):
    store = ObservationStore(":memory:")
    store.initialize()
    original = store._execute
    calls = 0

    def interrupted(sql, args=()):
        nonlocal calls
        if sql.startswith("INSERT OR IGNORE INTO ai_cycle_observations"):
            calls += 1
            if calls == 2:
                raise RuntimeError("simulated writer interruption")
        return original(sql, args)

    monkeypatch.setattr(store, "_execute", interrupted)
    rows = [{**observation(), "series_id": f"model-{i}"} for i in range(100)] + [
        {**observation(), "series_id": "other"}
    ]
    with pytest.raises(RuntimeError, match="writer interruption"):
        store.append_observations(rows)
    panel = build_snapshot(store, "2026-08-03T02:00:00Z")["indicators"][0]
    assert panel["status"] == "incomplete"
    assert panel["metrics"] == []


def test_rolling_financial_history_has_each_exact_ttm_window_and_its_own_provenance():
    store = ObservationStore(":memory:")
    rows = []
    periods = [
        ("2025-01-01", "2025-03-31"),
        ("2025-01-01", "2025-06-30"),
        ("2025-01-01", "2025-09-30"),
        ("2025-01-01", "2025-12-31"),
        ("2026-01-01", "2026-03-31"),
    ]
    for tag, values in [
        ("NetCashProvidedByUsedInOperatingActivities", [20, 60, 120, 200, 100]),
        ("PaymentsToAcquirePropertyPlantAndEquipment", [10, 20, 30, 40, 10]),
    ]:
        for index, ((start, end), value) in enumerate(zip(periods, values, strict=True)):
            rows.append(
                {
                    **observation(),
                    "indicator_id": "F1",
                    "source_id": "sec",
                    "lineage_group": "sec",
                    "series_id": "META." + tag,
                    "period_start": start,
                    "period_end": end,
                    "value": value,
                    "fetched_at": "2026-01-01T00:00:00Z" if index < 4 else "2026-04-01T00:00:00Z",
                    "raw_hash": f"{index + 1:064x}",
                    "metadata": dict(entity="META", tag=tag, verified_statement=True),
                }
            )
    store.append_observations(rows)
    panel = next(row for row in build_snapshot(store, "2026-04-02T00:00:00Z")["indicators"] if row["id"] == "F1")
    history = [row for row in panel["history"] if row["series_id"].startswith("META.ttm_coverage:")]
    assert [(row["date"], row["value"]) for row in history] == [("2025-12-31", 5), ("2026-03-31", 7)]
    latest = next(row for row in panel["metrics"] if row["id"] == "META.ttm_coverage")
    assert latest["value"] == 7
    assert latest["period_start"] == "2025-04-01"
    from scripts.ai_cycle.snapshot import derived_observations

    derived = derived_observations(store.read_observations(), "2026-04-02T00:00:00Z")
    first = next(
        row for row in derived if row["series_id"] == "META.ttm_coverage" and row["period_end"] == "2025-12-31"
    )
    assert first["fetched_at"] == "2026-01-01T00:00:00.000000Z"
    assert f"{5:064x}" not in first["metadata"]["input_hashes"]


def test_api_snapshot_is_compact_and_skips_history_scan_on_read():
    from datetime import datetime, timedelta, timezone

    from scripts.ai_cycle.snapshot import API_SNAPSHOT_MAX_BYTES, compact_snapshot, load_api_snapshot, persist_api_snapshot
    from scripts.db.hrana_http import _MAX_RESPONSE_BYTES

    store = ObservationStore(":memory:")
    store.record_source_status(
        dict(source_id="openrouter", status="available", reason="fixture", checked_at="2026-09-09T07:15:00Z")
    )
    store.append_observations(
        [
            {
                **observation(),
                "value": 10,
                "period_start": "2026-09-01",
                "period_end": "2026-09-02",
                "fetched_at": "2026-09-03T00:00:00Z",
            }
        ]
    )
    persist_api_snapshot(store)
    payload = store.read_api_snapshot()
    encoded = json.dumps(payload)
    assert payload["version"] == 1
    assert payload["indicators"][0]["metrics"][0]["value"] == 10
    assert len(encoded.encode()) < API_SNAPSHOT_MAX_BYTES < _MAX_RESPONSE_BYTES
    store.read_snapshot_observations = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("GET must not scan observation history")
    )
    loaded = load_api_snapshot(store)
    assert loaded["indicators"][0]["metrics"][0]["value"] == 10

    empty = ObservationStore(":memory:")
    empty.record_source_status(
        dict(source_id="vercel", status="available", reason="status only", checked_at="2026-09-09T07:15:00Z")
    )
    empty.read_snapshot_observations = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("missing compact snapshot must not scan history")
    )
    fallback = load_api_snapshot(empty)
    vercel = next(source for source in fallback["sources"] if source["id"] == "vercel")
    assert vercel["status"] == "available"
    assert all(not indicator["metrics"] for indicator in fallback["indicators"])

    base = datetime(2020, 1, 1, tzinfo=timezone.utc)
    bloated = build_snapshot(ObservationStore(":memory:"))
    bloated["indicators"][0]["history"] = [
        {
            "date": (base + timedelta(days=i % 2500)).date().isoformat(),
            "value": float(i),
            "unit": "tokens",
            "series_id": f"series-{i % 120}",
            "label": f"series-{i % 120}",
            "source_id": "openrouter",
        }
        for i in range(80_000)
    ]
    bloated["indicators"][0]["metrics"] = [
        {
            "id": "total_tokens",
            "label": "total",
            "value": 1,
            "unit": "tokens",
            "period_start": "2026-09-01",
            "period_end": "2026-09-02",
            "published_at": None,
            "fetched_at": "2026-09-03T00:00:00Z",
            "source_id": "openrouter",
            "source_url": "https://openrouter.ai/",
            "measurement": "observed",
            "methodology_version": "v1",
            "cohort_version": "v1",
            "lineage_group": "openrouter",
            "raw_hash": "a" * 64,
            "metadata": {},
        }
    ]
    compact = compact_snapshot(bloated)
    assert len(json.dumps(compact).encode()) < API_SNAPSHOT_MAX_BYTES
    assert compact["indicators"][0]["metrics"][0]["value"] == 1
    assert {source["id"] for source in compact["sources"]} == {source["id"] for source in bloated["sources"]}


def test_raw_archive_import_is_durable_and_idempotent(tmp_path):
    store = ObservationStore(":memory:")
    raw = b'{"publisher":"openrouter","rows":[1]}'
    digest = __import__("hashlib").sha256(raw).hexdigest()
    (tmp_path / f"{digest}.json").write_bytes(raw)
    (tmp_path / "not-a-digest.json").write_bytes(b"{}")
    (tmp_path / ".raw-temp.tmp").write_bytes(raw)
    assert store.import_raw_archive(tmp_path) == 1
    assert store.import_raw_archive(tmp_path) == 1
    assert store._query("SELECT COUNT(*) FROM ai_cycle_raw")[0][0] == 1
    assert store._query("SELECT hash FROM ai_cycle_raw")[0][0] == digest
