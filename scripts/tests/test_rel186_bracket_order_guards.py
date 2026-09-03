"""REL-186 (R-479): `place_bracket_order` goes through the same guards as
`place_order`, and the placement tripwire is per FUNCTION, not per module.

R-479: the method placed three live orders with neither `is_trading_halted()`
nor a limits check, and the REL-145 tripwire could not see it because
`"check_order_limits" in body` is module-granular — `ib_client.py` satisfied
it through `place_order` while `place_bracket_order` sat unguarded in the
same file. No production caller exists today; this closes the latent funnel.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _uncommented(path: Path) -> str:
    return "\n".join(
        line for line in path.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


def _client(placed):
    from clients.ib_client import IBClient

    client = IBClient.__new__(IBClient)
    client._require_connection = lambda: None

    def _bracket(action, qty, lmt, tp, sl):
        mk = lambda otype, px: SimpleNamespace(
            action=action, totalQuantity=qty, lmtPrice=px, orderType=otype
        )
        return [mk("LMT", lmt), mk("LMT", tp), mk("STP", sl)]

    client._ib = SimpleNamespace(
        bracketOrder=_bracket,
        placeOrder=lambda c, o: placed.append((c, o))
        or SimpleNamespace(order=SimpleNamespace(orderId=1)),
    )
    client.logger = SimpleNamespace(info=lambda *a, **k: None)
    return client


CONTRACT = SimpleNamespace(secType="STK", symbol="SLV", localSymbol="SLV")


class TestBracketOrderGuards:
    def test_halt_refuses_before_any_leg_reaches_the_socket(self, monkeypatch):
        import trading_halt
        from clients.ib_client import IBOrderError

        monkeypatch.setattr(trading_halt, "is_trading_halted", lambda: True)
        monkeypatch.setattr(
            trading_halt, "get_halt_state", lambda: {"halted": True, "reason": "drill"}
        )
        placed: list = []
        with pytest.raises(IBOrderError, match="HALTED|halted"):
            _client(placed).place_bracket_order(CONTRACT, "BUY", 10, 100.0, 110.0, 95.0)
        assert placed == []

    def test_over_cap_quantity_places_no_leg(self, monkeypatch):
        import order_limits
        import trading_halt
        from clients.ib_client import IBOrderError

        monkeypatch.setattr(trading_halt, "is_trading_halted", lambda: False)
        placed: list = []
        qty = order_limits.max_stock_order_qty() + 1
        with pytest.raises(IBOrderError):
            _client(placed).place_bracket_order(CONTRACT, "BUY", qty, 100.0, 110.0, 95.0)
        assert placed == []

    def test_a_within_limits_bracket_places_all_three_legs(self, monkeypatch):
        import trading_halt

        monkeypatch.setattr(trading_halt, "is_trading_halted", lambda: False)
        placed: list = []
        trades = _client(placed).place_bracket_order(
            CONTRACT, "BUY", 10, 100.0, 110.0, 95.0
        )
        assert len(placed) == 3
        assert len(trades) == 3


class TestTripwireIsPerFunction:
    """Every FUNCTION that touches `.placeOrder(` must itself reference the
    limits (or route through `place_order`, which carries them)."""

    GUARDS = re.compile(
        r"check_order_limits|check_quantity_limit|self\.place_order\("
    )

    def _functions_touching_placement(self):
        for path in sorted(SCRIPTS.rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            if "/tests/" in rel or rel.startswith("scripts/tests/"):
                continue
            src = path.read_text()
            if ".placeOrder(" not in src:
                continue
            tree = ast.parse(src)
            lines = src.splitlines()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    body = "\n".join(
                        line
                        for line in lines[node.lineno - 1 : node.end_lineno]
                        if not line.lstrip().startswith("#")
                    )
                    if ".placeOrder(" in body:
                        yield rel, node.name, body

    def test_every_placement_function_references_the_guards(self):
        offenders = [
            f"{rel}:{name}"
            for rel, name, body in self._functions_touching_placement()
            if not self.GUARDS.search(body)
        ]
        assert not offenders, (
            "these FUNCTIONS place a live order without referencing the "
            f"server-side limits or routing through place_order: {offenders}"
        )

    def test_the_tripwire_sees_the_known_functions(self):
        names = {name for _rel, name, _b in self._functions_touching_placement()}
        assert "place_order" in names
        assert "modify_order" in names

    def test_bracket_legs_route_through_place_order(self):
        src = _uncommented(SCRIPTS / "clients" / "ib_client.py")
        body = src[src.index("def place_bracket_order"):]
        body = body[: body.index("def cancel_order")]
        assert "self.place_order(" in body
        assert ".placeOrder(" not in body, (
            "place_bracket_order touches the raw socket instead of routing "
            "each leg through the guarded place_order funnel"
        )
