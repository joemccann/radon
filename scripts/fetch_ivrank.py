#!/usr/bin/env python3
"""IV RANK Indicator — SPY 1M implied volatility ranked over 252 sessions.

Descriptive regime read, not a forecast: a low rank means 1M option premium
is cheap versus the trailing year, not that volatility will rise. See
docs/indicators/ivrank.md section 0.

Source ladder (docs/indicators/ivrank.md section D.1): IB primary
(OPTION_IMPLIED_VOLATILITY daily bars on SPY), UW /api/stock/SPY/iv-rank
fallback, cached payload as last resort. Output is dual-written to Turso
ivrank_history + data/ivrank.json.

Usage:
    python3 scripts/fetch_ivrank.py              # human summary (stderr)
    python3 scripts/fetch_ivrank.py --json       # JSON to stdout
    python3 scripts/fetch_ivrank.py --backfill   # 5Y IB seed run
"""
from __future__ import annotations

import argparse
import json
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
except ImportError:  # pragma: no cover — tests inject a FakeWriter
    writer = None  # type: ignore[assignment]

# ── constants (docs/indicators/ivrank.md section B.1) ─────────────
RANK_WINDOW = 252          # trailing sessions, INCLUSIVE of the current session
MIN_OBSERVATIONS = 252     # == RANK_WINDOW. No partial-window rank, ever.
BACKFILL_DURATION = "5 Y"  # IB durationStr for --backfill (seed run)
INCREMENTAL_DURATION = "1 M"  # daily run: ~22 bars, survives missed runs
RANK_SUPPRESSED_MAX = 20.0    # regime band edges, strict per B.4
RANK_NORMAL_MAX = 50.0
RANK_ELEVATED_MAX = 80.0
# A bar that deviates by more than this ratio from BOTH neighbours is a bad
# print, not a vol event (IB served 2026-08-17 iv=0.2443 between 0.1153 and
# 0.1251 while UW had 0.127). Strict: exactly 1.5x is not an outlier.
OUTLIER_NEIGHBOR_RATIO = 1.5

IVRANK_JSON = _PROJECT_DIR / "data" / "ivrank.json"
SERVICE = "ivrank"

UW_BASE_URL = "https://api.unusualwhales.com"
UW_TIMEOUT_S = 10
USER_AGENT = "radon/2.0"

HISTORY_READ_PAGE_ROWS = 2000  # keyset page size for the ivrank_history read

STATUS_OK = "ok"
STATUS_DEGRADED_UW = "degraded_uw"
STATUS_STALE_SOURCE = "stale_source"

# ── pure math ─────────────────────────────────────────────────────

def rank_window(values: list[float]) -> Optional[float]:
    """IV rank of the last value within its window; None on a degenerate window."""
    current = values[-1]
    low, high = min(values), max(values)
    if high == low:
        return None
    return (current - low) / (high - low) * 100.0


def pct_window(values: list[float]) -> Optional[float]:
    """Share of the window strictly below the last value, as a percent.

    R-191: None on the same degenerate window `rank_window` refuses. It used
    to return 0.0 there (nothing is strictly below), and `build_current`'s
    `has_rank = iv_rank is not None or iv_pct is not None` made that 0.0
    sufficient to unlock the 1-year low/high block — publishing "1M IV is
    below every one of the trailing 252 sessions", the cheapest-premium
    signal the indicator can print, beside `iv_rank: null`.
    """
    current = values[-1]
    if max(values) == min(values):
        return None
    below = sum(1 for value in values if value < current)
    return below / len(values) * 100.0


def classify_regime(rank: Optional[float]) -> Optional[str]:
    """B.4 band for a rank; None for pre-window / degenerate rows."""
    if rank is None:
        return None
    if rank < RANK_SUPPRESSED_MAX:
        return "SUPPRESSED"
    if rank < RANK_NORMAL_MAX:
        return "NORMAL"
    if rank < RANK_ELEVATED_MAX:
        return "ELEVATED"
    return "EXTREME"


