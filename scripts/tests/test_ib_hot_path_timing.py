"""Hot-path timestamps on portfolio sync and place-order.

Plan: mark connect / qualify / sleep (sync) and connect / qualify / permId
(place) on stderr as ``ib_hot_path_timing`` JSON. Stdout stays result JSON.
"""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock, patch

import eventkit

import ib_place_order
import ib_sync


def _timing_records(stderr: str) -> list[dict]:
    records = []
    for line in stderr.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if parsed.get("event") == "ib_hot_path_timing":
            records.append(parsed)
    return records


def _make_place_client() -> MagicMock:
    trade = MagicMock()
    trade.order = MagicMock()
    trade.order.orderId = 99
    trade.order.permId = 12345
    trade.orderStatus = MagicMock()
    trade.orderStatus.status = "Submitted"
    trade.orderStatus.filled = 0
    trade.orderStatus.remaining = 100
    trade.orderStatus.avgFillPrice = 0.0
    trade.log = []

    client = MagicMock()
    client._ib = MagicMock()
    client._ib.errorEvent = eventkit.Event("errorEvent")
    client.place_order = MagicMock(return_value=trade)
    client.qualify_contracts = MagicMock(return_value=[MagicMock(conId=123456)])
    client.sleep = MagicMock()
    client.disconnect = MagicMock()
    client.connect = MagicMock()
    return client


def test_place_order_emits_connect_qualify_permId_timing(capsys):
    client = _make_place_client()
    params = {
        "type": "stock",
        "symbol": "AAPL",
        "action": "BUY",
        "quantity": 100,
        "limitPrice": 214.50,
        "tif": "DAY",
    }
    with patch("ib_place_order.IBClient", return_value=client), \
         patch("ib_place_order.Stock", return_value=MagicMock()), \
         patch("ib_place_order.LimitOrder", return_value=MagicMock()):
        result = ib_place_order.place_order(params)

    assert result["status"] == "ok"
    records = _timing_records(capsys.readouterr().err)
    assert records, "expected ib_hot_path_timing on stderr"
    assert records[0]["job"] == "ib_place_order"
    phases = [p["phase"] for p in records[0]["phases"]]
    assert "connect" in phases
    assert "qualify" in phases
    assert "permId" in phases


def test_ib_sync_main_emits_connect_qualify_sleep_timing(monkeypatch, capsys):
    client = MagicMock()
    client.ib.managedAccounts.return_value = ["DU1"]
    client.ib.reqPnL.return_value = MagicMock(
        dailyPnL=12.0, unrealizedPnL=8.0, realizedPnL=4.0,
    )
    client.is_connected.return_value = True
    client.disconnect = MagicMock()
    client.wait_until = MagicMock(return_value=True)
    client.sleep = MagicMock()
    client.cancel_pnl = MagicMock()

    monkeypatch.setattr(ib_sync, "connect_ib", lambda *a, **k: client)
    monkeypatch.setattr(ib_sync, "get_account_summary", lambda *a, **k: {"NetLiquidation": 100000})
    monkeypatch.setattr(ib_sync, "build_journal_basis_lookup", lambda *a, **k: {})
    monkeypatch.setattr(ib_sync, "fetch_positions", lambda *a, **k: [])
    monkeypatch.setattr(ib_sync, "collapse_positions", lambda positions: [])
    monkeypatch.setattr(ib_sync, "display_portfolio", lambda *a, **k: None)

    monkeypatch.setattr(sys, "argv", ["ib_sync.py", "--no-prices", "--skip-audit"])
    ib_sync.main()

    records = _timing_records(capsys.readouterr().err)
    assert records, "expected ib_hot_path_timing on stderr"
    assert records[0]["job"] == "ib_sync"
    phases = [p["phase"] for p in records[0]["phases"]]
    assert "connect" in phases
    assert "qualify" in phases
    assert "sleep" in phases
