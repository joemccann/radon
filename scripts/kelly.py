#!/usr/bin/env python3
"""Kelly criterion calculator.

f* = p - q/b is algebraically identical to (b*p - q)/b. This module is the
only calculator; TypeBox and the CLI consume its output verbatim.

Kelly allocates a given edge. It does not create one. Trade-level edge is
the M4 gate in evaluate.py; edge_exists here is only the math fact f* > 0.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from typing import Any, Iterable, Optional

import numpy as np

KELLY_MAX_FRACTION = 0.5
KELLY_MIN_FRACTION = 0.05
KELLY_MAX_PCT = 0.025
KELLY_RESTRUCTURE_PCT = 0.20
KELLY_DEFAULT_FRACTION = 0.5
KELLY_P_HAIRCUT_MAX = 0.25
KELLY_MAX_DEPLOYED_DEFAULT = 0.20
KELLY_DRAWDOWN_HALT_DEFAULT = 0.15

_P_SOURCES = frozenset({"estimated", "measured"})
_FRACTION_SOURCES = frozenset({"default", "explicit", "env", "env_clamped"})


def _clamp(value: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, value))


def _env_float(name: str) -> Optional[float]:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def kelly_config() -> dict:
    """Read RADON_KELLY_* from the process environment at call time."""
    env_fraction = _env_float("RADON_KELLY_FRACTION")
    if env_fraction is None:
        fraction = KELLY_DEFAULT_FRACTION
        fraction_source = "default"
    elif KELLY_MIN_FRACTION <= env_fraction <= KELLY_MAX_FRACTION:
        fraction = env_fraction
        fraction_source = "env"
    else:
        fraction = _clamp(env_fraction, KELLY_MIN_FRACTION, KELLY_MAX_FRACTION)
        fraction_source = "env_clamped"

    env_haircut = _env_float("RADON_KELLY_P_HAIRCUT")
    if env_haircut is None:
        p_haircut = 0.0
    else:
        p_haircut = _clamp(env_haircut, 0.0, KELLY_P_HAIRCUT_MAX)

    env_deployed = _env_float("RADON_KELLY_MAX_DEPLOYED_PCT")
    if env_deployed is None:
        max_deployed_pct = KELLY_MAX_DEPLOYED_DEFAULT
    else:
        max_deployed_pct = _clamp(env_deployed, 0.05, 0.50)

    env_dd = _env_float("RADON_KELLY_DRAWDOWN_HALT_PCT")
    if env_dd is None:
        drawdown_halt_pct = KELLY_DRAWDOWN_HALT_DEFAULT
    else:
        drawdown_halt_pct = _clamp(env_dd, 0.05, 0.50)

    enforce_raw = os.environ.get("RADON_KELLY_ENFORCE_ORDERS", "0")
    enforce_orders = enforce_raw.strip() == "1"

    return {
        "fraction": fraction,
        "fraction_source": fraction_source,
        "p_haircut": p_haircut,
        "max_deployed_pct": max_deployed_pct,
        "drawdown_halt_pct": drawdown_halt_pct,
        "enforce_orders": enforce_orders,
    }


def _validate_explicit_fraction(fraction: float, p_source: str, cfg: dict) -> None:
    if not math.isfinite(fraction) or not (KELLY_MIN_FRACTION <= fraction <= KELLY_MAX_FRACTION):
        raise ValueError(
            f"fraction must be in [{KELLY_MIN_FRACTION}, {KELLY_MAX_FRACTION}]; "
            f"full Kelly is banned"
        )
    if p_source == "estimated" and fraction > cfg["fraction"]:
        raise ValueError(
            f"estimated p may not use fraction {fraction} above configured "
            f"{cfg['fraction']}"
        )


def _resolve_fraction(fraction: Optional[float], p_source: str) -> tuple[float, str, dict]:
    cfg = kelly_config()
    if fraction is None:
        return cfg["fraction"], cfg["fraction_source"], cfg
    _validate_explicit_fraction(fraction, p_source, cfg)
    return fraction, "explicit", cfg


def _resolve_haircut(p_haircut: Optional[float], cfg: dict) -> float:
    if p_haircut is None:
        return cfg["p_haircut"]
    if not math.isfinite(p_haircut) or not (0.0 <= p_haircut <= KELLY_P_HAIRCUT_MAX):
        raise ValueError(
            f"p_haircut must be in [0.0, {KELLY_P_HAIRCUT_MAX}]"
        )
    return p_haircut


def _growth_rate(p_eff: float, odds: float, f: float) -> Optional[float]:
    if not (0.0 <= f < 1.0) or odds <= 0:
        return None
    q = 1.0 - p_eff
    inner_win = 1.0 + f * odds
    inner_loss = 1.0 - f
    if inner_win <= 0 or inner_loss <= 0:
        return None
    return p_eff * math.log(inner_win) + q * math.log(inner_loss)


def _recommendation(full_kelly: float) -> str:
    if full_kelly <= 0:
        return "DO NOT BET"
    if full_kelly > 0.10:
        return "STRONG"
    if full_kelly > KELLY_MAX_PCT:
        return "MARGINAL"
    return "WEAK"


def _base_result(
    *,
    full_kelly: float,
    frac_kelly: float,
    fraction: float,
    p_input: float,
    p_haircut: float,
    p_effective: float,
    p_source: str,
    fraction_source: str,
    growth_rate_full: Optional[float],
    growth_rate_used: Optional[float],
    restructure: bool,
    edge_exists: bool,
    recommendation: str,
) -> dict:
    return {
        "full_kelly_pct": round(full_kelly * 100, 2),
        "fractional_kelly_pct": round(frac_kelly * 100, 2),
        "fraction_used": fraction,
        "edge_exists": edge_exists,
        "recommendation": recommendation,
        "p_input": p_input,
        "p_haircut": p_haircut,
        "p_effective": p_effective,
        "p_source": p_source,
        "fraction_source": fraction_source,
        "growth_rate_full": growth_rate_full,
        "growth_rate_used": growth_rate_used,
        "restructure": restructure,
    }


def _apply_bankroll(result: dict, bankroll: float, frac_kelly: float) -> None:
    if not math.isfinite(bankroll) or bankroll <= 0:
        bankroll = 0.0
    dollar_size = round(bankroll * result["fractional_kelly_pct"] / 100, 2)
    if not result["edge_exists"] or dollar_size < 0:
        dollar_size = 0.0
    max_per_position = round(bankroll * KELLY_MAX_PCT, 2)
    use_size = min(dollar_size, max_per_position)
    result["dollar_size"] = dollar_size
    result["max_per_position"] = max_per_position
    result["use_size"] = use_size
    result["capped"] = dollar_size > max_per_position


def kelly(
    prob_win: float,
    odds: float,
    fraction: Optional[float] = None,
    *,
    bankroll: Optional[float] = None,
    p_haircut: Optional[float] = None,
    p_source: str = "estimated",
) -> dict:
    """Calculate fractional Kelly bet size.

    f* = p_effective - q/odds, identical to (odds*p_effective - q)/odds.
    Explicit fraction outside [KELLY_MIN_FRACTION, KELLY_MAX_FRACTION] raises.
    Full Kelly is rejected, never silent-clamped.
    """
    if p_source not in _P_SOURCES:
        raise ValueError("p_source must be 'estimated' or 'measured'")
    if not math.isfinite(prob_win) or not 0 <= prob_win <= 1:
        raise ValueError("prob_win must be between 0 and 1")
    if not math.isfinite(odds):
        raise ValueError("odds must be finite")

    used_fraction, fraction_source, cfg = _resolve_fraction(fraction, p_source)
    used_haircut = _resolve_haircut(p_haircut, cfg)
    p_effective = max(0.0, prob_win - used_haircut)

    if odds <= 0:
        result = _base_result(
            full_kelly=0.0,
            frac_kelly=0.0,
            fraction=used_fraction,
            p_input=prob_win,
            p_haircut=used_haircut,
            p_effective=p_effective,
            p_source=p_source,
            fraction_source=fraction_source,
            growth_rate_full=0.0,
            growth_rate_used=0.0,
            restructure=False,
            edge_exists=False,
            recommendation="DO NOT BET",
        )
        if bankroll is not None:
            _apply_bankroll(result, bankroll, 0.0)
        return result

    q = 1.0 - p_effective
    full_kelly = p_effective - (q / odds)
    frac_kelly = full_kelly * used_fraction
    edge = full_kelly > 0
    restructure = full_kelly > KELLY_RESTRUCTURE_PCT

    if math.isclose(p_effective, 1.0) and full_kelly >= 1.0:
        growth_full: Optional[float] = None
    elif edge:
        growth_full = _growth_rate(p_effective, odds, full_kelly)
    else:
        growth_full = 0.0

    if edge:
        growth_used = _growth_rate(p_effective, odds, frac_kelly)
    else:
        growth_used = 0.0

    result = _base_result(
        full_kelly=full_kelly,
        frac_kelly=frac_kelly,
        fraction=used_fraction,
        p_input=prob_win,
        p_haircut=used_haircut,
        p_effective=p_effective,
        p_source=p_source,
        fraction_source=fraction_source,
        growth_rate_full=growth_full,
        growth_rate_used=growth_used,
        restructure=restructure,
        edge_exists=edge,
        recommendation=_recommendation(full_kelly),
    )
    if bankroll is not None:
        _apply_bankroll(result, bankroll, frac_kelly)
    return result


def kelly_size_batch(
    prob_wins: np.ndarray,
    odds: np.ndarray,
    bankroll: float,
    fraction: Optional[float] = None,
    max_pct: float = KELLY_MAX_PCT,
) -> np.ndarray:
    """Vectorized Kelly sizing for N candidates simultaneously.

    Returns an array of dollar position sizes, one per candidate.
    Guards: odds <= 0 → 0, full_kelly <= 0 → 0, hard cap at bankroll * max_pct,
    non-finite or non-positive bankroll → treated as 0 (never a NaN or negative size).
    Out-of-domain prob_wins produce 0 size. Invalid fraction raises ValueError.
    max_pct stays a raw-math parameter so mutation-kill tests that pass 1.0 survive.
    """
    used_fraction, _, _ = _resolve_fraction(fraction, "measured")

    if len(prob_wins) == 0:
        return np.array([])

    if not np.isfinite(bankroll) or bankroll <= 0:
        bankroll = 0.0

    prob_wins = np.asarray(prob_wins, dtype=np.float64)
    odds_arr = np.asarray(odds, dtype=np.float64)

    invalid_p = ~np.isfinite(prob_wins) | (prob_wins < 0.0) | (prob_wins > 1.0)

    q = 1.0 - prob_wins

    with np.errstate(divide="ignore", invalid="ignore"):
        full_kelly = np.where(odds_arr > 0, prob_wins - q / odds_arr, 0.0)

    full_kelly = np.where(full_kelly > 0, full_kelly, 0.0)
    full_kelly = np.where(invalid_p, 0.0, full_kelly)

    frac_kelly = full_kelly * used_fraction
    frac_kelly_pct = np.round(frac_kelly * 100.0, 2)
    dollar_size = bankroll * frac_kelly_pct / 100.0

    cap = bankroll * max_pct
    dollar_size = np.minimum(dollar_size, cap)
    dollar_size = np.where(invalid_p, 0.0, dollar_size)
    return dollar_size


def portfolio_capacity(
    bankroll: float,
    open_max_losses: Iterable[float] = (),
    proposed_max_loss: float = 0.0,
    max_deployed_pct: Optional[float] = None,
) -> dict:
    """Refuse when open + proposed worst-case losses exceed the deployed cap."""
    cfg = kelly_config()
    if max_deployed_pct is None:
        max_deployed_pct = cfg["max_deployed_pct"]
    if not math.isfinite(bankroll) or bankroll <= 0:
        return {"ok": False, "deployed_pct": 0.0, "remaining": 0.0, "reason": "CAPACITY"}

    open_total = 0.0
    for loss in open_max_losses:
        try:
            value = float(loss)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            open_total += value

    proposed = proposed_max_loss if math.isfinite(proposed_max_loss) and proposed_max_loss > 0 else 0.0
    deployed = open_total + proposed
    cap = bankroll * max_deployed_pct
    remaining = max(0.0, cap - open_total)
    deployed_pct = deployed / bankroll
    ok = deployed <= cap + 1e-9
    return {
        "ok": ok,
        "deployed_pct": deployed_pct,
        "remaining": remaining,
        "reason": None if ok else "CAPACITY",
    }


def _ticket_refusal(reason: str, sized: Optional[dict] = None) -> dict:
    out = dict(sized) if sized else {}
    out.update({
        "contracts": 0,
        "total_cost": 0.0,
        "max_loss_total": 0.0,
        "position_pct": 0.0,
        "reason": reason,
    })
    return out


def kelly_ticket(
    *,
    prob_win: float,
    max_gain: float,
    max_loss: float,
    bankroll: float,
    fraction: Optional[float] = None,
    open_max_losses: Iterable[float] = (),
    nav_peak: Optional[float] = None,
    nav_now: Optional[float] = None,
    p_haircut: Optional[float] = None,
    p_source: str = "estimated",
    **kw: Any,
) -> dict:
    """Size a defined-risk ticket. Refusals return contracts 0 and a reason."""
    if not math.isfinite(max_loss) or max_loss <= 0:
        return _ticket_refusal("UNDEFINED_RISK")

    cfg = kelly_config()
    if (
        nav_peak is not None
        and nav_now is not None
        and math.isfinite(nav_peak)
        and math.isfinite(nav_now)
        and nav_peak > 0
    ):
        drawdown = (nav_peak - nav_now) / nav_peak
        if drawdown >= cfg["drawdown_halt_pct"]:
            return _ticket_refusal("DRAWDOWN_HALT")

    odds = max_gain / max_loss if math.isfinite(max_gain) else 0.0
    sized = kelly(
        prob_win,
        odds,
        fraction,
        bankroll=bankroll,
        p_haircut=p_haircut,
        p_source=p_source,
    )
    if not sized["edge_exists"]:
        return _ticket_refusal("NO_EDGE", sized)
    if sized.get("restructure"):
        return _ticket_refusal("RESTRUCTURE", sized)

    use_size = float(sized.get("use_size") or 0.0)
    contracts = int(math.floor(use_size / max_loss)) if max_loss > 0 else 0
    if contracts < 1:
        return _ticket_refusal("CAP_BELOW_ONE_CONTRACT", sized)

    max_loss_total = contracts * max_loss
    capacity = portfolio_capacity(bankroll, open_max_losses, max_loss_total)
    if not capacity["ok"]:
        return _ticket_refusal("CAPACITY", sized)

    position_pct = round(max_loss_total / bankroll * 100, 2) if bankroll > 0 else 0.0
    out = dict(sized)
    out.update({
        "contracts": contracts,
        "total_cost": round(max_loss_total, 2),
        "max_loss_total": round(max_loss_total, 2),
        "position_pct": position_pct,
        "reason": None,
    })
    return out


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
    p.add_argument(
        "--fraction",
        type=_bounded_float("fraction", minimum=0, maximum=KELLY_MAX_FRACTION, exclusive_minimum=True),
        default=None,
        help="Kelly fraction (default 0.5 half Kelly; max 0.5; 0.25 optional stricter)",
    )
    p.add_argument("--bankroll", type=_bounded_float("bankroll", minimum=0, maximum=1_000_000_000_000), default=None, help="Current bankroll for dollar sizing")
    p.add_argument(
        "--p-haircut",
        dest="p_haircut",
        type=_bounded_float("p_haircut", minimum=0, maximum=KELLY_P_HAIRCUT_MAX),
        default=None,
        help="Subtract from p before sizing (default 0.0)",
    )
    p.add_argument(
        "--p-source",
        dest="p_source",
        choices=sorted(_P_SOURCES),
        default="estimated",
        help="estimated (default) may not exceed the configured fraction",
    )
    args = p.parse_args()

    try:
        result = kelly(
            args.prob,
            args.odds,
            args.fraction,
            bankroll=args.bankroll,
            p_haircut=args.p_haircut,
            p_source=args.p_source,
        )
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from exc
    print(json.dumps(result, indent=2))
