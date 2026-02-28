#!/usr/bin/env python3
"""Kelly criterion calculator."""
import argparse, json

def kelly(prob_win: float, odds: float, fraction: float = 0.25) -> dict:
    """Calculate fractional Kelly bet size."""
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

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--prob", type=float, required=True, help="Probability of win (0-1)")
    p.add_argument("--odds", type=float, required=True, help="Win/loss odds ratio")
    p.add_argument("--fraction", type=float, default=0.25, help="Kelly fraction (default 0.25)")
    p.add_argument("--bankroll", type=float, default=None, help="Current bankroll for dollar sizing")
    args = p.parse_args()

    result = kelly(args.prob, args.odds, args.fraction)
    if args.bankroll:
        result["dollar_size"] = round(args.bankroll * result["fractional_kelly_pct"] / 100, 2)
        result["max_per_position"] = round(args.bankroll * 0.025, 2)
        result["use_size"] = min(result["dollar_size"], result["max_per_position"])
    print(json.dumps(result, indent=2))
