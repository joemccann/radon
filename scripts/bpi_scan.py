#!/usr/bin/env python3
"""Bullish Percent Index scanner — NDX / SPX / RUT.

BPI(index, session) = 100 x (members on a P&F BUY signal) / (members with
a RESOLVED P&F signal that session). Engine: scripts/utils/pnf.py
(close-only, traditional boxes, 3-box reversal). Contract:
tasks/bpi-indicator-plan.md §4 (payload schema_version 1).

Member closes live in Turso ``price_history_daily`` (shared with the
RV-ratio store). Member fetches are Yahoo-only by design — IB pacing and
UW rate limits are wrong for ~2,600 bulk daily-bar pulls (sanctioned
deviation, plan §2). Incremental runs fetch 1mo for stale members only;
``--backfill`` fetches 2y for every member.

CLI: ``--index NDX|SPX|RUT|all`` (default all), ``--backfill``,
``--no-db`` (skip ALL Turso I/O — smoke/dev runs). Progress to stderr,
exactly one JSON envelope on stdout (subprocess contract).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

# ── path setup ────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

# Load env before any db.* import so TURSO_DB_URL resolves.
try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv(_PROJECT_DIR / ".env")
    load_dotenv(_PROJECT_DIR / ".env.ib-mode")
    load_dotenv(_PROJECT_DIR / "web" / ".env")
except Exception:
    pass

from clients.index_constituents import resolve_constituents
from utils.atomic_io import atomic_save
from utils.market_calendar import last_completed_session_date
from utils.pnf import signal_state_series

# ── constants (plan section 4) ────────────────────────────────────
SCHEMA_VERSION = 1
SERVICE_NAME = "bpi-scan"
INDEX_NAMES = {"NDX": "Nasdaq-100", "SPX": "S&P 500", "RUT": "Russell 2000"}

HISTORY_SESSIONS = 504          # trailing sessions in payload history (~2y)
MIN_SESSIONS = 30               # below -> missing:true, no writes
MIN_LATEST_COVERAGE = 0.80      # resolved members / constituents on latest session
OVERSOLD = 30.0
OVERBOUGHT = 70.0
BPI_DECIMALS = 2

BACKFILL_RANGE = "2y"           # Yahoo range for --backfill (all members)
INCREMENTAL_RANGE = "1mo"       # Yahoo range for stale members only
FETCH_WORKERS = 8               # plan-capped Yahoo concurrency
YAHOO_COURTESY_SLEEP_S = 0.25
FETCH_TIMEOUT_S = 30            # spark + per-symbol chart urlopen
# Wall-clock ceiling for the whole NDX/SPX/RUT Yahoo sweep. 2026-08-24
# 21:30Z: RUT spark of 1996 members (~100 x 20-symbol chunks at ~60s)
# was still running when systemd SIGTERM'd at TimeoutStartSec=6900
# (Result=timeout, NRestarts=0, P1). Must end inside
# cloud/services/radon-bpi.service TimeoutStartSec with one in-flight
# FETCH_TIMEOUT_S plus persist slack. Do not raise TimeoutStartSec —
# R-071 keeps it under the 21:30→23:30 catch-up gap.
SWEEP_BUDGET_S = 6300
# Wall clock reserved for the phase AFTER the Yahoo sweep: _read_stored_max_dates,
# _store_fetched, ~100 chunked _read_stored_closes SELECTs for RUT alone, and
# _persist_index's upserts — for three indices. The sweep deadline covered only
# the fetch, so a run that stopped exactly on budget could still be SIGTERMed
# inside upsert_bpi_history_rows, leaving bpi_history partially written and the
# service_cycle finally unexecuted. R-225. Enforced in run_scan: an index whose
# fetch overran past `fetch deadline + PERSIST_RESERVE_S` has no reserve left to
# write in, so its Turso write is skipped rather than started into a SIGTERM.
PERSIST_RESERVE_S = 600
CLOSE_READ_CALENDAR_DAYS = 800  # ~2y of sessions + weekend/holiday slack

# Turso Hrana bounding (scripts/CLAUDE.md): keep every SELECT's row count
# bounded and the statement count per stream low — 20 symbols x ~500 small
# numeric rows per read; MAX(date) probes 200 symbols per statement.
CLOSE_READ_SYMBOL_CHUNK = 20
MAX_DATE_READ_SYMBOL_CHUNK = 200

SMOKE_CAP_ENV = "BPI_SMOKE_MEMBER_CAP"  # dev/smoke: first N members only
DATA_DIR_ENV = "BPI_DATA_DIR"           # dev/smoke: redirect disk artifacts


# ── scan math ─────────────────────────────────────────────────────

def member_signal_series(closes_by_date: dict[str, float]) -> tuple[list[str], list[Optional[str]]]:
    """Per-date P&F signal states for one member's date-indexed closes."""
    dates = sorted(closes_by_date)
    closes = [float(closes_by_date[d]) for d in dates]
    states = signal_state_series([date.fromisoformat(d) for d in dates], closes)
    return dates, states


