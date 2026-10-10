"""The return-capital reconcile reads only margin samples near a fill, and
re-applies only when its plan changed.

Every ib_sync / ib_orders cycle ran build_reconciliation_plan over the whole
account_margin_samples table (62.8k rows, 17 MB of positions JSON, growing
~1.2k rows a day) although only samples within 120 s of a fill can bracket an
event, then re-read the whole ledger to diff it. Both now scale with the
fills, not with the sample table.
"""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import capture_position_return_capital as rc  # noqa: E402

T0 = datetime(2026, 10, 8, 13, 30, tzinfo=timezone.utc)
MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"


def _iso(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _ledger_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)")
    db.executescript((MIGRATIONS / "0037_position_return_capital.sql").read_text())
    db.execute(
        "CREATE TABLE app_config (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    return db


def _add_fact(db, *, exec_id, con_id, side, at, perm_id, price=2.5, revision=1):
    payload = {
        "exec_id": exec_id, "account_id": "U1", "con_id": con_id, "side": side,
        "quantity": 1.0, "price": price, "filled_at": _iso(at), "perm_id": perm_id,
        "currency": "USD", "multiplier": 100.0,
    }
    text = json.dumps(payload, sort_keys=True)
    db.execute(
        """INSERT INTO position_execution_facts
           (account_id, exec_id, revision, payload_sha256, perm_id, order_ref, con_id, side,
            quantity, price, multiplier, currency, filled_at, payload, ingested_at)
           VALUES ('U1', ?, ?, ?, ?, NULL, ?, ?, 1.0, ?, 100.0, 'USD', ?, ?, ?)""",
        (exec_id, revision, hashlib.sha256(text.encode()).hexdigest(), perm_id, con_id,
         side, price, _iso(at), text, _iso(at)),
    )


def _add_sample(db, sample_id, start, end, margin, positions):
    text = json.dumps(positions, sort_keys=True)
    db.execute(
        """INSERT INTO account_margin_samples
           (sample_id, account_id, observed_from, observed_through, initial_margin,
            maintenance_margin, currency, positions, positions_sha256, source,
            idempotency_key, recorded_at)
           VALUES (?, 'U1', ?, ?, ?, NULL, 'USD', ?, ?, 'ib-account-values', ?, ?)""",
        (sample_id, _iso(start), _iso(end), margin, text,
         hashlib.sha256(text.encode()).hexdigest(), sample_id, _iso(end)),
    )


def _seed_trading_day(db, seed: int, *, fills: int = 60) -> None:
    """Fills every ~10 min; margin sampled every 30-400 s with exact positions.

    Long gaps leave some events stale or unbracketed; tight ones isolate them.
    """
    rng = random.Random(seed)
    held: dict[str, float] = {}
    fill_times = []
    clock = 600.0
    for index in range(fills):
        con_id = 100 + rng.randrange(6)
        side = "SLD" if held.get(str(con_id), 0) > 0 and rng.random() < 0.6 else "BOT"
        _add_fact(db, exec_id=f"0001.{index:04x}.01", con_id=con_id, side=side,
                  at=clock, perm_id=index + 1)
        fill_times.append((clock, str(con_id), 1.0 if side == "BOT" else -1.0))
        clock += rng.uniform(200, 1_200)
    margin, positions, cursor, sample, pending = 10_000.0, {}, 0.0, 0, list(fill_times)
    while cursor < clock + 600:
        span = rng.uniform(1, 20)
        while pending and pending[0][0] < cursor:
            _, key, delta = pending.pop(0)
            positions[key] = positions.get(key, 0.0) + delta
            positions = {k: v for k, v in positions.items() if v}
            margin += 500.0 * delta
        _add_sample(db, f"s{sample:05d}", cursor, cursor + span, margin, positions)
        sample += 1
        cursor += span + (rng.uniform(5, 25) if rng.random() < 0.95 else rng.uniform(150, 400))


def _full_table_samples(db, windows=None):
    """The pre-fix loader: every sample, every cycle."""
    rows = db.execute(
        """SELECT sample_id, account_id, observed_from, observed_through, initial_margin,
                  maintenance_margin, currency, positions
           FROM account_margin_samples
           ORDER BY account_id ASC, observed_from ASC, observed_through ASC, sample_id ASC"""
    ).fetchall()
    return rc._group_samples(rows)


def _comparable(plan):
    rejected = sorted(
        (item["event_id"],
         "missing-distinct-margin-samples" if item["reason"] == "stale-samples" else item["reason"])
        for item in plan["rejected"]
    )
    return {
        "observations": sorted(json.dumps(o, sort_keys=True) for o in plan["observations"]),
        "rejected": rejected,
        "events": [e["event_id"] for e in plan["events"]],
    }


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_windowed_plan_matches_the_full_table_plan(seed, monkeypatch):
    db = _ledger_db()
    _seed_trading_day(db, seed)

    windowed = rc.build_reconciliation_plan(db)
    monkeypatch.setattr(rc, "_load_samples", _full_table_samples)
    full = rc.build_reconciliation_plan(db)

    assert full["observations"], "fixture must isolate at least one event"
    assert _comparable(windowed) == _comparable(full)


def test_only_stale_rejections_change_reason(monkeypatch):
    """A stale pair needs a sample > 120 s from the fill, which the window excludes."""
    db = _ledger_db()
    _add_fact(db, exec_id="0001.0001.01", con_id=101, side="BOT", at=1_000, perm_id=1)
    _add_sample(db, "s-old", 0, 5, 10_000.0, {})
    _add_sample(db, "s-after", 1_010, 1_015, 10_500.0, {"101": 1.0})

    windowed = rc.build_reconciliation_plan(db)
    monkeypatch.setattr(rc, "_load_samples", _full_table_samples)
    full = rc.build_reconciliation_plan(db)

    assert [r["reason"] for r in full["rejected"]] == ["stale-samples"]
    assert [r["reason"] for r in windowed["rejected"]] == ["missing-distinct-margin-samples"]


class _RecordingDb:
    def __init__(self, db):
        self._db = db
        self.statements: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.statements.append((" ".join(sql.split()), tuple(params)))
        return self._db.execute(sql, params)

    def commit(self):
        self._db.commit()

    def rollback(self):
        self._db.rollback()


def test_samples_load_in_one_statement_bounded_to_fill_windows():
    db = _ledger_db()
    _add_fact(db, exec_id="0001.0001.01", con_id=101, side="BOT", at=1_000, perm_id=1)
    _add_fact(db, exec_id="0001.0002.01", con_id=101, side="SLD", at=5_000, perm_id=2)
    recorder = _RecordingDb(db)

    rc.build_reconciliation_plan(recorder)

    sample_reads = [(s, p) for s, p in recorder.statements if "account_margin_samples" in s]
    assert len(sample_reads) == 1
    sql, params = sample_reads[0]
    assert sql.count("(?, ?, ?)") == 2
    assert params == ("U1", _iso(1_000 - 121), _iso(1_000 + 121),
                      "U1", _iso(5_000 - 121), _iso(5_000 + 121))


def test_overlapping_fill_windows_merge_into_one_range():
    assert rc._merged_windows([
        ("U1", 1_000.0, 1_030.0), ("U1", 1_100.0, 1_100.0), ("U1", 9_000.0, 9_000.0),
        ("U2", 1_000.0, 1_000.0),
    ], 120) == [
        ("U1", 1_000.0 - 121, 1_100.0 + 121),
        ("U1", 9_000.0 - 121, 9_000.0 + 121),
        ("U2", 1_000.0 - 121, 1_000.0 + 121),
    ]


def test_sample_read_uses_the_account_time_index():
    db = _ledger_db()
    sql, params = rc._samples_query([("U1", 0.0, 100.0), ("U1", 500.0, 600.0)])
    plan = " ".join(row[3] for row in db.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall())
    assert "idx_account_margin_samples_time" in plan
    assert "SCAN account_margin_samples" not in plan


def test_hundreds_of_windows_fit_tursos_expression_depth_limit():
    """Turso caps expression depth at 100: an OR per window failed live (2026-10-10)."""
    db = _ledger_db()
    db.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 100)
    db.setlimit(sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, 500)
    base = rc._epoch(_iso(0))
    windows = [("U1", base + index * 1_000.0, base + index * 1_000.0 + 300)
               for index in range(600)]
    _add_sample(db, "s-hit", 250_100, 250_110, 1.0, {})

    grouped = rc._load_samples(db, windows)

    assert [item["sample_id"] for item in grouped["U1"]] == ["s-hit"]


