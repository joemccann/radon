"""Built-in workflow node implementations.

Each node is a callable that takes ``(rows, params, run_ctx)`` and returns a
``NodeOutcome``. Rows are the row-set flowing through the pipeline (a list of
dicts, e.g. scanner results). ``params`` is the node's static config from the
graph; ``run_ctx`` carries execution-wide flags such as ``confirm_order``.

External effects are isolated behind MODULE-LEVEL seams so tests patch them and
nothing touches UW / IB / Pushover / Turso:

  - ``run_data_source(source, params)``   — wrap an existing scanner / UW fetcher
  - ``dispatch_notification(rows, channel)`` — route through the F6 dispatcher
  - ``emit_order(rows, params)``           — emit an order-emitting effect

The gate node calls into the SHARED ``gates`` contract; it never re-encodes the
Four Gates. The order node refuses to fire unless the run was explicitly
confirmed (``confirm_order=True``) — the analogue of ``OrderRiskGate`` being the
mandatory confirmation before any order-emitting node fires.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .expression import evaluate_expression
from .gates import convexity_gate, kelly_gate


@dataclass
class NodeOutcome:
    """Result of running one node.

    ``rows`` is the row-set passed downstream. ``blocked`` stops the graph;
    ``gate`` / ``requires_confirmation`` explain why for the report.
    """

    rows: list
    blocked: bool = False
    gate: Optional[str] = None
    requires_confirmation: bool = False
    info: dict = field(default_factory=dict)


# ── external-effect seams (patched in tests) ────────────────────────

def run_data_source(source: str, params: dict) -> list:
    """Run an existing scanner / UW fetcher named ``source`` and return its
    result rows. Kept thin so the dependency on the heavy scan modules is lazy
    and so tests patch this single function instead of the whole import graph.
    """
    if source == "scanner":
        import scanner as scanner_mod

        top_n = int(params.get("top_n", 20))
        min_score = float(params.get("min_score", 0))
        return _run_scanner(scanner_mod, top_n=top_n, min_score=min_score)

    if source == "discover":
        import discover as discover_mod  # noqa: F401 — lazy import seam

        raise NotImplementedError("discover data-source wiring is scaffolding")

    raise ValueError(f"unknown data source: {source!r}")


def _run_scanner(scanner_mod, *, top_n: int, min_score: float) -> list:
    """Adapter around ``scanner.scan`` that returns rows rather than printing.

    ``scanner.scan`` prints JSON to stdout; for the executor we want the rows
    in hand. We replicate only the watchlist read + per-ticker processing it
    already exposes, reusing ``_process_ticker`` so the signal math is the same.
    """
    import json
    from pathlib import Path

    watchlist_path = Path(scanner_mod.WATCHLIST)
    if not watchlist_path.exists():
        return []
    with open(watchlist_path) as handle:
        watchlist = json.load(handle)
    open_positions = scanner_mod.get_open_positions()
    items = [i for i in watchlist.get("tickers", []) if i["ticker"] not in open_positions]
    rows = []
    for item in items:
        result = scanner_mod._process_ticker(item)
        if result is not None:
            rows.append(result)
    rows.sort(key=lambda r: r.get("score", 0), reverse=True)
    return [r for r in rows if r.get("score", 0) >= min_score][:top_n]


def dispatch_notification(rows: list, channel: str) -> None:
    """Route ``rows`` through the EXISTING F6 alert dispatcher rather than any
    new transport code. One synthetic scan-tail call per row reuses
    ``alerts.run_alerts_for_results`` so user alert rules + throttle apply."""
    from alerts import run_alerts_for_results

    run_alerts_for_results(rows)


def run_order_placement(payload: dict) -> dict:
    """Place ONE order through the existing IB placement path.

    Thin seam over ``ib_place_order.place_order`` so the dependency on the heavy
    IB import stays lazy and tests patch this single function instead of the
    whole order-placement import graph. The payload mirrors the ``/orders/place``
    body shape (``symbol`` / ``action`` / ``quantity`` / ``limitPrice``)."""
    from ib_place_order import place_order as _place_order

    return _place_order(payload)


def place_order_via_bridge(row: dict, params: dict) -> dict:
    """Map ONE candidate row to an order payload and place it.

    Translates a scanner/flow row into the ``/orders/place`` body shape and hands
    it to ``run_order_placement``. A row that lacks the actionable fields
    (``action`` + ``quantity``) is rejected rather than placing a malformed
    order."""
    payload = _order_payload_from_row(row, params)
    return run_order_placement(payload)


def _order_payload_from_row(row: dict, params: dict) -> dict:
    symbol = row.get("ticker") or row.get("symbol")
    action = row.get("action")
    quantity = row.get("quantity")
    if not symbol or not action or not quantity:
        raise ValueError(
            "order row needs ticker/symbol, action, and quantity to place an order"
        )
    payload = {
        "type": params.get("type", "option"),
        "symbol": symbol,
        "action": action,
        "quantity": quantity,
        "tif": params.get("tif", "DAY"),
    }
    if row.get("limit_price") is not None:
        payload["limitPrice"] = row["limit_price"]
    if params.get("structure"):
        payload["structure"] = params["structure"]
    return payload


def emit_order(rows: list, params: dict) -> None:
    """Place an order for EACH row through the placement bridge.

    Only reached once the run is confirmed (``_order_node`` blocks otherwise),
    so this is the post-confirmation analogue of OrderRiskGate's Confirm: every
    row becomes a live order via ``place_order_via_bridge``."""
    for row in rows:
        place_order_via_bridge(row, params)


# ── node implementations ────────────────────────────────────────────

def _data_source_node(rows: list, params: dict, run_ctx: dict) -> NodeOutcome:
    source = params.get("source")
    if not source:
        raise ValueError("data-source node requires a 'source' param")
    produced = run_data_source(source, params)
    return NodeOutcome(rows=list(produced), info={"produced": len(produced)})


def _filter_node(rows: list, params: dict, run_ctx: dict) -> NodeOutcome:
    expression = params.get("expression")
    if not expression:
        raise ValueError("filter node requires an 'expression' param")
    kept = [row for row in rows if evaluate_expression(expression, row)]
    return NodeOutcome(rows=kept, info={"kept": len(kept), "dropped": len(rows) - len(kept)})


def _gate_node(rows: list, params: dict, run_ctx: dict) -> NodeOutcome:
    """Evaluate the configured gate against EACH row via the shared contract.
    The gate blocks the graph if ANY row fails the gate (defined-risk-only
    semantics: one non-convex / no-edge candidate stops the pipeline)."""
    gate_kind = params.get("gate")
    survivors = []
    for row in rows:
        result = _evaluate_row_gate(gate_kind, row, params)
        if not result.passed:
            return NodeOutcome(rows=[], blocked=True, gate=result.gate, info={"reason": result.reason})
        survivors.append(row)
    return NodeOutcome(rows=survivors, info={"gate": gate_kind})


def _evaluate_row_gate(gate_kind: str, row: dict, params: dict):
    if gate_kind == "convexity":
        return convexity_gate(
            max_gain=row.get(params.get("max_gain", "max_gain")),
            max_loss=row.get(params.get("max_loss", "max_loss")),
        )
    if gate_kind == "kelly":
        return kelly_gate(
            prob_win=row.get(params.get("prob_win", "prob_win")),
            odds=row.get(params.get("odds", "odds")),
        )
    raise ValueError(f"unknown gate kind: {gate_kind!r}")


def _notify_node(rows: list, params: dict, run_ctx: dict) -> NodeOutcome:
    channel = params.get("channel", "service_health")
    dispatch_notification(rows, channel)
    return NodeOutcome(rows=rows, info={"notified": len(rows), "channel": channel})


def _order_node(rows: list, params: dict, run_ctx: dict) -> NodeOutcome:
    """Order-emitting node. Mandatory confirmation: without an explicit
    ``confirm_order`` flag on the run the node blocks (the OrderRiskGate
    confirmation has not happened) and NO order is emitted."""
    if not run_ctx.get("confirm_order"):
        return NodeOutcome(
            rows=rows,
            blocked=True,
            requires_confirmation=True,
            info={"reason": "order requires OrderRiskGate confirmation"},
        )
    emit_order(rows, params)
    return NodeOutcome(rows=rows, info={"placed": len(rows)})


# Factory map. A factory returns the node-callable for a given type. Kept as a
# zero-arg-config indirection so custom nodes can be registered the same way.
def _factory(node_fn: Callable) -> Callable:
    def make() -> Callable:
        return node_fn

    return make


BUILTIN_FACTORIES = {
    "data-source": _factory(_data_source_node),
    "filter": _factory(_filter_node),
    "gate": _factory(_gate_node),
    "notify": _factory(_notify_node),
    "order": _factory(_order_node),
}
