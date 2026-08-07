"""Safety contract for the real-money POST /orders/place path.

Margin preview and persistence are deliberately separate from live placement:
an unsupported SMART-combo what-if must never delay or reject the order, and a
database write must never enter the post-transmit response path.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"
SERVER_PATH = SCRIPTS_DIR / "api" / "server.py"
NEXT_PLACE_ROUTE_PATH = ROOT / "web" / "app" / "api" / "orders" / "place" / "route.ts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def trusted_client(monkeypatch):
    from scripts.api import auth, server

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "test_mode", False)
    return TestClient(server.app), server


def _successful_place_result() -> SimpleNamespace:
    return SimpleNamespace(
        ok=True,
        error=None,
        data={
            "status": "ok",
            "orderId": 42,
            "permId": 420042,
            "initialStatus": "Submitted",
            "message": "BUY 1 AAPL @ $200.00 — Submitted",
        },
    )


_STOCK_ORDER = {
    "type": "stock",
    "symbol": "AAPL",
    "action": "BUY",
    "quantity": 1,
    "limitPrice": 200.0,
    "tif": "DAY",
}


def test_orders_place_never_calls_position_margin_writer(trusted_client, monkeypatch):
    client, server = trusted_client
    writer_call = MagicMock(side_effect=AssertionError("live placement touched margin persistence"))
    fake_writer = ModuleType("db.writer")
    fake_writer.upsert_position_entry_margin = writer_call  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "db.writer", fake_writer)
    monkeypatch.setitem(sys.modules, "scripts.db.writer", fake_writer)

    async def fake_recovery(*_args, **_kwargs):
        return _successful_place_result()

    monkeypatch.setattr(server, "_run_ib_script_with_recovery", fake_recovery)
    response = client.post("/orders/place", json=_STOCK_ORDER)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    writer_call.assert_not_called()


def test_orders_place_source_has_no_margin_persistence_or_db_writer_dependency():
    source = SERVER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(SERVER_PATH))
    route = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "orders_place"
    )

    def dotted_name(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            prefix = dotted_name(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        return ""

    dependencies: list[str] = []
    for node in ast.walk(route):
        if isinstance(node, ast.Import):
            dependencies.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            dependencies.append(node.module or "")
            dependencies.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.Call):
            dependencies.append(dotted_name(node.func))

    forbidden = ("db.writer", "position_entry_margin", "entry_margin", "persist")
    violations = [
        dependency
        for dependency in dependencies
        if any(token in dependency for token in forbidden)
    ]
    assert violations == []


def test_orders_place_subprocess_timeout_fits_inside_next_budget(trusted_client, monkeypatch):
    client, server = trusted_client
    observed: dict[str, object] = {}

    async def fake_recovery(script, args, timeout):
        observed.update(script=script, args=args, timeout=timeout)
        return _successful_place_result()

    monkeypatch.setattr(server, "_run_ib_script_with_recovery", fake_recovery)
    response = client.post("/orders/place", json=_STOCK_ORDER)
    assert response.status_code == 200
    assert observed["script"] == "ib_place_order.py"
    assert "--whatif" not in observed["args"]

    next_source = NEXT_PLACE_ROUTE_PATH.read_text(encoding="utf-8")
    match = re.search(
        r'radonFetch<Record<string, unknown>>\("/orders/place".*?timeout:\s*([\d_]+)',
        next_source,
        flags=re.DOTALL,
    )
    assert match is not None, "Next /orders/place timeout contract not found"
    next_timeout_ms = int(match.group(1).replace("_", ""))
    assert next_timeout_ms == 30_000
    assert float(observed["timeout"]) * 1_000 < next_timeout_ms
