"""The return-capital reconcile runs inside every orders/portfolio sync.

Both syncs are 30 s subprocesses. A plan that re-parses every margin sample
per event, and an apply that issues a SELECT + DELETE + INSERT per execution
on a ledger that already holds them, pushed ib_orders.py --sync past 30 s on
most runs (orders-sync `Script timed out after 30s`, 2026-10-09).
"""

from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import capture_position_return_capital as rc  # noqa: E402

T0 = datetime(2026, 10, 8, 13, 30, tzinfo=timezone.utc)


def _iso(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def _sample(index: int, start: float, end: float) -> dict:
    return {"sample_id": f"s{index}", "account_id": "U1",
            "observed_from": _iso(start), "observed_through": _iso(end)}


def _naive_bracket(samples, first, last):
    before = [s for s in samples if rc._epoch(s["observed_through"]) <= first]
    after = [s for s in samples if rc._epoch(s["observed_from"]) >= last]
    return (before[-1] if before else None, after[0] if after else None)


def test_sample_index_matches_the_linear_scan_on_unordered_windows():
    rng = random.Random(7)
    samples = []
    for index in range(300):
        start = rng.uniform(0, 5_000)
        samples.append(_sample(index, start, start + rng.uniform(0, 400)))
    samples.sort(key=lambda s: s["observed_from"])
    index = rc._SampleIndex(samples)
    for _ in range(500):
        first = rng.uniform(-100, 5_500)
        last = first + rng.uniform(0, 200)
        expected = _naive_bracket(
            samples, rc._epoch(_iso(first)), rc._epoch(_iso(last))
        )
        assert index.bracket(rc._epoch(_iso(first)), rc._epoch(_iso(last))) == expected


def test_plan_parses_each_sample_timestamp_once(monkeypatch):
    samples = [_sample(i, i * 60.0, i * 60.0 + 30) for i in range(2_000)]
    events = [
        {"event_id": f"e{i}", "account_id": "U1", "instance_id": "i1", "kind": "ADD",
         "executions": [{"filled_at": _iso(i * 600.0 + 45)}]}
        for i in range(100)
    ]
    monkeypatch.setattr(rc, "_load_executions", lambda db: [])
    monkeypatch.setattr(rc, "group_execution_transactions", lambda execs: [])
    monkeypatch.setattr(rc, "replay_transactions",
                        lambda txns: {"events": events, "instances": [], "errors": []})
    monkeypatch.setattr(rc, "_load_samples", lambda db: {"U1": samples})
    monkeypatch.setattr(rc, "validate_margin_window",
                        lambda *a, **k: {"valid": False, "reason": "test"})
    calls = {"n": 0}
    real_epoch = rc._epoch

    def counting_epoch(value):
        calls["n"] += 1
        return real_epoch(value)

    monkeypatch.setattr(rc, "_epoch", counting_epoch)

    rc.build_reconciliation_plan(object())

    assert calls["n"] <= 2 * len(samples) + 4 * len(events) * 2


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _LedgerDb:
    """Holds one instance, event, execution and observation already applied."""

    def __init__(self, plan, *, execution_row):
        self.statements: list[str] = []
        self._plan = plan
        self._execution_row = execution_row

    def execute(self, sql, params=()):
        text = " ".join(sql.split())
        self.statements.append(text)
        event = self._plan["events"][0]
        if text.startswith("SELECT instance_id FROM position_instances"):
            return _Cursor([("i1",)])
        if text.startswith("SELECT event_id, instance_id, kind"):
            return _Cursor([(event["event_id"], *rc._event_row(event))])
        if text.startswith("SELECT event_id, account_id, exec_id, revision"):
            return _Cursor([self._execution_row])
        if text.startswith("SELECT observation_id FROM position_capital_observations"):
            return _Cursor([("obs-1",)])
        if text.startswith("SELECT revision, price FROM position_event_executions"):
            return _Cursor([(self._execution_row[3], self._execution_row[8])])
        return _Cursor([])

    def commit(self):
        self.statements.append("COMMIT")

    def rollback(self):
        self.statements.append("ROLLBACK")


def _plan():
    execution = {
        "exec_id": "0001.6a.01", "account_id": "U1", "revision": 1, "con_id": 101,
        "perm_id": 9, "order_ref": "radon-x", "signed_quantity": 10.0, "price": 2.5,
        "multiplier": 100.0, "currency": "USD", "filled_at": _iso(0),
    }
    event = {
        "event_id": "ev-1", "instance_id": "i1", "kind": "OPEN", "transaction_key": "t1",
        "effective_at": _iso(0), "before_legs": {}, "after_legs": {"101": 10.0},
        "executions": [execution],
    }
    instance = {"instance_id": "i1", "account_id": "U1", "strategy_key": "k",
                "episode": 1, "opened_at": _iso(0)}
    observation = {
        "observation_id": "obs-1", "instance_id": "i1", "through_event_id": "ev-1",
        "amount": 2500.0, "delta_amount": 2500.0, "before_sample_id": "s0",
        "after_sample_id": "s1", "currency": "USD", "evidence": {}, "idempotency_key": "k",
    }
    return {"instances": [instance], "events": [event], "observations": [observation]}


def _writes(statements):
    return [s for s in statements if s.split(" ", 1)[0] in {"INSERT", "DELETE", "UPDATE"}]


def test_reapplying_an_unchanged_plan_writes_nothing():
    plan = _plan()
    row = ("ev-1", "U1", "0001.6a.01", 1, 101, 9, "radon-x", 10.0, 2.5, 100.0, "USD", _iso(0))
    db = _LedgerDb(plan, execution_row=row)

    rc.apply_reconciliation_plan(db, plan)

    assert _writes(db.statements) == []
    assert not any("WHERE event_id=? AND account_id=? AND exec_id=?" in s
                   and s.startswith("SELECT") for s in db.statements)
    assert db.statements[-1] == "COMMIT"


def test_a_revised_execution_still_voids_and_rewrites():
    plan = _plan()
    row = ("ev-1", "U1", "0001.6a.01", 0, 101, 9, "radon-x", 10.0, 2.4, 100.0, "USD", _iso(0))
    db = _LedgerDb(plan, execution_row=row)

    rc.apply_reconciliation_plan(db, plan)

    kinds = [s.split(" ", 2)[:2] for s in _writes(db.statements)]
    assert kinds == [["UPDATE", "position_capital_observations"],
                     ["DELETE", "FROM"], ["INSERT", "INTO"]]


def _sqlite_ledger():
    import sqlite3

    db = sqlite3.connect(":memory:", isolation_level=None)
    schema = (Path(__file__).resolve().parents[1] / "db" / "migrations"
              / "0037_position_return_capital.sql").read_text()
    db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)")
    db.executescript(schema)
    return db


