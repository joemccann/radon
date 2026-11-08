#!/usr/bin/env python3
"""Quant sleeve execution bridge: latest ``quant_targets`` -> IB stock orders.

The one piece of the systematic sleeve (docs/quant-strategies.md §8) that can
reach the broker, and it is off until the operator arms it:

* ``RADON_QUANT_EXECUTE=1`` places orders; anything else logs the plan.
* Trades only a ``rebalance_today`` targets session, at most 4 calendar days
  old, and only once per session (a timer retry cannot double-trade).
* Holdings are the signed sum of journal fills tagged ``quant:<strategy>``,
  so the sleeve counts only what it opened. The discretionary book is
  invisible to it and untouchable by it.
* Every order goes through ``ib_place_order.place_order``: the fat-finger
  caps, the trading halt and the armed bankroll gate apply unchanged. Kelly
  does not; that is the sleeve's point.

stdout is reserved for the result JSON (``--json``); progress goes to stderr.

Usage:
    python3 scripts/quant_execute.py --strategy inverse_vol            # plan only
    python3 scripts/quant_execute.py --strategy inverse_vol --json --persist
    python3 scripts/quant_execute.py --scheduled                      # radon-quant-execute.timer
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

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

from quant.strategies import STRATEGIES, get_strategy  # noqa: E402 - sys.path + dotenv must precede
from quant_run import enabled_strategies, sleeve_nav_from_snapshot, sleeve_pct  # noqa: E402

EXECUTE_SERVICE = "quant-execute"
ENV_EXECUTE = "RADON_QUANT_EXECUTE"
ENV_ALLOW_SHORT = "RADON_QUANT_ALLOW_SHORT"
ENV_OFFSET_BPS = "RADON_QUANT_LIMIT_OFFSET_BPS"
DEFAULT_OFFSET_BPS = 20.0
MAX_TARGET_AGE_DAYS = 4
MIN_TRADE_NOTIONAL = 50.0
ORDER_REF_PREFIX = "quant:"
#: IB caps orderRef at 32 characters; every registry key fits with the prefix.
ORDER_REF_MAX = 32


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def order_ref(strategy_key: str) -> str:
    return (ORDER_REF_PREFIX + strategy_key)[:ORDER_REF_MAX]


def env_flag(name: str, env: Optional[dict] = None) -> bool:
    return (env if env is not None else os.environ).get(name, "").strip() == "1"


def limit_offset_bps(env: Optional[dict] = None) -> float:
    raw = (env if env is not None else os.environ).get(ENV_OFFSET_BPS, "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_OFFSET_BPS
    return value if 0.0 <= value <= 500.0 else DEFAULT_OFFSET_BPS


# --------------------------------------------------------------------------- #
# pure logic                                                                   #
# --------------------------------------------------------------------------- #
def _fill_signed_qty(fill: dict[str, Any]) -> float:
    """Signed share count of one journal fill (stock rows only)."""
    if "contracts" in fill or "strike" in fill:
        return 0.0  # the sleeve never trades options; ignore any such row
    try:
        from utils.journal_action import signed_qty

        value = signed_qty(fill.get("action"), fill.get("shares") or fill.get("quantity"))
        if value:
            return float(value)
    except Exception:  # noqa: BLE001 - fall back to the label sign below
        pass
    action = str(fill.get("action") or "").upper()
    sign = 1 if action.startswith("BUY") else -1 if action.startswith(("SELL", "SHORT")) else 0
    try:
        qty = abs(float(fill.get("shares") or fill.get("quantity") or 0))
    except (TypeError, ValueError):
        qty = 0.0
    return sign * qty


def sleeve_holdings(fills: list[dict[str, Any]]) -> dict[str, float]:
    """Net shares per symbol from the sleeve's own fills; flat names dropped."""
    held: dict[str, float] = {}
    for fill in fills:
        symbol = str(fill.get("ticker") or fill.get("symbol") or "").upper()
        if not symbol:
            continue
        held[symbol] = held.get(symbol, 0.0) + _fill_signed_qty(fill)
    return {s: q for s, q in held.items() if abs(q) >= 1}


def target_shares(notional: Optional[float], close: Optional[float], *, allow_short: bool) -> Optional[int]:
    if notional is None or close is None or not math.isfinite(close) or close <= 0:
        return None
    shares = int(math.copysign(math.floor(abs(notional) / close), notional))
    if shares < 0 and not allow_short:
        return 0
    return shares


