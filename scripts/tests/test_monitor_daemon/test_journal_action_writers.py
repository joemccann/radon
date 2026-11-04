"""Journal writers label through the shared classifier (audit §1 #11).

Covers the hazards the per-handler copies had: an unknown side labelled
BUY, a failed prior-qty lookup defaulting to 0 (a close written as a
phantom SELL_TO_OPEN), a stock short sale written as a close, a flip
through zero losing its residual, and CLOSED counted -qty by one reader
and 0 by another.
"""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from clients import journal_basis
from monitor_daemon.handlers import expiry_sweep, fill_monitor
from monitor_daemon.handlers.fill_monitor import FillMonitorHandler
from monitor_daemon.handlers.journal_sync import JournalSyncHandler


# -- fill_monitor --------------------------------------------------------------


def _fm_inputs(action="SELL", sec_type="OPT", filled=5):
    contract = SimpleNamespace(
        symbol="AAOI", secType=sec_type, conId=11, strike=40.0, right="C",
        lastTradeDateOrContractMonth="20261218", localSymbol="AAOI C40",
    )
    order = SimpleNamespace(action=action, permId=77, orderId=3)
    status = SimpleNamespace(filled=filled, remaining=0, avgFillPrice=1.5)
    fill_info = {
        "order_id": 3, "symbol": "AAOI", "total_filled": filled, "newly_filled": filled,
        "avg_price": 1.5, "prev_filled": 0, "prev_avg_price": None,
    }
    return fill_info, contract, order, status


def _fm_persist(monkeypatch, *, action="SELL", sec_type="OPT", prior=0.0, filled=5):
    upsert = MagicMock()
    monkeypatch.setattr(fill_monitor, "upsert_journal_entry", upsert)
    handler = FillMonitorHandler(send_notifications=False)
    with patch.object(FillMonitorHandler, "_lookup_prior_qty", return_value=prior):
        handler._persist_fill_to_journal(*_fm_inputs(action, sec_type, filled))
    return upsert


def test_fill_monitor_unknown_side_writes_nothing(monkeypatch):
    upsert = _fm_persist(monkeypatch, action="SSHORT")
    upsert.assert_not_called()


def test_fill_monitor_failed_prior_lookup_writes_nothing(monkeypatch):
    upsert = _fm_persist(monkeypatch, prior=None)
    upsert.assert_not_called()


def test_fill_monitor_prior_lookup_error_is_none_not_zero(monkeypatch):
    def boom():
        raise RuntimeError("turso down")

    monkeypatch.setattr(fill_monitor, "get_db", boom)
    contract = _fm_inputs()[1]
    assert FillMonitorHandler(send_notifications=False)._lookup_prior_qty(contract) is None


def test_fill_monitor_stock_short_sale_is_sell_to_open(monkeypatch):
    upsert = _fm_persist(monkeypatch, sec_type="STK", prior=0.0, filled=100)
    payload = upsert.call_args[0][1]
    assert payload["action"] == "SELL_TO_OPEN"
    assert payload["structure"].startswith("Short")


def test_fill_monitor_flip_records_residual(monkeypatch):
    upsert = _fm_persist(monkeypatch, prior=2.0, filled=5)
    payload = upsert.call_args[0][1]
    assert payload["action"] == "SELL_OPTION"
    assert payload["flip_open_qty"] == 3


# -- journal_sync --------------------------------------------------------------


def _fill(*, exec_id, side, shares, sec_type="OPT", symbol="AAOI"):
    execution = SimpleNamespace(
        execId=exec_id, side=side, shares=shares, price=2.0, permId=9,
        time=datetime(2026, 11, 2, 15, 0), acctNumber="U1", orderRef="",
    )
    contract = SimpleNamespace(
        symbol=symbol, secType=sec_type, strike=40.0 if sec_type != "STK" else None,
        right="C" if sec_type != "STK" else None,
        lastTradeDateOrContractMonth="20261218" if sec_type != "STK" else None,
        conId=11, currency="USD", multiplier=None,
    )
    return SimpleNamespace(execution=execution, contract=contract,
                           commissionReport=SimpleNamespace(commission=1.0, currency="USD"))