def aggregate_bpi(
    member_series: dict[str, tuple[list[str], list[Optional[str]]]],
    sessions: list[str],
) -> list[dict[str, Any]]:
    """Per-session BPI rows over ``sessions`` (ascending).

    A member's state on a session is its signal on the latest member date
    <= that session (carry-forward across missing member days). Members
    with no resolved signal yet are excluded from the denominator; a
    session with zero resolved members yields no row.
    """
    trackers = list(member_series.values())
    pointers = [0] * len(trackers)
    current: list[Optional[str]] = [None] * len(trackers)
    rows: list[dict[str, Any]] = []
    for session in sessions:
        bullish = resolved = 0
        for i, (dates, states) in enumerate(trackers):
            p = pointers[i]
            while p < len(dates) and dates[p] <= session:
                current[i] = states[p]
                p += 1
            pointers[i] = p
            if current[i] == "buy":
                bullish += 1
                resolved += 1
            elif current[i] == "sell":
                resolved += 1
        if resolved:
            rows.append({
                "date": session,
                "bpi": round(100.0 * bullish / resolved, BPI_DECIMALS),
                "members": resolved,
                "bullish": bullish,
            })
    return rows


def classify_state(bpi: float) -> str:
    if bpi <= OVERSOLD:
        return "OVERSOLD"
    if bpi >= OVERBOUGHT:
        return "OVERBOUGHT"
    return "NEUTRAL"


def detect_cross_up_30(rows: list[dict[str, Any]]) -> bool:
    """Previous session under 30 and the latest at/above it."""
    if len(rows) < 2:
        return False
    return rows[-2]["bpi"] < OVERSOLD <= rows[-1]["bpi"]


def build_index_payload(
    *,
    index_symbol: str,
    member_series: dict[str, tuple[list[str], list[Optional[str]]]],
    member_count: int,
    taken_at: str,
    sources: dict[str, Any],
) -> dict[str, Any]:
    """schema_version 1 payload, or the missing variant when the run fails
    the >=MIN_SESSIONS / >=80%-latest-coverage gate (never cache/persist an
    empty payload — feedback_dont_cache_empty_results).

    Carries the full per-session rows under the private ``_bpi_rows`` key
    for the bpi_history writer; run_scan strips it before output.
    """
    all_dates = sorted({d for dates, _ in member_series.values() for d in dates})
    sessions = all_dates[-HISTORY_SESSIONS:]
    rows = aggregate_bpi(member_series, sessions)

    if len(rows) < MIN_SESSIONS:
        return _missing_payload(index_symbol, "insufficient_history", taken_at)
    latest = rows[-1]
    # `latest["members"]` counts RESOLVED members, and aggregate_bpi
    # carry-forwards each member's last known state — so a member frozen at
    # yesterday's bar because the sweep was cut off still counts. The 80%
    # gate could therefore never see a truncated sweep: 400 of 1996 members
    # carrying today's bar was published as a fresh, complete BPI that was
    # 80% yesterday's breadth. Count only members that actually reported the
    # latest session. R-224.
    members_fresh = sum(
        1 for dates, _states in member_series.values() if dates and dates[-1] >= latest["date"]
    )
    if member_count <= 0 or members_fresh < MIN_LATEST_COVERAGE * member_count:
        return _missing_payload(index_symbol, "insufficient_coverage", taken_at)

    return {
        "schema_version": SCHEMA_VERSION,
        "index_symbol": index_symbol,
        "index_name": INDEX_NAMES[index_symbol],
        "taken_at": taken_at,
        "as_of_session": latest["date"],
        "bpi": latest["bpi"],
        "members": latest["members"],
        # Members that reported the latest session THIS run, as against the
        # carried-forward `members` above. R-224.
        "members_fresh": members_fresh,
        "bullish": latest["bullish"],
        "state": classify_state(latest["bpi"]),
        "cross_up_30": detect_cross_up_30(rows),
        # Latest aggregated session lags the last completed ET session —
        # some members' Yahoo candles haven't published yet. The catch-up
        # timer pass (Tue-Sat 11:00 UTC) re-fetches laggards and converges.
        "stale": latest["date"] < last_completed_session_date(),
        "thresholds": {"oversold": int(OVERSOLD), "overbought": int(OVERBOUGHT)},
        "history": [{"date": r["date"], "bpi": r["bpi"]} for r in rows],
        "sources": sources,
        "missing": False,
        "_bpi_rows": rows,
    }


