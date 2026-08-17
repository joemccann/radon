"""Return-capital v2: execution episodes and isolated IB margin evidence."""

from __future__ import annotations

import sys
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from position_return_capital import (  # noqa: E402
    build_return_capital_payload,
    hydrate_return_capital,
    replay_transactions,
    validate_margin_window,
)
from capture_position_return_capital import reconcile_position_return_capital  # noqa: E402
from db.readers import read_active_position_return_capital  # noqa: E402
from db.writer import insert_account_margin_sample, upsert_position_execution_fact  # noqa: E402


def _execution(
    exec_id: str,
    *,
    account: str = "U1",
    con_id: int = 101,
    side: str = "BOT",
    quantity: float = 10,
    filled_at: str = "2026-08-07T16:00:30Z",
    perm_id: int = 9001,
    order_ref: str = "radon-spcx-1",
) -> dict:
    return {
        "exec_id": exec_id,
        "account_id": account,
        "con_id": con_id,
        "side": side,
        "quantity": quantity,
        "price": 4.25,
        "filled_at": filled_at,
        "perm_id": perm_id,
        "order_ref": order_ref,
        "currency": "USD",
        "multiplier": 100,
        "symbol": "SPCX",
        "sec_type": "OPT",
    }


def _sample(
    sample_id: str,
    at: str,
    margin: float,
    positions: dict[int, float],
    *,
    observed_from: str | None = None,
) -> dict:
    return {
        "sample_id": sample_id,
        "account_id": "U1",
        "observed_from": observed_from or at,
        "observed_through": at,
        "initial_margin": margin,
        "currency": "USD",
        "positions": {str(key): value for key, value in positions.items()},
    }


def test_replay_open_add_reduce_close_reopen_creates_new_episode():
    transactions = [
        [_execution("E1", quantity=10, filled_at="2026-08-07T16:00:00Z", perm_id=1)],
        [_execution("E2", quantity=5, filled_at="2026-08-07T16:01:00Z", perm_id=2)],
        [_execution("E3", side="SLD", quantity=4, filled_at="2026-08-07T16:02:00Z", perm_id=3)],
        [_execution("E4", side="SLD", quantity=11, filled_at="2026-08-07T16:03:00Z", perm_id=4)],
        [_execution("E5", quantity=3, filled_at="2026-08-07T16:04:00Z", perm_id=5)],
    ]

    replay = replay_transactions(transactions)

    assert [event["kind"] for event in replay["events"]] == [
        "OPEN", "ADD", "REDUCE", "CLOSE", "OPEN"
    ]
    first, reopened = replay["instances"]
    assert first["instance_id"] != reopened["instance_id"]
    assert first["episode"] == 1
    assert reopened["episode"] == 2
    assert reopened["legs"] == {"101": 3.0}


def test_replay_is_account_scoped_and_duplicate_execution_is_idempotent():
    e1 = _execution("E1", account="U1")
    e1_duplicate = dict(e1)
    e2 = _execution("E1", account="U2")

    replay = replay_transactions([[e1], [e1_duplicate], [e2]])

    assert len(replay["events"]) == 2
    assert {item["account_id"] for item in replay["instances"]} == {"U1", "U2"}


def test_replay_zero_cross_closes_old_episode_and_opens_residual():
    replay = replay_transactions([
        [_execution("E1", quantity=10, perm_id=1)],
        [_execution("E2", side="SLD", quantity=14, perm_id=2, filled_at="2026-08-07T16:01:30Z")],
    ])

    assert [event["kind"] for event in replay["events"]] == ["OPEN", "CLOSE", "OPEN"]
    assert replay["instances"][0]["legs"] == {}
    assert replay["instances"][1]["legs"] == {"101": -4.0}