def compute_series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ascending series with iv_rank / iv_pct computed per B.2 over each
    trailing RANK_WINDOW-value window; both null before MIN_OBSERVATIONS."""
    ordered = sorted(rows, key=lambda row: row["date"])
    ivs = [float(row["iv"]) for row in ordered]
    series: list[dict[str, Any]] = []
    for i, row in enumerate(ordered):
        if i + 1 < MIN_OBSERVATIONS:
            iv_rank, iv_pct = None, None
        else:
            window = ivs[i - RANK_WINDOW + 1: i + 1]
            iv_rank = rank_window(window)
            iv_pct = pct_window(window)
        series.append({**row, "iv": ivs[i], "iv_rank": iv_rank, "iv_pct": iv_pct})
    return series


def merge_history(
    stored: list[dict[str, Any]], fetched: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Upsert-merge fetched rows over stored history by date.

    Fetched wins (IB restates the current session's bar), with two refusals:
    a `uw` row never overwrites a stored `ib` row, and — R-154 — an `ib` row
    never overwrites a stored REPAIRED row. The daily IB "1M" fetch restates
    the same bad bar, which used to clobber the repair; `repair_outliers`
    then re-detected it and re-called UW, and a lookup that raised or
    returned None left `_rows_changed` True, so `_write_db` upserted the bad
    print back over the good value. Once the date aged out of the 1-month IB
    window it froze at whatever the last run wrote — and a 0.2443 print
    against a ~0.12 series sets the max of the 252-session window, distorting
    `iv_rank` and its label for every one of the next 252 sessions.
    """
    by_date = {row["date"]: dict(row) for row in stored}
    for row in fetched:
        existing = by_date.get(row["date"])
        if existing and existing.get("source") == "ib" and row.get("source") == "uw":
            continue
        if existing and existing.get("repaired") and row.get("source") == "ib":
            continue
        by_date[row["date"]] = dict(row)
    return [by_date[date] for date in sorted(by_date)]


def detect_outliers(rows: list[dict[str, Any]]) -> list[str]:
    """Dates of ib-sourced bars that sit more than OUTLIER_NEIGHBOR_RATIO
    above or below BOTH adjacent sessions. Edges (one neighbour) never
    qualify; uw-sourced rows are already vouched for and are not retested."""
    flagged: list[str] = []
    for i in range(1, len(rows) - 1):
        row = rows[i]
        if row.get("source") != "ib":
            continue
        iv, prev_iv, next_iv = row["iv"], rows[i - 1]["iv"], rows[i + 1]["iv"]
        spike = iv > prev_iv * OUTLIER_NEIGHBOR_RATIO and iv > next_iv * OUTLIER_NEIGHBOR_RATIO
        crater = iv * OUTLIER_NEIGHBOR_RATIO < prev_iv and iv * OUTLIER_NEIGHBOR_RATIO < next_iv
        if spike or crater:
            flagged.append(row["date"])
    return flagged