def _missing_payload(index_symbol: str, reason: str, taken_at: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "index_symbol": index_symbol,
        "missing": True,
        "reason": reason,
        "taken_at": taken_at,
    }


# ── member close history (Turso store + Yahoo-only fetch) ─────────

def ensure_member_history(
    members: list[str],
    *,
    backfill: bool,
    no_db: bool,
    sweep_deadline: float | None = None,
) -> tuple[dict[str, dict[str, float]], dict[str, int]]:
    """Date-indexed closes per member plus fetch counters.

    Incremental: fetch INCREMENTAL_RANGE for members whose stored
    max(date) trails the last completed ET session; --backfill fetches
    BACKFILL_RANGE for everyone. Only completed-session bars are kept —
    an in-progress bar stored as a close poisons the durable store
    (rv_ratio_scan precedent). ``no_db`` skips ALL Turso I/O.
    """
    last_complete = last_completed_session_date()
    stored_max = {} if (backfill or no_db) else _read_stored_max_dates(members)
    to_fetch = [m for m in members if backfill or stored_max.get(m, "") < last_complete]
    print(
        f"  history: {len(members)} members, fetching {len(to_fetch)} "
        f"(range {BACKFILL_RANGE if backfill else INCREMENTAL_RANGE})",
        file=sys.stderr,
    )

    if backfill:
        fetched = _fetch_members(to_fetch, BACKFILL_RANGE, sweep_deadline)
    else:
        # Batch-first: the spark endpoint serves ~100 symbols per request, so
        # the nightly full-universe staleness sweep is ~26 requests instead of
        # ~2,600 chart calls (which Yahoo tarpitted to ~60s each on
        # 2026-07-27, blowing the unit's start budget). Stragglers the batch
        # left stale fall back to per-symbol chart (meta-close recovery).
        fetched = _fetch_members_spark(to_fetch, sweep_deadline)
        stragglers = members_still_stale(to_fetch, fetched, last_complete)
        if stragglers:
            print(
                f"  history: chart fallback for {len(stragglers)} members the "
                "spark batch left stale",
                file=sys.stderr,
            )
            for member, bars in _fetch_members(
                stragglers, INCREMENTAL_RANGE, sweep_deadline
            ).items():
                merged = dict(fetched.get(member, {}))
                merged.update(bars)
                fetched[member] = merged
    fetched = {
        m: {d: c for d, c in bars.items() if d <= last_complete}
        for m, bars in fetched.items()
    }
    ok_fetches = {m for m, bars in fetched.items() if bars}
    print(f"  history: {len(ok_fetches)}/{len(to_fetch)} Yahoo fetches returned bars", file=sys.stderr)

    stored_closes: dict[str, dict[str, float]] = {}
    if not no_db:
        _drop_cached_db_connection()  # fetch phase idled for minutes (Hrana rule 3)
        _store_fetched(fetched, stored_max)
        read_members = (
            [m for m in members if m not in ok_fetches] if backfill else list(members)
        )
        stored_closes = _read_stored_closes(read_members)

    closes: dict[str, dict[str, float]] = {}
    for member in members:
        merged = dict(stored_closes.get(member, {}))
        merged.update(fetched.get(member, {}))
        if merged:
            closes[member] = merged
    counts = {
        "yahoo": len(ok_fetches),
        "stored": sum(1 for m in closes if m not in ok_fetches),
    }
    return closes, counts


