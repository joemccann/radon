"""Regression — orders-sync passed LIST parameters to libsql execute (2026-08-09).

Production incident: service_health row "orders-sync" latched state=error with
``argument 'parameters': 'list' object cannot be converted to 'PyTuple'``.

Root cause: ``db.writer.replace_open_orders_for_session`` (T-024 rewrite,
bd8718e8) builds both the chunked multi-row UPSERT ``params`` and the prune
``DELETE ... NOT IN`` id list as Python LISTS. ``libsql_experimental``'s PyO3
binding converts positional parameters via ``PyTuple`` and rejects lists with
that exact TypeError. The empty-snapshot path (bare ``DELETE FROM open_orders``,
no params) never trips it, so the defect only detonates when the IB snapshot
carries working orders — the intraday orders-sync cycle.

Production caller chain encoded here:
    ib_orders._dual_write_orders_to_db
      → service_cycle("orders-sync")               (error row + embargo on raise)
        → db.writer.replace_open_orders_for_session
          → libsql_experimental Connection.execute(sql, <list>)   ← raises

These tests run the REAL ``libsql_experimental`` binding against an in-memory
DB (never live Turso — ``get_db`` is monkeypatched at the same boundary the
existing writer tests use), so they fail for exactly the production reason
until the writer passes tuples.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

libsql = pytest.importorskip(
    "libsql_experimental",
    reason="regression must run against the real PyO3 binding that rejects lists",
)


def _iso_minutes_ago(minutes: int) -> str:
    stamp = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return stamp.isoformat().replace("+00:00", "Z")


@pytest.fixture
def libsql_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    """Real libsql_experimental in-memory connection wired in as get_db().

    Patching ``db.client.get_db`` AND ``db.writer.get_db`` covers both the
    writer's import-time binding and the late ``from db.client import get_db``
    inside the return-capital reconcile step. The PYTEST_CURRENT_TEST guard in
    the real ``get_db`` never fires because it is fully replaced.
    """
    conn = libsql.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS open_orders (
          perm_id     INTEGER PRIMARY KEY,
          payload     TEXT    NOT NULL,
          updated_at  TEXT    NOT NULL
        )
        """
    )
    conn.commit()

    monkeypatch.setenv("RADON_DB_NO_REPLICA", "1")

    import db.client as client_mod
    import db.writer as writer_mod

    # Pin the real writer module in case a sibling test left a stub behind.
    monkeypatch.setitem(sys.modules, "db.writer", writer_mod)
    monkeypatch.setattr(client_mod, "get_db", lambda: conn)
    monkeypatch.setattr(writer_mod, "get_db", lambda: conn)
    yield conn


def _open_order_payload(perm_id: int, symbol: str) -> dict:
    return {
        "permId": perm_id,
        "orderId": perm_id - 8000,
        "symbol": symbol,
        "action": "BUY",
        "orderType": "LMT",
        "totalQuantity": 2.0,
        "limitPrice": 1.25,
        "status": "Submitted",
        "tif": "DAY",
    }


def _select_perm_ids(conn: object) -> list[int]:
    rows = conn.execute("SELECT perm_id FROM open_orders ORDER BY perm_id").fetchall()
    return [row[0] for row in rows]


# ---------------------------------------------------------------------------
# Writer level — the statements that received lists
# ---------------------------------------------------------------------------


def test_replace_open_orders_snapshot_round_trips_through_real_libsql(libsql_db):
    """Non-empty snapshot: chunked UPSERT + prune DELETE against the real
    binding. Red while either statement passes a list."""
    import db.writer as writer_mod

    # Stale row from a previous session — the prune DELETE must remove it.
    libsql_db.execute(
        "INSERT INTO open_orders (perm_id, payload, updated_at) VALUES (?, ?, ?)",
        (1111, "{}", _iso_minutes_ago(90)),
    )
    libsql_db.commit()

    snapshot = [
        (9001, _open_order_payload(9001, "AAPL")),
        (9002, _open_order_payload(9002, "MSFT")),
    ]
    writer_mod.replace_open_orders_for_session(snapshot)

    assert _select_perm_ids(libsql_db) == [9001, 9002]


def test_replace_open_orders_survives_chunk_boundary(libsql_db):
    """201 working orders crosses the 200-row chunk boundary — every chunk's
    params and the 201-id prune DELETE must be binding-legal."""
    import db.writer as writer_mod

    snapshot = [
        (10_000 + i, _open_order_payload(10_000 + i, f"SYM{i}")) for i in range(201)
    ]
    writer_mod.replace_open_orders_for_session(snapshot)

    perm_ids = _select_perm_ids(libsql_db)
    assert len(perm_ids) == 201
    assert perm_ids[0] == 10_000 and perm_ids[-1] == 10_200


# ---------------------------------------------------------------------------
# Full production chain — ib_orders orders-sync cycle
# ---------------------------------------------------------------------------


def test_orders_sync_cycle_heartbeats_ok_with_working_orders(
    libsql_db, monkeypatch: pytest.MonkeyPatch
):
    """_dual_write_orders_to_db with a non-empty open-orders snapshot must end
    the service_cycle("orders-sync") with an ok heartbeat and the rows
    persisted — not the error row + retry embargo production latched."""
    import db.writer as writer_mod

    health: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        writer_mod,
        "record_service_health",
        lambda service, state, **kw: health.append((service, state, kw)),
    )

    if "ib_orders" in sys.modules:
        del sys.modules["ib_orders"]
    import ib_orders

    data = {
        "last_sync": _iso_minutes_ago(1),
        "open_orders": [
            _open_order_payload(9001, "AAPL"),
            _open_order_payload(9002, "MSFT"),
        ],
        "executed_orders": [],
    }
    ib_orders._dual_write_orders_to_db(data)

    cycle_rows = [(state, kw) for service, state, kw in health if service == "orders-sync"]
    error_rows = [kw.get("error") for state, kw in cycle_rows if state == "error"]
    assert not error_rows, (
        f"orders-sync cycle latched error row(s) (production incident shape): {error_rows}"
    )
    assert [state for state, _ in cycle_rows] == ["ok"]
    assert _select_perm_ids(libsql_db) == [9001, 9002]
