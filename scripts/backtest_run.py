"""Strategy backtester CLI (F12) — run a walk-forward backtest for one strategy
and (optionally) persist the run to Turso + a disk fallback.

Offline analysis tool: NO service_cycle / service-health writer (operator- or
cron-invoked, not a scheduled service). stdout is reserved for the result JSON;
all progress goes to stderr.

Only the CRI strategy is wired in this first increment; stubbed strategies
return a clear ``status: "not_wired"`` payload rather than a fake result.

Usage:
    python3 scripts/backtest_run.py --strategy cri --horizon 1 --persist
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent
DISK_DIR = PROJECT_DIR / "data" / "backtest"

try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]

    load_dotenv(PROJECT_DIR / ".env")
    load_dotenv(PROJECT_DIR / "web" / ".env")
except Exception:  # pragma: no cover - dotenv optional at import
    pass


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_and_maybe_persist(
    strategy: str,
    *,
    horizon: int = 1,
    cost_fraction: float = 0.0,
    persist: bool = False,
) -> dict:
    """Run a strategy backtest; on ``persist`` write the run to DB + disk.

    A stubbed (registered-but-unwired) strategy returns a ``not_wired`` status
    payload rather than raising, so the CLI/API surface stays uniform.
    """
    from backtest.strategies import STRATEGIES, run_strategy

    strategy_meta = STRATEGIES.get(strategy)
    if strategy_meta is None:
        return {"strategy": strategy, "status": "unknown_strategy"}
    if not strategy_meta.wired:
        return {
            "strategy": strategy,
            "label": strategy_meta.label,
            "status": "not_wired",
            "entry_doc": strategy_meta.entry_doc,
        }

    result = run_strategy(strategy, horizon=horizon, cost_fraction=cost_fraction)
    result["status"] = "ok"
    result["run_at"] = _iso_now()

    if persist:
        _persist(strategy, horizon, result)
    return result


def _persist(strategy: str, horizon: int, result: dict) -> None:
    """Dual-write: Turso ``backtest_runs`` + ``data/backtest/{strategy}.json``."""
    _write_disk_fallback(strategy, result)
    try:
        from db.writer import ensure_no_replica_for_writers, upsert_backtest_run

        ensure_no_replica_for_writers()
        upsert_backtest_run(
            strategy, result["run_at"], horizon=horizon, payload=result
        )
    except Exception as exc:  # best-effort: disk fallback already written
        print(f"backtest_run: DB persist failed for {strategy}: {exc}", file=sys.stderr)


def _write_disk_fallback(strategy: str, result: dict) -> None:
    try:
        DISK_DIR.mkdir(parents=True, exist_ok=True)
        (DISK_DIR / f"{strategy}.json").write_text(json.dumps(result, indent=2))
    except Exception as exc:
        print(f"backtest_run: disk fallback failed for {strategy}: {exc}", file=sys.stderr)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--strategy", default="cri")
    parser.add_argument("--horizon", type=int, default=1)
    parser.add_argument("--cost-fraction", type=float, default=0.0)
    parser.add_argument("--persist", action="store_true")
    args = parser.parse_args(argv)

    result = run_and_maybe_persist(
        args.strategy,
        horizon=args.horizon,
        cost_fraction=args.cost_fraction,
        persist=args.persist,
    )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
