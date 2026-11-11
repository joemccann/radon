"""REL-186 (R-479): the placement tripwire is per FUNCTION, not per module.

R-479: `place_bracket_order` placed three live orders with neither
`is_trading_halted()` nor a limits check, and the REL-145 tripwire could not
see it because `"check_order_limits" in body` is module-granular. The method
had no caller and was deleted; this tripwire keeps any new placement funnel
honest.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


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