def repair_outliers(
    rows: list[dict[str, Any]],
    uw_iv_lookup: Callable[[str], Optional[float]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Substitute UW's iv for each detected outlier date (row re-tagged
    source 'uw'). A lookup that returns None or raises leaves the bar as is:
    the gate only overrides a print a second feed can contradict."""
    repaired = [dict(row) for row in rows]
    repairs: list[dict[str, Any]] = []
    index = {row["date"]: i for i, row in enumerate(repaired)}
    outliers = detect_outliers(repaired)
    if len(outliers) > UW_REPAIR_MAX_LOOKUPS:
        # Newest first: a fresh bad print is worth more than a 2023 one that
        # UW may never be able to serve (R-152).
        outliers = sorted(outliers, reverse=True)[:UW_REPAIR_MAX_LOOKUPS]
        print(
            f"[ivrank] repair pass bounded to {UW_REPAIR_MAX_LOOKUPS} UW lookups "
            f"(newest first); the remainder retries tomorrow",
            file=sys.stderr,
        )
    for date in outliers:
        try:
            uw_iv = uw_iv_lookup(date)
        except Exception:  # noqa: BLE001 — advisory feed; body never logged
            print(f"[ivrank] uw lookup for outlier {date} failed (non-fatal)", file=sys.stderr)
            continue
        if uw_iv is None:
            continue
        i = index[date]
        repairs.append({"date": date, "ib_iv": repaired[i]["iv"], "uw_iv": float(uw_iv)})
        # `repaired` is what makes the substitution durable against the next
        # IB restatement of the same bad bar (R-154).
        repaired[i] = {
            "date": date, "iv": float(uw_iv), "source": "uw", "repaired": True,
        }
    return repaired, repairs


def map_uw_rows(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """UW iv-rank rows (string-typed) → [{date, iv, source: 'uw'}], skipping
    malformed rows."""
    rows: list[dict[str, Any]] = []
    for entry in raw:
        date = entry.get("date")
        if not isinstance(date, str) or not date:
            continue
        try:
            iv = float(entry["volatility"])
        except (KeyError, TypeError, ValueError):
            continue
        rows.append({"date": date, "iv": iv, "source": "uw"})
    return rows


# ── gateway gate ──────────────────────────────────────────────────

def gateway_auth_state() -> Optional[str]:
    """FastAPI /health ib_gateway.auth_state, or None when unreachable."""
    from utils.ib_preflight import ib_auth_state

    return ib_auth_state()


# ── real fetchers (constructed lazily when nothing is injected) ───

def _bar_date(value: Any) -> str:
    if isinstance(value, str):
        return value[:10]
    return value.isoformat()[:10]


def _real_ib_fetch(duration: str) -> list[dict[str, Any]]:
    """SPY OPTION_IMPLIED_VOLATILITY daily closes from the gateway."""
    from clients.ib_client import IBClient
    from ib_insync import Stock

    contract = Stock("SPY", "SMART", "USD", primaryExchange="ARCA")
    client = IBClient()
    client.connect(client_id="auto", timeout=10)
    try:
        bars = client.get_historical_data(
            contract,
            duration=duration,
            bar_size="1 day",
            what_to_show="OPTION_IMPLIED_VOLATILITY",
            use_rth=True,
        )
    finally:
        client.disconnect()
    return [{"date": _bar_date(bar.date), "iv": float(bar.close)} for bar in bars or []]


def _uw_token() -> str:
    """UW_TOKEN with surrounding quotes stripped (web/.env stores it quoted)."""
    return (os.environ.get("UW_TOKEN") or "").strip().strip('"').strip("'")


# R-152: `repair_outliers` walks the ENTIRE stored history and re-flags every
# row still tagged `source == "ib"`. A date UW cannot serve (its 2023-09-22
# floor) stays `ib` and is re-fetched every night forever — unbounded and
# monotone. Bound the per-run lookups; the backlog drains a slice a night and
# a permanently unservable date stops costing quota every single run.
UW_REPAIR_MAX_LOOKUPS = 25


def _uw_get(path: str) -> dict[str, Any]:
    """Bounded UW GET. Never log the response body — 401 bodies echo the token.

    R-152: this was a raw `urlopen` with no `record_hit`, so every ivrank UW
    hit was invisible to `/uw/usage`, to `top_callers` and to
    `should_block_universe_scan` — the R-062 hole REL-036 closed for the six
    Next.js routes, reopened by a job merged in this delta.
    """
    from urllib.request import Request, urlopen

    token = _uw_token()
    if not token:
        raise ValueError("ivrank: UW_TOKEN is not set")
    req = Request(
        f"{UW_BASE_URL}{path}",
        headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
    )
    try:
        with urlopen(req, timeout=UW_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    finally:
        _count_uw_hit(path)


def _count_uw_hit(path: str) -> None:
    """Attribute one UW request to the shared daily budget. Never raises."""
    try:
        from utils import uw_budget

        uw_budget.record_hit(
            caller="ivrank", endpoint=path.split("?", 1)[0]
        )
    except Exception:  # noqa: BLE001 — the gauge must never fail the job
        pass


def _real_uw_fetch() -> list[dict[str, Any]]:
    payload = _uw_get("/api/stock/SPY/iv-rank")
    return map_uw_rows(payload.get("data") or [])


def _real_uw_iv_for_date(date: str) -> Optional[float]:
    """UW 30d IV for one session (the iv-rank window anchored on ``date``)."""
    payload = _uw_get(f"/api/stock/SPY/iv-rank?date={date}")
    for row in map_uw_rows(payload.get("data") or []):
        if row["date"] == date:
            return row["iv"]
    return None


def _real_uw_check() -> Optional[dict[str, Any]]:
    payload = _uw_get("/api/stock/SPY/volatility/stats")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    return {"date": data["date"], "iv_rank": float(data["iv_rank"])}


# ── persistence ───────────────────────────────────────────────────

def _history_from_json() -> list[dict[str, Any]]:
    """data/ivrank.json fallback series, ascending."""
    try:
        payload = json.loads(IVRANK_JSON.read_text())
    except (OSError, ValueError):
        return []
    default_source = payload.get("source") or "ib"
    rows = [
        {
            "date": row["date"],
            "iv": float(row["iv"]),
            "source": row.get("source") or default_source,
        }
        for row in (payload.get("series") or [])
        if row.get("iv") is not None
    ]
    rows.sort(key=lambda row: row["date"])
    return rows


def load_history() -> list[dict[str, Any]]:
    """Turso ivrank_history first; data/ivrank.json series fallback when empty.

    Keyset-paginated on date (Hrana I/O bounding).
    """
    try:
        from db.client import get_db

        db = get_db()
        rows: list[dict[str, Any]] = []
        cursor = ""
        while True:
            page = db.execute(
                "SELECT date, iv, source FROM ivrank_history "
                "WHERE date > ? ORDER BY date LIMIT ?",
                (cursor, HISTORY_READ_PAGE_ROWS),
            ).fetchall()
            if not page:
                break
            rows.extend(
                {"date": row[0], "iv": float(row[1]), "source": row[2]} for row in page
            )
            cursor = page[-1][0]
            if len(page) < HISTORY_READ_PAGE_ROWS:
                break
        if rows:
            return rows
    except Exception as exc:  # noqa: BLE001 — JSON fallback still works
        print(f"[ivrank] turso history rehydrate non-fatal: {exc}", file=sys.stderr)
    return _history_from_json()


def load_prior_payload() -> Optional[dict[str, Any]]:
    """Last dual-written payload from data/ivrank.json, if present."""
    try:
        return json.loads(IVRANK_JSON.read_text())
    except (OSError, ValueError):
        return None


def persist_json(payload: dict[str, Any]) -> None:
    """Atomic write of data/ivrank.json."""
    IVRANK_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = IVRANK_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, IVRANK_JSON)


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
    dead writer trips the staleness banner (feedback_service_health_heartbeat).
    """
    if writer is None:
        return
    # R-192: the row upsert, the snapshot AND the heartbeat used to share one
    # `except`, so a Hrana 502 on the first skipped the other two — and
    # `run()` then wrote data/*.json unconditionally and exited 0. Turso held
    # day N-1, the JSON held day N, routes prefer the DB, and no heartbeat
    # explained it. The row write is bounded on its own and its failure is
    # FOLDED INTO the heartbeat instead of silencing it.
    row_error: Optional[dict[str, Any]] = None
    try:
        writer.ensure_no_replica_for_writers()
        if rows_changed and rows:
            writer.upsert_ivrank_rows(rows, recorded_at=scan_time)
    except Exception as exc:  # noqa: BLE001
        print(f"[ivrank] row upsert failed: {exc}", file=sys.stderr)
        row_error = {
            "message": f"ivrank row upsert failed: {exc}",
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
    except Exception as exc:  # noqa: BLE001 — best-effort mirror
        print(f"[ivrank] db cache non-fatal: {exc}", file=sys.stderr)


# ── payload ───────────────────────────────────────────────────────

def _market_status(now: datetime) -> str:
    try:
        from utils.market_calendar import market_state

        return "open" if market_state(now).get("is_open") else "closed"
    except Exception:  # noqa: BLE001 — daily post-close job defaults closed
        return "closed"


def _expected_session(now: datetime) -> Optional[str]:
    try:
        from utils.market_calendar import last_completed_session_date

        return last_completed_session_date(now)
    except Exception:  # noqa: BLE001 — advisory field only
        return None


def _percentile(ordered: list[float], q: float) -> float:
    """Linear-interpolated percentile over an ascending list."""
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    frac = pos - lo
    if lo + 1 >= len(ordered):
        return ordered[-1]
    return ordered[lo] + (ordered[lo + 1] - ordered[lo]) * frac


def compute_stats(series: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Distribution of all non-null iv_rank history, or None when empty."""
    ranks = sorted(row["iv_rank"] for row in series if row["iv_rank"] is not None)
    if not ranks:
        return None
    return {
        "min": ranks[0],
        "p25": _percentile(ranks, 0.25),
        "median": _percentile(ranks, 0.50),
        "p75": _percentile(ranks, 0.75),
        "max": ranks[-1],
        "mean": sum(ranks) / len(ranks),
        "share_suppressed": sum(1 for r in ranks if r < RANK_SUPPRESSED_MAX) / len(ranks),
        "share_extreme": sum(1 for r in ranks if r >= RANK_ELEVATED_MAX) / len(ranks),
    }


def build_current(series: list[dict[str, Any]]) -> dict[str, Any]:
    """The payload's current block: newest row + its window range and regime."""
    cur = series[-1]
    has_rank = cur["iv_rank"] is not None or cur["iv_pct"] is not None
    if len(series) >= MIN_OBSERVATIONS:
        window = [row["iv"] for row in series[-RANK_WINDOW:]]
        iv_1y_low, iv_1y_high = min(window), max(window)
    else:
        iv_1y_low, iv_1y_high = None, None
    rank_change_1d = None
    if cur["iv_rank"] is not None:
        prior = next(
            (row["iv_rank"] for row in reversed(series[:-1]) if row["iv_rank"] is not None),
            None,
        )
        if prior is not None:
            rank_change_1d = cur["iv_rank"] - prior
    return {
        "date": cur["date"],
        "iv": cur["iv"],
        "iv_rank": cur["iv_rank"],
        "iv_pct": cur["iv_pct"],
        "iv_1y_low": iv_1y_low if has_rank else None,
        "iv_1y_high": iv_1y_high if has_rank else None,
        "rank_change_1d": rank_change_1d,
        "regime": classify_regime(cur["iv_rank"]),
    }


def build_payload(
    series: list[dict[str, Any]],
    *,
    scan_time: str,
    status: str,
    source: str,
    uw_check: Optional[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    """Full API contract for the ivrank snapshot / JSON fallback (spec F.3)."""
    return {
        "scan_time": scan_time,
        "status": status,
        "source": source,
        "as_of": series[-1]["date"],
        "expected_session": _expected_session(now),
        "market_status": _market_status(now),
        "rank_window": RANK_WINDOW,
        "count": len(series),
        "rank_count": sum(1 for row in series if row["iv_rank"] is not None),
        "current": build_current(series),
        "uw_check": uw_check,
        "stats": compute_stats(series),
        "series": [
            {"date": row["date"], "iv": row["iv"], "iv_rank": row["iv_rank"], "iv_pct": row["iv_pct"]}
            for row in series
        ],
    }


# ── orchestration ─────────────────────────────────────────────────

def _fetch_rows(
    ib_fetch: Optional[Callable[[str], list[dict[str, Any]]]],
    uw_fetch: Optional[Callable[[], list[dict[str, Any]]]],
    *,
    backfill: bool,
) -> tuple[Optional[list[dict[str, Any]]], str, str, list[str]]:
    """Source ladder (spec D.1): (rows, source, status, error messages)."""
    duration = BACKFILL_DURATION if backfill else INCREMENTAL_DURATION
    errors: list[str] = []

    auth = gateway_auth_state()
    if auth is not None and auth != "authenticated":
        print(f"[ivrank] IB skipped — gateway auth_state={auth}", file=sys.stderr)
        errors.append(f"IB skipped: gateway auth_state={auth}")
    else:
        try:
            fetcher = ib_fetch if ib_fetch is not None else _real_ib_fetch
            rows = [
                {"date": row["date"], "iv": float(row["iv"]), "source": "ib"}
                for row in fetcher(duration)
            ]
            if rows:
                return rows, "ib", STATUS_OK, errors
            errors.append("IB returned zero bars")
        except Exception as exc:  # noqa: BLE001 — ladder continues to UW
            print(f"[ivrank] IB fetch failed: {exc}", file=sys.stderr)
            errors.append(f"IB fetch failed: {exc}")

    if backfill:
        raise RuntimeError(
            "ivrank: --backfill is IB-only (UW pages 5 rows per call with a "
            f"2023-09-22 floor) and IB is unavailable: {'; '.join(errors)}"
        )

    try:
        fetcher = uw_fetch if uw_fetch is not None else _real_uw_fetch
        rows = fetcher()
        if rows:
            return rows, "uw", STATUS_DEGRADED_UW, errors
        errors.append("UW returned zero rows")
    except Exception as exc:  # noqa: BLE001 — never log UW bodies; str(exc) is safe
        print(f"[ivrank] UW fallback failed: {type(exc).__name__}", file=sys.stderr)
        errors.append(f"UW fallback failed: {type(exc).__name__}")

    return None, "none", STATUS_STALE_SOURCE, errors


def _rows_changed(stored: list[dict[str, Any]], merged: list[dict[str, Any]]) -> bool:
    """Any date added or any iv changed vs the loaded history."""
    stored_ivs = {row["date"]: float(row["iv"]) for row in stored}
    return any(stored_ivs.get(row["date"]) != row["iv"] for row in merged)


def _run_uw_check(uw_check: Optional[Callable[[], Optional[dict[str, Any]]]]) -> Optional[dict[str, Any]]:
    """Advisory cross-check; every failure swallows to None."""
    try:
        checker = uw_check if uw_check is not None else _real_uw_check
        return checker()
    except Exception:  # noqa: BLE001 — advisory only; body never logged
        print("[ivrank] uw cross-check unavailable (non-fatal)", file=sys.stderr)
        return None


def _serve_cached(
    scan_time: str, errors: list[str]
) -> dict[str, Any]:
    """Both feeds down: re-serve the cached payload as stale_source, page."""
    cached = load_prior_payload()
    if not cached:
        raise RuntimeError(
            f"ivrank: both IB and UW failed with no cached payload: {'; '.join(errors)}"
        )
    payload = {**cached, "scan_time": scan_time, "status": STATUS_STALE_SOURCE}
    print(
        f"[ivrank] both feeds down; re-serving cached payload through {payload.get('as_of')}",
        file=sys.stderr,
    )
    health_error = {
        "message": (
            f"ivrank: both IB and UW failed ({'; '.join(errors)}); "
            f"serving cached payload through {payload.get('as_of')}"
        )
    }
    _write_db(payload, scan_time, rows_changed=False, health_error=health_error)
    persist_json(payload)
    return payload


def run(
    ib_fetch: Optional[Callable[[str], list[dict[str, Any]]]] = None,
    uw_fetch: Optional[Callable[[], list[dict[str, Any]]]] = None,
    uw_check: Optional[Callable[[], Optional[dict[str, Any]]]] = None,
    uw_iv_lookup: Optional[Callable[[str], Optional[float]]] = None,
    *,
    now: Optional[datetime] = None,
    backfill: bool = False,
) -> dict[str, Any]:
    """Fetch SPY 1M IV, merge over stored history, rank, dual-write, return.

    Weekend and holiday runs restate the same bars: rows_changed is False and
    the cycle refreshes the snapshot + heartbeat only.
    """
    now = now or datetime.now(timezone.utc)
    scan_time = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    fetched, source, status, errors = _fetch_rows(ib_fetch, uw_fetch, backfill=backfill)
    if fetched is None:
        return _serve_cached(scan_time, errors)

    stored = load_history()
    merged = merge_history(stored, fetched)
    merged, repairs = repair_outliers(merged, uw_iv_lookup or _real_uw_iv_for_date)
    for repair in repairs:
        print(
            f"[ivrank] repaired bad print {repair['date']}: ib {repair['ib_iv']:.4f} "
            f"-> uw {repair['uw_iv']:.4f}",
            file=sys.stderr,
        )
    series = compute_series(merged)
    rows_changed = _rows_changed(stored, merged)
    if not rows_changed:
        print("[ivrank] source unchanged; refreshing snapshot only", file=sys.stderr)

    check = _run_uw_check(uw_check) if source == "ib" else None
    payload = build_payload(
        series,
        scan_time=scan_time,
        status=status,
        source=source,
        uw_check=check,
        now=now,
    )
    payload["outliers_repaired"] = repairs
    print(
        f"[ivrank] {payload['count']} sessions through {payload['as_of']} "
        f"({payload['rank_count']} with a rank) via {source}",
        file=sys.stderr,
    )
    _write_db(payload, scan_time, rows_changed=rows_changed, rows=series)
    persist_json(payload)
    return payload


# ── CLI ───────────────────────────────────────────────────────────

def _print_summary(payload: dict[str, Any]) -> None:
    current = payload.get("current") or {}
    iv_rank = current.get("iv_rank")
    print(
        f"\nIV RANK — {payload['count']} sessions through {payload.get('as_of')} "
        f"[{payload.get('status')}]",
        file=sys.stderr,
    )
    print(
        f"  iv_rank   {iv_rank:.1f}" if iv_rank is not None else "  iv_rank   n/a",
        file=sys.stderr,
    )
    print(f"  1m iv     {current.get('iv')}", file=sys.stderr)
    print(f"  regime    {current.get('regime')}", file=sys.stderr)
    print(f"  uw check  {payload.get('uw_check')}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="IV RANK — SPY 1M implied volatility rank, trailing 252 sessions"
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