def test_reversal_allocates_each_execution_quantity_once():
    replay = replay_transactions([
        [_execution("E1", quantity=10, perm_id=1)],
        [_execution("E2", side="SLD", quantity=14, perm_id=2, filled_at="2026-08-07T16:01:30Z")],
    ])

    close_event, reopen_event = replay["events"][1:]
    assert close_event["executions"][0]["quantity"] == 10
    assert reopen_event["executions"][0]["quantity"] == 4
    assert sum(
        execution["quantity"]
        for event in (close_event, reopen_event)
        for execution in event["executions"]
    ) == 14


def test_isolated_margin_window_accepts_exact_position_diff():
    event = {
        "kind": "OPEN",
        "account_id": "U1",
        "before_legs": {},
        "after_legs": {"101": 10.0},
        "executions": [_execution("E1")],
        "effective_at": "2026-08-07T16:00:30Z",
    }
    before = _sample("S1", "2026-08-07T16:00:00Z", 100_000, {})
    after = _sample("S2", "2026-08-07T16:01:00Z", 127_000, {101: 10})

    result = validate_margin_window(
        event,
        before,
        after,
        executions_in_window=[_execution("E1")],
        max_window_seconds=120,
    )

    assert result["valid"] is True
    assert result["delta_amount"] == 27_000
    assert result["isolation"] == "isolated"


def test_margin_window_rejects_sample_acquisition_that_straddles_fill():
    event = {
        "kind": "OPEN",
        "account_id": "U1",
        "before_legs": {},
        "after_legs": {"101": 10.0},
        "executions": [_execution("E1")],
        "effective_at": "2026-08-07T16:00:30Z",
    }
    before = _sample("S1", "2026-08-07T16:00:00Z", 100_000, {})
    straddling_after = _sample(
        "S2",
        "2026-08-07T16:01:00Z",
        127_000,
        {101: 10},
        observed_from="2026-08-07T16:00:20Z",
    )

    rejected = validate_margin_window(
        event,
        before,
        straddling_after,
        executions_in_window=[_execution("E1")],
    )
    assert rejected["reason"] == "samples-do-not-bracket-fill"

    after = {**straddling_after, "observed_from": "2026-08-07T16:00:40Z"}
    assert validate_margin_window(
        event,
        before,
        after,
        executions_in_window=[_execution("E1")],
    )["valid"] is True


def test_margin_window_fails_closed_on_concurrent_stale_mismatch_or_wrong_sign():
    event = {
        "kind": "OPEN",
        "account_id": "U1",
        "before_legs": {},
        "after_legs": {"101": 10.0},
        "executions": [_execution("E1")],
        "effective_at": "2026-08-07T16:00:30Z",
    }
    before = _sample("S1", "2026-08-07T16:00:00Z", 100_000, {})
    after = _sample("S2", "2026-08-07T16:01:00Z", 127_000, {101: 10})

    concurrent = _execution("E2", con_id=202, quantity=1)
    assert validate_margin_window(
        event, before, after, executions_in_window=[_execution("E1"), concurrent]
    )["valid"] is False

    stale_after = {
        **after,
        "observed_from": "2026-08-07T16:09:59Z",
        "observed_through": "2026-08-07T16:10:00Z",
    }
    assert validate_margin_window(
        event, before, stale_after, executions_in_window=[_execution("E1")]
    )["reason"] == "stale-samples"

    wrong_positions = {**after, "positions": {"101": 9.0}}
    assert validate_margin_window(
        event, before, wrong_positions, executions_in_window=[_execution("E1")]
    )["reason"] == "position-diff-mismatch"

    wrong_sign = {**after, "initial_margin": 99_000}
    assert validate_margin_window(
        event, before, wrong_sign, executions_in_window=[_execution("E1")]
    )["reason"] == "unexpected-margin-direction"