def _wide_plan(executions: int) -> dict:
    plan = _plan()
    template = plan["events"][0]["executions"][0]
    plan["events"][0]["executions"] = [
        {**template, "exec_id": f"0001.{index:04x}.01", "con_id": 100 + index}
        for index in range(executions)
    ]
    return plan


def test_batched_writes_land_on_real_sqlite_and_reapply_is_a_no_op():
    db = _sqlite_ledger()
    plan = _wide_plan(250)

    rc.apply_reconciliation_plan(db, plan)
    first = db.total_changes
    rc.apply_reconciliation_plan(db, plan)

    count = lambda table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: E731
    assert count("position_instances") == 1
    assert count("position_instance_events") == 1
    assert count("position_event_executions") == 250
    assert db.execute(
        "SELECT status, method, quality, source FROM position_capital_observations"
    ).fetchall() == [("VALID", "OBSERVED_INIT_MARGIN_DELTA", "observed", "ib-account-values")]
    assert db.total_changes == first


def test_revised_price_voids_the_observation_on_real_sqlite():
    db = _sqlite_ledger()
    plan = _wide_plan(3)
    rc.apply_reconciliation_plan(db, plan)

    plan["events"][0]["executions"][1]["price"] = 2.75
    rc.apply_reconciliation_plan(db, plan)

    assert db.execute(
        "SELECT status, amount FROM position_capital_observations"
    ).fetchall() == [("VOID", None)]
    assert db.execute(
        "SELECT price FROM position_event_executions WHERE exec_id = '0001.0001.01'"
    ).fetchall() == [(2.75,)]
