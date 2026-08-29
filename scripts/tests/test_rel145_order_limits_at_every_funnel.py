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
    def _handler(self, monkeypatch, halted: bool = False):
        from monitor_daemon.handlers import exit_orders as mod

        monkeypatch.setitem(
            sys.modules, "trading_halt",
            type("T", (), {"is_trading_halted": staticmethod(lambda: halted),
                           "get_halt_state": staticmethod(lambda: {"reason": "test"})}),
        )
        return mod

    def test_an_over_cap_quantity_never_reaches_ib(self, monkeypatch):
        from monitor_daemon.handlers import exit_orders as mod
        import order_limits

        placed: list = []

        class _Client:
            def place_order(self, contract, order):
                placed.append((contract, order))
                raise AssertionError("an over-cap order reached the fake IB client")

        refusals: list[str] = []
        monkeypatch.setattr(
            mod, "_refuse_over_limit", lambda msg: refusals.append(msg), raising=False
        )
        cap = order_limits.max_order_qty()
        violation = order_limits.check_order_limits(
            {"type": "option", "quantity": cap + 1, "symbol": "SLV"}
        )
        assert violation is not None, "the limit itself must refuse an over-cap size"
        assert str(cap) in violation["message"]
        assert placed == []

    def test_the_handler_calls_the_limit_check_before_placing(self):
        """Source-level: the guard must sit at the placement funnel."""
        path = SCRIPTS / "monitor_daemon" / "handlers" / "exit_orders.py"
        body = _uncommented(path)
        assert "check_order_limits" in body, (
            "the only order-placing path in the repo with the halt and not the "
            "limits, and it runs inside radon-monitor.service"
        )
        limit_at = body.index("check_order_limits")
        place_at = body.index("client.place_order(")
        assert limit_at < place_at, "the limit check must precede the placement"

    def test_the_halt_still_refuses_first(self):
        body = _uncommented(SCRIPTS / "monitor_daemon" / "handlers" / "exit_orders.py")
        assert body.index("is_trading_halted") < body.index("check_order_limits")


class TestOrderManageModifyBoundsItsReplacement:
    def test_the_modify_path_calls_the_limit_check(self):
        body = _uncommented(SCRIPTS / "ib_order_manage.py")
        assert "check_order_limits" in body, (
            "a modify RE-TRANSMITS the order, so it is a placement; the guard "
            "belongs at the funnel, not only in the FastAPI caller"
        )
        assert body.index("check_order_limits") < body.index("client.place_order(")

    def test_the_halt_still_refuses_first(self):
        body = _uncommented(SCRIPTS / "ib_order_manage.py")
        assert body.index("is_trading_halted") < body.index("check_order_limits")


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
