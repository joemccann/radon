"""Destructive cleanup requires proven, exclusive constituent executions."""
import copy
import json
import sqlite3

import pytest

from scripts import cleanup_legacy_flex_aggregates as cleanup
from scripts.clients.journal_basis import _derive_journal_state_from_rows
from scripts.clients.journal_realized import realized_pnl_by_exec_id


def fill(exec_id, qty=5, day="2026-08-03", action="BUY_OPTION"):
    return dict(ib_exec_id=exec_id, ticker="SPY", strike=600, right="C",
                expiry="20261016", action=action, contracts=qty, date=day,
                fill_price=2, total_cost=qty * 200, commission=0)


def rows():
    return [("agg", fill("old.1+old.2", 10)),
            ("one", fill("old.1")), ("two", fill("old.2"))]


def ambiguous(case):
    data = rows()
    if case == "disjoint_later":
        data[1][1].update(ib_exec_id="new.1", date="2026-09-01")
        data[2][1].update(ib_exec_id="new.2", date="2026-09-01")
    elif case == "partial_overlap":
        data[2][1]["ib_exec_id"] = "unrelated"
    elif case == "correction":
        data[2][1]["ib_exec_id"] = "old.3"
    elif case == "same_size_aggregates":
        data.insert(1, ("other", fill("else.1+else.2", 10)))
        data[2][1]["ib_exec_id"] = "new.1"
        data[3][1]["ib_exec_id"] = "new.2"
    elif case == "shared_claim":
        data.insert(1, ("other", fill("old.1+old.2", 10)))
    elif case == "stamped_claim":
        other = fill("old.1+old.2", 10)
        other["fill_breakdown"] = [{"date": other["date"], "qty": 10}]
        data.insert(1, ("other", other))
    elif case == "duplicate_individual":
        data.append(("duplicate", copy.deepcopy(data[1][1])))
    elif case == "duplicate_parts":
        data[0][1]["ib_exec_id"] = "old.1+old.1"
    elif case == "empty_part":
        data[0][1]["ib_exec_id"] = "old.1++old.2"
    elif case == "wrong_contract":
        data[2][1]["strike"] = 610
    elif case == "wrong_quantity":
        data[2][1]["contracts"] = 6
    elif case == "wrong_account":
        data[0][1]["account_id"] = "A"
        data[1][1]["account_id"] = "B"
    elif case == "invalid_date":
        data[1][1]["date"] = "not-a-date"
    elif case == "later_date":
        data[1][1]["date"] = data[2][1]["date"] = "2026-09-01"
    elif case == "nonfinite_quantity":
        data[1][1]["contracts"] = float("inf")
    elif case == "hidden_turnover":
        data[0][1]["total_round_trip_quantity"] = 15
    return data


CASES = ["disjoint_later", "partial_overlap", "correction", "same_size_aggregates",
         "shared_claim", "stamped_claim", "duplicate_individual", "duplicate_parts",
         "empty_part", "wrong_contract", "wrong_quantity", "wrong_account",
         "invalid_date", "later_date", "nonfinite_quantity", "hidden_turnover"]


@pytest.mark.parametrize("case", CASES)
def test_ambiguous_coverage_never_authorizes_a_mutation(case):
    data = ambiguous(case)
    before = copy.deepcopy(data)
    plan = cleanup.plan_cleanup(data)
    assert plan["delete"] == []
    assert plan["redate"] == []
    assert "agg" in {item["trade_id"] for item in plan["unreconstructable"]}
    assert data == before


def database(data):
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE journal (trade_id TEXT PRIMARY KEY, payload TEXT)")
    db.executemany("INSERT INTO journal VALUES (?, ?)", [(k, json.dumps(v)) for k, v in data])
    db.commit()
    return db


def test_apply_disjoint_fills_preserves_quantity_basis_and_realized_pnl(monkeypatch):
    data = ambiguous("disjoint_later")
    data.append(("close", fill("close.1", 5, "2026-09-02", "SELL_OPTION")))
    db = database(data)
    monkeypatch.setattr(cleanup, "_connect", lambda: db)
    def state():
        raw = db.execute("SELECT payload FROM journal ORDER BY trade_id").fetchall()
        journal = [(p, json.loads(p)["date"], json.loads(p)["date"]) for (p,) in raw]
        return raw, _derive_journal_state_from_rows(journal, basis_tickers=["SPY"],
            option_net_keys=["SPY|20261016|C|600"]), realized_pnl_by_exec_id(journal)
    before = state()
    assert cleanup.main(["--apply"]) == 0
    assert state() == before


def test_exact_duplicate_apply_retains_canonical_fills_and_is_idempotent(monkeypatch):
    db = database(rows())
    monkeypatch.setattr(cleanup, "_connect", lambda: db)
    expected = db.execute("SELECT trade_id, payload FROM journal WHERE trade_id != 'agg' ORDER BY trade_id").fetchall()
    assert cleanup.main(["--apply"]) == 0
    assert db.execute("SELECT trade_id, payload FROM journal ORDER BY trade_id").fetchall() == expected
    assert cleanup.main(["--apply"]) == 0
    assert db.execute("SELECT trade_id, payload FROM journal ORDER BY trade_id").fetchall() == expected


def test_dry_run_never_writes_even_a_proven_duplicate(monkeypatch):
    db = database(rows())
    monkeypatch.setattr(cleanup, "_connect", lambda: db)
    statements = []
    db.set_trace_callback(statements.append)
    assert cleanup.main([]) == 0
    assert all(sql.startswith("SELECT") for sql in statements)
    assert db.execute("SELECT count(*) FROM journal").fetchone()[0] == 3


def test_exact_closed_multiday_members_can_be_removed():
    agg = fill("open.1+close.1", 10, action="CLOSED")
    plan = cleanup.plan_cleanup([("agg", agg), ("open", fill("open.1", 10)),
        ("close", fill("close.1", 10, "2026-08-05", "SELL_OPTION"))])
    assert [x["trade_id"] for x in plan["delete"]] == ["agg"]
    assert plan["redate"] == []
    assert plan["delete"][0]["breakdown"] == [
        {"date": "2026-08-03", "qty": 10}, {"date": "2026-08-05", "qty": -10}]


@pytest.mark.parametrize("gross,allowed", [
    ([{"date": "2026-08-03", "qty": 10}], True),
    ([{"date": "2026-08-04", "qty": 10}], False),
    ([], False),
    ([{"date": "2026-08-03", "qty": "bad"}], False),
])
def test_gross_coverage_must_agree_with_proven_constituents(gross, allowed):
    data = rows()
    data[0][1]["gross_fill_breakdown"] = gross
    plan = cleanup.plan_cleanup(data)
    assert bool(plan["delete"]) is allowed
    assert bool(plan["unreconstructable"]) is not allowed
    assert plan["redate"] == []


def test_missing_identity_and_invalid_aggregate_date_are_reported():
    data = [("closed", {**fill("", 10), "action": "CLOSED"}),
            ("bad-day", {**fill("x+y", 10), "date": "unknown"}),
            ("empty", "not json"), ("list", "[]")]
    plan = cleanup.plan_cleanup(data)
    assert plan["delete"] == plan["redate"] == []
    assert {x["trade_id"] for x in plan["unreconstructable"]} == {"closed", "bad-day"}