def install_sigterm_unwind() -> None:
    """Turn SIGTERM into a SystemExit so context managers unwind.

    SIGTERM's default disposition terminates the process without unwinding, so
    a systemd timeout during the write phase skipped the `service_cycle`
    `finally` entirely and left no error row — the `Result=timeout,
    NRestarts=0` shape 26168ed5 set out to eliminate. R-225.
    """
    import signal

    def _unwind(signum, _frame):
        print(f"  received signal {signum}; unwinding", file=sys.stderr)
        raise SystemExit(143)

    try:
        signal.signal(signal.SIGTERM, _unwind)
    except (ValueError, OSError):
        # Not the main thread, or a platform without SIGTERM.
        pass


def _sweep_deadline(deadline: float | None) -> float:
    return time.monotonic() + SWEEP_BUDGET_S if deadline is None else deadline


def _fetch_members(
    members: list[str],
    range_str: str,
    deadline: float | None = None,
) -> dict[str, dict[str, float]]:
    """Per-symbol chart fetch. Stops submitting/waiting at SWEEP_BUDGET_S
    so systemd TimeoutStartSec cannot SIGTERM the oneshot mid-pool.
    Unfinished members are omitted; ensure_member_history uses stored
    closes and the catch-up timer retries them."""
    if not members:
        return {}
    stop_at = _sweep_deadline(deadline)
    results: dict[str, dict[str, float]] = {}
    pool = ThreadPoolExecutor(max_workers=FETCH_WORKERS)
    pending = {pool.submit(_fetch_yahoo_daily, m, range_str): m for m in members}
    done_count = 0
    try:
        while pending:
            remaining = stop_at - time.monotonic()
            if remaining <= 0:
                print(
                    f"  history: chart wall-clock budget spent "
                    f"({done_count}/{len(members)}); deferring the rest",
                    file=sys.stderr,
                    flush=True,
                )
                break
            finished, _still = wait(
                pending, timeout=remaining, return_when=FIRST_COMPLETED
            )
            if not finished:
                print(
                    f"  history: chart wall-clock budget spent "
                    f"({done_count}/{len(members)}); deferring the rest",
                    file=sys.stderr,
                    flush=True,
                )
                break
            for fut in finished:
                member = pending.pop(fut)
                results[member] = fut.result()
                done_count += 1
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    return results


SPARK_BATCH_SIZE = 20           # symbols per spark request — >20 gets HTTP 400
                                # (empirical cap, probed 2026-07-27)
SPARK_RANGE = "5d"              # covers the missing tail incl. long weekends


def parse_yahoo_spark(payload: dict[str, Any]) -> dict[str, dict[str, float]]:
    """{symbol: {date: close}} from a v8 spark response (top-level dict keyed
    by symbol). A null LAST close is recovered from spark meta the same way
    parse_yahoo_chart recovers regularMarketPrice when regularMarketTime
    maps to that last bar's session; mid-series nulls stay dropped.
    In-progress bars are still dropped later via d <= last_complete."""
    bars: dict[str, dict[str, float]] = {}
    for symbol, entry in payload.items():
        if not isinstance(entry, dict):
            continue
        timestamps = entry.get("timestamp") or []
        closes = entry.get("close") or []
        series = {
            _utc_date(ts): float(c)
            for ts, c in zip(timestamps, closes)
            if c is not None
        }
        _recover_null_last_close(series, timestamps, closes, entry.get("meta") or {})
        if series:
            bars[symbol] = series
    return bars


def members_still_stale(
    members: list[str], fetched: dict[str, dict[str, float]], last_complete: str
) -> list[str]:
    """Members whose fetched bars still lack the last completed session."""
    return [m for m in members if last_complete not in fetched.get(m, {})]