def test_v2_payload_requires_complete_execution_and_sample_provenance():
    payload = build_return_capital_payload(
        amount=27_000,
        instance={
            "instance_id": "PI-1",
            "account_id": "U1",
            "legs": {"101": 10.0},
        },
        observation={
            "observation_id": "O1",
            "observed_at": "2026-08-07T16:01:00Z",
            "before_sample_id": "S1",
            "after_sample_id": "S2",
            "exec_ids": ["E1"],
            "perm_ids": [9001],
            "order_refs": ["radon-spcx-1"],
            "multipliers": {"101": 100},
            "currency": "USD",
        },
    )

    assert payload is not None
    assert payload["version"] == 2
    assert payload["measurement"]["quality"] == "observed"
    assert payload["measurement"]["isolation"] == "isolated"
    assert payload["linkage"]["position_instance_id"] == "PI-1"
    assert payload["linkage"]["exec_ids"] == ["E1"]


def test_hydration_requires_account_instance_and_exact_leg_vector():
    position = {
        "ticker": "SPCX",
        "account_id": "U1",
        "legs": [{"con_id": 101, "contracts": 10, "direction": "LONG"}],
    }
    basis = {
        "instance_id": "PI-1",
        "account_id": "U1",
        "legs": {"101": 10.0},
        "payload": {"version": 2, "amount": 27_000},
    }

    assert hydrate_return_capital([position], [basis]) == 1
    assert position["position_instance_id"] == "PI-1"

    mismatched = {
        "ticker": "SPCX",
        "account_id": "U2",
        "legs": [{"con_id": 101, "contracts": 10, "direction": "LONG"}],
    }
    assert hydrate_return_capital([mismatched], [basis]) == 0
    assert "return_capital" not in mismatched


def _ledger_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
    migration = ROOT / "scripts" / "db" / "migrations" / "0037_position_return_capital.sql"
    db.executescript(migration.read_text())
    return db


def test_db_reconcile_is_append_only_idempotent_and_hydrates_v2():
    db = _ledger_db()
    assert insert_account_margin_sample(
        "U1",
        observed_from="2026-08-07T15:59:59Z",
        observed_through="2026-08-07T16:00:00Z",
        initial_margin=100_000,
        maintenance_margin=80_000,
        currency="USD",
        positions={},
        db=db,
    )
    assert upsert_position_execution_fact(_execution("E1"), db=db) is True
    assert upsert_position_execution_fact(_execution("E1"), db=db) is False
    assert insert_account_margin_sample(
        "U1",
        observed_from="2026-08-07T16:00:59Z",
        observed_through="2026-08-07T16:01:00Z",
        initial_margin=127_000,
        maintenance_margin=100_000,
        currency="USD",
        positions={"101": 10},
        db=db,
    )

    first = reconcile_position_return_capital(db, apply=True)
    second = reconcile_position_return_capital(db, apply=True)
    assert first["observations"] == 1
    assert second["observations"] == 1
    assert db.execute("SELECT count(*) FROM position_instances").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM position_instance_events").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM position_capital_observations").fetchone()[0] == 1

    bases = read_active_position_return_capital(db)
    assert len(bases) == 1
    assert bases[0]["payload"]["amount"] == 27_000
    assert bases[0]["payload"]["measurement"]["quality"] == "observed"


def test_execution_fact_hash_conflict_raises_instead_of_overwriting():
    db = _ledger_db()
    assert upsert_position_execution_fact(_execution("E1"), db=db) is True
    changed = {**_execution("E1"), "price": 9.99}
    try:
        upsert_position_execution_fact(changed, db=db)
    except ValueError as exc:
        assert "execution fact conflict" in str(exc)
    else:  # pragma: no cover - safety assertion
        raise AssertionError("conflicting immutable execution was overwritten")


