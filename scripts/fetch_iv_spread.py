#!/usr/bin/env python3
"""IV SPREAD Indicator - NDX minus SPX 30-day ATM implied volatility.

Descriptive regime read, not a forecast: a wide spread means tech vol is
rich relative to the broad index, a thin or negative spread means that
premium has been bid away. See docs/indicators/iv-spread.md section 0.

Source ladder (docs/indicators/iv-spread.md section D.1): IB only
(OPTION_IMPLIED_VOLATILITY daily bars on the SPX and NDX indices, both legs
in one connection). No UW or Yahoo rung serves index-level 30d IV, so an IB
outage re-serves the cached payload as stale_source with an error
heartbeat. Output is dual-written to Turso iv_spread_history +
data/iv_spread.json.

Usage:
    python3 scripts/fetch_iv_spread.py              # human summary (stderr)
    python3 scripts/fetch_iv_spread.py --json       # JSON to stdout
    python3 scripts/fetch_iv_spread.py --backfill   # 5Y IB seed run
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

# ── path setup ────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
for _path in (_PROJECT_DIR, _SCRIPT_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv(_PROJECT_DIR / ".env")
    load_dotenv(_PROJECT_DIR / "web" / ".env")
except Exception:
    pass

try:
    from db import writer  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - tests inject a FakeWriter
    writer = None  # type: ignore[assignment]

# ── constants (docs/indicators/iv-spread.md section B.1) ──────────
LEGS = (("SPX", "CBOE"), ("NDX", "NASDAQ"))   # (symbol, exchange), Index/USD
VOL_POINTS = 100.0            # decimal IV -> vol points
BACKFILL_DURATION = "5 Y"     # IB durationStr for --backfill (seed run)
INCREMENTAL_DURATION = "1 M"  # daily run: ~22 bars, survives missed runs
Z_COMPRESSED_MAX = -1.0       # regime band edges on the z-score, strict per B.4
Z_NORMAL_MAX = 1.0
Z_ELEVATED_MAX = 2.0
# A leg close that deviates by more than this ratio from BOTH neighbours is
# a bad print, not a vol event (ivrank saw 0.2443 between 0.1153 and 0.1251
# on 2026-08-17). Strict: exactly 1.5x is not an outlier. There is no second
# feed to repair from, so the session is excluded (spread null, legs kept).
OUTLIER_NEIGHBOR_RATIO = 1.5

IV_SPREAD_JSON = _PROJECT_DIR / "data" / "iv_spread.json"
SERVICE = "iv-spread"

# Leg symbol -> stored column / series key.
LEG_KEYS = {"SPX": "spx_iv", "NDX": "ndx_iv"}

HISTORY_READ_PAGE_ROWS = 2000  # keyset page size for the iv_spread_history read

STATUS_OK = "ok"
STATUS_STALE_SOURCE = "stale_source"

# ── pure math (spec B.2) ──────────────────────────────────────────

def compute_spread(spx_iv: float, ndx_iv: float) -> float:
    """NDX minus SPX 30d IV in volatility points."""
    return (float(ndx_iv) - float(spx_iv)) * VOL_POINTS


def mean_of(values: list[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def stdev_of(values: list[float]) -> Optional[float]:
    """Sample standard deviation (n - 1); None when n < 2."""
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))


def z_score(value: Optional[float], mean: Optional[float], stdev: Optional[float]) -> Optional[float]:
    """(value - mean) / stdev; None whenever stdev is None or 0. Never divides by zero."""
    if value is None or mean is None or not stdev:
        return None
    return (value - mean) / stdev


def pct_below(values: list[float], value: Optional[float]) -> Optional[float]:
    """Share of ``values`` strictly below ``value``, as a percent."""
    if value is None or not values:
        return None
    return sum(1 for v in values if v < value) / len(values) * 100.0


def classify_regime(z: Optional[float]) -> Optional[str]:
    """B.4 band for a z-score; None when z is unavailable."""
    if z is None:
        return None
    if z < Z_COMPRESSED_MAX:
        return "COMPRESSED"
    if z < Z_NORMAL_MAX:
        return "NORMAL"
    if z < Z_ELEVATED_MAX:
        return "ELEVATED"
    return "EXTREME"


# ── merge (spec D.2) ──────────────────────────────────────────────

def _leg_maps(
    stored: list[dict[str, Any]], fetched: dict[str, list[dict[str, Any]]]
) -> dict[str, dict[str, float]]:
    """Per-leg {date: iv} with fetched winning over stored by date (IB
    restates the current session's bar)."""
    maps: dict[str, dict[str, float]] = {sym: {} for sym in LEG_KEYS}
    for row in stored:
        for sym, key in LEG_KEYS.items():
            if row.get(key) is not None:
                maps[sym][row["date"]] = float(row[key])
    for sym in LEG_KEYS:
        for bar in fetched.get(sym) or []:
            maps[sym][bar["date"]] = float(bar["iv"])
    return maps


def _paired(maps: dict[str, dict[str, float]]) -> tuple[list[str], list[str]]:
    """(dates present on both legs with positive closes, dropped dates)."""
    spx, ndx = maps["SPX"], maps["NDX"]
    kept: list[str] = []
    dropped: list[str] = []
    for date in sorted(set(spx) | set(ndx)):
        if date in spx and date in ndx and spx[date] > 0 and ndx[date] > 0:
            kept.append(date)
        else:
            dropped.append(date)
    return kept, dropped


def merge_history(
    stored: list[dict[str, Any]], fetched: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Ascending [{date, spx_iv, ndx_iv}] over stored history + fetched legs.

    A date that ends up on only one leg, or with a non-positive close on
    either leg, is dropped (see count_unpaired) so a half-served day never
    prints a spread against a stale leg.
    """
    maps = _leg_maps(stored, fetched)
    kept, _ = _paired(maps)
    return [
        {"date": date, "spx_iv": maps["SPX"][date], "ndx_iv": maps["NDX"][date]}
        for date in kept
    ]


def count_unpaired(
    stored: list[dict[str, Any]], fetched: dict[str, list[dict[str, Any]]]
) -> int:
    """Number of dates merge_history dropped (one leg only, or a leg <= 0)."""
    _, dropped = _paired(_leg_maps(stored, fetched))
    return len(dropped)


# ── bad-print gate (spec D.2a) ────────────────────────────────────

def detect_outliers(rows: list[dict[str, Any]], leg_key: str) -> list[str]:
    """Dates whose ``leg_key`` close sits strictly more than
    OUTLIER_NEIGHBOR_RATIO above or below BOTH adjacent sessions. Edges
    (one neighbour) never qualify."""
    ordered = sorted(rows, key=lambda row: row["date"])
    flagged: list[str] = []
    for i in range(1, len(ordered) - 1):
        iv = float(ordered[i][leg_key])
        prev_iv = float(ordered[i - 1][leg_key])
        next_iv = float(ordered[i + 1][leg_key])
        spike = iv > prev_iv * OUTLIER_NEIGHBOR_RATIO and iv > next_iv * OUTLIER_NEIGHBOR_RATIO
        crater = iv * OUTLIER_NEIGHBOR_RATIO < prev_iv and iv * OUTLIER_NEIGHBOR_RATIO < next_iv
        if spike or crater:
            flagged.append(ordered[i]["date"])
    return flagged


def excluded_sessions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """[{date, leg, iv, prev_iv, next_iv}] for every flagged leg close."""
    ordered = sorted(rows, key=lambda row: row["date"])
    index = {row["date"]: i for i, row in enumerate(ordered)}
    excluded: list[dict[str, Any]] = []
    for leg_key in LEG_KEYS.values():
        for date in detect_outliers(ordered, leg_key):
            i = index[date]
            excluded.append({
                "date": date,
                "leg": leg_key,
                "iv": float(ordered[i][leg_key]),
                "prev_iv": float(ordered[i - 1][leg_key]),
                "next_iv": float(ordered[i + 1][leg_key]),
            })
    excluded.sort(key=lambda entry: (entry["date"], entry["leg"]))
    return excluded


def compute_series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ascending [{date, spx_iv, ndx_iv, spread}]; spread is None on a
    session the bad-print gate excluded (raw legs kept)."""
    ordered = sorted(rows, key=lambda row: row["date"])
    excluded = {entry["date"] for entry in excluded_sessions(ordered)}
    series: list[dict[str, Any]] = []
    for row in ordered:
        spx_iv, ndx_iv = float(row["spx_iv"]), float(row["ndx_iv"])
        series.append({
            "date": row["date"],
            "spx_iv": spx_iv,
            "ndx_iv": ndx_iv,
            "spread": None if row["date"] in excluded else compute_spread(spx_iv, ndx_iv),
        })
    return series


# ── stats + current (spec B.2 / F.3) ──────────────────────────────

def compute_stats(series: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Distribution of all non-null spread history, or None when empty."""
    rows = [row for row in series if row["spread"] is not None]
    if not rows:
        return None
    spreads = [row["spread"] for row in rows]
    high = max(rows, key=lambda row: row["spread"])
    low = min(rows, key=lambda row: row["spread"])
    return {
        "count": len(spreads),
        "high": high["spread"],
        "high_date": high["date"],
        "low": low["spread"],
        "low_date": low["date"],
        "mean": mean_of(spreads),
        "stdev": stdev_of(spreads),
        "last": spreads[-1],
    }


def build_current(
    series: list[dict[str, Any]], stats: Optional[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    """The payload's current block: newest row + z, percentile, 1d change, regime."""
    if not series:
        return None
    cur = series[-1]
    spreads = [row["spread"] for row in series if row["spread"] is not None]
    mean = stats["mean"] if stats else None
    stdev = stats["stdev"] if stats else None
    z = z_score(cur["spread"], mean, stdev)
    change_1d = None
    if cur["spread"] is not None:
        prior = next(
            (row["spread"] for row in reversed(series[:-1]) if row["spread"] is not None),
            None,
        )
        if prior is not None:
            change_1d = cur["spread"] - prior
    return {
        "date": cur["date"],
        "spx_iv": cur["spx_iv"],
        "ndx_iv": cur["ndx_iv"],
        "spread": cur["spread"],
        "z_score": z,
        "pctile": pct_below(spreads, cur["spread"]),
        "change_1d": change_1d,
        "regime": classify_regime(z),
    }


# ── gateway gate ──────────────────────────────────────────────────

def gateway_auth_state() -> Optional[str]:
    """FastAPI /health ib_gateway.auth_state, or None when unreachable."""
    from utils.ib_preflight import ib_auth_state

    return ib_auth_state()


# ── real fetcher (constructed lazily when nothing is injected) ────

def _bar_date(value: Any) -> str:
    if isinstance(value, str):
        return value[:10]
    return value.isoformat()[:10]


def _real_ib_fetch(duration: str) -> dict[str, list[dict[str, Any]]]:
    """SPX and NDX OPTION_IMPLIED_VOLATILITY daily closes from the gateway,
    both legs over one connection. A leg with zero bars raises: a spread
    needs both."""
    from clients.ib_client import IBClient
    from ib_insync import Index

    legs: dict[str, list[dict[str, Any]]] = {}
    client = IBClient()
    client.connect(client_id="auto", timeout=10)
    try:
        for sym, exch in LEGS:
            bars = client.get_historical_data(
                Index(sym, exch, "USD"),
                duration=duration,
                bar_size="1 day",
                what_to_show="OPTION_IMPLIED_VOLATILITY",
                use_rth=True,
            )
            if not bars:
                raise RuntimeError(f"IB returned zero bars for {sym}")
            legs[sym] = [{"date": _bar_date(bar.date), "iv": float(bar.close)} for bar in bars]
    finally:
        client.disconnect()
    return legs


# ── persistence ───────────────────────────────────────────────────

def _history_from_json() -> list[dict[str, Any]]:
    """data/iv_spread.json fallback series as [{date, spx_iv, ndx_iv}], ascending."""
    try:
        payload = json.loads(IV_SPREAD_JSON.read_text())
    except (OSError, ValueError):
        return []
    rows = [
        {"date": row["date"], "spx_iv": float(row["spx_iv"]), "ndx_iv": float(row["ndx_iv"])}
        for row in (payload.get("series") or [])
        if row.get("spx_iv") is not None and row.get("ndx_iv") is not None
    ]
    rows.sort(key=lambda row: row["date"])
    return rows


def load_history() -> list[dict[str, Any]]:
    """Turso iv_spread_history first; data/iv_spread.json series fallback when empty.

    Keyset-paginated on date (Hrana I/O bounding).
    """
    try:
        from db.client import get_db

        db = get_db()
        rows: list[dict[str, Any]] = []
        cursor = ""
        while True:
            page = db.execute(
                "SELECT date, spx_iv, ndx_iv FROM iv_spread_history "
                "WHERE date > ? ORDER BY date LIMIT ?",
                (cursor, HISTORY_READ_PAGE_ROWS),
            ).fetchall()
            if not page:
                break
            rows.extend(
                {"date": row[0], "spx_iv": float(row[1]), "ndx_iv": float(row[2])}
                for row in page
            )
            cursor = page[-1][0]
            if len(page) < HISTORY_READ_PAGE_ROWS:
                break
        if rows:
            return rows
    except Exception as exc:  # noqa: BLE001 - JSON fallback still works
        print(f"[iv-spread] turso history rehydrate non-fatal: {exc}", file=sys.stderr)
    return _history_from_json()


def load_prior_payload() -> Optional[dict[str, Any]]:
    """Last dual-written payload from data/iv_spread.json, if present."""
    try:
        return json.loads(IV_SPREAD_JSON.read_text())
    except (OSError, ValueError):
        return None


def persist_json(payload: dict[str, Any]) -> None:
    """Atomic write of data/iv_spread.json."""
    IV_SPREAD_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = IV_SPREAD_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, IV_SPREAD_JSON)


def _write_db(
    payload: dict[str, Any],
    scan_time: str,
    *,
    rows_changed: bool,
    rows: Optional[list[dict[str, Any]]] = None,
    health_error: Optional[dict[str, Any]] = None,
) -> None:
    """Snapshot + heartbeat every cycle; row upserts only when the source moved.

    The snapshot and heartbeat run even on the unchanged path so a silently
    dead writer trips the staleness banner. The row write is bounded on its
    own and its failure is FOLDED INTO the heartbeat instead of silencing it
    (the ivrank R-192 shape).
    """
    if writer is None:
        return
    row_error: Optional[dict[str, Any]] = None
    try:
        writer.ensure_no_replica_for_writers()
        if rows_changed and rows:
            writer.upsert_iv_spread_rows(rows, recorded_at=scan_time)
    except Exception as exc:  # noqa: BLE001
        print(f"[iv-spread] row upsert failed: {exc}", file=sys.stderr)
        row_error = {
            "message": f"iv-spread row upsert failed: {exc}",
            "class": "db_write_failed",
        }
    try:
        writer.upsert_scan_snapshot(SERVICE, scan_time, payload)
        writer.record_service_health(
            SERVICE,
            "ok" if (health_error is None and row_error is None) else "error",
            finished_at=scan_time,
            error=health_error or row_error,
        )
    except Exception as exc:  # noqa: BLE001 - best-effort mirror
        print(f"[iv-spread] db cache non-fatal: {exc}", file=sys.stderr)


# ── payload ───────────────────────────────────────────────────────

def _market_status(now: datetime) -> str:
    try:
        from utils.market_calendar import market_state

        return "open" if market_state(now).get("is_open") else "closed"
    except Exception:  # noqa: BLE001 - daily post-close job defaults closed
        return "closed"


def _expected_session(now: datetime) -> Optional[str]:
    try:
        from utils.market_calendar import last_completed_session_date

        return last_completed_session_date(now)
    except Exception:  # noqa: BLE001 - advisory field only
        return None


def build_payload(
    series: list[dict[str, Any]],
    *,
    scan_time: str,
    status: str,
    source: str,
    dropped_unpaired: int,
    excluded: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    """Full API contract for the iv-spread snapshot / JSON fallback (spec F.3)."""
    stats = compute_stats(series)
    return {
        "scan_time": scan_time,
        "status": status,
        "source": source,
        "as_of": series[-1]["date"],
        "expected_session": _expected_session(now),
        "market_status": _market_status(now),
        "count": len(series),
        "spread_count": sum(1 for row in series if row["spread"] is not None),
        "dropped_unpaired": dropped_unpaired,
        "current": build_current(series, stats),
        "stats": stats,
        "excluded": excluded,
        "series": [
            {"date": row["date"], "spx_iv": row["spx_iv"], "ndx_iv": row["ndx_iv"], "spread": row["spread"]}
            for row in series
        ],
    }


# ── orchestration ─────────────────────────────────────────────────

def _validate_legs(raw: Any) -> dict[str, list[dict[str, Any]]]:
    """Coerce a fetcher result to {"SPX": [{date, iv}], "NDX": [...]}; a leg
    with zero bars raises (a spread needs both)."""
    legs: dict[str, list[dict[str, Any]]] = {}
    for sym in LEG_KEYS:
        bars = [
            {"date": bar["date"], "iv": float(bar["iv"])}
            for bar in ((raw or {}).get(sym) or [])
        ]
        if not bars:
            raise RuntimeError(f"IB returned zero bars for {sym}")
        legs[sym] = bars
    return legs


def _rows_changed(stored: list[dict[str, Any]], merged: list[dict[str, Any]]) -> bool:
    """Any date added or any leg IV changed vs the loaded history."""
    stored_legs = {
        row["date"]: (float(row["spx_iv"]), float(row["ndx_iv"])) for row in stored
    }
    return any(
        stored_legs.get(row["date"]) != (row["spx_iv"], row["ndx_iv"]) for row in merged
    )


def _cached_fallback(
    scan_time: str, why: str, *, backfill: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    """IB skipped or down: (stale_source payload, error heartbeat) from the
    cached payload. Raises when there is nothing cached (never cache empty)
    or under --backfill (IB-only seed)."""
    if backfill:
        raise RuntimeError(f"iv-spread: --backfill is IB-only and IB is unavailable: {why}")
    cached = load_prior_payload()
    if not cached:
        raise RuntimeError(f"iv-spread: IB unavailable ({why}) and no cached payload")
    payload = {**cached, "scan_time": scan_time, "status": STATUS_STALE_SOURCE}
    print(
        f"[iv-spread] IB unavailable; re-serving cached payload through {payload.get('as_of')}",
        file=sys.stderr,
    )
    health_error = {
        "message": (
            f"iv-spread: IB unavailable ({why}); "
            f"serving cached payload through {payload.get('as_of')}"
        )
    }
    return payload, health_error


def run(
    ib_fetch: Optional[Callable[[str], dict[str, list[dict[str, Any]]]]] = None,
    *,
    now: Optional[datetime] = None,
    backfill: bool = False,
) -> dict[str, Any]:
    """Fetch both legs, merge over stored history, spread, dual-write, return.

    Weekend and holiday runs restate the same bars: rows_changed is False and
    the cycle refreshes the snapshot + heartbeat only.
    """
    now = now or datetime.now(timezone.utc)
    scan_time = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    duration = BACKFILL_DURATION if backfill else INCREMENTAL_DURATION

    # Source ladder (spec D.1). The gate skips the socket without attempting
    # it; anything else attempts IB, and a failed attempt lands in the SAME
    # health-guarded handler so the run always leaves a heartbeat behind
    # (test_service_registration_completeness: no client is built outside a
    # try whose handler writes the health row).
    auth = gateway_auth_state()
    if auth is not None and auth != "authenticated":
        print(f"[iv-spread] IB skipped: gateway auth_state={auth}", file=sys.stderr)
        payload, health_error = _cached_fallback(
            scan_time, f"IB skipped: gateway auth_state={auth}", backfill=backfill
        )
        _write_db(payload, scan_time, rows_changed=False, health_error=health_error)
        persist_json(payload)
        return payload

    try:
        raw = ib_fetch(duration) if ib_fetch is not None else _real_ib_fetch(duration)
        legs = _validate_legs(raw)
    except Exception as exc:  # noqa: BLE001 - the cached path decides
        print(f"[iv-spread] IB fetch failed: {exc}", file=sys.stderr)
        payload, health_error = _cached_fallback(
            scan_time, f"IB fetch failed: {exc}", backfill=backfill
        )
        _write_db(payload, scan_time, rows_changed=False, health_error=health_error)
        persist_json(payload)
        return payload

    stored = load_history()
    merged = merge_history(stored, legs)
    dropped = count_unpaired(stored, legs)
    if dropped:
        print(f"[iv-spread] dropped {dropped} unpaired session(s)", file=sys.stderr)
    if not merged:
        raise RuntimeError("iv-spread: no session is present on both legs; nothing to write")
    series = compute_series(merged)
    excluded = excluded_sessions(merged)
    for entry in excluded:
        print(
            f"[iv-spread] excluded bad print {entry['date']} {entry['leg']}={entry['iv']:.4f} "
            f"between {entry['prev_iv']:.4f} and {entry['next_iv']:.4f}",
            file=sys.stderr,
        )
    rows_changed = _rows_changed(stored, merged)
    if not rows_changed:
        print("[iv-spread] source unchanged; refreshing snapshot only", file=sys.stderr)

    payload = build_payload(
        series,
        scan_time=scan_time,
        status=STATUS_OK,
        source="ib",
        dropped_unpaired=dropped,
        excluded=excluded,
        now=now,
    )
    print(
        f"[iv-spread] {payload['count']} sessions through {payload['as_of']} "
        f"({payload['spread_count']} with a spread) via ib",
        file=sys.stderr,
    )
    _write_db(payload, scan_time, rows_changed=rows_changed, rows=series)
    persist_json(payload)
    return payload


# ── CLI ───────────────────────────────────────────────────────────

def _print_summary(payload: dict[str, Any]) -> None:
    current = payload.get("current") or {}
    spread = current.get("spread")
    z = current.get("z_score")
    print(
        f"\nIV SPREAD - {payload.get('count')} sessions through {payload.get('as_of')} "
        f"[{payload.get('status')}]",
        file=sys.stderr,
    )
    print(
        f"  spread    {spread:.2f} vol pts" if spread is not None else "  spread    n/a",
        file=sys.stderr,
    )
    print(f"  spx 1m iv {current.get('spx_iv')}", file=sys.stderr)
    print(f"  ndx 1m iv {current.get('ndx_iv')}", file=sys.stderr)
    print(f"  z-score   {z:+.2f}" if z is not None else "  z-score   n/a", file=sys.stderr)
    print(f"  regime    {current.get('regime')}", file=sys.stderr)
    print(f"  excluded  {len(payload.get('excluded') or [])}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="IV SPREAD - NDX minus SPX 1M ATM implied volatility, in vol points"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    parser.add_argument(
        "--backfill", action="store_true", help="Seed the full 5Y IB history"
    )
    args = parser.parse_args()

    payload = run(backfill=args.backfill)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_summary(payload)


if __name__ == "__main__":
    main()