def _fetch_spark_chunk(chunk: list[str]) -> dict[str, dict[str, float]]:
    from urllib.parse import quote
    from urllib.request import Request, urlopen

    url = (
        "https://query1.finance.yahoo.com/v8/finance/spark"
        f"?symbols={quote(','.join(chunk))}&range={SPARK_RANGE}&interval=1d"
    )
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:
        payload = json.load(resp)
    return parse_yahoo_spark(payload)


def _fetch_members_spark(
    members: list[str], deadline: float | None = None
) -> dict[str, dict[str, float]]:
    """Batched latest-closes fetch via the multi-symbol spark endpoint.

    Sequential chunks stop at SWEEP_BUDGET_S so a tarpitted RUT universe
    cannot hold the oneshot past TimeoutStartSec (2026-08-24 21:30Z).
    """
    if not members:
        return {}
    stop_at = _sweep_deadline(deadline)
    fetched: dict[str, dict[str, float]] = {}
    for i in range(0, len(members), SPARK_BATCH_SIZE):
        remaining = stop_at - time.monotonic()
        if remaining <= 0:
            print(
                f"  history: spark wall-clock budget spent "
                f"({i}/{len(members)}); deferring the rest",
                file=sys.stderr,
                flush=True,
            )
            break
        chunk = members[i : i + SPARK_BATCH_SIZE]
        time.sleep(min(YAHOO_COURTESY_SLEEP_S, max(0.0, remaining)))
        remaining = stop_at - time.monotonic()
        if remaining <= 0:
            print(
                f"  history: spark wall-clock budget spent "
                f"({i}/{len(members)}); deferring the rest",
                file=sys.stderr,
                flush=True,
            )
            break
        try:
            fetched.update(_fetch_spark_chunk(chunk))
        except Exception as exc:
            print(
                f"  Yahoo spark: chunk {i // SPARK_BATCH_SIZE} failed: {exc}",
                file=sys.stderr,
            )
            continue
    return fetched


def _fetch_yahoo_daily(symbol: str, range_str: str) -> dict[str, float]:
    """Daily closes from the Yahoo v8 chart API (quote close: split-adjusted,
    dividend-UNadjusted — matches the price_history_daily contract; never
    adjclose)."""
    from urllib.request import Request, urlopen

    time.sleep(YAHOO_COURTESY_SLEEP_S)
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?range={range_str}&interval=1d"
    )
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:
            result = json.load(resp)["chart"]["result"][0]
    except Exception as exc:
        print(f"  Yahoo: {symbol} failed: {exc}", file=sys.stderr)
        return {}
    return parse_yahoo_chart(result)


def _utc_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def parse_yahoo_chart(result: dict[str, Any]) -> dict[str, float]:
    """Date->close from a v8 chart result. Yahoo intermittently serves the
    just-closed session's candle with close:null while meta carries the
    official close (2026-07-24 repro: every NDX mega-cap null a day after
    the close). Recover the FINAL bar from meta.regularMarketPrice only
    when regularMarketTime maps to that same session date; an in-progress
    session never persists because ensure_member_history filters bars to
    d <= last_completed_session_date().
    """
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    bars = {
        _utc_date(ts): float(c)
        for ts, c in zip(timestamps, closes)
        if c is not None
    }
    _recover_null_last_close(bars, timestamps, closes, result.get("meta") or {})
    return bars


def _recover_null_last_close(
    bars: dict[str, float],
    timestamps: list[Any],
    closes: list[Any],
    meta: dict[str, Any],
) -> None:
    """Fill the final bar from meta only when that last close is null and
    regularMarketTime maps to the same UTC session date as the last stamp."""
    if not timestamps or not closes or closes[-1] is not None:
        return
    market_time, market_price = meta.get("regularMarketTime"), meta.get("regularMarketPrice")
    if market_time and market_price is not None:
        last_bar_date = _utc_date(timestamps[-1])
        if _utc_date(market_time) == last_bar_date:
            bars[last_bar_date] = float(market_price)


