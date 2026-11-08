#!/usr/bin/env python3
"""journal_sync labels an execution against a prior that excludes the
fill_monitor mirror rows duplicating that same execution.

fill_monitor journals a detected fill under ``fill-monitor:con-…:order-…:
{date}:filled-N`` before journal_sync imports the real execution. The prior
read (``prior_net_qty_for_contract``) sums every journal row for the
contract, so without this exclusion the execution being labelled is already
in its own prior: a full close of a long read prior 0 and was written
SELL_TO_OPEN (phantom short), and a close split across a partial mirror read
a phantom FLIP residual. The mirror is deleted by the supersede pass
afterwards, so only the wrong label survived.

Runs the real ``prior_net_qty_for_contract`` against an in-memory journal.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from unittest.mock import MagicMock, patch

from monitor_daemon.handlers.journal_sync import JournalSyncHandler

DATE = "2026-08-07"
CON = 123
PERM = 777


def _journal_db(*rows: tuple[str, dict]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE journal (trade_id TEXT PRIMARY KEY, payload TEXT, "
        "filled_at TEXT, written_at TEXT)"
    )
    for trade_id, payload in rows:
        conn.execute(
            "INSERT INTO journal VALUES (?, ?, ?, ?)",
            (trade_id, json.dumps(payload), DATE, f"{DATE}T14:00:00Z"),
        )
    return conn


def _option_row(trade_id: str, action: str, contracts: int, **extra) -> dict:
    return {
        "date": DATE, "ticker": "MU", "action": action, "contracts": contracts,
        "strike": 100.0, "right": "C", "expiry": "20260918",
        "fill_price": 1.0, "total_cost": contracts * 100.0, "ib_exec_id": trade_id,
        **extra,
    }


def _opening_long(contracts: int = 10) -> tuple[str, dict]:
    return ("0000.open", _option_row(
        "0000.open", "BUY_OPTION", contracts, con_id=CON, perm_id=111, date="2026-08-06",
    ))


def _mirror(action: str, newly: int, total: int, perm: int = PERM) -> tuple[str, dict]:
    trade_id = f"fill-monitor:con-{CON}:order-{perm}:{DATE}:filled-{total}"
    return (trade_id, _option_row(
        trade_id, action, newly, decision="FILL_MONITOR_AUTO_IMPORT",
        newly_filled=newly, total_filled=total,
    ))


def _sell(exec_id: str, shares: int, minute: int, perm: int = PERM) -> MagicMock:
    fill = MagicMock()
    fill.execution.execId = exec_id
    fill.execution.permId = perm
    fill.execution.side = "SLD"
    fill.execution.shares = shares
    fill.execution.price = 1.0
    fill.execution.time = datetime(2026, 8, 7, 14, minute, 0)
    fill.execution.acctNumber = "U000000"
    fill.execution.orderRef = ""
    fill.contract.symbol = "MU"
    fill.contract.secType = "OPT"
    fill.contract.strike = 100.0
    fill.contract.right = "C"
    fill.contract.lastTradeDateOrContractMonth = "20260918"
    fill.contract.conId = CON
    fill.contract.multiplier = "100"
    fill.contract.currency = "USD"
    fill.commissionReport.commission = 0.0
    fill.commissionReport.currency = "USD"
    return fill


def _entries(conn: sqlite3.Connection, fills: list) -> list:
    handler = JournalSyncHandler()  # trade_log_path=None: prod wiring
    existing = handler._load_existing_from_journal(conn, allow_empty=True)
    with patch("monitor_daemon.handlers.journal_sync.et_session_date", return_value=DATE):
        rows, _corrections = handler._fills_to_entries(fills, existing, db=conn)
    assert handler.unlabelled == 0
    return rows


def test_full_close_mirrored_before_import_is_a_close_not_a_short():
    conn = _journal_db(_opening_long(10), _mirror("SELL_OPTION", 10, 10))

    rows = _entries(conn, [_sell("0001.aa", 10, 30)])

    assert [r["action"] for r in rows] == ["SELL_OPTION"]
    assert "flip_open_qty" not in rows[0]


def test_close_split_across_a_partial_mirror_records_no_phantom_flip():
    conn = _journal_db(_opening_long(10), _mirror("SELL_OPTION", 4, 4))

    rows = _entries(conn, [_sell("0001.aa", 4, 30), _sell("0001.ab", 6, 31)])

    assert [r["action"] for r in rows] == ["SELL_OPTION", "SELL_OPTION"]
    assert all("flip_open_qty" not in r for r in rows)


def test_mirror_beyond_the_imported_executions_still_counts():
    """Only the overlap is a duplicate: a mirror increment no execution
    covers yet is still the journal's only record of that fill."""
    conn = _journal_db(_opening_long(10), _mirror("SELL_OPTION", 6, 10))

    # Executions cover filled 0..4 only; the mirror covers 4..10.
    rows = _entries(conn, [_sell("0001.aa", 4, 30)])

    # Prior = 10 - 6 (uncovered mirror) = 4, so a 4-lot sell is a plain close.
    assert [r["action"] for r in rows] == ["SELL_OPTION"]
    assert "flip_open_qty" not in rows[0]


def test_unrelated_order_mirror_still_counts_in_the_prior():
    conn = _journal_db(_opening_long(10), _mirror("SELL_OPTION", 4, 4, perm=888))

    rows = _entries(conn, [_sell("0001.aa", 8, 30)])

    # Prior 10 - 4 = 6: an 8-lot sell flips through zero with a 2-lot residual.
    assert rows[0]["action"] == "SELL_OPTION"
    assert rows[0]["flip_open_qty"] == 2
