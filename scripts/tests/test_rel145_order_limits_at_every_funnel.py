"""R-427 / R-428 / REL-145: every order-placing path carries BOTH guards.

Standing sweep 6 over the WHOLE repo, not the delta. `ib_place_order.py` and
`ib_execute.py` check the halt AND the limits before placing. A site that
checked only the halt:

R-428: `ib_order_manage.py`'s modify path sets `totalQuantity` and RE-PLACES
behind the halt with no limit check. The FastAPI route bounds it, so the web
path is covered -- but the script is directly invocable on the host, and the
guard living in the caller rather than at the placement funnel is the exact
inversion `ib_place_order.py` was restructured to avoid.

The tripwire at the bottom is the point: it enumerates every module that calls
`client.place_order(` / `.placeOrder(` outside tests and requires each to
reference `check_order_limits` or `check_placement_limits` (the placement
wrapper that calls it, and waives only a fresh reducing close), so the next
site cannot be added silently.
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
            if "check_order_limits" not in body and "check_placement_limits" not in body:
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
        ):
            assert expected in found, (expected, sorted(found))

class TestRel166PricelessOrders(TestTheTransportIsTheLastFunnel):
    """R-467: absent pricing cannot silently skip the dollar cap."""

    @pytest.mark.parametrize("operation", ["place_order", "modify_order"])
    @pytest.mark.parametrize("price", [None, 0, -1, float("nan"), float("inf"), "bad"])
    def test_unpriced_non_combo_never_reaches_the_socket(self, price, operation):
        from clients.ib_client import IBOrderError
        placed = []
        contract, order = self._order(50)
        order.orderType = "MKT" if price is None else "LMT"
        order.lmtPrice = price
        with pytest.raises(IBOrderError, match="positive finite.*price"):
            getattr(self._client(placed), operation)(contract, order)
        assert placed == []

    @pytest.mark.parametrize("operation", ["place_order", "modify_order"])
    def test_stop_order_uses_its_trigger_for_the_notional_cap(self, operation):
        import order_limits
        from clients.ib_client import IBOrderError
        placed = []
        contract, order = self._order(1, sec_type="STK")
        order.orderType = "STP"
        order.lmtPrice = 0
        order.auxPrice = order_limits.max_order_notional() + 1
        with pytest.raises(IBOrderError, match="notional"):
            getattr(self._client(placed), operation)(contract, order)
        assert placed == []

    @pytest.mark.parametrize("operation", ["place_order", "modify_order"])
    def test_priced_stop_remains_usable_for_bracket_protection(self, operation):
        placed = []
        contract, order = self._order(1, sec_type="STK")
        order.orderType = "STP"
        order.lmtPrice = 0
        order.auxPrice = 100
        getattr(self._client(placed), operation)(contract, order)
        assert placed == [(contract, order)]


class TestFutureModifyAtTheTransport(TestTheTransportIsTheLastFunnel):
    """DS-2026-10-24-03: the modify re-submit prices a FUT with its own multiplier."""

    def _future(self, multiplier):
        contract, order = self._order(1, sec_type="FUT")
        contract.multiplier = multiplier
        order.orderType = "LMT"
        order.lmtPrice = 4000.0
        return contract, order

    @pytest.mark.parametrize("operation", ["place_order", "modify_order"])
    def test_a_future_is_priced_with_the_contract_multiplier(self, operation, monkeypatch):
        from clients.ib_client import IBOrderError

        monkeypatch.setenv("RADON_MAX_ORDER_NOTIONAL", "1000000")
        placed = []
        contract, order = self._future("1000")
        with pytest.raises(IBOrderError, match="notional"):
            getattr(self._client(placed), operation)(contract, order)
        assert placed == []

    @pytest.mark.parametrize("operation", ["place_order", "modify_order"])
    def test_a_small_future_still_places(self, operation, monkeypatch):
        monkeypatch.setenv("RADON_MAX_ORDER_NOTIONAL", "1000000")
        placed = []
        contract, order = self._future("50")
        getattr(self._client(placed), operation)(contract, order)
        assert placed == [(contract, order)]