def _read_stored_max_dates(members: list[str]) -> dict[str, str]:
    import db.client as db_client

    db = db_client.get_db()
    result: dict[str, str] = {}
    for start in range(0, len(members), MAX_DATE_READ_SYMBOL_CHUNK):
        chunk = members[start:start + MAX_DATE_READ_SYMBOL_CHUNK]
        placeholders = ", ".join("?" for _ in chunk)
        cursor = db.execute(
            "SELECT symbol, MAX(date) FROM price_history_daily "
            f"WHERE symbol IN ({placeholders}) GROUP BY symbol",
            tuple(chunk),
        )
        for symbol, max_date in cursor.fetchall():
            result[symbol] = str(max_date)
    return result


def _read_stored_closes(members: list[str]) -> dict[str, dict[str, float]]:
    if not members:
        return {}
    import db.client as db_client
    from datetime import timedelta

    db = db_client.get_db()
    cutoff = (date.today() - timedelta(days=CLOSE_READ_CALENDAR_DAYS)).isoformat()
    closes: dict[str, dict[str, float]] = {}
    for start in range(0, len(members), CLOSE_READ_SYMBOL_CHUNK):
        chunk = members[start:start + CLOSE_READ_SYMBOL_CHUNK]
        placeholders = ", ".join("?" for _ in chunk)
        cursor = db.execute(
            "SELECT symbol, date, close FROM price_history_daily "
            f"WHERE symbol IN ({placeholders}) AND date >= ? ORDER BY symbol, date",
            tuple(chunk) + (cutoff,),
        )
        for symbol, d, close in cursor.fetchall():
            closes.setdefault(symbol, {})[str(d)] = float(close)
    return closes


def _store_fetched(fetched: dict[str, dict[str, float]], stored_max: dict[str, str]) -> None:
    from db.writer import upsert_price_history_symbol_rows

    rows = [
        {"symbol": member, "date": d, "close": close, "source": "yahoo"}
        for member, bars in fetched.items()
        for d, close in sorted(bars.items())
        if d > stored_max.get(member, "")
    ]
    print(f"  history: upserting {len(rows)} new close rows", file=sys.stderr)
    upsert_price_history_symbol_rows(rows)


def _drop_cached_db_connection() -> None:
    """Drop the cached libsql connection so post-fetch Turso work opens a
    fresh stream — a stream that idles through minutes of network fetches
    dies server-side and poisons every later statement (Hrana rule 3)."""
    try:
        import db.client as db_client
        db_client.reset_for_tests()
    except Exception:
        pass


# ── scan + persistence ────────────────────────────────────────────

def scan_index(
    index_symbol: str,
    *,
    backfill: bool = False,
    no_db: bool = False,
    sweep_deadline: float | None = None,
) -> dict[str, Any]:
    taken_at = _now_iso()
    print(f"BPI SCAN: {index_symbol} ({INDEX_NAMES[index_symbol]})", file=sys.stderr)
    tickers, source = resolve_constituents(
        index_symbol, cache_dir=_data_dir() / "constituents"
    )
    cap = _smoke_member_cap()
    if cap:
        tickers = tickers[:cap]
        print(f"  smoke cap active: first {cap} members only", file=sys.stderr)

    closes, fetch_counts = ensure_member_history(
        tickers, backfill=backfill, no_db=no_db, sweep_deadline=sweep_deadline
    )
    member_series = {
        member: member_signal_series(series)
        for member, series in closes.items()
        if len(series) >= 2
    }
    print(
        f"  members: {len(tickers)} constituents, {len(member_series)} with close history",
        file=sys.stderr,
    )
    return build_index_payload(
        index_symbol=index_symbol,
        member_series=member_series,
        member_count=len(tickers),
        taken_at=taken_at,
        sources={"constituents": source, "member_close_fetches": fetch_counts},
    )