def limit_price(close: float, action: str, offset_bps: float) -> float:
    factor = 1.0 + offset_bps / 10_000.0 if action == "BUY" else 1.0 - offset_bps / 10_000.0
    return round(close * factor, 2)


def plan_orders(
    rows: list[dict[str, Any]],
    holdings: dict[str, float],
    *,
    allow_short: bool,
    offset_bps: float,
    sleeve_nav: Optional[float] = None,
    min_trade_notional: float = MIN_TRADE_NOTIONAL,
) -> list[dict[str, Any]]:
    """Diff target shares against sleeve holdings. Every symbol gets a line;
    lines with ``action`` None carry a ``skip`` reason instead."""
    plan: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        symbol = str(row["symbol"]).upper()
        seen.add(symbol)
        close = row.get("close")
        notional = row.get("notional")
        if notional is None and sleeve_nav is not None:
            notional = float(row["weight"]) * sleeve_nav
        held = holdings.get(symbol, 0.0)
        line: dict[str, Any] = {
            "symbol": symbol,
            "weight": float(row["weight"]),
            "notional": notional,
            "close": close,
            "held_shares": held,
            "target_shares": None,
            "action": None,
            "quantity": 0,
            "limit_price": None,
            "skip": None,
        }
        target = target_shares(notional, close, allow_short=allow_short)
        if target is None:
            line["skip"] = "no_notional" if notional is None else "no_price"
            plan.append(line)
            continue
        line["target_shares"] = target
        delta = target - held
        if delta == 0:
            line["skip"] = "on_target"
        elif close is not None and abs(delta) * close < min_trade_notional:
            line["skip"] = "dust"
        else:
            action = "BUY" if delta > 0 else "SELL"
            quantity = int(abs(delta))
            if action == "SELL" and not allow_short and quantity > max(held, 0):
                quantity = int(max(held, 0))
            if quantity < 1:
                line["skip"] = "would_short"
            else:
                line.update(action=action, quantity=quantity, limit_price=limit_price(float(close), action, offset_bps))
        plan.append(line)
    # A symbol the sleeve still holds but the targets no longer name is sold to flat.
    for symbol, held in sorted(holdings.items()):
        if symbol in seen or held == 0:
            continue
        action = "SELL" if held > 0 else "BUY"
        plan.append(
            {
                "symbol": symbol,
                "weight": 0.0,
                "notional": 0.0,
                "close": None,
                "held_shares": held,
                "target_shares": 0,
                "action": None,
                "quantity": int(abs(held)),
                "limit_price": None,
                "skip": "no_price",
                "note": f"{action} to flat needs a close; symbol left the universe",
            }
        )
    return plan


def admit(
    targets: Optional[dict[str, Any]],
    *,
    today: date,
    force: bool,
    prior_orders: list[dict[str, Any]],
) -> tuple[bool, str]:
    if not targets or not targets.get("rows"):
        return False, "no_targets"
    try:
        age = (today - date.fromisoformat(str(targets["as_of"])[:10])).days
    except (TypeError, ValueError):
        return False, "bad_as_of"
    if age > MAX_TARGET_AGE_DAYS:
        return False, f"stale_targets:{age}d"
    if prior_orders and not force:
        return False, "already_executed"
    if not targets.get("rebalance_today") and not force:
        return False, "not_rebalance_session"
    return True, "ok"


def order_params(line: dict[str, Any], strategy_key: str) -> dict[str, Any]:
    return {
        "type": "stock",
        "symbol": line["symbol"],
        "action": line["action"],
        "quantity": int(line["quantity"]),
        "limitPrice": float(line["limit_price"]),
        "tif": "DAY",
        "orderRef": order_ref(strategy_key),
    }


def _status_of(result: dict[str, Any]) -> tuple[str, Optional[str]]:
    status = str(result.get("status") or "error")
    if status == "ok":
        return "ok", None
    if result.get("indeterminate"):
        return "indeterminate", str(result.get("message") or "no broker acknowledgement")[:512]
    return "error", str(result.get("message") or result.get("detail") or "placement failed")[:512]


