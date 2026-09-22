"""Rebuild gross_fill_breakdown on legacy Flex aggregates from saved statements."""

from __future__ import annotations

import json
import sqlite3

import pytest

from scripts import rebuild_flex_gross_breakdown as rebuild
from scripts.clients.journal_basis import flex_aggregate_budget

OPT = {"ticker": "SPY", "strike": 500.0, "right": "C", "expiry": "20260918"}


def _trade(tid, when, side, qty, symbol="SPY", strike="500", right="C", expiry="20260918"):
    return (
        f'<Trade tradeID="{tid}" symbol="{symbol}" assetCategory="OPT" '
        f'dateTime="{when}" buySell="{side}" quantity="{qty}" tradePrice="1.5" '
        f'ibCommission="-1" strike="{strike}" putCall="{right}" expiry="{expiry}"/>'
    )


def _xml(*trades):
    return (
        '<FlexQueryResponse><FlexStatements><FlexStatement accountId="U1">'
        f"<Trades>{''.join(trades)}</Trades></FlexStatement></FlexStatements>"
        "</FlexQueryResponse>"
    )


def _agg(ids, action="CLOSED", contracts=10, date="2026-08-10", **extra):
    payload = {**OPT, "date": date, "action": action, "contracts": contracts,
               "ib_exec_id": "+".join(ids)}
    payload.update(extra)
    return payload


FULL = _xml(
    _trade("101", "20260810;100000", "BUY", 10),
    _trade("102", "20260811;100000", "SELL", 4),
    _trade("103", "20260811;150000", "BUY", 2),
    _trade("104", "20260812;100000", "SELL", 8),
)


def _plan(rows, *xmls):
    executions = rebuild.load_statement_executions(list(xmls))
    return rebuild.plan_rebuild([(tid, json.dumps(p)) for tid, p in rows], executions)


def _reasons(plan):
    return {item["trade_id"]: item["reason"] for item in plan["refuse"]}


def test_full_match_stamps_gross_breakdown_on_every_fill_date():
    plan = _plan([("agg", _agg(["101", "102", "103", "104"], contracts=12, total_round_trip_quantity=12))], FULL)
    assert [item["trade_id"] for item in plan["stamp"]] == ["agg"]
    assert plan["stamp"][0]["gross_fill_breakdown"] == [
        {"date": "2026-08-10", "qty": 10},
        {"date": "2026-08-11", "qty": -4},
        {"date": "2026-08-11", "qty": 2},
        {"date": "2026-08-12", "qty": -8},
    ]


def test_missing_part_refuses():
    plan = _plan([("agg", _agg(["101", "102", "103", "104", "999"], contracts=12))], FULL)
    assert not plan["stamp"]
    assert "missing" in _reasons(plan)["agg"]


def test_part_listed_twice_in_statements_refuses():
    plan = _plan([("agg", _agg(["101", "102", "103", "104"], contracts=12))], FULL, FULL)
    assert not plan["stamp"]
    assert "exactly once" in _reasons(plan)["agg"]


def test_part_shared_by_two_aggregates_refuses_both():
    rows = [("a", _agg(["101", "102", "103", "104"], contracts=12)), ("b", _agg(["101", "105"]))]
    xml = _xml(
        _trade("101", "20260810;100000", "BUY", 10), _trade("102", "20260811;100000", "SELL", 4),
        _trade("103", "20260811;150000", "BUY", 2), _trade("104", "20260812;100000", "SELL", 8),
        _trade("105", "20260812;110000", "SELL", 10),
    )
    plan = _plan(rows, xml)
    assert not plan["stamp"]
    assert set(_reasons(plan)) == {"a", "b"}
    assert all("claimed" in reason for reason in _reasons(plan).values())


def test_totals_disagree_refuses():
    plan = _plan([("agg", _agg(["101", "102", "103", "104"], action="BUY_OPTION", contracts=3))], FULL)
    assert not plan["stamp"]
    assert "disagree" in _reasons(plan)["agg"]


def test_round_trip_quantity_disagree_refuses():
    plan = _plan([("agg", _agg(["101", "102", "103", "104"], contracts=12, total_round_trip_quantity=11))], FULL)
    assert "disagree" in _reasons(plan)["agg"]


def test_wrong_contract_refuses():
    plan = _plan([("agg", _agg(["101", "102", "103", "104"], contracts=12, strike=505.0))], FULL)
    assert "contract" in _reasons(plan)["agg"]


def test_row_date_must_be_first_fill_date():
    plan = _plan([("agg", _agg(["101", "102", "103", "104"], contracts=12, date="2026-08-11"))], FULL)
    assert "disagree" in _reasons(plan)["agg"]