def run_scan(
    indices: list[str],
    *,
    backfill: bool,
    no_db: bool,
    sweep_deadline: float | None = None,
) -> dict[str, Any]:
    """Scan each index, persist gated payloads, mirror to disk.

    The whole run sits inside one service_cycle: any hard failure writes
    the error row (+ retry embargo) and re-raises, so the timer slot is
    never latched on a retryable failure. Gated (missing) payloads exit
    cleanly — the heartbeat is still written. One wall-clock deadline
    covers NDX+SPX+RUT so a tarpitted later index cannot spend a fresh
    SWEEP_BUDGET_S after earlier indices already used the start budget.
    """
    generated_at = _now_iso()
    install_sigterm_unwind()
    deadline = _sweep_deadline(sweep_deadline)
    persist_deadline = deadline + PERSIST_RESERVE_S
    results: dict[str, Any] = {}
    with _heartbeat_cycle(no_db) as cycle:
        for index_symbol in indices:
            payload = scan_index(
                index_symbol,
                backfill=backfill,
                no_db=no_db,
                sweep_deadline=deadline,
            )
            if payload.get("missing"):
                print(
                    f"  {index_symbol}: {payload.get('reason')}; no rows/snapshot written",
                    file=sys.stderr,
                )
            elif not no_db:
                # The write phase is ~130 chunked Hrana SELECTs plus the
                # upserts; PERSIST_RESERVE_S is the wall clock set aside for
                # it. Past that, starting the write only buys a SIGTERM
                # mid-upsert and a half-written bpi_history. R-225/T-231.
                if time.monotonic() >= persist_deadline:
                    print(
                        f"  {index_symbol}: persist reserve "
                        f"({PERSIST_RESERVE_S}s) exhausted; skipping the Turso "
                        "write, the catch-up pass retries it",
                        file=sys.stderr,
                    )
                else:
                    _persist_index(index_symbol, payload)
            payload.pop("_bpi_rows", None)
            results[index_symbol] = payload
        if cycle is not None:
            cycle.finished_at = generated_at
    _write_disk_mirror(results, generated_at)
    return {"generated_at": generated_at, "indices": results}


@contextmanager
def _heartbeat_cycle(no_db: bool) -> Iterator[Any]:
    if no_db:
        yield None
        return
    from db.service_cycle import service_cycle

    with service_cycle(SERVICE_NAME, market_hours_class="daily") as cycle:
        yield cycle


def _persist_index(index_symbol: str, payload: dict[str, Any]) -> None:
    from db.writer import (
        ensure_no_replica_for_writers,
        upsert_bpi_history_rows,
        upsert_bpi_snapshot,
    )

    ensure_no_replica_for_writers()
    upsert_bpi_history_rows(index_symbol, payload["_bpi_rows"])
    snapshot = {k: v for k, v in payload.items() if k != "_bpi_rows"}
    upsert_bpi_snapshot(index_symbol, payload["taken_at"], snapshot)
    print(f"  {index_symbol}: Turso history + snapshot upserted", file=sys.stderr)


def _write_disk_mirror(results: dict[str, Any], generated_at: str) -> None:
    """Merge non-missing payloads over the existing mirror; a run with
    nothing fresh leaves the file untouched (never cache empty results)."""
    fresh = {k: v for k, v in results.items() if not v.get("missing")}
    if not fresh:
        return
    path = _data_dir() / "bpi.json"
    existing: dict[str, Any] = {}
    try:
        existing = json.loads(path.read_text()).get("indices") or {}
    except (OSError, ValueError):
        pass
    kept = {k: v for k, v in existing.items() if k in INDEX_NAMES}
    atomic_save(str(path), {"generated_at": generated_at, "indices": {**kept, **fresh}})
    print(f"  mirror saved: {path}", file=sys.stderr)


def _data_dir() -> Path:
    return Path(os.environ.get(DATA_DIR_ENV) or (_PROJECT_DIR / "data"))


def _smoke_member_cap() -> int:
    try:
        return max(0, int(os.environ.get(SMOKE_CAP_ENV) or 0))
    except ValueError:
        return 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ── CLI ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bullish Percent Index scanner (NDX/SPX/RUT, P&F buy-signal breadth)",
    )
    parser.add_argument("--index", choices=[*INDEX_NAMES, "all"], default="all")
    parser.add_argument("--backfill", action="store_true", help="2y Yahoo range for all members")
    parser.add_argument("--no-db", dest="no_db", action="store_true", help="skip all Turso I/O")
    args = parser.parse_args()

    indices = list(INDEX_NAMES) if args.index == "all" else [args.index]
    envelope = run_scan(indices, backfill=args.backfill, no_db=args.no_db)
    print(json.dumps(envelope))


if __name__ == "__main__":
    main()
