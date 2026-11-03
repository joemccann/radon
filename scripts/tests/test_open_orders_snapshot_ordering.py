"""open_orders has two snapshot writers (audit §3.1, C3).

FastAPI's orders-sync and fill_monitor's mirror both whole-replace the
table. With no snapshot-time ordering, an older book written last deleted
the rows a newer book had inserted. Each snapshot now carries the time IB
was read; an older snapshot is rejected and can neither overwrite nor
delete a newer one's rows. fill_monitor's mirror no longer heartbeats the
orders-sync service row it does not own.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

T1 = "2026-11-03T15:00:00.000000Z"
T2 = "2026-11-03T15:00:05.000000Z"


class _Db:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.isolation_level = None
        self.conn.execute(
            "CREATE TABLE open_orders (perm_id INTEGER PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        self.conn.execute(
            "CREATE TABLE app_config (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )

    def execute(self, sql, params=()):
        return self.conn.execute(sql, params)

    def commit(self):
        pass

    def book(self):
        return {
            row[0]: json.loads(row[1])
            for row in self.conn.execute("SELECT perm_id, payload FROM open_orders")
        }


@pytest.fixture
def db(monkeypatch):
    import db.writer as writer_mod

    fake = _Db()
    monkeypatch.setattr(writer_mod, "get_db", lambda: fake)
    return fake


def test_older_snapshot_is_rejected_after_a_newer_one(db):
    from db.writer import replace_open_orders_for_session

    assert replace_open_orders_for_session([(2, {"v": "new"})], snapshot_at=T2) is True
    assert replace_open_orders_for_session([(1, {"v": "old"})], snapshot_at=T1) is False
    assert db.book() == {2: {"v": "new"}}


def test_older_snapshot_cannot_delete_or_overwrite_newer_rows(db):
    from db.writer import replace_open_orders_for_session

    db.conn.execute(
        "INSERT INTO open_orders VALUES (?, ?, ?), (?, ?, ?)",
        (4, json.dumps({"v": "new"}), T2, 9, json.dumps({"v": "new"}), T2),
    )
    replace_open_orders_for_session([(4, {"v": "old"})], snapshot_at=T1)
    assert db.book() == {4: {"v": "new"}, 9: {"v": "new"}}
    replace_open_orders_for_session([], snapshot_at=T1)
    assert db.book() == {4: {"v": "new"}, 9: {"v": "new"}}


def test_newer_snapshot_replaces_older_rows(db):
    from db.writer import replace_open_orders_for_session

    replace_open_orders_for_session([(1, {"v": 1}), (2, {"v": 2})], snapshot_at=T1)
    replace_open_orders_for_session([(2, {"v": 22})], snapshot_at=T2)
    assert db.book() == {2: {"v": 22}}
    replace_open_orders_for_session([], snapshot_at="2026-11-03T15:00:09.000000Z")
    assert db.book() == {}


def test_build_orders_data_carries_snapshot_time_to_the_writer(monkeypatch):
    calls = []
    fake = types.ModuleType("db.writer")
    fake.ensure_no_replica_for_writers = lambda: None
    fake.replace_open_orders_for_session = lambda rows, **kw: calls.append(kw)
    fake.upsert_executed_orders_for_session = lambda rows: 0
    fake.upsert_position_execution_facts = lambda rows: SimpleNamespace(written=0, failures=[])
    monkeypatch.setitem(sys.modules, "db.writer", fake)
    sys.modules.pop("ib_orders", None)
    import ib_orders

    data = ib_orders.build_orders_data([{"permId": 5}], [], snapshot_at=T1)
    assert data["snapshot_at"] == T1
    with patch("db.service_cycle._record"):
        ib_orders._dual_write_orders_to_db(data)
    assert calls == [{"snapshot_at": T1}]


def test_mirror_write_does_not_heartbeat_orders_sync(monkeypatch):
    fake = types.ModuleType("db.writer")
    fake.ensure_no_replica_for_writers = lambda: None
    fake.replace_open_orders_for_session = lambda rows, **kw: True
    fake.upsert_executed_orders_for_session = lambda rows: 0
    fake.upsert_position_execution_facts = lambda rows: SimpleNamespace(written=0, failures=[])
    monkeypatch.setitem(sys.modules, "db.writer", fake)
    sys.modules.pop("ib_orders", None)
    import ib_orders

    with patch("db.service_cycle._record") as record:
        ib_orders.save_orders(ib_orders.build_orders_data([], []), heartbeat=False)
    record.assert_not_called()
    with patch("db.service_cycle._record") as record:
        ib_orders.save_orders(ib_orders.build_orders_data([], []))
    assert record.call_args[0][:2] == ("orders-sync", "ok")


def test_fill_monitor_mirror_passes_read_time_and_no_heartbeat(monkeypatch):
    from monitor_daemon.handlers import fill_monitor

    save = MagicMock()
    monkeypatch.setattr(fill_monitor, "save_orders_snapshot", save)
    monkeypatch.setattr(fill_monitor, "fetch_open_orders_for_mirror", lambda c: [{"permId": 1, "orderId": 1}])
    monkeypatch.setattr(fill_monitor, "fetch_executed_orders_for_mirror", lambda c: [])
    monkeypatch.setattr(
        fill_monitor, "build_orders_data_for_mirror",
        lambda o, e, snapshot_at=None: {"open_orders": o, "snapshot_at": snapshot_at},
    )
    handler = fill_monitor.FillMonitorHandler(send_notifications=False)
    handler.known_orders = {1: {"order_id": 1, "perm_id": 1}}
    handler._mirror_ib_orders_snapshot(MagicMock())
    data = save.call_args[0][0]
    assert data["snapshot_at"]
    assert save.call_args.kwargs == {"heartbeat": False}
