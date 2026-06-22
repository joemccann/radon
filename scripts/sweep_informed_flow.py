#!/usr/bin/env python3
"""FU4 — watchlist sweep wrapper for the per-ticker F4 informed-flow fetcher.

``fetch_informed_flow.py`` is per-ticker; a scheduler needs a single entrypoint
that refreshes the whole surveillance set. This iterates every ticker in
``data/watchlist.json`` and calls ``fetch_informed_flow.run(ticker)`` for each,
sharing one ``UWClient`` so the sweep reuses connections / rate-limit state.

The watchlist has two historical shapes; both are supported:
  - top-level ``tickers: [{ticker: ...}]``
  - ``subcategories: {<name>: {tickers: [{ticker: ...}]}}``

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
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

WATCHLIST = _PROJECT_DIR / "data" / "watchlist.json"


def _collect_tickers(watchlist: dict[str, Any]) -> list[str]:
    """Flatten both watchlist shapes into a de-duplicated, ordered ticker list."""
    seen: dict[str, None] = {}

    def _add(items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            symbol = item.get("ticker") if isinstance(item, dict) else item
            if isinstance(symbol, str) and symbol.strip():
                seen.setdefault(symbol.strip().upper(), None)

    _add(watchlist.get("tickers"))
    subcategories = watchlist.get("subcategories")
    if isinstance(subcategories, dict):
        for entry in subcategories.values():
            if isinstance(entry, dict):
                _add(entry.get("tickers"))
    return list(seen)


def load_watchlist_tickers() -> list[str]:
    if not WATCHLIST.exists():
        return []
    try:
        watchlist = json.loads(WATCHLIST.read_text())
    except (ValueError, OSError):
        return []
    return _collect_tickers(watchlist)


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
