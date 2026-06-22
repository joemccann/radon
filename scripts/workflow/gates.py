"""Shared Four-Gates contract for workflow gate nodes.

This is the ONE place a Python flow-pipeline gate node evaluates a Radon gate.
It deliberately does NOT re-implement the gate math that already exists:

  - Gate 1 (Convexity): the documented rule is "Gain >= 2x loss, defined-risk
    only" (project CLAUDE.md, Four Gates table). Encoded once here.
  - Gate 3 (Risk / Kelly): delegates to the existing ``kelly.kelly`` so there
    is exactly one Kelly implementation in the Python tree. The fractional
    Kelly cap (2.5% bankroll) lives in ``kelly`` / ``portfolio_risk`` and is
    not duplicated.

The web order surfaces enforce the same gates through ``web/lib/order/risk``
(``OrderRiskGate`` + ``useOrderRisk``). The workflow order node treats that gate
as the MANDATORY confirmation seam (see ``nodes.emit_order``) rather than
re-deriving order risk here — keeping a single conceptual contract across the
Python scheduler and the TypeScript order entry surfaces.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from kelly import kelly

# Gate 1 — convexity floor. Gain must be at least this multiple of max loss.
CONVEXITY_MIN_RATIO = 2.0


@dataclass(frozen=True)
class GateResult:
    """Outcome of evaluating a single gate.

    ``gate`` is the canonical gate name ("convexity" | "risk") so the executor
    can name the failing gate exactly as the Four-Gates rule demands ("Any gate
    fails → stop. Name the gate.").
    """

    gate: str
    passed: bool
    reason: str
    detail: Optional[dict] = None


def convexity_gate(max_gain: float, max_loss: float) -> GateResult:
    """Gate 1. Passes when ``max_gain >= CONVEXITY_MIN_RATIO * max_loss`` and
    the loss is defined (finite, > 0). An undefined / unbounded loss always
    fails — defined-risk only."""
    try:
        gain = float(max_gain)
        loss = float(max_loss)
    except (TypeError, ValueError):
        return GateResult("convexity", False, "max_gain / max_loss not numeric")

    if loss <= 0:
        return GateResult("convexity", False, "max_loss must be a defined positive risk")
    ratio = gain / loss
    passed = ratio >= CONVEXITY_MIN_RATIO
    reason = (
        f"convex: gain/loss = {ratio:.2f}x >= {CONVEXITY_MIN_RATIO:g}x"
        if passed
        else f"insufficient convexity: gain/loss = {ratio:.2f}x < {CONVEXITY_MIN_RATIO:g}x"
    )
    return GateResult("convexity", passed, reason, {"ratio": ratio})


def kelly_gate(prob_win: float, odds: float, fraction: float = 0.25) -> GateResult:
    """Gate 3. Delegates to ``kelly.kelly`` and passes only when a positive
    edge exists (``edge_exists``). The sizing cap is enforced downstream by the
    existing portfolio risk layer — this gate is the go/no-go decision."""
    try:
        result = kelly(float(prob_win), float(odds), fraction)
    except (TypeError, ValueError):
        return GateResult("risk", False, "prob_win / odds not numeric")

    passed = bool(result.get("edge_exists"))
    reason = (
        f"edge: full Kelly {result.get('full_kelly_pct')}% ({result.get('recommendation')})"
        if passed
        else f"no edge: {result.get('recommendation')}"
    )
    return GateResult("risk", passed, reason, result)