def test_superseded_part_refuses_and_surviving_correction_is_used():
    xml = _xml(
        _trade("aa.bb.01.01", "20260810;100000", "BUY", 99),
        _trade("aa.bb.01.02", "20260810;100000", "BUY", 10),
        _trade("102", "20260811;100000", "SELL", 10),
    )
    stale = _plan([("agg", _agg(["aa.bb.01.01", "102"]))], xml)
    assert "superseded" in _reasons(stale)["agg"]
    good = _plan([("agg", _agg(["aa.bb.01.02", "102"]))], xml)
    assert good["stamp"][0]["gross_fill_breakdown"] == [
        {"date": "2026-08-10", "qty": 10}, {"date": "2026-08-11", "qty": -10},
    ]


def test_entirely_absent_parts_are_out_of_statement_period():
    plan = _plan([("old", _agg(["7", "8"]))], FULL)
    assert [item["trade_id"] for item in plan["out_of_period"]] == ["old"]
    assert not plan["refuse"]
    assert plan["coverage"] == ("2026-08-10", "2026-08-12")


def test_non_trade_statement_is_rejected():
    with pytest.raises(SystemExit):
        rebuild.load_statement_executions(["<FlexQueryResponse><EquitySummaryByReportDateInBase/>"
                                           "<CashTransactions/><Transfers/></FlexQueryResponse>"])


def test_already_stamped_rows_are_skipped_and_stamp_feeds_budget():
    payload = _agg(["101", "102", "103", "104"], contracts=12)
    plan = _plan([("agg", payload)], FULL)
    stamped = {**payload, "gross_fill_breakdown": plan["stamp"][0]["gross_fill_breakdown"]}
    again = _plan([("agg", stamped)], FULL)
    assert not again["stamp"] and not again["refuse"] and again["already_stamped"] == 1
    budget = flex_aggregate_budget(stamped)
    key = "SPY|20260918|C|500.0"
    assert budget == {
        (key, "2026-08-10", 1): 10.0, (key, "2026-08-11", -1): 4.0,
        (key, "2026-08-11", 1): 2.0, (key, "2026-08-12", -1): 8.0,
    }


def _db(payloads):
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE journal (trade_id TEXT PRIMARY KEY, payload TEXT)")
    for trade_id, payload in payloads:
        db.execute("INSERT INTO journal VALUES (?, ?)", (trade_id, json.dumps(payload)))
    db.commit()
    return db


def _snapshot(db):
    return dict(db.execute("SELECT trade_id, payload FROM journal").fetchall())


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    path = tmp_path / "s.xml"
    path.write_text(FULL)
    db = _db([("agg", _agg(["101", "102", "103", "104"], contracts=12))])
    before = _snapshot(db)
    monkeypatch.setattr(rebuild, "_connect", lambda: db)
    assert rebuild.main(["--xml", str(path)]) == 0
    assert _snapshot(db) == before


def test_apply_writes_only_gross_field_and_is_idempotent(tmp_path, monkeypatch, capsys):
    path = tmp_path / "s.xml"
    path.write_text(FULL)
    original = _agg(["101", "102", "103", "104"], contracts=12, notes="keep",
                    fill_breakdown=[{"date": "2026-08-10", "qty": 10}, {"date": "2026-08-11", "qty": -2},
                                    {"date": "2026-08-12", "qty": -8}])
    other = _agg(["7", "8"])
    db = _db([("agg", original), ("old", other)])
    monkeypatch.setattr(rebuild, "_connect", lambda: db)
    assert rebuild.main(["--xml", str(path), "--apply"]) == 0
    after = {k: json.loads(v) for k, v in _snapshot(db).items()}
    gross = after["agg"].pop("gross_fill_breakdown")
    assert after["agg"] == original and after["old"] == other
    assert len(gross) == 4
    assert "APPLIED: stamped 1, verified 1" in capsys.readouterr().out
    assert rebuild.main(["--xml", str(path), "--apply"]) == 0
    assert "stamp: 0" in capsys.readouterr().out


@pytest.mark.parametrize("change", [
    {"ticker": "QQQ"}, {"strike": 505}, {"contracts": 99},
    {"ib_exec_id": "other.1+other.2"}, {"date": "2026-08-11"},
    {"gross_fill_breakdown": [{"date": "2026-08-10", "qty": 99}]},
    {"fill_breakdown": [{"date": "2026-08-10", "qty": 99}]},
])
def test_apply_refuses_changed_validated_payload(change):
    original = _agg(["101", "102", "103", "104"], contracts=12)
    plan = _plan([("agg", original)], FULL)
    db = _db([("agg", {**original, **change})])
    before = _snapshot(db)
    with pytest.raises(RuntimeError, match="changed since planning"):
        rebuild._apply(db, plan)
    assert _snapshot(db) == before


@pytest.mark.parametrize("existing", [False, True])
def test_apply_refuses_new_competing_claim(existing):
    rows = [("agg", _agg(["101", "102", "103", "104"], contracts=12))]
    if existing:
        rows.append(("competitor", _agg(["7", "8"])))
    plan = _plan(rows, FULL)
    db = _db(rows)
    competitor = _agg(["101", "105"])
    db.execute("INSERT OR REPLACE INTO journal VALUES (?, ?)",
               ("competitor", json.dumps(competitor)))
    db.commit()
    before = _snapshot(db)
    with pytest.raises(RuntimeError, match="changed since planning"):
        rebuild._apply(db, plan)
    assert _snapshot(db) == before
    assert rebuild.FIELD not in json.loads(before["agg"])


