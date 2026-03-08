"""Test that cri_scan.py uses a unique IB client ID (not 0).

The CRI scanner was connecting with clientId=0, which collides with TWS
and other IB connections.  It must use a dedicated client ID (50).
"""
import ast
from pathlib import Path


CRI_SCRIPT = Path(__file__).resolve().parent.parent / "cri_scan.py"
EXPECTED_CLIENT_ID = 50


def _extract_client_ids_from_fetch_ib() -> list[int]:
    """Parse cri_scan.py's AST and extract every clientId=<int> keyword
    argument inside the _fetch_ib function's ib.connect() calls."""
    source = CRI_SCRIPT.read_text()
    tree = ast.parse(source)

    client_ids: list[int] = []

    for node in ast.walk(tree):
        # Find the _fetch_ib function definition
        if isinstance(node, ast.FunctionDef) and node.name == "_fetch_ib":
            for child in ast.walk(node):
                # Look for ib.connect(...) calls
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "connect"
                ):
                    for kw in child.keywords:
                        if kw.arg == "clientId" and isinstance(kw.value, ast.Constant):
                            client_ids.append(kw.value.value)

    return client_ids


class TestCRIClientId:
    """Verify _fetch_ib() uses a unique, non-zero client ID."""

    def test_client_id_is_not_zero(self):
        """clientId must not be 0 — that collides with TWS / other scripts."""
        ids = _extract_client_ids_from_fetch_ib()
        assert ids, "No ib.connect() calls found in _fetch_ib()"
        for cid in ids:
            assert cid != 0, (
                f"_fetch_ib() still uses clientId=0 — must use a unique ID"
            )

    def test_client_id_is_expected_value(self):
        """Both ib.connect() calls in _fetch_ib() must use clientId={EXPECTED_CLIENT_ID}."""
        ids = _extract_client_ids_from_fetch_ib()
        assert len(ids) == 2, (
            f"Expected 2 ib.connect() calls in _fetch_ib(), found {len(ids)}"
        )
        for cid in ids:
            assert cid == EXPECTED_CLIENT_ID, (
                f"Expected clientId={EXPECTED_CLIENT_ID}, got clientId={cid}"
            )

    def test_client_id_does_not_collide_with_other_scripts(self):
        """The chosen client ID must not collide with known IB script IDs."""
        known_ids = {
            0,    # TWS default / ib_client.py default
            18,   # evaluate.py
            26,   # ib_place_order.py
            100,  # ib_realtime_server.js
            200,  # test_ib_realtime.py
        }
        ids = _extract_client_ids_from_fetch_ib()
        for cid in ids:
            assert cid not in known_ids, (
                f"clientId={cid} collides with another IB script"
            )
