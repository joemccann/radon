"""REL-020 (R-023): ib_execute exit discipline.

A FILLED trade whose journal write fails must NOT exit 0 with "COMPLETE" -
it must print a loud "FILLED BUT NOT JOURNALED" banner and exit non-zero (4).
A journal numeric-id read failure must abort the journal write instead of
silently falling back to next_id=1 (id collision).
"""
import sys
from types import SimpleNamespace

import pytest

import ib_execute

FILLED_RESULT = {
    "status": "filled",
    "order_id": 7,
    "symbol": "NFLX",
    "side": "SELL",
    "quantity": 100,
    "avg_price": 98.70,
    "total_value": 9870.0,
    "commission": 1.25,
}

CLI_ARGS = [
    "ib_execute.py",
    "--type", "stock",
    "--symbol", "NFLX",
    "--qty", "100",
    "--side", "SELL",
    "--limit", "98.70",
    "--yes",
]


class FakeExecutor:
    """Stands in for OrderExecutor in main(); order fills, journaling varies."""

    def __init__(self, host, port, client_id):
        pass

    def connect(self):
        return True

    def disconnect(self):
        pass

    def get_stock_contract(self, symbol):
        return SimpleNamespace(symbol=symbol)

    def get_market_data(self, contract):
        return {"bid": 98.0, "ask": 99.0, "mid": 98.5, "last": 98.5, "spread": 1.0}

    def place_order(self, contract, side, qty, limit_price):
        return object()

    def monitor_order(self, trade, timeout, interval):
        return dict(FILLED_RESULT)

    def log_trade(self, *args, **kwargs):
        return True


def run_main(monkeypatch, executor_cls):
    monkeypatch.setattr(ib_execute, "OrderExecutor", executor_cls)
    monkeypatch.setattr(sys, "argv", CLI_ARGS)
    with pytest.raises(SystemExit) as excinfo:
        ib_execute.main()
    return excinfo.value.code


def test_journal_write_failure_exits_nonzero_with_banner(monkeypatch, capsys):
    class JournalWriteFails(FakeExecutor):
        def log_trade(self, *args, **kwargs):
            return False

    code = run_main(monkeypatch, JournalWriteFails)
    out = capsys.readouterr().out
    assert "FILLED BUT NOT JOURNALED" in out
    assert "✅ COMPLETE" not in out
    assert code == 4


def test_id_read_failure_aborts_journal_write(monkeypatch, capsys):
    import db.readers
    import db.writer

    def boom(*args, **kwargs):
        raise RuntimeError("turso unreachable")

    writes = []
    monkeypatch.setattr(db.readers, "read_next_journal_numeric_id", boom)
    monkeypatch.setattr(
        db.writer, "upsert_journal_entry", lambda *a, **k: writes.append(a)
    )

    executor = ib_execute.OrderExecutor.__new__(ib_execute.OrderExecutor)
    logged = executor.log_trade(
        dict(FILLED_RESULT), SimpleNamespace(symbol="NFLX"), "SELL", 98.70
    )

    assert logged is False
    assert writes == []  # must never write under a next_id=1 fallback


def test_successful_journal_keeps_exit_zero_complete(monkeypatch, capsys):
    code = run_main(monkeypatch, FakeExecutor)
    out = capsys.readouterr().out
    assert code == 0
    assert "✅ COMPLETE" in out
    assert "FILLED BUT NOT JOURNALED" not in out
