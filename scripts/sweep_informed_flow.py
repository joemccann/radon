#!/usr/bin/env python3
"""FU4 — watchlist sweep wrapper for the per-ticker F4 informed-flow fetcher.

``fetch_informed_flow.py`` is per-ticker; a scheduler needs a single entrypoint
that refreshes the whole surveillance set. This iterates every ticker in
the Turso watchlist and calls ``fetch_informed_flow.run(ticker)`` for each,
sharing one ``UWClient`` so the sweep reuses connections / rate-limit state.

Per-ticker failures are logged and skipped so one bad symbol never aborts the
sweep. Scoped to the watchlist deliberately: a market-wide informed-flow sweep
would multiply UW calls 3x per symbol with no consumer for off-watchlist names.

Usage:
    python3 scripts/sweep_informed_flow.py            # human summary
    python3 scripts/sweep_informed_flow.py --json     # JSON summary to stdout
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))


def load_watchlist_tickers() -> list[str]:
    from db.readers import read_watchlist_tickers

    return read_watchlist_tickers()


def run() -> dict[str, Any]:
    """Sweep every watchlist ticker through the F4 informed-flow fetcher."""
    from clients.uw_client import UWClient
    import fetch_informed_flow

    tickers = load_watchlist_tickers()
    client = UWClient()

    succeeded: list[str] = []
    failed: list[str] = []
    for ticker in tickers:
        try:
            fetch_informed_flow.run(ticker, client=client)
            succeeded.append(ticker)
        except Exception as exc:  # noqa: BLE001 — one bad symbol must not abort
            print(f"[sweep-informed-flow] {ticker} failed: {exc}", file=sys.stderr)
            failed.append(ticker)

    return {
        "total": len(tickers),
        "succeeded": len(succeeded),
        "failed": failed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sweep the watchlist through the informed-flow fetcher"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON summary to stdout")
    args = parser.parse_args()

    summary = run()
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(
            f"[sweep-informed-flow] {summary['succeeded']}/{summary['total']} ok, "
            f"{len(summary['failed'])} failed",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
