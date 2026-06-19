#!/usr/bin/env python3
"""Fetch the US-equity trading calendar from IBKR and cache it.

Pulls ``SPY`` ``contractDetails.liquidHours`` (the authoritative RTH
open/closed/early-close schedule, native ET), parses it, and MERGES the rolling
~6-day window into ``data/market_calendar.json`` so the forward calendar
accumulates across daily runs. The relay (``scripts/lib/marketCalendar.js``) and
the Python resolver (``utils.market_calendar.market_state``) read that cache.

stdout is reserved for the JSON result; progress goes to stderr (the FastAPI
subprocess bridge parses the last JSON line on stdout).

Usage:
    python3 scripts/fetch_market_calendar.py [--symbol SPY] [--port 4001]
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from utils.market_calendar import parse_liquid_hours  # noqa: E402
from utils.atomic_io import atomic_save  # noqa: E402
from utils.ib_preflight import ib_auth_state  # noqa: E402
from clients.ib_client import DEFAULT_HOST  # noqa: E402

CACHE_PATH = _SCRIPTS.parent / "data" / "market_calendar.json"
# CLI client-id range (90-99) per scripts/CLAUDE.md — a daily one-shot, distinct
# from the pool (0-9), relay (10-19), and subprocess (20-49) ranges.
_CLIENT_IDS = (95, 96, 97, 98)


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def merge_days(existing_days: dict, new_days: dict) -> dict:
    """Pure merge: IBKR ``new_days`` overrides existing dates; result sorted by date."""
    merged = dict(existing_days)
    merged.update(new_days)
    return dict(sorted(merged.items()))


def _read_existing_days() -> dict:
    try:
        with open(CACHE_PATH) as f:
            blob = json.load(f)
        if isinstance(blob, dict) and isinstance(blob.get("days"), dict):
            return blob["days"]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {}


def _connect(ib, port: int, timeout: int = 8) -> bool:
    for cid in _CLIENT_IDS:
        try:
            ib.connect(DEFAULT_HOST, port, clientId=cid, timeout=timeout)
            return True
        except Exception as exc:  # noqa: BLE001 — try the next id
            _log(f"  IB connect failed clientId {cid}: {exc}")
    return False


def fetch_liquid_hours(symbol: str, port: int) -> "tuple[str, str]":
    """Return ``(liquidHours, timeZoneId)`` from IBKR for ``symbol``."""
    auth = ib_auth_state()
    if auth and auth != "authenticated":
        raise RuntimeError(f"gateway auth_state={auth} (not authenticated)")
    from ib_insync import IB, Stock

    ib = IB()
    if not _connect(ib, port):
        raise RuntimeError("could not connect to IB gateway")
    try:
        details = ib.reqContractDetails(Stock(symbol, "SMART", "USD"))
        if not details:
            raise RuntimeError(f"no contractDetails for {symbol}")
        cd = details[0]
        return (cd.liquidHours or ""), getattr(cd, "timeZoneId", "")
    finally:
        try:
            ib.disconnect()
        except Exception:  # noqa: BLE001
            pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="SPY", help="Calendar source contract (SPY = US equity RTH).")
    ap.add_argument("--port", type=int, default=4001)
    args = ap.parse_args()

    try:
        liquid_hours, tz_id = fetch_liquid_hours(args.symbol, args.port)
        new_days = parse_liquid_hours(liquid_hours)
        if not new_days:
            print(json.dumps({"ok": False, "error": "empty/unparseable liquidHours",
                              "raw": liquid_hours[:200]}))
            sys.exit(1)

        merged = merge_days(_read_existing_days(), new_days)
        out = {
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source": "ibkr",
            "days": merged,
        }
        atomic_save(str(CACHE_PATH), out)

        et_today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        print(json.dumps({
            "ok": True,
            "symbol": args.symbol,
            "timeZoneId": tz_id,
            "days_fetched": len(new_days),
            "total_days": len(merged),
            "today": et_today,
            "today_status": new_days.get(et_today, merged.get(et_today)),
            "generated_at": out["generated_at"],
        }))
    except Exception as exc:  # noqa: BLE001 — surface a clean JSON error for the bridge
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