def test_identity_hash_field_set_is_pinned():
    """The hashed field set is a contract, not whatever normalize_execution
    happens to return. Adding a field there must be a deliberate act.

    2026-08-13: commit 4eaaf5e9 added `revision` and `source_exec_id` to
    normalize_execution for reversal splitting. The hash covered every field
    except `commission`, so 70 of 76 stored facts instantly hashed differently
    and orders-sync aborted with `execution fact conflict` on the first
    pre-existing exec_id it re-synced.
    """
    from db.writer import _LIFECYCLE_HASH_FIELDS

    assert _LIFECYCLE_HASH_FIELDS == (
        "account_id",
        "con_id",
        "currency",
        "exec_id",
        "filled_at",
        "multiplier",
        "order_ref",
        "perm_id",
        "price",
        "quantity",
        "sec_type",
        "side",
        "signed_quantity",
        "symbol",
    )


def test_new_normalize_execution_field_does_not_manufacture_a_conflict(monkeypatch):
    """A field added to normalize_execution must not rewrite stored identities."""
    import db.writer as writer_mod
    from position_return_capital import normalize_execution as real_normalize

    db = _ledger_db()
    assert upsert_position_execution_fact(_execution("E1"), db=db) is True

    def normalize_with_new_field(raw):
        item = real_normalize(raw)
        item["some_future_field"] = "added by a later feature"
        return item

    monkeypatch.setattr(writer_mod, "normalize_execution", normalize_with_new_field, raising=False)
    monkeypatch.setattr(
        "position_return_capital.normalize_execution", normalize_with_new_field
    )

    # Same execution, unchanged lifecycle facts — must be a no-op, not a raise.
    assert upsert_position_execution_fact(_execution("E1"), db=db) is False


def test_real_lifecycle_change_still_conflicts(monkeypatch):
    """Pinning must not blunt the guard: a changed fact still raises."""
    db = _ledger_db()
    assert upsert_position_execution_fact(_execution("E1"), db=db) is True
    for field, value in (("price", 9.99), ("quantity", 11), ("side", "SLD")):
        changed = {**_execution("E1"), field: value}
        try:
            upsert_position_execution_fact(changed, db=db)
        except ValueError as exc:
            assert "execution fact conflict" in str(exc)
        else:  # pragma: no cover - safety assertion
            raise AssertionError(f"changed {field} was silently accepted")


def test_late_commission_enrichment_is_not_treated_as_execution_correction():
    db = _ledger_db()
    initial = {**_execution("E1"), "commission": 0}
    enriched = {**_execution("E1"), "commission": 19.5}
    assert upsert_position_execution_fact(initial, db=db) is True
    assert upsert_position_execution_fact(enriched, db=db) is False


def test_sparse_then_enriched_ib_fill_is_not_a_conflict():
    db = _ledger_db()
    exec_id = "0000e1a7.deadbeef.01.01"
    sparse = {
        "execId": exec_id,
        "account_id": "U1",
        "con_id": 101,
        "side": "BOT",
        "quantity": 10,
        "price": 4.25,
        "filled_at": "2026-08-07T16:00:30Z",
        "order_ref": "radon-spcx-1",
    }
    enriched = {
        "execId": exec_id,
        "account_id": "U1",
        "side": "BOT",
        "quantity": 10,
        "price": 4.25,
        "avgPrice": 4.80,
        "time": "2026-08-07T12:00:30-04:00",
        "permId": 9001,
        "orderRef": "radon-spcx-1",
        "symbol": "SPCX P120",
        "currency": "USD",
        "multiplier": 100,
        "sec_type": "OPT",
        "contract": {
            "conId": 101,
            "symbol": "SPCX",
            "secType": "OPT",
            "currency": "USD",
            "multiplier": "100",
        },
    }
    assert upsert_position_execution_fact(sparse, db=db) is True
    # Tolerated drift converges (R-077): the enriched sync updates the row
    # in place (perm_id reaches the ledger) instead of being dropped — still
    # never a conflict, and idempotent on the next identical sync.
    assert upsert_position_execution_fact(enriched, db=db) is True
    assert upsert_position_execution_fact(dict(enriched), db=db) is False
    row = db.execute(
        "SELECT COUNT(*), MAX(perm_id) FROM position_execution_facts WHERE exec_id = ?",
        (exec_id,),
    ).fetchone()
    assert tuple(row) == (1, 9001)