def execute_plan(
    plan: list[dict[str, Any]],
    *,
    strategy_key: str,
    as_of: str,
    execute: bool,
    placer: Callable[[dict[str, Any]], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Place (or merely log) every actionable line. Dry runs never call the placer."""
    orders: list[dict[str, Any]] = []
    for line in plan:
        if not line.get("action"):
            continue
        row = {
            "as_of": as_of,
            "symbol": line["symbol"],
            "action": line["action"],
            "quantity": int(line["quantity"]),
            "limit_price": float(line["limit_price"]),
            "order_ref": order_ref(strategy_key),
            "status": "planned",
            "order_id": None,
            "perm_id": None,
            "message": None,
        }
        if execute:
            try:
                result = placer(order_params(line, strategy_key))
            except Exception as exc:  # noqa: BLE001 - one rejected order must not abort the rest
                result = {"status": "error", "message": str(exc)}
            row["status"], row["message"] = _status_of(result)
            row["order_id"] = result.get("orderId")
            row["perm_id"] = result.get("permId")
        orders.append(row)
    return orders


# --------------------------------------------------------------------------- #
# wiring                                                                       #
# --------------------------------------------------------------------------- #
@dataclass
class Deps:
    load_targets: Callable[[str], Optional[dict[str, Any]]]
    load_fills: Callable[[str], list[dict[str, Any]]]
    prior_orders: Callable[[str, str], list[dict[str, Any]]]
    net_liq: Callable[[], Optional[float]]
    placer: Callable[[dict[str, Any]], dict[str, Any]]
    persist: Callable[[str, dict[str, Any]], None]
    heartbeat: Callable[..., None]


def _disk_targets(strategy_key: str) -> Optional[dict[str, Any]]:
    path = _DATA_DIR / "quant" / f"{strategy_key}_targets.json"
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return {
        "as_of": payload.get("as_of"),
        "recorded_at": payload.get("run_at"),
        "rebalance_today": bool(payload.get("rebalance_today")),
        "rows": [
            {"symbol": r["symbol"], "weight": r["weight"], "notional": r.get("notional"), "close": r.get("close")}
            for r in payload.get("targets") or []
        ],
    }


def _turso_targets(strategy_key: str) -> Optional[dict[str, Any]]:
    try:
        from db.readers import read_latest_quant_targets

        found = read_latest_quant_targets(strategy_key)
    except Exception as exc:  # noqa: BLE001 - the disk copy is the fallback
        print(f"  Turso targets read failed: {exc}", file=sys.stderr)
        found = None
    return found or _disk_targets(strategy_key)


def _turso_fills(strategy_key: str) -> list[dict[str, Any]]:
    from db.readers import read_quant_sleeve_fills

    return read_quant_sleeve_fills(strategy_key)


def _turso_orders(strategy_key: str, as_of: str) -> list[dict[str, Any]]:
    from db.readers import read_quant_orders

    return read_quant_orders(strategy_key, as_of)


def _net_liq() -> Optional[float]:
    from quant_run import _snapshot_net_liq

    return _snapshot_net_liq()


def _place(params: dict[str, Any]) -> dict[str, Any]:
    from ib_place_order import place_order

    return place_order(params)


def _write_disk(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    os.replace(tmp, path)


def _persist(strategy_key: str, result: dict[str, Any]) -> None:
    _write_disk(_DATA_DIR / "quant" / f"{strategy_key}_execution.json", result)
    if result.get("orders"):
        from db.writer import ensure_no_replica_for_writers, insert_quant_orders

        ensure_no_replica_for_writers()
        insert_quant_orders(strategy_key, result["orders"], placed_at=result["run_at"])


def _heartbeat(state: str, **kw: Any) -> None:
    """The one ``quant-execute`` health row the scheduled run writes."""
    from db.writer import record_service_health

    record_service_health(EXECUTE_SERVICE, state, **kw)


def default_deps() -> Deps:
    return Deps(
        load_targets=_turso_targets,
        load_fills=_turso_fills,
        prior_orders=_turso_orders,
        net_liq=_net_liq,
        placer=_place,
        persist=_persist,
        heartbeat=_heartbeat,
    )


def run_strategy(
    strategy_key: str,
    *,
    deps: Deps,
    execute: bool,
    force: bool = False,
    today: Optional[date] = None,
    env: Optional[dict] = None,
    persist: bool = False,
) -> dict[str, Any]:
    strategy = get_strategy(strategy_key)
    today = today or datetime.now(timezone.utc).date()
    allow_short = strategy.long_short and env_flag(ENV_ALLOW_SHORT, env)
    targets = deps.load_targets(strategy_key)
    as_of = str((targets or {}).get("as_of") or "")
    prior = deps.prior_orders(strategy_key, as_of) if as_of else []
    admitted, reason = admit(targets, today=today, force=force, prior_orders=prior)
    result: dict[str, Any] = {
        "strategy": strategy_key,
        "label": strategy.label,
        "as_of": as_of or None,
        "mode": "execute" if execute else "dry-run",
        "admitted": admitted,
        "reason": reason,
        "allow_short": allow_short,
        "holdings": {},
        "plan": [],
        "orders": [],
        "run_at": _iso_now(),
    }
    if not admitted:
        return result
    holdings = sleeve_holdings(deps.load_fills(strategy_key))
    rows = targets["rows"]  # type: ignore[index]
    sleeve_nav = None
    if any(r.get("notional") is None for r in rows):
        sleeve_nav = sleeve_nav_from_snapshot(sleeve_pct(env), deps.net_liq())
    plan = plan_orders(rows, holdings, allow_short=allow_short, offset_bps=limit_offset_bps(env), sleeve_nav=sleeve_nav)
    orders = execute_plan(plan, strategy_key=strategy_key, as_of=as_of, execute=execute, placer=deps.placer)
    result.update({"holdings": holdings, "sleeve_nav": sleeve_nav, "plan": plan, "orders": orders})
    if persist:
        deps.persist(strategy_key, result)
    return result


def _summary(result: dict[str, Any]) -> str:
    if not result["admitted"]:
        return f"{result['strategy']}: not admitted ({result['reason']})"
    lines = ", ".join(
        f"{o['action']} {o['quantity']} {o['symbol']} @ {o['limit_price']:.2f} [{o['status']}]"
        for o in result["orders"]
    )
    return f"{result['strategy']} {result['mode']} as of {result['as_of']}: {lines or 'nothing to trade'}"


def run_scheduled(*, deps: Deps, env: Optional[dict] = None) -> int:
    keys = enabled_strategies(env)
    execute = env_flag(ENV_EXECUTE, env)
    if not keys:
        print("RADON_QUANT_STRATEGIES is empty; nothing to do", file=sys.stderr)
        deps.heartbeat("ok", finished_at=_iso_now())
        return 0
    problems: list[str] = []
    for key in keys:
        try:
            result = run_strategy(key, deps=deps, execute=execute, env=env, persist=True)
            print(_summary(result), file=sys.stderr)
            bad = [o for o in result["orders"] if o["status"] in ("error", "indeterminate")]
            if bad:
                problems.append(f"{key}: " + "; ".join(f"{o['symbol']} {o['status']}: {o['message']}" for o in bad))
        except Exception as exc:  # noqa: BLE001 - one strategy must not hide the others
            print(f"{key}: {exc}", file=sys.stderr)
            problems.append(f"{key}: {str(exc)[:160]}")
    if problems:
        deps.heartbeat("error", finished_at=_iso_now(), error={"message": "; ".join(problems)[:512]})
        return 1
    deps.heartbeat("ok", finished_at=_iso_now())
    return 0


def main(argv: Optional[list[str]] = None, *, deps: Optional[Deps] = None, env: Optional[dict] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--strategy", help="registry key")
    parser.add_argument(
        "--force", action="store_true", help="trade even if not a rebalance session or already executed"
    )
    parser.add_argument("--json", action="store_true", help="print the result JSON to stdout")
    parser.add_argument("--persist", action="store_true", help="write quant_orders rows + data/quant/ fallback")
    parser.add_argument(
        "--scheduled", action="store_true", help="timer entry: every RADON_QUANT_STRATEGIES key, persisted, heartbeat"
    )
    args = parser.parse_args(argv)
    deps = deps or default_deps()
    if args.scheduled:
        return run_scheduled(deps=deps, env=env)
    if not args.strategy:
        parser.print_usage(sys.stderr)
        print("error: --strategy is required unless --scheduled", file=sys.stderr)
        return 2
    if args.strategy not in STRATEGIES:
        print(f"error: unknown strategy: {args.strategy}", file=sys.stderr)
        return 2
    execute = env_flag(ENV_EXECUTE, env)
    result = run_strategy(args.strategy, deps=deps, execute=execute, force=args.force, env=env, persist=args.persist)
    print(_summary(result), file=sys.stderr)
    if args.json:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
