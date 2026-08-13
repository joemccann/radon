#!/usr/bin/env python3
"""Kelly criterion calculator."""
import argparse
import json
import math

import numpy as np

def kelly(prob_win: float, odds: float, fraction: float = 0.25) -> dict:
    """Calculate fractional Kelly bet size."""
    if not math.isfinite(prob_win) or not 0 <= prob_win <= 1:
        raise ValueError("prob_win must be between 0 and 1")
    if not math.isfinite(odds):
        raise ValueError("odds must be finite")
    if not math.isfinite(fraction) or not 0 < fraction <= 1:
        raise ValueError("fraction must be greater than 0 and at most 1")
    # Guard against invalid inputs that would cause division by zero or nonsensical results
    if odds <= 0:
        return {
            "full_kelly_pct": 0.0,
            "fractional_kelly_pct": 0.0,
            "fraction_used": fraction,
            "edge_exists": False,
            "recommendation": "DO NOT BET"
        }
    
    q = 1 - prob_win
    full_kelly = prob_win - (q / odds)
    frac_kelly = full_kelly * fraction
    return {
        "full_kelly_pct": round(full_kelly * 100, 2),
        "fractional_kelly_pct": round(frac_kelly * 100, 2),
        "fraction_used": fraction,
        "edge_exists": full_kelly > 0,
        "recommendation": (
            "DO NOT BET" if full_kelly <= 0
            else "STRONG" if full_kelly > 0.10
            else "MARGINAL" if full_kelly > 0.025
            else "WEAK"
        )
    }

def kelly_size_batch(
    prob_wins: np.ndarray,
    odds: np.ndarray,
    bankroll: float,
    fraction: float = 0.25,
    max_pct: float = 0.025,
) -> np.ndarray:
    """Vectorized Kelly sizing for N candidates simultaneously.

    Returns an array of dollar position sizes, one per candidate.
    Guards: odds <= 0 → 0, full_kelly <= 0 → 0, hard cap at bankroll * max_pct,
    non-finite or non-positive bankroll → treated as 0 (never a NaN or negative size).
    """
    if len(prob_wins) == 0:
        return np.array([])

    if not np.isfinite(bankroll) or bankroll <= 0:
        bankroll = 0.0

    prob_wins = np.asarray(prob_wins, dtype=np.float64)
    odds = np.asarray(odds, dtype=np.float64)

    q = 1.0 - prob_wins

    # full_kelly = prob_win - q / odds, but guard odds <= 0
    with np.errstate(divide="ignore", invalid="ignore"):
        full_kelly = np.where(odds > 0, prob_wins - q / odds, 0.0)

    # No edge → 0
    full_kelly = np.where(full_kelly > 0, full_kelly, 0.0)

    frac_kelly = full_kelly * fraction
    # Round to 2 decimal places (as percentage) to match scalar kelly() behavior
    frac_kelly_pct = np.round(frac_kelly * 100.0, 2)
    dollar_size = bankroll * frac_kelly_pct / 100.0

    # Hard cap
    cap = bankroll * max_pct
    dollar_size = np.minimum(dollar_size, cap)

    return dollar_size


def _bounded_float(
    name: str,
    *,
    minimum: float,
    maximum: float,
    exclusive_minimum: bool = False,
):
    def parse(raw: str) -> float:
        try:
            value = float(raw)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{name} must be numeric") from exc
        lower_invalid = value <= minimum if exclusive_minimum else value < minimum
        if not math.isfinite(value) or lower_invalid or value > maximum:
            lower = "greater than" if exclusive_minimum else "at least"
            raise argparse.ArgumentTypeError(
                f"{name} must be {lower} {minimum} and at most {maximum}"
            )
        return value

    return parse


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--prob", type=_bounded_float("prob", minimum=0, maximum=1), required=True, help="Probability of win (0-1)")
    p.add_argument("--odds", type=_bounded_float("odds", minimum=0, maximum=1_000, exclusive_minimum=True), required=True, help="Win/loss odds ratio")
    p.add_argument("--fraction", type=_bounded_float("fraction", minimum=0, maximum=1, exclusive_minimum=True), default=0.25, help="Kelly fraction (default 0.25)")
    p.add_argument("--bankroll", type=_bounded_float("bankroll", minimum=0, maximum=1_000_000_000_000), default=None, help="Current bankroll for dollar sizing")
    args = p.parse_args()

    result = kelly(args.prob, args.odds, args.fraction)
    if args.bankroll is not None:
        result["dollar_size"] = round(args.bankroll * result["fractional_kelly_pct"] / 100, 2)
        result["max_per_position"] = round(args.bankroll * 0.025, 2)
        result["use_size"] = min(result["dollar_size"], result["max_per_position"])
    print(json.dumps(result, indent=2))