def test_naive_and_offset_equivalent_fill_times_are_not_conflicts():
    db = _ledger_db()
    naive = {**_execution("E1"), "filled_at": "2026-08-07T16:00:30"}
    offset = {**_execution("E1"), "filled_at": "2026-08-07T16:00:30+00:00"}
    assert upsert_position_execution_fact(naive, db=db) is True
    assert upsert_position_execution_fact(offset, db=db) is False


def test_avg_price_fallback_then_explicit_price_is_not_a_conflict():
    db = _ledger_db()
    fallback = {**_execution("E1")}
    fallback.pop("price")
    fallback["avgPrice"] = 4.25
    explicit = {**_execution("E1"), "price": 4.25, "avgPrice": 4.80}
    assert upsert_position_execution_fact(fallback, db=db) is True
    assert upsert_position_execution_fact(explicit, db=db) is False


def test_avg_price_drift_without_explicit_price_is_not_a_conflict():
    db = _ledger_db()
    first = {**_execution("E1")}
    first.pop("price")
    first["avgPrice"] = 4.25
    drifted = {**_execution("E1")}
    drifted.pop("price")
    drifted["avgPrice"] = 4.80
    assert upsert_position_execution_fact(first, db=db) is True
    # Tolerated drift converges (R-077): the drifted payload is adopted in
    # place — still never a conflict, and idempotent once adopted.
    assert upsert_position_execution_fact(drifted, db=db) is True
    assert upsert_position_execution_fact(dict(drifted), db=db) is False


def test_ib_exec_correction_invalidates_observed_basis_instead_of_double_counting():
    db = _ledger_db()
    insert_account_margin_sample(
        "U1", observed_from="2026-08-07T15:59:59Z", observed_through="2026-08-07T16:00:00Z", initial_margin=100_000,
        maintenance_margin=80_000, currency="USD", positions={}, db=db,
    )
    upsert_position_execution_fact(_execution("ROOT.01"), db=db)
    insert_account_margin_sample(
        "U1", observed_from="2026-08-07T16:00:59Z", observed_through="2026-08-07T16:01:00Z", initial_margin=127_000,
        maintenance_margin=100_000, currency="USD", positions={"101": 10}, db=db,
    )
    reconcile_position_return_capital(db, apply=True)
    assert len(read_active_position_return_capital(db)) == 1

    correction = {**_execution("ROOT.02"), "price": 4.20}
    upsert_position_execution_fact(correction, db=db)
    reconcile_position_return_capital(db, apply=True)

    assert read_active_position_return_capital(db) == []


def test_execution_correction_supersedes_prior_lifecycle_once():
    db = _ledger_db()
    insert_account_margin_sample(
        "U1", observed_from="2026-08-07T15:59:59Z",
        observed_through="2026-08-07T16:00:00Z", initial_margin=100_000,
        maintenance_margin=80_000, currency="USD", positions={}, db=db,
    )
    upsert_position_execution_fact(_execution("ROOT.01"), db=db)
    insert_account_margin_sample(
        "U1", observed_from="2026-08-07T16:00:59Z",
        observed_through="2026-08-07T16:01:00Z", initial_margin=127_000,
        maintenance_margin=100_000, currency="USD", positions={"101": 10}, db=db,
    )
    reconcile_position_return_capital(db, apply=True)

    upsert_position_execution_fact({**_execution("ROOT.02"), "price": 4.20}, db=db)
    reconcile_position_return_capital(db, apply=True)

    assert db.execute("SELECT count(*) FROM position_instances").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM position_instance_events").fetchone()[0] == 1
    links = db.execute(
        "SELECT exec_id, revision, price FROM position_event_executions"
    ).fetchall()
    assert links == [("ROOT", 2, 4.20)]
    observations = db.execute(
        "SELECT status, amount FROM position_capital_observations"
    ).fetchall()
    assert observations == [("VOID", None)]