def test_apply_refuses_deleted_snapshot_row():
    rows = [("agg", _agg(["101", "102", "103", "104"], contracts=12)),
            ("old", _agg(["7", "8"]))]
    plan = _plan(rows, FULL)
    db = _db(rows[:1])
    before = _snapshot(db)
    with pytest.raises(RuntimeError, match="changed since planning"):
        rebuild._apply(db, plan)
    assert _snapshot(db) == before


def test_apply_rolls_back_prior_stamp_on_mid_batch_conflict():
    rows = [("first", _agg(["101", "102", "103", "104"], contracts=12)),
            ("second", _agg(["201", "202"]))]
    xml = _xml(_trade("201", "20260810;100000", "BUY", 10),
               _trade("202", "20260811;100000", "SELL", 10))
    plan = _plan(rows, FULL, xml)
    assert len(plan["stamp"]) == 2
    db = _db(rows)
    # A real transactional side effect changes the second row after the first write.
    db.execute("""CREATE TRIGGER conflict AFTER UPDATE ON journal
                  WHEN NEW.trade_id = 'first' BEGIN
                  UPDATE journal SET payload = '{}' WHERE trade_id = 'second'; END""")
    before = _snapshot(db)
    with pytest.raises(RuntimeError):
        rebuild._apply(db, plan)
    assert _snapshot(db) == before


def test_apply_serializes_snapshot_validation_against_competing_writer(tmp_path):
    path = tmp_path / "journal.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE journal (trade_id TEXT PRIMARY KEY, payload TEXT)")
    payload = _agg(["101", "102", "103", "104"], contracts=12)
    db.execute("INSERT INTO journal VALUES (?, ?)", ("agg", json.dumps(payload)))
    db.commit()
    plan = _plan([("agg", payload)], FULL)
    competitor = sqlite3.connect(path, timeout=0)
    attempts = []

    class RacingConnection:
        def execute(self, sql, params=()):
            if sql.startswith("SELECT") and not attempts:
                attempts.append(True)
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    competitor.execute("INSERT INTO journal VALUES (?, ?)",
                                       ("competing", json.dumps(_agg(["101", "105"]))))
                competitor.rollback()
            return db.execute(sql, params)

        def commit(self):
            db.commit()

        def rollback(self):
            db.rollback()

    try:
        assert rebuild._apply(RacingConnection(), plan) == 1
        assert attempts == [True]
        assert set(_snapshot(db)) == {"agg"}
    finally:
        competitor.close()
        db.close()


@pytest.mark.parametrize("changed", [False, True])
def test_apply_uses_native_libsql_transaction(changed):
    import subprocess
    import sys

    # The suite's global fixture replaces libsql.connect. An isolated interpreter
    # exercises the installed driver against an in-memory database only.
    code = r"""
import json
import sys
import libsql_experimental as libsql
from scripts import rebuild_flex_gross_breakdown as rebuild
from scripts.tests.test_rebuild_flex_gross_breakdown import _agg, _plan, _snapshot, FULL

db = libsql.connect(":memory:")
db.execute("CREATE TABLE journal (trade_id TEXT PRIMARY KEY, payload TEXT)")
payload = _agg(["101", "102", "103", "104"], contracts=12)
db.execute("INSERT INTO journal VALUES (?, ?)", ("agg", json.dumps(payload)))
db.commit()
plan = _plan([("agg", payload)], FULL)
if sys.argv[1] == "True":
    db.execute("UPDATE journal SET payload = ?", (json.dumps({**payload, "contracts": 99}),))
    db.commit()
    before = _snapshot(db)
    try:
        rebuild._apply(db, plan)
    except RuntimeError as exc:
        assert "changed since planning" in str(exc)
    else:
        raise AssertionError("changed snapshot was accepted")
    assert _snapshot(db) == before
else:
    assert rebuild._apply(db, plan) == 1
    actual = json.loads(_snapshot(db)["agg"])
    assert actual.pop(rebuild.FIELD) == plan["stamp"][0][rebuild.FIELD]
    assert actual == payload
db.close()
"""
    result = subprocess.run([sys.executable, "-c", code, str(changed)],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_plan_freezes_mutable_input():
    payload = _agg(["101", "102", "103", "104"], contracts=12)
    plan = rebuild.plan_rebuild([("agg", payload)], rebuild.load_statement_executions([FULL]))
    payload["contracts"] = 99
    db = _db([("agg", payload)])
    before = _snapshot(db)
    with pytest.raises(RuntimeError, match="changed since planning"):
        rebuild._apply(db, plan)
    assert _snapshot(db) == before