def _sync_entries(fills, prior):
    handler = JournalSyncHandler()
    with patch.object(JournalSyncHandler, "_lookup_prior_qty", return_value=prior):
        rows, _ = handler._fills_to_entries(fills, {"trades": []}, db=MagicMock())
    return rows


def test_journal_sync_failed_prior_lookup_refuses_every_fill_of_that_contract():
    rows = _sync_entries(
        [_fill(exec_id="A.01", side="SLD", shares=5), _fill(exec_id="B.01", side="SLD", shares=5)],
        prior=None,
    )
    assert rows == []


def test_journal_sync_prior_lookup_error_returns_none():
    db = MagicMock()
    with patch("monitor_daemon.handlers.journal_sync.prior_net_qty_for_contract",
               side_effect=RuntimeError("down")):
        assert JournalSyncHandler()._lookup_prior_qty(db, _fill(exec_id="x", side="SLD", shares=1)) is None
    assert JournalSyncHandler()._lookup_prior_qty(None, _fill(exec_id="x", side="SLD", shares=1)) is None


def test_journal_sync_cycle_with_unlabelled_fills_is_not_ok():
    handler = JournalSyncHandler()
    client = MagicMock()
    client.get_fills.return_value = [_fill(exec_id="U.01", side="SLD", shares=5)]
    with patch("monitor_daemon.handlers.journal_sync.IBClient", return_value=client), \
         patch.object(JournalSyncHandler, "_open_db", return_value=MagicMock()), \
         patch.object(JournalSyncHandler, "_load_existing_from_journal", return_value={"trades": []}), \
         patch.object(JournalSyncHandler, "_lookup_prior_qty", return_value=None), \
         patch.object(JournalSyncHandler, "_supersede_fill_monitor_mirrors", return_value=0):
        result = handler.execute()
    assert result["imported"] == 0
    assert result["unlabelled"] == 1
    assert "not labelled" in result["error"]


def test_journal_sync_stock_short_sale_is_sell_to_open():
    rows = _sync_entries([_fill(exec_id="S.01", side="SLD", shares=100, sec_type="STK")], prior=0.0)
    assert rows[0]["action"] == "SELL_TO_OPEN"


def test_journal_sync_stock_cover_is_buy_to_close():
    rows = _sync_entries([_fill(exec_id="C.01", side="BOT", shares=100, sec_type="STK")], prior=-100.0)
    assert rows[0]["action"] == "BUY_TO_CLOSE"


def test_journal_sync_flip_records_residual():
    rows = _sync_entries([_fill(exec_id="F.01", side="SLD", shares=8)], prior=5.0)
    assert rows[0]["action"] == "SELL_OPTION"
    assert rows[0]["flip_open_qty"] == 3


# -- CLOSED semantics: journal_basis and expiry_sweep agree --------------------


def _opt(action, qty, exec_id, ticker="AAOI"):
    return {
        "ticker": ticker, "action": action, "contracts": qty, "strike": 40.0,
        "right": "C", "expiry": "20261218", "date": "2026-11-02",
        "ib_exec_id": exec_id, "total_cost": 100.0,
    }


@pytest.mark.parametrize(
    "rows",
    [
        # Both sides journaled by the daemon AND the Flex round trip: flat.
        [_opt("BUY_OPTION", 10, "0001.a.01"), _opt("SELL_OPTION", 10, "0001.b.01"),
         _opt("CLOSED", 10, "111+222")],
        # A lone Flex round trip: flat.
        [_opt("CLOSED", 10, "111+222")],
    ],
)
def test_closed_round_trip_nets_zero_in_basis_and_sweep(rows):
    _, net = journal_basis._derive_journal_state_from_rows(
        [(json.dumps(r), "2026-11-02", "2026-11-02") for r in rows],
        option_net_keys=("AAOI|20261218|C|40.0",),
    )
    sweep_net, _ = expiry_sweep._net_open_quantity(rows)
    assert net["AAOI|20261218|C|40.0"] == 0
    assert sweep_net == 0


def test_expiry_sweep_close_row_uses_shared_closing_label():
    # A short residual is covered with the shared BUY_TO_CLOSE label.
    from utils.journal_action import Action, classify_fill

    assert classify_fill("BOT", "OPT", prior_qty=-3, fill_qty=3).action is Action.BUY_TO_CLOSE
