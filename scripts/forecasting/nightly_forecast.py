"""Nightly forecasting runner — orchestrates the three-step pipeline.

Pre-market (off-hours) orchestration that, in order:

  1. Backfills ticker flow history from the dark-pool cache.
  2. Ranks the watchlist by flow-surprise residual and writes the dashboard
     cache.
  3. Builds + persists the calibration verdict (does Chronos beat the
     deterministic baseline on the watchlist's recent history?).

Each step is independent: a failure in one is captured (status recorded,
traceback to stderr) and the remaining steps still run. The aggregated summary
is returned and, from ``main``, printed as JSON on stdout — progress goes to
stderr only.

Heavy modules (chronos/torch via the forecasting steps) are imported LAZILY
inside ``run_nightly`` to avoid import races and to keep this module importable
on the lean fleet.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from typing import Optional


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _log(message: str) -> None:
    print(f"[nightly-forecast] {message}", file=sys.stderr)


def run_nightly(
    *,
    metric: str = "flow_strength",
    top: int = 25,
    lookback_days: int = 250,
    backfill_days: int = 20,
) -> dict:
    """Run backfill -> flow-surprise -> calibration and return a summary.

    Imports the step modules lazily so import-time torch/chronos races can't
    sink the runner. Every step is wrapped so one failure never aborts the
    others; failed steps record an ``err`` string in the summary.
    """
    from forecasting import (
        backfill_flow_history,
        calibration_report,
        flow_surprise,
    )

    summary: dict = {"ran_at": _iso_now()}

    summary["backfill"] = _run_backfill(backfill_flow_history, backfill_days)
    summary["surprise_count"] = _run_surprise(
        flow_surprise, metric=metric, top=top, lookback_days=lookback_days
    )
    summary["calibration"] = _run_calibration(
        calibration_report, metric=metric, lookback_days=lookback_days
    )

    return summary


def _run_backfill(backfill_flow_history, backfill_days: int):
    try:
        return backfill_flow_history.backfill_from_cache(days=backfill_days)
    except Exception as exc:  # noqa: BLE001 — one step can't sink the run
        traceback.print_exc()
        _log(f"backfill failed: {exc}")
        return {"err": str(exc)}


def _run_surprise(flow_surprise, *, metric: str, top: int, lookback_days: int):
    try:
        surprise = flow_surprise.rank_watchlist_surprise(
            metric=metric, top=top, lookback_days=lookback_days
        )
    except Exception as exc:  # noqa: BLE001 — one step can't sink the run
        traceback.print_exc()
        _log(f"flow-surprise failed: {exc}")
        return None

    _write_surprise_cache(flow_surprise, surprise)
    return surprise.get("count_ok")


def _write_surprise_cache(flow_surprise, surprise) -> None:
    """Persist the dashboard cache when the SECTION D helper is present.

    ``write_cache`` is added by SECTION D; call it defensively so the runner
    works before that section lands.
    """
    write_cache = getattr(flow_surprise, "write_cache", None)
    if write_cache is None:
        return
    try:
        write_cache(surprise)
    except Exception as exc:  # noqa: BLE001 — cache persist must not sink the step
        traceback.print_exc()
        _log(f"flow-surprise cache write failed: {exc}")


def _run_calibration(calibration_report, *, metric: str, lookback_days: int):
    try:
        cal = calibration_report.build_calibration_report(
            metric=metric, lookback_days=lookback_days, persist=True
        )
    except Exception as exc:  # noqa: BLE001 — one step can't sink the run
        traceback.print_exc()
        _log(f"calibration failed: {exc}")
        return {"err": str(exc)}

    return {
        "n_ok": cal.get("n_ok"),
        "chronos_available": cal.get("chronos_available"),
        "chronos_beats_baseline": cal.get("chronos_beats_baseline"),
    }


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--metric", default="flow_strength")
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--lookback", type=int, default=250)
    parser.add_argument("--backfill-days", type=int, default=20)
    args = parser.parse_args(argv)

    out = run_nightly(
        metric=args.metric,
        top=args.top,
        lookback_days=args.lookback,
        backfill_days=args.backfill_days,
    )

    print(json.dumps(out))
    required_failed = (
        isinstance(out.get("backfill"), dict)
        and "err" in out["backfill"]
    ) or out.get("surprise_count") is None or (
        isinstance(out.get("calibration"), dict)
        and "err" in out["calibration"]
    )
    return 1 if required_failed else 0


if __name__ == "__main__":
    sys.exit(main())
