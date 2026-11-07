#!/usr/bin/env python3
"""Systematic strategy runner: backtest a registered strategy or emit its
current target weights (docs/quant-strategies.md).

This is the quant sleeve, deliberately OUTSIDE the Four Gates evaluation
pipeline: no flow signal, no Kelly ticket, no structure. It never places an
order; execution is a separate bridge that reads ``quant_targets``.

stdout is reserved for the result JSON (``--json``); progress goes to stderr.

Usage:
    python3 scripts/quant_run.py --list
    python3 scripts/quant_run.py --strategy inverse_vol --mode backtest --json
    python3 scripts/quant_run.py --strategy trend_inverse_vol --mode targets --sleeve-nav 250000 --json --persist
    python3 scripts/quant_run.py --scheduled      # radon-quant-targets.timer: RADON_QUANT_STRATEGIES x sleeve NAV

Scheduled mode reads ``RADON_QUANT_STRATEGIES`` (comma-separated registry
keys) and ``RADON_QUANT_SLEEVE_PCT`` (fraction of net liquidation from the
latest portfolio snapshot), persists each strategy's targets and writes one
``quant-targets`` heartbeat. Nothing enabled is a clean no-op, not an error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
_DATA_DIR = _PROJECT_DIR / "data"

try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]

    load_dotenv(_PROJECT_DIR / ".env")
    load_dotenv(_PROJECT_DIR / "web" / ".env")
except Exception:  # pragma: no cover - dotenv optional at import
    pass

from quant.data import PriceMatrix, load_price_matrix  # noqa: E402 - sys.path + dotenv must precede
from quant.engine import latest_targets, rebalance_mask, run_weights_backtest  # noqa: E402 - sys.path + dotenv must precede
from quant.strategies import STRATEGIES, QuantStrategy, get_strategy, list_strategies  # noqa: E402 - sys.path + dotenv must precede

DEFAULT_COST_BPS = 5.0
DEFAULT_CAPITAL = 100_000.0
TARGETS_SERVICE = "quant-targets"
ENV_STRATEGIES = "RADON_QUANT_STRATEGIES"
ENV_SLEEVE_PCT = "RADON_QUANT_SLEEVE_PCT"


def enabled_strategies(env: Optional[dict] = None) -> list[str]:
    """Registry keys named in RADON_QUANT_STRATEGIES, in order, unknown keys dropped loudly."""
    raw = (env if env is not None else os.environ).get(ENV_STRATEGIES, "")
    keys = []
    for token in raw.split(","):
        key = token.strip()
        if not key:
            continue
        if key not in STRATEGIES:
            print(f"warning: {ENV_STRATEGIES} names unknown strategy {key!r}; skipped", file=sys.stderr)
            continue
        keys.append(key)
    return keys


def sleeve_pct(env: Optional[dict] = None) -> float:
    raw = (env if env is not None else os.environ).get(ENV_SLEEVE_PCT, "0")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return value if 0.0 <= value <= 1.0 else 0.0


def _snapshot_net_liq() -> Optional[float]:
    """Net liquidation from the latest Turso portfolio snapshot, or None."""
    from db.readers import read_latest_portfolio_snapshot

    snapshot = read_latest_portfolio_snapshot()
    raw = ((snapshot or {}).get("account_summary") or {}).get("net_liquidation")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def sleeve_nav_from_snapshot(pct: float, net_liq: Optional[float]) -> Optional[float]:
    if pct <= 0 or net_liq is None:
        return None
    return round(net_liq * pct, 2)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_backtest(
    strategy: QuantStrategy,
    prices: PriceMatrix,
    *,
    cost_bps: float = DEFAULT_COST_BPS,
    capital: float = DEFAULT_CAPITAL,
    max_gross: Optional[float] = None,
) -> dict[str, Any]:
    targets = strategy.target_weights(prices.closes, prices.dates)
    result = run_weights_backtest(
        prices.closes,
        prices.dates,
        targets,
        policy=strategy.policy,
        cost_bps=cost_bps,
        initial_capital=capital,
        max_gross=strategy.max_gross if max_gross is None else max_gross,
        symbols=prices.symbols,
    )
    result.update(
        {
            "strategy": strategy.key,
            "label": strategy.label,
            "source": strategy.source,
            "mode": "backtest",
            "since": prices.dates[0] if prices.dates else None,
            "until": prices.dates[-1] if prices.dates else None,
            "universe": prices.symbols,
            "price_sources": prices.sources,
            "run_at": _iso_now(),
        }
    )
    return result


def _rebalance_today(strategy: QuantStrategy, prices: PriceMatrix, targets: np.ndarray) -> bool:
    if strategy.policy.frequency == "SIGNAL":
        if targets.shape[0] < 2:
            return bool(np.abs(targets[-1]).max() > strategy.policy.signal_change_threshold)
        return bool(np.abs(targets[-1] - targets[-2]).max() > strategy.policy.signal_change_threshold)
    return bool(rebalance_mask(prices.dates, strategy.policy)[-1])


def run_targets(
    strategy: QuantStrategy,
    prices: PriceMatrix,
    *,
    sleeve_nav: Optional[float] = None,
) -> dict[str, Any]:
    targets = strategy.target_weights(prices.closes, prices.dates)
    latest = latest_targets(prices.dates, prices.symbols, targets)
    last_close = prices.closes[-1]
    rows = []
    for j, symbol in enumerate(prices.symbols):
        weight = latest["weights"][symbol]
        close = float(last_close[j]) if np.isfinite(last_close[j]) else None
        rows.append(
            {
                "symbol": symbol,
                "weight": weight,
                "notional": weight * sleeve_nav if sleeve_nav is not None else None,
                "close": close,
                "source": prices.sources.get(symbol),
            }
        )
    return {
        "strategy": strategy.key,
        "label": strategy.label,
        "source": strategy.source,
        "mode": "targets",
        "as_of": latest["as_of"],
        "dates": [prices.dates[0], prices.dates[-1]] if prices.dates else [],
        "n_sessions": prices.n_sessions,
        "sleeve_nav": sleeve_nav,
        "gross": latest["gross"],
        "net": latest["net"],
        "policy": strategy.policy.to_dict(),
        "rebalance_today": _rebalance_today(strategy, prices, targets),
        "targets": rows,
        "run_at": _iso_now(),
    }


# --------------------------------------------------------------------------- #
# persistence: Turso first, disk fallback                                      #
# --------------------------------------------------------------------------- #
def _writer_funcs() -> tuple[Callable[..., Any], Callable[..., Any], Callable[..., Any]]:
    from db.writer import ensure_no_replica_for_writers, record_service_health, upsert_quant_run, upsert_quant_targets

    ensure_no_replica_for_writers()
    return upsert_quant_run, upsert_quant_targets, record_service_health


def _write_disk(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    os.replace(tmp, path)


def _persist(strategy: QuantStrategy, result: dict[str, Any]) -> None:
    """Disk fallback first, then the Turso rows. No heartbeat: manual runs are
    on-demand; the scheduled run writes the one ``quant-targets`` heartbeat."""
    mode = result["mode"]
    _write_disk(_DATA_DIR / "quant" / f"{strategy.key}_{mode}.json", result)
    upsert_run, upsert_targets, _record_health = _writer_funcs()
    if True:
        if mode == "backtest":
            m = result["metrics"]
            upsert_run(
                strategy.key,
                result["run_at"],
                {
                    "mode": mode,
                    "since": result["since"],
                    "until": result["until"],
                    "n_sessions": result["n_sessions"],
                    "n_rebalances": result["n_rebalances"],
                    "ann_return": m["annualized_return"],
                    "ann_vol": m["ann_vol"],
                    "sharpe": m["sharpe"],
                    "max_drawdown": m["max_drawdown"],
                },
                result,
            )
        else:
            upsert_targets(
                strategy.key,
                result["as_of"],
                [
                    {"symbol": r["symbol"], "weight": r["weight"], "notional": r["notional"], "close": r["close"]}
                    for r in result["targets"]
                ],
                rebalance_today=bool(result["rebalance_today"]),
                recorded_at=result["run_at"],
            )


def run_scheduled(
    *,
    load_prices: Callable[..., PriceMatrix],
    env: Optional[dict] = None,
    net_liq: Optional[float] = None,
) -> int:
    """radon-quant-targets.timer entry: persist targets for every enabled
    strategy, then one heartbeat. A strategy failure records ``error`` and
    continues with the rest; nothing enabled heartbeats ``ok``."""
    keys = enabled_strategies(env)
    _upsert_run, _upsert_targets, record_health = _writer_funcs()
    finished = _iso_now()
    if not keys:
        print(f"{ENV_STRATEGIES} is empty; nothing to do", file=sys.stderr)
        record_health(TARGETS_SERVICE, "ok", finished_at=finished)
        return 0
    pct = sleeve_pct(env)
    nav = sleeve_nav_from_snapshot(pct, net_liq if net_liq is not None else _snapshot_net_liq())
    if nav is None:
        print(f"no sleeve NAV ({ENV_SLEEVE_PCT}={pct:g}); targets persist without notional", file=sys.stderr)
    failures: list[str] = []
    for key in keys:
        strategy = get_strategy(key)
        try:
            prices = load_prices(list(strategy.universe), since=strategy.default_since, allow_backfill=True)
            if prices.n_sessions <= strategy.warmup:
                raise RuntimeError(f"{prices.n_sessions} sessions is not enough for warmup {strategy.warmup}")
            result = run_targets(strategy, prices, sleeve_nav=nav)
            print(_summary(result), file=sys.stderr)
            _persist(strategy, result)
        except Exception as exc:  # noqa: BLE001 - one strategy must not hide the others
            print(f"{key}: {exc}", file=sys.stderr)
            failures.append(f"{key}: {str(exc)[:160]}")
    if failures:
        record_health(TARGETS_SERVICE, "error", finished_at=_iso_now(), error={"message": "; ".join(failures)[:512]})
        return 1
    record_health(TARGETS_SERVICE, "ok", finished_at=_iso_now())
    return 0


def _summary(result: dict[str, Any]) -> str:
    if result["mode"] == "backtest":
        m = result["metrics"]
        return (
            f"{result['strategy']}: {result['since']} to {result['until']} "
            f"({result['n_sessions']} sessions, {result['n_rebalances']} rebalances) "
            f"ann {m['annualized_return']:+.2%} vol {m['ann_vol']:.2%} "
            f"sharpe {m['sharpe']:.2f} maxdd {m['max_drawdown']:.1%}"
        )
    weights = ", ".join(f"{r['symbol']} {r['weight']:+.3f}" for r in result["targets"] if r["weight"])
    return (
        f"{result['strategy']} targets as of {result['as_of']} "
        f"(gross {result['gross']:.2f}, net {result['net']:+.2f}, rebalance today: {result['rebalance_today']}): "
        f"{weights or 'cash'}"
    )


def main(argv: Optional[list[str]] = None, *, load_prices: Callable[..., PriceMatrix] = load_price_matrix) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="print the strategy registry as JSON and exit")
    parser.add_argument("--strategy", help="registry key (see --list)")
    parser.add_argument("--mode", choices=("backtest", "targets"), default="backtest")
    parser.add_argument("--since", help="first session (YYYY-MM-DD); default per strategy")
    parser.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS, help="cost per side on traded notional")
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL, help="backtest starting equity")
    parser.add_argument("--max-gross", type=float, help="override the strategy's gross exposure cap")
    parser.add_argument("--sleeve-nav", type=float, help="targets mode: sleeve NAV to turn weights into notional")
    parser.add_argument("--no-backfill", action="store_true", help="read Turso only; never fetch")
    parser.add_argument("--json", action="store_true", help="print the full result JSON to stdout")
    parser.add_argument("--persist", action="store_true", help="write Turso rows + data/quant/ fallback")
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="timer entry: targets for every RADON_QUANT_STRATEGIES key, persisted, one quant-targets heartbeat",
    )
    args = parser.parse_args(argv)

    if args.list:
        print(json.dumps(list_strategies(), indent=2))
        return 0
    if args.scheduled:
        return run_scheduled(load_prices=load_prices)
    if not args.strategy:
        parser.print_usage(sys.stderr)
        print("error: --strategy is required unless --list", file=sys.stderr)
        return 2
    try:
        strategy = get_strategy(args.strategy)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    since = args.since or strategy.default_since
    prices = load_prices(list(strategy.universe), since=since, allow_backfill=not args.no_backfill)
    if prices.n_sessions <= strategy.warmup:
        print(
            f"error: {prices.n_sessions} sessions from {since} is not enough for warmup {strategy.warmup}",
            file=sys.stderr,
        )
        return 1

    if args.mode == "backtest":
        result = run_backtest(strategy, prices, cost_bps=args.cost_bps, capital=args.capital, max_gross=args.max_gross)
    else:
        result = run_targets(strategy, prices, sleeve_nav=args.sleeve_nav)

    print(_summary(result), file=sys.stderr)
    if args.persist:
        _persist(strategy, result)
    if args.json:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
