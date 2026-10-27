"""Retention for account_margin_samples (follow-up to PR #989).

Policy: delete samples older than 90 days EXCEPT samples a capital
observation references (before/after_sample_id) and samples inside the 121 s
bracket window of a fill that has no VALID observation yet. Deletes run in
bounded batches so one direct-to-cloud Hrana statement never sweeps the whole
table.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import capture_position_return_capital as rc  # noqa: E402
from db import retention  # noqa: E402

MIGRATIONS = SCRIPTS / "db" / "migrations"
OLD = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=200)
RECENT = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=10)


def _iso(base: datetime, seconds: float) -> str:
    return (base + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)")
    db.executescript((MIGRATIONS / "0037_position_return_capital.sql").read_text())
    db.execute("CREATE TABLE app_config (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)")
    return db


def _add_fact(db, *, exec_id, con_id, side, at, perm_id, base=OLD):
    payload = {
        "exec_id": exec_id, "account_id": "U1", "con_id": con_id, "side": side,
        "quantity": 1.0, "price": 2.5, "filled_at": _iso(base, at), "perm_id": perm_id,
        "currency": "USD", "multiplier": 100.0,
    }
    text = json.dumps(payload, sort_keys=True)
    db.execute(
        """INSERT INTO position_execution_facts
           (account_id, exec_id, revision, payload_sha256, perm_id, order_ref, con_id, side,
            quantity, price, multiplier, currency, filled_at, payload, ingested_at)
           VALUES ('U1', ?, 1, ?, ?, NULL, ?, ?, 1.0, 2.5, 100.0, 'USD', ?, ?, ?)""",
        (exec_id, hashlib.sha256(text.encode()).hexdigest(), perm_id, con_id, side,
         _iso(base, at), text, _iso(base, at)),
    )


def _add_sample(db, sample_id, start, end, margin, positions, base=OLD):
    text = json.dumps(positions, sort_keys=True)
    db.execute(
        """INSERT INTO account_margin_samples
           (sample_id, account_id, observed_from, observed_through, initial_margin,
            maintenance_margin, currency, positions, positions_sha256, source,
            idempotency_key, recorded_at)
           VALUES (?, 'U1', ?, ?, ?, NULL, 'USD', ?, ?, 'ib-account-values', ?, ?)""",
        (sample_id, _iso(base, start), _iso(base, end), margin, text,
         hashlib.sha256(text.encode()).hexdigest(), sample_id, _iso(base, end)),
    )


def _sample_ids(db) -> set[str]:
    return {row[0] for row in db.execute("SELECT sample_id FROM account_margin_samples")}


def _policy(**overrides):
    by_table = {p.table: p for p in retention.SNAPSHOT_RETENTION_POLICIES}
    policy = by_table["account_margin_samples"]
    if overrides:
        from dataclasses import replace
        policy = replace(policy, **overrides)
    return policy


def _seed_reconciled_and_open_fills(db) -> None:
    # Fill A (old) isolates cleanly: the reconciler records an observation.
    _add_fact(db, exec_id="0001.0001.01", con_id=101, side="BOT", at=1_000, perm_id=1)
    _add_sample(db, "a-early", 940, 945, 10_000.0, {})          # in window, unreferenced
    _add_sample(db, "a-before", 990, 995, 10_000.0, {})
    _add_sample(db, "a-after", 1_010, 1_015, 10_500.0, {"101": 1.0})
    # Fill B (old) has no after-sample yet: not reconciled.
    _add_fact(db, exec_id="0001.0002.01", con_id=101, side="SLD", at=50_000, perm_id=2)
    _add_sample(db, "b-before", 49_990, 49_995, 10_500.0, {"101": 1.0})
    _add_sample(db, "b-edge", 49_879, 49_880, 10_500.0, {"101": 1.0})  # 121 s before fill
    # Old samples far from any fill.
    _add_sample(db, "old-1", 20_000, 20_005, 10_500.0, {"101": 1.0})
    _add_sample(db, "old-2", 30_000, 30_005, 10_500.0, {"101": 1.0})
    _add_sample(db, "b-far", 49_870, 49_875, 10_500.0, {"101": 1.0})  # 125 s before fill
    # Recent samples, unreferenced and far from fills.
    _add_sample(db, "recent-1", 0, 5, 10_500.0, {"101": 1.0}, base=RECENT)
    _add_sample(db, "recent-2", 600, 605, 10_500.0, {"101": 1.0}, base=RECENT)


def test_account_margin_samples_has_a_policy_with_the_operator_horizon():
    policy = _policy()
    assert policy.days == 90
    assert policy.bracket_window_s == 121
    assert 0 < policy.batch_size <= 1_000
    assert policy.max_batches >= 1


def test_deletes_old_unreferenced_and_keeps_protected_rows():
    db = _db()
    _seed_reconciled_and_open_fills(db)
    applied = rc.reconcile_position_return_capital(db, apply=True)
    assert applied["observations"] == 1, applied["rejected"]

    deleted = retention.apply_policy(db, _policy())

    remaining = _sample_ids(db)
    # Old, unreferenced, outside any unreconciled fill window: gone.
    assert not ({"old-1", "old-2", "b-far", "a-early"} & remaining)
    # FK-referenced by the observation: kept.
    assert {"a-before", "a-after"} <= remaining
    # Inside the 121 s bracket window of the unreconciled fill B: kept.
    assert {"b-before", "b-edge"} <= remaining
    # Younger than 90 days: kept.
    assert {"recent-1", "recent-2"} <= remaining
    assert deleted == 4


def test_retention_leaves_the_reconciliation_plan_unchanged():
    db = _db()
    _seed_reconciled_and_open_fills(db)
    rc.reconcile_position_return_capital(db, apply=True)
    before = rc.build_reconciliation_plan(db)

    retention.apply_policy(db, _policy())

    after = rc.build_reconciliation_plan(db)
    assert after["observations"] == before["observations"]
    assert after["rejected"] == before["rejected"]
    assert rc.reconcile_position_return_capital(db, apply=True)["status"] == "unchanged"


def test_void_observation_still_protects_its_samples():
    db = _db()
    _seed_reconciled_and_open_fills(db)
    rc.reconcile_position_return_capital(db, apply=True)
    db.execute("UPDATE position_capital_observations SET status='VOID', amount=NULL")

    retention.apply_policy(db, _policy())

    remaining = _sample_ids(db)
    assert {"a-before", "a-after"} <= remaining
    # The fill is unreconciled again, so its whole bracket window is kept.
    assert "a-early" in remaining


def test_null_sample_references_do_not_block_deletion():
    """NOT IN over a NULL-bearing set is never true; the policy must filter NULLs."""
    db = _db()
    _add_sample(db, "old-1", 20_000, 20_005, 10_000.0, {})
    db.execute(
        """INSERT INTO position_capital_observations
           (observation_id, instance_id, through_event_id, status, method, quality,
            amount, delta_amount, before_sample_id, after_sample_id, currency, source,
            evidence, idempotency_key, recorded_at)
           VALUES ('o1', 'i1', 'e1', 'VALID', 'DEBIT_CASH', 'exact', 100.0, NULL,
                   NULL, NULL, 'USD', 'test', '{}', 'k1', ?)""",
        (_iso(OLD, 0),),
    )

    retention.apply_policy(db, _policy())

    assert _sample_ids(db) == set()


class _RecordingDb:
    def __init__(self, db):
        self._db = db
        self.statements: list[tuple[str, object]] = []
        self.commits = 0

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        return self._db.execute(sql, params)

    def commit(self):
        self.commits += 1


def test_deletes_are_batched_and_bounded_per_run():
    db = _db()
    for index in range(7):
        _add_sample(db, f"old-{index}", 10_000 * index, 10_000 * index + 5, 10_000.0, {})
    recorder = _RecordingDb(db)

    deleted = retention.apply_policy(recorder, _policy(batch_size=3, max_batches=2))

    assert deleted == 6
    assert len(_sample_ids(db)) == 1
    assert len(recorder.statements) == 2
    for sql, params in recorder.statements:
        assert sql.lstrip().upper().startswith("DELETE FROM")
        assert "LIMIT ?" in sql
        assert isinstance(params, tuple)  # libsql rejects list params
        assert params[-1] == 3
    assert recorder.commits == 2


def test_batches_stop_once_a_batch_comes_back_short():
    db = _db()
    for index in range(4):
        _add_sample(db, f"old-{index}", 10_000 * index, 10_000 * index + 5, 10_000.0, {})
    recorder = _RecordingDb(db)

    deleted = retention.apply_policy(recorder, _policy(batch_size=3, max_batches=10))

    assert deleted == 4
    assert len(recorder.statements) == 2


def test_http_path_batches_over_hrana_query(monkeypatch):
    db = _db()
    for index in range(5):
        _add_sample(db, f"old-{index}", 10_000 * index, 10_000 * index + 5, 10_000.0, {})
    calls: list[tuple[str, object, float]] = []

    def fake_query(sql, args=(), timeout=0.0):
        calls.append((sql, args, timeout))
        return db.execute(sql, args).fetchall()

    from db import hrana_http
    monkeypatch.setattr(hrana_http, "hrana_query", fake_query)
    monkeypatch.setattr(
        hrana_http, "hrana_execute",
        lambda *a, **k: pytest.fail("batched policy must read RETURNING rows"),
    )

    deleted = retention.apply_policy_http(_policy(batch_size=2, max_batches=5), timeout=7.0)

    assert deleted == 5
    assert len(calls) == 3
    assert all(isinstance(args, tuple) and timeout == 7.0 for _, args, timeout in calls)
    assert _sample_ids(db) == set()


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_http_margin_delete_refuses_developer_before_credentials_or_transport(monkeypatch, platform):
    from db import hrana_http
    from utils.production_writer_guard import ProductionWriterRefused

    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("RADON_MODE", raising=False)
    monkeypatch.setattr(hrana_http, "read_env", lambda: pytest.fail("must refuse before credentials"))
    monkeypatch.setattr(hrana_http, "_post_pipeline", lambda *a, **k: pytest.fail("must not send DELETE"))
    with pytest.raises(ProductionWriterRefused):
        retention.apply_policy_http(_policy())


def test_http_margin_delete_is_allowed_on_linux_outside_pytest(monkeypatch):
    from db import hrana_http

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    calls = []
    monkeypatch.setattr(hrana_http, "hrana_query", lambda sql, args, **kw: calls.append(sql) or [])
    assert retention.apply_policy_http(_policy()) == 0
    assert len(calls) == 1
    assert calls[0].startswith('DELETE FROM account_margin_samples')
