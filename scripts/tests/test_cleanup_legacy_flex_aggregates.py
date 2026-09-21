"""Retro-cleanup of legacy Flex aggregate journal rows (PR #569 follow-up)."""

from __future__ import annotations

import json

from scripts.cleanup_legacy_flex_aggregates import plan_cleanup, render_plan


def _row(trade_id: str, payload: dict) -> tuple[str, str]:
    return (trade_id, json.dumps(payload))


def _fill(date: str, ticker: str, action: str, shares: float, exec_id: str) -> dict:
    return {
        "date": date,
        "ticker": ticker,
        "action": action,
        "shares": shares,
        "ib_exec_id": exec_id,
    }


def _closed_aggregate(date: str, ticker: str, qty: float, exec_ids: list[str]) -> dict:
    return {
        "date": date,
        "ticker": ticker,
        "action": "CLOSED",
        "shares": qty,
        "ib_exec_id": "+".join(exec_ids),
    }


def test_aggregate_beside_individual_fills_is_deleted():
    rows = [
        _row("agg", _closed_aggregate("2026-08-11", "SPY", 900, ["a.1", "a.2", "a.3"])),
        _row("f1", _fill("2026-08-11", "SPY", "BUY", 600, "a.1")),
        _row("f2", _fill("2026-08-11", "SPY", "BUY", 300, "a.2")),
        _row("f3", _fill("2026-08-12", "SPY", "SELL", 900, "a.3")),
    ]
    plan = plan_cleanup(rows)
    assert [item["trade_id"] for item in plan["delete"]] == ["agg"]
    assert plan["redate"] == []


def test_multiday_partial_coverage_is_reported_without_fabricating_dates():
    # The absent buy executions cannot establish their own dates or identity.
    rows = [
        _row("agg", _closed_aggregate("2026-08-11", "QQQ", 500, ["b.1", "b.2", "b.3"])),
        _row("s1", _fill("2026-08-11", "QQQ", "SELL", 200, "b.1")),
        _row("s2", _fill("2026-08-13", "QQQ", "SELL", 300, "b.2")),
    ]
    plan = plan_cleanup(rows)
    assert plan["delete"] == []
    assert plan["redate"] == []
    assert [item["trade_id"] for item in plan["unreconstructable"]] == ["agg"]


def test_aggregate_with_only_one_leg_covered_is_not_deleted():
    # Deleting this row would lose the sell leg, which no individual fill
    # carries. It is single-day, so there is nothing to re-date either.
    rows = [
        _row("agg", _closed_aggregate("2026-08-11", "IWM", 100, ["1", "2"])),
        _row("f1", _fill("2026-08-11", "IWM", "BUY", 100, "c.1")),
    ]
    plan = plan_cleanup(rows)
    assert plan["delete"] == plan["redate"] == []
    assert [item["trade_id"] for item in plan["unreconstructable"]] == ["agg"]


def test_aggregate_already_carrying_breakdown_is_untouched():
    payload = _closed_aggregate("2026-08-11", "SPY", 900, ["a.1", "a.2"])
    payload["fill_breakdown"] = [{"date": "2026-08-11", "qty": 900}]
    rows = [
        _row("agg", payload),
        _row("f1", _fill("2026-08-11", "SPY", "BUY", 900, "a.1")),
        _row("f2", _fill("2026-08-12", "SPY", "SELL", 900, "a.2")),
    ]
    plan = plan_cleanup(rows)
    assert plan == {"delete": [], "redate": [], "unreconstructable": []}


def test_non_aggregate_rows_are_never_planned():
    rows = [
        _row("f1", _fill("2026-08-11", "SPY", "BUY", 900, "a.1")),
        _row("f2", _fill("2026-08-12", "SPY", "SELL", 900, "a.2")),
    ]
    assert plan_cleanup(rows) == {"delete": [], "redate": [], "unreconstructable": []}


def test_unreconstructable_aggregate_is_reported_not_changed():
    rows = [
        _row("agg", _closed_aggregate("2026-08-11", "BTU", 700, ["1", "2"])),
        _row("f1", _fill("2026-08-11", "BTU", "BUY", 123, "d.1")),
    ]
    plan = plan_cleanup(rows)
    assert plan["delete"] == []
    assert plan["redate"] == []
    assert [item["trade_id"] for item in plan["unreconstructable"]] == ["agg"]


def test_overshooting_coverage_does_not_redate():
    # 200 + 400 overshoots the 500 budget: no exact prefix, so no guess.
    rows = [
        _row("agg", _closed_aggregate("2026-08-11", "QQQ", 500, ["1", "2"])),
        _row("s1", _fill("2026-08-11", "QQQ", "SELL", 200, "e.1")),
        _row("s2", _fill("2026-08-13", "QQQ", "SELL", 400, "e.2")),
    ]
    plan = plan_cleanup(rows)
    assert plan["redate"] == []
    assert [item["trade_id"] for item in plan["unreconstructable"]] == ["agg"]


def test_fills_before_the_aggregate_date_are_not_borrowed():
    rows = [
        _row("agg", _closed_aggregate("2026-08-11", "QQQ", 500, ["1", "2"])),
        _row("s0", _fill("2026-08-01", "QQQ", "SELL", 500, "f.0")),
    ]
    plan = plan_cleanup(rows)
    assert plan["delete"] == plan["redate"] == []
    assert [item["trade_id"] for item in plan["unreconstructable"]] == ["agg"]


def test_plan_is_idempotent_after_applying_a_proven_delete():
    rows = [
        _row("agg", _closed_aggregate("2026-08-11", "QQQ", 500, ["b.1", "b.2"])),
        _row("b1", _fill("2026-08-11", "QQQ", "BUY", 500, "b.1")),
        _row("s2", _fill("2026-08-13", "QQQ", "SELL", 500, "b.2")),
    ]
    plan = plan_cleanup(rows)
    assert [item["trade_id"] for item in plan["delete"]] == ["agg"]
    assert plan_cleanup(rows[1:]) == {"delete": [], "redate": [], "unreconstructable": []}


def test_render_plan_prints_every_planned_change():
    rows = [
        _row("agg", _closed_aggregate("2026-08-11", "SPY", 900, ["a.1", "a.2"])),
        _row("f1", _fill("2026-08-11", "SPY", "BUY", 900, "a.1")),
        _row("f2", _fill("2026-08-12", "SPY", "SELL", 900, "a.2")),
    ]
    text = render_plan(plan_cleanup(rows))
    assert "DELETE agg" in text
    assert "delete (class B" in text
