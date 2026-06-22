#!/usr/bin/env python3
"""FU6 (B) — paper shadow-placement subprocess.

Runs the pure paper matcher + fills core against a single market observation and
persists a simulated fill via ``scripts/paper/store.py`` into ``paper_fills``.

Invoked by the FastAPI ``POST /paper/place`` route through ``run_script`` so the
synchronous libsql write in ``store.record_paper_fill`` runs in its OWN process
(its own GIL) and never blocks the uvicorn event loop — the same discipline the
scan-mirror writers follow (see ``scripts/api/CLAUDE.md`` §Event-Loop
Discipline).

stdout is reserved for the result JSON; everything else goes to stderr.

Order spec via ``--spec '<json>'`` (``run_script`` passes CLI args, not stdin):
  ticker, side (BUY|SELL), order_type (LIMIT|STOP), quantity,
  limit_price?, stop_price?, bid?, ask?, last?, fill_id?, account?

Result on stdout (JSON):
  { status: "filled" | "working", ticker, side, quantity,
    fill_price?, fill_id? }
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from paper import (  # noqa: E402
    FillContext,
    MarketTick,
    RestingOrder,
    compute_fill_price,
    match_order,
)
from paper.store import PaperFill, record_paper_fill  # noqa: E402


def _tick_from_spec(spec: dict) -> MarketTick:
    return MarketTick(
        last=spec.get("last"),
        bid=spec.get("bid"),
        ask=spec.get("ask"),
    )


def _order_from_spec(spec: dict) -> RestingOrder:
    return RestingOrder(
        side=spec["side"],
        order_type=spec["order_type"],
        limit_price=spec.get("limit_price"),
        stop_price=spec.get("stop_price"),
        triggered=bool(spec.get("triggered", False)),
    )


def _fill_reference(spec: dict, side: str) -> float | None:
    """Crossing reference the matcher used: marketable side of the quote, or
    the limit/stop price as a last resort for the fills engine's base price."""
    tick = _tick_from_spec(spec)
    if side == "BUY":
        ref = tick.buy_reference()
    else:
        ref = tick.sell_reference()
    if ref is not None:
        return ref
    return spec.get("limit_price") or spec.get("stop_price")


def place_paper_order(spec: dict) -> dict[str, Any]:
    """Match one order against the supplied quote; persist + report a fill.

    Returns ``status: "working"`` (no persistence) when the order is not
    marketable against the quote, ``status: "filled"`` (one persisted row)
    otherwise."""
    order = _order_from_spec(spec)
    tick = _tick_from_spec(spec)
    decision = match_order(order, tick)

    if not decision.filled:
        return {
            "status": "working",
            "ticker": spec["ticker"],
            "side": spec["side"],
            "quantity": spec["quantity"],
        }

    fill_price = compute_fill_price(
        FillContext(
            side=spec["side"],
            bid=spec.get("bid"),
            ask=spec.get("ask"),
            reference_price=_fill_reference(spec, spec["side"]),
        )
    )

    fill_id = spec.get("fill_id") or uuid.uuid4().hex
    fill = PaperFill(
        fill_id=fill_id,
        ticker=spec["ticker"],
        side=spec["side"],
        order_type=spec["order_type"],
        quantity=float(spec["quantity"]),
        fill_price=fill_price,
        payload=spec,
        account=spec.get("account", "PAPER"),
    )
    record_paper_fill(fill)

    return {
        "status": "filled",
        "ticker": spec["ticker"],
        "side": spec["side"],
        "quantity": spec["quantity"],
        "fill_price": fill_price,
        "fill_id": fill_id,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Paper shadow-placement engine")
    parser.add_argument("--spec", required=True, help="order spec as a JSON string")
    parsed = parser.parse_args()
    try:
        spec = json.loads(parsed.spec)
    except json.JSONDecodeError as exc:
        print(json.dumps({"status": "error", "message": f"bad --spec JSON: {exc}"}))
        return 1
    try:
        result = place_paper_order(spec)
    except (KeyError, ValueError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
