"""R-427 / R-428 / REL-145: every order-placing path carries BOTH guards.

Standing sweep 6 over the WHOLE repo, not the delta. `ib_place_order.py`,
`ib_execute.py` and (since REL-129) `exit_order_service.py` all check the halt
AND the limits before placing. Two sites checked only the halt:

R-427: `monitor_daemon/handlers/exit_orders.py` places a live GTC limit order
behind `is_trading_halted()` with no `check_order_limits()`. Unlike
`exit_order_service.py` this one is definitely running -- it is a handler in
`radon-monitor.service`. The exit leg is sized from the existing position so the
fat-finger class is bounded in practice, but the notional and combo max-loss
branches were skipped entirely and a corrupt position size reached IB unbounded.

R-428: `ib_order_manage.py`'s modify path sets `totalQuantity` and RE-PLACES
behind the halt with no limit check. The FastAPI route bounds it, so the web
path is covered -- but the script is directly invocable on the host, and the
guard living in the caller rather than at the placement funnel is the exact
inversion `ib_place_order.py` was restructured to avoid.

The tripwire at the bottom is the point: it enumerates every module that calls
`client.place_order(` / `.placeOrder(` outside tests and requires each to
reference `check_order_limits`, so the next site cannot be added silently.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _uncommented(path: Path) -> str:
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


class TestExitOrdersHandlerBoundsItsPlacement:
    """Driven through ``execute()`` with a stub client (T-313): the old test
    called ``check_order_limits`` directly and asserted a list nothing could
    ever append to, so it passed with the ``continue`` deleted."""

    SPEC = {"symbol": "SLV", "expiry": "20260417", "strike": 26, "right": "C"}
    LOCAL_SYMBOL = "SLV   260417C00026000"
    TARGET_PRICE = 3.20

    def _stub_halt(self, monkeypatch, halted: bool):
        monkeypatch.setitem(
            sys.modules, "trading_halt",
            type("T", (), {"is_trading_halted": staticmethod(lambda: halted),
                           "get_halt_state": staticmethod(lambda: {"reason": "test"})}),
        )

    def _pending(self, contracts: int) -> list[dict]:
        return [{
            "trade_id": 8,
            "ticker": "SLV",
            "structure": "Bull Call Spread",
            "order_type": "target",
            "target_price": self.TARGET_PRICE,
            "contracts": contracts,
            "held_contracts": contracts,
            "contract_spec": dict(self.SPEC),
            "action": "SELL",
            "journal_trade_id": "trade-8",
        }]

    def _stub_client(self):
        from unittest.mock import MagicMock

        client = MagicMock(name="IBClient")
        contract = MagicMock(name="contract")
        contract.localSymbol = self.LOCAL_SYMBOL
        contract.conId = 606060
        client.qualify_contracts.return_value = [contract]
        quote = MagicMock(name="quote")
        quote.bid, quote.ask, quote.halted = 3.10, 3.30, 0
        client.get_quote.return_value = quote
        client.get_open_orders.return_value = []

        def _acknowledged(*_a, **_k):
            trade = MagicMock(name="trade")
            trade.order.orderId = 99
            trade.order.permId = 777001
            trade.orderStatus.status = "Submitted"
            return trade

        client.place_order.side_effect = _acknowledged
        return client

    def _handler(self, monkeypatch, contracts: int, *, halted: bool = False):
        from unittest.mock import MagicMock
        from monitor_daemon.handlers import exit_orders as mod

        self._stub_halt(monkeypatch, halted)
        client = self._stub_client()
        monkeypatch.setattr(mod, "IBClient", lambda: client)
        monkeypatch.setattr(mod, "Option", MagicMock(name="Option"))
        monkeypatch.setattr(mod, "LimitOrder", MagicMock(name="LimitOrder"))

        handler = mod.ExitOrdersHandler(db=object())
        monkeypatch.setattr(handler, "_load_pending_orders", lambda: self._pending(contracts))
        monkeypatch.setattr(handler, "_can_place_order", lambda *_a: True)
        monkeypatch.setattr(handler, "_is_halted", lambda _ticker: False)
        monkeypatch.setattr(handler, "_update_journal_trade", lambda *_a, **_k: True)
        return handler, client

    def test_an_over_cap_quantity_never_reaches_ib(self, monkeypatch):
        import order_limits

        cap = order_limits.max_order_qty()
        handler, client = self._handler(monkeypatch, cap + 1)

        result = handler.execute()

        client.place_order.assert_not_called()
        assert result["orders_placed"] == 0 and result["placed"] == []
        assert result["orders_failed"] == 1, result
        assert result["failed"][0]["ticker"] == "SLV"
        assert str(cap) in result["failed"][0]["error"]
        # The refusal is THIS cycle's own outcome: a position left
        # unprotected must not heartbeat ok (base.py records error only
        # on a truthy result["error"]).
        assert result.get("error"), (
            "a limit refusal left result['error'] unset, so the watchdog "
            f"would see a green exit-orders cycle: {result}"
        )
        assert str(cap) in result["error"]
        assert "exceeds the server-side limit" in result["error"]

    def test_a_within_cap_exit_still_places(self, monkeypatch):
        handler, client = self._handler(monkeypatch, 1)

        result = handler.execute()

        assert client.place_order.call_count == 1, result
        assert result["orders_placed"] == 1
        assert result.get("orders_failed", 0) == 0
        assert not result.get("error"), result

    def test_a_refused_exit_heartbeats_error_not_ok(self, monkeypatch):
        """Through the BaseHandler wrapper: the service_health row the
        watchdog reads must say ``error`` when the exit was refused."""
        import db.writer as writer_mod
        import order_limits

        beats: list[tuple] = []
        monkeypatch.setattr(
            writer_mod, "record_service_health",
            lambda service, state, **kw: beats.append((service, state, kw)),
            raising=False,
        )
        cap = order_limits.max_order_qty()
        handler, client = self._handler(monkeypatch, cap + 1)

        wrapped = handler.run()

        client.place_order.assert_not_called()
        assert wrapped["status"] == "ok", wrapped  # soft: retried next cycle
        states = [(service, state) for service, state, _ in beats]
        assert states == [("exit-orders", "error")], beats
        assert str(cap) in str(beats[0][2].get("error", {}).get("message"))

    def test_the_halt_still_refuses_first(self, monkeypatch):
        """Halted: no placement AND no limit refusal — the halt is a
        deliberate operator state, so the cycle stays quiet and ok."""
        import order_limits

        cap = order_limits.max_order_qty()
        handler, client = self._handler(monkeypatch, cap + 1, halted=True)

        result = handler.execute()

        assert result.get("halted") is True, result
        client.place_order.assert_not_called()
        assert result.get("orders_failed", 0) == 0
        assert not result.get("error")


class TestOrderManageModifyBoundsItsReplacement:
    """Driven through ``modify_order()`` with a stub client (T-313); the
    old tests were substring-order greps that survived deleting the
    ``return`` after the refusal."""

    def _stub_halt(self, monkeypatch, halted: bool = False):
        monkeypatch.setitem(
            sys.modules, "trading_halt",
            type("T", (), {"is_trading_halted": staticmethod(lambda: halted),
                           "get_halt_state": staticmethod(lambda: {"reason": "test"})}),
        )

    def _working(self, quantity: int, *, price: float = 3.20):
        from unittest.mock import MagicMock

        trade = MagicMock(name="trade")
        trade.order.orderId = 10
        trade.order.permId = 12345
        trade.order.orderType = "LMT"
        trade.order.lmtPrice = price
        trade.order.totalQuantity = quantity
        trade.order.outsideRth = False
        trade.order.clientId = 0
        trade.orderStatus.status = "Submitted"
        trade.contract.secType = "OPT"
        trade.contract.symbol = "SLV"
        return trade

    def _client(self, snapshots):
        from unittest.mock import MagicMock

        client = MagicMock(name="IBClient")
        client.get_open_orders.side_effect = list(snapshots)
        client.sleep = MagicMock()
        client.ib = MagicMock()
        client.ib.client.clientId = 0
        client.place_order = MagicMock()
        return client

    def _modify(self, client, new_quantity):
        from ib_order_manage import modify_order

        with pytest.raises(SystemExit) as exc:
            modify_order(client, 10, 12345, None, "127.0.0.1", 4001,
                         new_quantity=new_quantity)
        return exc.value.code

    def test_an_over_cap_modify_never_reaches_ib(self, monkeypatch, capsys):
        import order_limits

        self._stub_halt(monkeypatch)
        cap = order_limits.max_order_qty()
        client = self._client([[self._working(1)]])

        code = self._modify(client, cap + 1)

        client.place_order.assert_not_called()
        assert code == 1
        import json

        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "error"
        assert str(cap) in data["message"]
        assert "exceeds the server-side limit" in data["message"]

    def test_the_refusal_does_not_rely_on_output_exiting(self, monkeypatch):
        """``output()`` sys.exits today; the ``return`` after it is the
        funnel's own guard. Silence ``output`` and the order must STILL
        not be re-transmitted."""
        import order_limits
        import ib_order_manage as mod

        self._stub_halt(monkeypatch)
        emitted: list[tuple] = []
        monkeypatch.setattr(mod, "output", lambda status, message, **kw: emitted.append((status, message)))
        cap = order_limits.max_order_qty()
        client = self._client([[self._working(1)]] * 12)

        mod.modify_order(client, 10, 12345, None, "127.0.0.1", 4001, new_quantity=cap + 1)

        client.place_order.assert_not_called()
        assert emitted and emitted[0][0] == "error" and str(cap) in emitted[0][1]

    def test_a_within_cap_modify_still_places(self, monkeypatch):
        self._stub_halt(monkeypatch)
        working = self._working(1)
        confirmed = self._working(2)
        client = self._client([[working], [confirmed]])

        code = self._modify(client, 2)

        assert code == 0
        client.place_order.assert_called_once_with(working.contract, working.order)

    def test_the_halt_still_refuses_first(self, monkeypatch, capsys):
        import json
        import order_limits

        self._stub_halt(monkeypatch, halted=True)
        client = self._client([[self._working(1)]])

        code = self._modify(client, order_limits.max_order_qty() + 1)

        client.place_order.assert_not_called()
        assert code == 1
        data = json.loads(capsys.readouterr().out)
        assert "TRADING HALTED" in data["message"]
        assert "server-side limit" not in data["message"]


class TestTheTransportIsTheLastFunnel:
    """`IBClient.place_order` is the one place a new call site cannot go around."""

    def _order(self, qty, sec_type="OPT"):
        from types import SimpleNamespace

        return (
            SimpleNamespace(secType=sec_type, symbol="SLV", localSymbol="SLV 260C"),
            SimpleNamespace(action="SELL", totalQuantity=qty, lmtPrice=3.2),
        )

    def _client(self, placed):
        from clients.ib_client import IBClient

        client = IBClient.__new__(IBClient)
        client._require_connection = lambda: None
        client._ib = type("IB", (), {"placeOrder": staticmethod(
            lambda c, o: placed.append((c, o)) or type("T", (), {"order": type("O", (), {"orderId": 1})()})()
        )})()
        client.logger = type("L", (), {"info": staticmethod(lambda *a, **k: None)})()
        return client

    def test_an_over_cap_order_never_reaches_the_socket(self):
        import order_limits
        from clients.ib_client import IBOrderError

        placed: list = []
        contract, order = self._order(order_limits.max_order_qty() + 1)
        with pytest.raises(IBOrderError, match="exceeds the server-side limit"):
            self._client(placed).place_order(contract, order)
        assert placed == []

    def test_a_within_limits_order_still_places(self):
        placed: list = []
        contract, order = self._order(1)
        self._client(placed).place_order(contract, order)
        assert len(placed) == 1

    def test_a_legitimate_combo_is_not_refused_for_want_of_leg_detail(self):
        """A comboLeg carries a conId, not a strike, so the combo branch would
        fail CLOSED on every BAG. The funnel applies the quantity cap only."""
        placed: list = []
        contract, order = self._order(2, sec_type="BAG")
        self._client(placed).place_order(contract, order)
        assert len(placed) == 1

    def test_an_over_cap_combo_is_still_refused(self):
        import order_limits
        from clients.ib_client import IBOrderError

        placed: list = []
        contract, order = self._order(order_limits.max_order_qty() + 1, sec_type="BAG")
        with pytest.raises(IBOrderError):
            self._client(placed).place_order(contract, order)
        assert placed == []


class TestNoPlacementSiteEscapesTheLimits:
    """The tripwire: the next site cannot be added silently."""

    PLACEMENT = re.compile(r"(?<!def )\bclient\.place_order\(|\.placeOrder\(")

    def _sources(self):
        for path in sorted(SCRIPTS.rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            if "/tests/" in rel or rel.startswith("scripts/tests/"):
                continue
            yield path

    def test_every_place_order_call_site_references_the_limits(self):
        offenders = []
        for path in self._sources():
            body = _uncommented(path)
            if not self.PLACEMENT.search(body):
                continue
            if "check_order_limits" not in body:
                offenders.append(path.relative_to(REPO).as_posix())
        assert not offenders, (
            "these modules place a live order without referencing the "
            f"server-side order limits: {offenders}"
        )

    def test_the_tripwire_finds_the_known_placement_sites(self):
        """A tripwire that matches nothing asserts nothing."""
        found = {
            path.relative_to(REPO).as_posix()
            for path in self._sources()
            if self.PLACEMENT.search(_uncommented(path))
        }
        for expected in (
            "scripts/ib_place_order.py",
            "scripts/ib_order_manage.py",
            "scripts/monitor_daemon/handlers/exit_orders.py",
        ):
            assert expected in found, (expected, sorted(found))