def test_no_events_reads_no_samples():
    recorder = _RecordingDb(_ledger_db())
    rc.build_reconciliation_plan(recorder)
    assert not any("account_margin_samples" in s for s, _ in recorder.statements)


def _ledger_reads(statements):
    return [s for s, _ in statements
            if s.startswith("SELECT") and "position_event_executions" in s]


def _writes(statements):
    return [s for s, _ in statements if s.split(" ", 1)[0] in {"INSERT", "DELETE", "UPDATE"}]


def test_unchanged_facts_and_samples_skip_the_apply():
    db = _ledger_db()
    _seed_trading_day(db, 3)
    first = rc.reconcile_position_return_capital(db, apply=True)
    recorder = _RecordingDb(db)

    second = rc.reconcile_position_return_capital(recorder, apply=True)

    assert first["status"] == "applied"
    assert second["status"] == "unchanged"
    assert _ledger_reads(recorder.statements) == []
    assert _writes(recorder.statements) == []


def test_a_late_after_sample_reopens_the_gate():
    """A fill synced before its after-sample exists must be reconciled later."""
    db = _ledger_db()
    _add_fact(db, exec_id="0001.0001.01", con_id=101, side="BOT", at=1_000, perm_id=1)
    _add_sample(db, "s-before", 960, 970, 10_000.0, {})
    first = rc.reconcile_position_return_capital(db, apply=True)
    _add_sample(db, "s-after", 1_010, 1_015, 10_500.0, {"101": 1.0})

    second = rc.reconcile_position_return_capital(db, apply=True)

    assert first["observations"] == 0
    assert second["status"] == "applied"
    assert db.execute("SELECT amount FROM position_capital_observations").fetchall() == [(500.0,)]


