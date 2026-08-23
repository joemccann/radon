#!/usr/bin/env python3
"""REL-040 / R-085 (P0) — target and stop must not both fill.

904f2f18 made broker-truth adoption price-keyed (correctly: a target and a
stop are two SELL limits on ONE conId). That removed the property REL-003
relied on — "any working SELL on this contract is mine" — and nothing
replaced it. Two exit legs for one position, both inside the 40% gap
window, are both transmitted at FULL size with no ocaGroup, no parentId
and no held-size check. A whipsaw fills both and the account is left short
`contracts` naked options.

Two independent guards, because either alone leaves a hole:
  1. OCA — the legs of one journal row share `ocaGroup` + `ocaType=1`, so
     the broker cancels the sibling on the first fill.
  2. Oversell — working SELL quantity on the contract plus the new order
     may not exceed the held size, which also covers a price-edited or
     manually-placed working order the OCA group knows nothing about.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from monitor_daemon.handlers.exit_orders import ExitOrdersHandler
from test_exit_orders import FakeJournalDb
from test_exit_orders_guard_durability import _open_sell_trade, _confirmed_trade


SPEC = {"symbol": "GOOG", "expiry": "20260417", "strike": 315, "right": "C"}


def _trade(*, target=None, stop=None, held=44):
    exits = {}
    if target is not None:
        exits["target"] = {
            "price": target, "status": "PENDING", "order_id": None,
            "contracts": held, "contract_spec": dict(SPEC),
        }
    if stop is not None:
        exits["stop"] = {
            "price": stop, "status": "PENDING", "order_id": None,
            "contracts": held, "contract_spec": dict(SPEC),
        }
    return {
        "id": 8, "ticker": "GOOG", "structure": "Bull Call Spread",
        "contracts": held, "exit_orders": exits,
    }


def _run(db, *, mid=5.00, open_orders=()):
    """Same wiring as the guard-durability drill, with a settable mid so a
    target at 7.00 and a stop at 3.00 both sit exactly on the 40% edge."""
    with patch("monitor_daemon.handlers.exit_orders.IBClient") as mock_cls, \
         patch("monitor_daemon.handlers.exit_orders.Option"), \
         patch("monitor_daemon.handlers.exit_orders.LimitOrder") as mock_limit:
        client = MagicMock()
        mock_cls.return_value = client
        contract = MagicMock()
        contract.localSymbol = "GOOG  260417C00315000"
        contract.conId = 606060
        client.qualify_contracts.return_value = [contract]
        quote = MagicMock()
        quote.bid = mid - 0.10
        quote.ask = mid + 0.10
        quote.halted = 0
        client.get_quote.return_value = quote
        client.get_open_orders.return_value = list(open_orders)
        client.place_order.side_effect = lambda *_a, **_k: _confirmed_trade()
        result = ExitOrdersHandler(db=db).execute()
        return result, client, mock_limit


def _oca_kwargs(mock_limit):
    return [call.kwargs for call in mock_limit.call_args_list]


class TestOcaGroup:
    def test_target_and_stop_of_one_row_share_an_oca_group(self):
        """Both legs inside the gap window: whichever fills first must
        cancel the other at the broker, not leave a naked short."""
        db = FakeJournalDb([_trade(target=7.00, stop=3.00)])
        result, client, mock_limit = _run(db)

        assert client.place_order.call_count == 2, result
        groups = [kw.get("ocaGroup") for kw in _oca_kwargs(mock_limit)]
        assert all(groups), f"exit legs placed with no OCA group: {groups}"
        assert len(set(groups)) == 1, f"legs landed in different groups: {groups}"
        types = [kw.get("ocaType") for kw in _oca_kwargs(mock_limit)]
        assert types == [1, 1], f"ocaType must be 1 (cancel with block): {types}"

    def test_oca_group_is_scoped_to_the_journal_row(self):
        """Two positions must not share a group, or filling one cancels
        the other's exit."""
        db = FakeJournalDb([_trade(target=7.00, stop=3.00)])
        _, _, mock_limit = _run(db)
        group = _oca_kwargs(mock_limit)[0]["ocaGroup"]
        assert "8" in str(group), f"group must identify its journal row: {group}"


class TestOversellGuard:
    def test_working_sell_at_another_price_blocks_a_full_size_second_leg(self):
        """The R-085 injection: a working SELL of the whole position at
        7.00, and a stop at 3.00 still PENDING. Price-keyed adoption does
        not match it, so the old code placed a second full-size SELL."""
        db = FakeJournalDb([_trade(stop=3.00, held=44)])
        # Foreign order: no OCA link to this row (a manual TWS SELL, or
        # a legacy pre-OCA placement).
        working = _open_sell_trade(limit_price=7.00, oca_group=None, quantity=44)
        result, client, _ = _run(db, open_orders=[working])

        client.place_order.assert_not_called()
        reasons = {s.get("reason") for s in result.get("skipped", [])}
        assert "oversell_guard" in reasons, result

    def test_partial_working_quantity_still_allows_the_remainder(self):
        """Control: 20 of 44 working leaves room for 24, not for 44."""
        db = FakeJournalDb([_trade(stop=3.00, held=44)])
        db.trades["trade-8"]["exit_orders"]["stop"]["contracts"] = 24
        working = _open_sell_trade(limit_price=7.00, oca_group=None, quantity=20)
        result, client, _ = _run(db, open_orders=[working])

        assert client.place_order.call_count == 1, result

    def test_unreadable_working_quantity_is_not_treated_as_zero(self):
        """A MagicMock/None totalQuantity must fail safe, not pass the
        guard by comparing as 0."""
        db = FakeJournalDb([_trade(stop=3.00, held=44)])
        working = _open_sell_trade(limit_price=7.00, oca_group=None, quantity=None)
        result, client, _ = _run(db, open_orders=[working])

        client.place_order.assert_not_called()
        reasons = {s.get("reason") for s in result.get("skipped", [])}
        assert "oversell_guard" in reasons, result
