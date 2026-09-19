#!/usr/bin/env python3
"""Drain TradingView alert rows into one digest Pushover per cycle.

Spec: docs/tradingview-integration.md (Phase 1, "Drain job"). Runs every
5 minutes via radon-tv-alerts.timer. Per cycle:

1. Read unprocessed ``tv_alert_events`` rows on an id cursor.
2. Resolve TradingView symbols to Radon tickers; NULL when unsure.
3. Mark exact repeats of (alert_name, symbol, interval, bar_time, price)
   inside 5 seconds as ``duplicate_of`` the first fire.
4. Send ONE normal-priority Pushover digest; nothing when no new rows.
5. Stamp ``processed_at`` (and ``digest_sent_at`` when the push landed).
6. Prune rows older than 180 days.
7. ``service_health`` ``tv-alerts-drain`` heartbeat every cycle, so a quiet
   webhook reads dormant, not down.

Reads and writes go over bounded Hrana HTTP. Never places or routes an order.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Sequence
from urllib import request as urllib_request

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

SERVICE = "tv-alerts-drain"
PAGE_SIZE = 200
DEDUPE_WINDOW = timedelta(seconds=5)
RETENTION = timedelta(days=180)
PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"
MAX_DIGEST_SYMBOLS = 8

_EQUITY_RE = re.compile(r"^[A-Z]{1,6}(\.[A-Z])?$")
_CONTINUOUS_FUTURE_RE = re.compile(r"^([A-Z]{1,3})[12]!$")
_FUTURES_EXCHANGES = {"CME", "CME_MINI", "CBOT", "CBOT_MINI", "NYMEX", "COMEX", "CBOE", "CFE"}
_EQUITY_EXCHANGES = {"NASDAQ", "NYSE", "AMEX", "ARCA", "NYSEARCA", "BATS", "CBOE", "TVC", "SP", "DJ", "CBOEINDEX", ""}


# ── I/O seams (monkeypatched in tests) ─────────────────────────────


def _query(sql: str, args: Sequence[Any] = ()) -> list[tuple]:
    from db.hrana_http import hrana_query

    return hrana_query(sql, args)


def _execute(sql: str, args: Sequence[Any] = ()) -> None:
    from db.hrana_http import hrana_execute

    hrana_execute(sql, args)


def _record_health(state: str, error: Optional[dict] = None) -> None:
    from db import writer

    writer.record_service_health(SERVICE, state, finished_at=_iso(datetime.now(timezone.utc)), error=error)


def _send_pushover(payload: dict) -> None:
    req = urllib_request.Request(
        PUSHOVER_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib_request.urlopen(req, timeout=10) as resp:  # noqa: S310 — fixed https URL
        if resp.status != 200:
            raise RuntimeError(f"pushover HTTP {resp.status}")


# ── Pure logic ─────────────────────────────────────────────────────


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def resolve_ticker(symbol: Optional[str], exchange: Optional[str]) -> Optional[str]:
    """``NASDAQ:AAPL`` -> ``AAPL``, ``CME_MINI:ES1!`` -> ``ES``. None when unsure."""
    if not symbol:
        return None
    raw = symbol.strip().upper()
    if ":" in raw:
        prefix, raw = raw.split(":", 1)
        exchange = exchange or prefix
    exch = (exchange or "").strip().upper()
    future = _CONTINUOUS_FUTURE_RE.match(raw)
    if future:
        return future.group(1) if exch in _FUTURES_EXCHANGES or not exch else None
    if exch in _EQUITY_EXCHANGES and _EQUITY_RE.match(raw):
        return raw
    return None


def _dedupe_key(row: dict) -> tuple:
    return (row["alert_name"], row["symbol"], row["interval"], row["bar_time"], row["price"])


def build_digest(rows: list[dict], unresolved: int) -> str:
    counts = Counter((r["symbol"] or "unparsed") for r in rows)
    parts = [f"{sym} x{n}" for sym, n in counts.most_common(MAX_DIGEST_SYMBOLS)]
    rest = len(counts) - MAX_DIGEST_SYMBOLS
    if rest > 0:
        parts.append(f"+{rest} more")
    noun = "alert" if len(rows) == 1 else "alerts"
    message = f"{len(rows)} TradingView {noun}: {', '.join(parts)}"
    if unresolved:
        message += f" ({unresolved} unresolved)"
    return message


# ── Cycle ──────────────────────────────────────────────────────────

_COLUMNS = ("id", "received_at", "symbol", "exchange", "price", "interval", "alert_name", "bar_time")
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM tv_alert_events"


def _rows(sql: str, args: Sequence[Any]) -> list[dict]:
    return [dict(zip(_COLUMNS, r)) for r in _query(sql, args)]


def run(now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    fresh: list[dict] = []
    unresolved = 0
    # key -> (first id, received) for the 5s echo check; seeded per page with
    # the already-processed rows just before it.
    seen: dict[tuple, tuple[int, datetime]] = {}
    cursor = 0
    processed_ids: list[int] = []

    while True:
        page = _rows(
            f"{_SELECT} WHERE processed_at IS NULL AND id > ? ORDER BY id LIMIT ?",
            (cursor, PAGE_SIZE),
        )
        if not page:
            break
        first_ts = _parse_ts(page[0]["received_at"])
        if first_ts is not None:
            for prior in _rows(
                f"{_SELECT} WHERE processed_at IS NOT NULL AND duplicate_of IS NULL "
                "AND received_at >= ? ORDER BY id",
                (_iso(first_ts - DEDUPE_WINDOW),),
            ):
                seen.setdefault(_dedupe_key(prior), (prior["id"], _parse_ts(prior["received_at"])))

        for row in page:
            ticker = resolve_ticker(row["symbol"], row["exchange"])
            received = _parse_ts(row["received_at"])
            key = _dedupe_key(row)
            duplicate_of = None
            earlier = seen.get(key)
            if earlier and received and earlier[1] and received - earlier[1] <= DEDUPE_WINDOW:
                duplicate_of = earlier[0]
            else:
                seen[key] = (row["id"], received)
                fresh.append(row)
                if ticker is None:
                    unresolved += 1
            _execute(
                "UPDATE tv_alert_events SET ticker = ?, duplicate_of = ?, processed_at = ? WHERE id = ?",
                (ticker, duplicate_of, _iso(now), row["id"]),
            )
            processed_ids.append(row["id"])
        cursor = page[-1]["id"]

    health_error: Optional[dict] = None
    if fresh:
        user, token = os.environ.get("PUSHOVER_USER"), os.environ.get("PUSHOVER_TOKEN")
        if not (user and token):
            health_error = {"message": "PUSHOVER_USER / PUSHOVER_TOKEN not set; digest not sent"}
        else:
            try:
                _send_pushover({
                    "token": token,
                    "user": user,
                    "title": "TradingView alerts",
                    "message": build_digest(fresh, unresolved),
                    "priority": 0,
                })
            except Exception as exc:  # noqa: BLE001 — surfaced via health, rows stay unstamped
                health_error = {"message": f"pushover failed: {exc}"[:300]}
            else:
                for start in range(0, len(processed_ids), PAGE_SIZE):
                    chunk = processed_ids[start:start + PAGE_SIZE]
                    _execute(
                        f"UPDATE tv_alert_events SET digest_sent_at = ? WHERE id IN ({','.join('?' * len(chunk))})",
                        (_iso(now), *chunk),
                    )

    while True:
        stale = _query(
            "SELECT id FROM tv_alert_events WHERE received_at < ? ORDER BY id LIMIT ?",
            (_iso(now - RETENTION), PAGE_SIZE),
        )
        if not stale:
            break
        ids = [r[0] for r in stale]
        _execute(f"DELETE FROM tv_alert_events WHERE id IN ({','.join('?' * len(ids))})", ids)

    _record_health("error" if health_error else "ok", health_error)
    return {"processed": len(processed_ids), "digested": len(fresh), "unresolved": unresolved}


def main(argv: Optional[list[str]] = None) -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    try:
        from dotenv import load_dotenv  # type: ignore[import-untyped]

        load_dotenv(SCRIPTS_DIR.parent / ".env")
    except ImportError:
        pass
    try:
        summary = run()
    except Exception as exc:  # noqa: BLE001 — heartbeat the failure, then exit non-zero
        print(f"[{SERVICE}] cycle failed: {exc}", file=sys.stderr)
        try:
            _record_health("error", {"message": str(exc)[:300]})
        except Exception as health_exc:  # noqa: BLE001
            print(f"[{SERVICE}] health write failed: {health_exc}", file=sys.stderr)
        return 1
    print(f"[{SERVICE}] {json.dumps(summary)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