def test_an_exec_id_correction_reopens_the_gate_and_voids():
    db = _ledger_db()
    _add_fact(db, exec_id="0001.0001.01", con_id=101, side="BOT", at=1_000, perm_id=1)
    _add_sample(db, "s-before", 960, 970, 10_000.0, {})
    _add_sample(db, "s-after", 1_010, 1_015, 10_500.0, {"101": 1.0})
    rc.reconcile_position_return_capital(db, apply=True)
    _add_fact(db, exec_id="0001.0001.02", con_id=101, side="BOT", at=1_000, perm_id=1,
              price=2.75)

    result = rc.reconcile_position_return_capital(db, apply=True)

    assert result["status"] == "applied"
    statuses = db.execute(
        "SELECT status FROM position_capital_observations ORDER BY status"
    ).fetchall()
    assert ("VOID",) in statuses


def test_a_failed_apply_leaves_the_gate_open(monkeypatch):
    db = _ledger_db()
    _seed_trading_day(db, 3)

    def boom(*args, **kwargs):
        raise RuntimeError("hrana 502")

    monkeypatch.setattr(rc, "_write_ledger_changes", boom)
    with pytest.raises(RuntimeError):
        rc.reconcile_position_return_capital(db, apply=True)
    monkeypatch.undo()

    assert rc.reconcile_position_return_capital(db, apply=True)["status"] == "applied"


def test_preview_never_reads_or_writes_the_gate():
    recorder = _RecordingDb(_ledger_db())
    rc.reconcile_position_return_capital(recorder, apply=False)
    assert not any("app_config" in s for s, _ in recorder.statements)


class _LibsqlParamsDb(_RecordingDb):
    """libsql's execute() takes a tuple; a list raises TypeError (prod, 2026-10-10)."""

    def execute(self, sql, params=()):
        if not isinstance(params, tuple):
            raise TypeError("argument 'parameters': 'list' object cannot be converted to 'PyTuple'")
        return super().execute(sql, params)


def test_reads_and_batched_writes_bind_tuples_as_libsql_requires():
    db = _ledger_db()
    _seed_trading_day(db, 3)
    libsql = _LibsqlParamsDb(db)

    first = rc.reconcile_position_return_capital(libsql, apply=True)
    original = db.execute(
        "SELECT con_id, side, filled_at, perm_id FROM position_execution_facts "
        "WHERE exec_id = '0001.0000.01'"
    ).fetchone()
    _add_fact(db, exec_id="0001.0000.02", con_id=original[0], side=original[1],
              at=600, perm_id=original[3], price=9.75)
    corrected = rc.reconcile_position_return_capital(libsql, apply=True)

    assert (first["status"], corrected["status"]) == ("applied", "applied")
    assert db.execute("SELECT COUNT(*) FROM position_event_executions").fetchone()[0] > 0
    assert db.execute(
        "SELECT price FROM position_event_executions WHERE exec_id = '0001.0000'"
    ).fetchall() == [(9.75,)]
