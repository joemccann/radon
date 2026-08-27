#!/usr/bin/env python3
"""Short crowding / squeeze scoreboard — Equibles short interest + squeeze score.

Radon already knows BORROW COST and availability from IB
(``/short-availability/{ticker}``) but has no short interest, days-to-cover, or
squeeze ranking. This fetcher supplies the missing half:

  - /stocks/{ticker}/short-interest  — bi-monthly FINRA settlement series
    (settlementDate, shortPosition, changeInShortPosition, averageDailyVolume,
    daysToCover)
  - /short-squeeze-scores            — 0-100 score, rank, base + catalyst
    components, factor percentiles
  - /short-interest/snapshot         — the market-wide board

Two readings come out of one number, and the payload carries both:
  Gate 1 (convexity) — a crowded short is convex UPSIDE for long call structures.
  Gate 3 (risk)      — the same crowding is a fat LEFT tail for short/naked put
                       legs. Naked shorts were re-enabled 2026-04-30, so this is
                       a live risk input, not a curiosity.
The squeeze score is NOT a directional buy signal and is never presented as one.

VINTAGE: short interest settles bi-monthly and publishes with a lag. Every row
carries its settlementDate and the payload carries the derived age in days; no
surface may imply the figures are real-time.

BORROW JOIN: each entry ships an unresolved ``borrow`` seam pointing at the
existing IB route. Wiring the live probe in is a read-side join, deliberately
left out of this writer.

Output is dual-written to Turso (equibles_short_interest +
equibles_squeeze_scores durable rows, scan_snapshots payload) and
data/equibles_short_crowding.json.

Usage:
    python3 scripts/fetch_equibles_short_crowding.py                 # summary
    python3 scripts/fetch_equibles_short_crowding.py --json          # stdout JSON
    python3 scripts/fetch_equibles_short_crowding.py --tickers GME,AMC
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

# ── path setup ────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv(_PROJECT_DIR / ".env")
    load_dotenv(_PROJECT_DIR / "web" / ".env")
except Exception:
    pass

# ── constants ─────────────────────────────────────────────────────

SERVICE = "equibles-short-crowding"
SHORT_CROWDING_JSON = _PROJECT_DIR / "data" / "equibles_short_crowding.json"
WATCHLIST_JSON = _PROJECT_DIR / "data" / "watchlist.json"

# Every ticker costs two billed requests per cycle (short interest + squeeze
# score), so the universe is bounded rather than "whatever the watchlist grew to".
MAX_TICKERS = 40
SETTLEMENT_HISTORY_LIMIT = 26  # ~1 year of bi-monthly settlements
BOARD_ROW_LIMIT = 25
BOARD_MIN_AVERAGE_DAILY_VOLUME = 1_000_000

FALLBACK_TICKERS = ["GME", "AMC", "TSLA", "NVDA", "AAPL"]

DB_INSERT_CHUNK_ROWS = 200

SHORT_INTEREST_UPSERT_SQL = """
INSERT INTO equibles_short_interest
  (ticker, settlement_date, short_position, change_in_short_position,
   average_daily_volume, days_to_cover, recorded_at)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(ticker, settlement_date) DO UPDATE SET
  short_position           = excluded.short_position,
  change_in_short_position = excluded.change_in_short_position,
  average_daily_volume     = excluded.average_daily_volume,
  days_to_cover            = excluded.days_to_cover,
  recorded_at              = excluded.recorded_at
"""

SQUEEZE_SCORE_UPSERT_SQL = """
INSERT INTO equibles_squeeze_scores
  (ticker, score, rank, base_score, catalyst_boost, short_interest_pct,
   short_interest_pct_basis, market_cap, factors, as_of, recorded_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(ticker) DO UPDATE SET
  score                       = excluded.score,
  rank                        = excluded.rank,
  base_score                  = excluded.base_score,
  catalyst_boost              = excluded.catalyst_boost,
  short_interest_pct           = excluded.short_interest_pct,
  short_interest_pct_basis     = excluded.short_interest_pct_basis,
  market_cap                  = excluded.market_cap,
  factors                     = excluded.factors,
  as_of                       = excluded.as_of,
  recorded_at                 = excluded.recorded_at
"""


# ── defensive field access ────────────────────────────────────────

def _rows_of(payload: Any) -> list[dict[str, Any]]:
    """Rows from a documented ``{data: [...]}`` envelope or a bare list."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []
    rows = payload.get("data")
    if isinstance(rows, list):
        return [r for r in rows if isinstance(r, dict)]
    return []


def _number(row: dict[str, Any], *names: str) -> Optional[float]:
    """First parseable numeric value among ``names``; None when absent/garbage."""
    for name in names:
        value = row.get(name)
        if isinstance(value, bool) or value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _integer(row: dict[str, Any], *names: str) -> Optional[int]:
    value = _number(row, *names)
    return int(value) if value is not None else None


def _text(row: dict[str, Any], *names: str) -> Optional[str]:
    for name in names:
        value = row.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _day(value: Optional[str]) -> Optional[str]:
    """ISO date, tolerating a full timestamp."""
    return value[:10] if value else None


# ── normalization ─────────────────────────────────────────────────

def normalize_short_interest_rows(payload: Any) -> list[dict[str, Any]]:
    """Settlement rows newest-first; rows without a settlement date are dropped."""
    rows: list[dict[str, Any]] = []
    for raw in _rows_of(payload):
        settlement_date = _day(_text(raw, "settlementDate", "settlement_date", "date"))
        if not settlement_date:
            continue
        rows.append(
            {
                "settlement_date": settlement_date,
                "short_position": _number(raw, "shortPosition", "shortInterest"),
                "change_in_short_position": _number(raw, "changeInShortPosition", "change"),
                "average_daily_volume": _number(raw, "averageDailyVolume", "avgDailyVolume"),
                "days_to_cover": _number(raw, "daysToCover"),
            }
        )
    rows.sort(key=lambda r: r["settlement_date"], reverse=True)
    return rows


_SQUEEZE_SCALAR_KEYS = {
    "ticker",
    "symbol",
    "score",
    "squeezeScore",
    "rank",
    "baseScore",
    "catalystBoost",
    "shortInterestPctOfFloat",
    "shortInterestPctOfShares",
    "marketCap",
    "marketCapitalization",
    "dollarVolume",
    "averageDailyDollarVolume",
    "asOf",
    "settlementDate",
}

# Squeeze-score factors that arrive as FRACTIONS and are absent from
# EquiblesClient.FRACTION_FIELDS (verified live 2026-08-11). The client cannot
# be edited from here, so the same convention is applied locally: rescale to a
# percent and rename, leaving no ambiguous key in the payload. Anything already
# suffixed "Percentile" is a 0-100 rank and must not be touched.
_LOCAL_FRACTION_FACTORS = {
    "shortInterestChangePercent": "shortInterestChangePct",
    "shortVolumeShareTrend": "shortVolumeShareTrendPct",
    "failsToDeliverPercentOfShares": "failsToDeliverPctOfShares",
}


def _short_interest_pct(raw: dict[str, Any]) -> tuple[Optional[float], Optional[str]]:
    """Short interest as a percent, plus the basis it was measured against.

    Equibles reports percent-of-shares on the squeeze score and percent-of-float
    elsewhere; the two are not interchangeable, so the basis travels with the
    number instead of being flattened into a misleading label.
    """
    of_shares = _number(raw, "shortInterestPctOfShares")
    if of_shares is not None:
        return of_shares, "shares"
    of_float = _number(raw, "shortInterestPctOfFloat")
    if of_float is not None:
        return of_float, "float"
    return None, None


def _normalize_factors(raw: dict[str, Any]) -> dict[str, Any]:
    factors: dict[str, Any] = {}
    for key, value in raw.items():
        if key in _SQUEEZE_SCALAR_KEYS:
            continue
        percent_key = _LOCAL_FRACTION_FACTORS.get(key)
        if percent_key is None:
            factors[key] = value
            continue
        factors[percent_key] = value * 100 if isinstance(value, (int, float)) and not isinstance(value, bool) else value
    return factors


def normalize_squeeze_score(payload: Any) -> Optional[dict[str, Any]]:
    """Single squeeze-score row; the vintage may sit on the row OR the envelope."""
    rows = _rows_of(payload)
    raw = rows[0] if rows else (payload if isinstance(payload, dict) and "score" in payload else None)
    if not raw:
        return None
    envelope = payload if isinstance(payload, dict) else {}
    short_interest_pct, basis = _short_interest_pct(raw)
    return {
        "score": _number(raw, "score", "squeezeScore"),
        "rank": _integer(raw, "rank"),
        "base_score": _number(raw, "baseScore"),
        "catalyst_boost": _number(raw, "catalystBoost"),
        "short_interest_pct": short_interest_pct,
        "short_interest_pct_basis": basis,
        "market_cap": _number(raw, "marketCap", "marketCapitalization"),
        "dollar_volume": _number(raw, "dollarVolume", "averageDailyDollarVolume"),
        "as_of": _day(
            _text(raw, "asOf", "settlementDate", "reportDate")
            or _text(envelope, "settlementDate", "asOf")
        ),
        "factors": _normalize_factors(raw),
    }


def normalize_board_rows(payload: Any, *, limit: int = BOARD_ROW_LIMIT) -> list[dict[str, Any]]:
    """Market-wide snapshot rows; tickerless rows dropped, envelope vintage carried."""
    envelope = payload if isinstance(payload, dict) else {}
    envelope_settlement = _day(_text(envelope, "settlementDate", "asOf"))
    board: list[dict[str, Any]] = []
    for raw in _rows_of(payload):
        ticker = _text(raw, "ticker", "symbol")
        if not ticker:
            continue
        board.append(
            {
                "ticker": ticker.upper(),
                "settlement_date": _day(_text(raw, "settlementDate", "settlement_date"))
                or envelope_settlement,
                "short_position": _number(raw, "shortPosition", "shortInterest"),
                "days_to_cover": _number(raw, "daysToCover"),
                "average_daily_volume": _number(raw, "averageDailyVolume"),
                "short_interest_pct": _short_interest_pct(raw)[0],
            }
        )
        if len(board) >= limit:
            break
    return board


# ── crowding readings ─────────────────────────────────────────────

_TIER_THRESHOLDS = (("extreme", 5.0), ("crowded", 3.0), ("moderate", 1.5))


def crowding_tier(days_to_cover: Optional[float]) -> str:
    """Days-to-cover ladder: how many sessions of ADV the short side must buy back."""
    if days_to_cover is None:
        return "unknown"
    for name, floor in _TIER_THRESHOLDS:
        if days_to_cover >= floor:
            return name
    return "light"


_INTENSITY_THRESHOLDS = (("extreme", 80.0, 6.0), ("high", 60.0, 4.0), ("moderate", 40.0, 2.0))


def crowding_intensity(days_to_cover: Optional[float], squeeze_score: Optional[float]) -> str:
    """One magnitude, read two ways (upside convexity / short-leg tail risk)."""
    for name, score_floor, cover_floor in _INTENSITY_THRESHOLDS:
        if squeeze_score is not None and squeeze_score >= score_floor:
            return name
        if days_to_cover is not None and days_to_cover >= cover_floor:
            return name
    return "low"


def settlement_age_days(settlement_date: Optional[str], *, today: Optional[date] = None) -> Optional[int]:
    """Days since the settlement the figures belong to — the honest vintage."""
    if not settlement_date:
        return None
    try:
        settled = date.fromisoformat(settlement_date[:10])
    except ValueError:
        return None
    return ((today or date.today()) - settled).days


def _change_pct(latest: dict[str, Any], prior: Optional[dict[str, Any]]) -> Optional[float]:
    position = latest.get("short_position")
    prior_position = prior.get("short_position") if prior else None
    if prior_position:
        return (position / prior_position - 1.0) * 100.0 if position is not None else None
    change = latest.get("change_in_short_position")
    if position is None or change is None or position == change:
        return None
    return (change / (position - change)) * 100.0


def build_ticker_entry(
    ticker: str,
    short_interest_rows: list[dict[str, Any]],
    squeeze: Optional[dict[str, Any]],
    *,
    today: Optional[date] = None,
) -> Optional[dict[str, Any]]:
    """One scoreboard row: crowding vintage, magnitude, and both gate readings."""
    if not short_interest_rows:
        return None

    latest = short_interest_rows[0]
    prior = short_interest_rows[1] if len(short_interest_rows) > 1 else None
    days_to_cover = latest.get("days_to_cover")
    squeeze_score = squeeze.get("score") if squeeze else None
    intensity = crowding_intensity(days_to_cover, squeeze_score)

    return {
        "ticker": ticker.upper(),
        "settlement_date": latest["settlement_date"],
        "settlement_age_days": settlement_age_days(latest["settlement_date"], today=today),
        "prior_settlement_date": prior["settlement_date"] if prior else None,
        "short_position": latest.get("short_position"),
        "prior_short_position": prior.get("short_position") if prior else None,
        "change_in_short_position": latest.get("change_in_short_position"),
        "short_position_change_pct": _change_pct(latest, prior),
        "average_daily_volume": latest.get("average_daily_volume"),
        "days_to_cover": days_to_cover,
        "crowding_tier": crowding_tier(days_to_cover),
        "squeeze_score": squeeze_score,
        "squeeze_rank": squeeze.get("rank") if squeeze else None,
        "base_score": squeeze.get("base_score") if squeeze else None,
        "catalyst_boost": squeeze.get("catalyst_boost") if squeeze else None,
        "short_interest_pct": squeeze.get("short_interest_pct") if squeeze else None,
        "short_interest_pct_basis": squeeze.get("short_interest_pct_basis") if squeeze else None,
        "market_cap": squeeze.get("market_cap") if squeeze else None,
        "factors": squeeze.get("factors") if squeeze else None,
        "readings": {
            "upside_convexity": intensity,
            "short_leg_tail_risk": intensity,
        },
        "borrow": {"source": "ib-short-availability", "resolved": False},
        "history": short_interest_rows,
    }


def rank_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scored names first (highest score), then the unscored by days-to-cover."""
    def sort_key(entry: dict[str, Any]) -> tuple:
        score = entry.get("squeeze_score")
        cover = entry.get("days_to_cover")
        return (0 if score is not None else 1, -(score or 0.0), -(cover or 0.0))

    return sorted(entries, key=sort_key)


# ── payload ───────────────────────────────────────────────────────

def build_payload(
    *,
    entries: list[dict[str, Any]],
    board: list[dict[str, Any]],
    scan_time: str,
    requests_used: int = 0,
) -> dict[str, Any]:
    ranked = rank_entries([e for e in entries if e])
    settlements = [e["settlement_date"] for e in ranked if e.get("settlement_date")]
    return {
        "scan_time": scan_time,
        "count": len(ranked),
        "latest_settlement_date": max(settlements) if settlements else None,
        "requests_used": requests_used,
        "entries": ranked,
        "board": board,
    }


def is_payload_valid(payload: dict[str, Any]) -> bool:
    """Write gate — never cache an empty scoreboard over a good one."""
    return bool(payload.get("entries"))


# A cycle covering less than this fraction of the resolved universe is not a
# cycle, it is a partial read that happens to have some rows in it.
MIN_COVERAGE_RATIO = 0.6
# Below this many requested tickers the ratio says nothing useful — one failure
# out of two is 50% — and the run is almost always a deliberate `--tickers`
# override rather than the scheduled watchlist sweep. The all-empty gate still
# applies at every size.
MIN_UNIVERSE_FOR_RATIO = 5


def has_sufficient_coverage(covered: int, requested: int) -> bool:
    if requested <= 0:
        return False
    if requested < MIN_UNIVERSE_FOR_RATIO:
        return covered > 0
    return covered / requested >= MIN_COVERAGE_RATIO





# ── universe ──────────────────────────────────────────────────────

def _watchlist_tickers() -> list[str]:
    try:
        watchlist = json.loads(WATCHLIST_JSON.read_text())
    except (OSError, ValueError):
        return []
    tickers: list[str] = []
    for item in watchlist.get("tickers") or []:
        symbol = item.get("ticker") if isinstance(item, dict) else item
        if isinstance(symbol, str) and symbol.strip():
            tickers.append(symbol.strip().upper())
    return tickers


def resolve_universe(tickers: Optional[Iterable[str]] = None) -> list[str]:
    """Explicit tickers, else the watchlist, else a liquid fallback — deduped, bounded."""
    candidates = [t.strip().upper() for t in (tickers or []) if t and t.strip()]
    if not candidates:
        candidates = _watchlist_tickers() or FALLBACK_TICKERS

    seen: set[str] = set()
    universe: list[str] = []
    for ticker in candidates:
        if ticker in seen:
            continue
        seen.add(ticker)
        universe.append(ticker)
        if len(universe) >= MAX_TICKERS:
            break
    return universe


# ── persistence ───────────────────────────────────────────────────

def expand_values_clause(single_row_sql: str, row_count: int) -> str:
    """Repeat a single-row upsert's VALUES tuple so one statement writes many rows.

    Keeps the upsert defined once: the ON CONFLICT clause and column list are
    never restated for the bulk path.
    """
    head, _, rest = single_row_sql.partition("VALUES")
    placeholders, _, conflict_clause = rest.partition(")")
    tuple_sql = f"{placeholders.strip()})"
    return f"{head}VALUES {', '.join([tuple_sql] * row_count)}{conflict_clause}"


def _chunked_upsert(single_row_sql: str, rows: list[tuple]) -> None:
    """Multi-row chunked INSERTs — executemany is one round-trip PER ROW on Hrana."""
    if not rows:
        return
    from db.client import get_db

    db = get_db()
    for start in range(0, len(rows), DB_INSERT_CHUNK_ROWS):
        chunk = rows[start:start + DB_INSERT_CHUNK_ROWS]
        params: list[Any] = []
        for row in chunk:
            params.extend(row)
        db.execute(expand_values_clause(single_row_sql, len(chunk)), tuple(params))
    db.commit()


def _short_interest_args(payload: dict[str, Any], recorded_at: str) -> list[tuple]:
    rows: list[tuple] = []
    for entry in payload["entries"]:
        for row in entry.get("history") or []:
            rows.append(
                (
                    entry["ticker"],
                    row["settlement_date"],
                    row.get("short_position"),
                    row.get("change_in_short_position"),
                    row.get("average_daily_volume"),
                    row.get("days_to_cover"),
                    recorded_at,
                )
            )
    return rows


def _squeeze_args(payload: dict[str, Any], recorded_at: str) -> list[tuple]:
    return [
        (
            entry["ticker"],
            entry.get("squeeze_score"),
            entry.get("squeeze_rank"),
            entry.get("base_score"),
            entry.get("catalyst_boost"),
            entry.get("short_interest_pct"),
            entry.get("short_interest_pct_basis"),
            entry.get("market_cap"),
            json.dumps(entry.get("factors") or {}),
            entry.get("settlement_date"),
            recorded_at,
        )
        for entry in payload["entries"]
        if entry.get("squeeze_score") is not None
    ]


def _write_db_cache(payload: dict[str, Any], scan_time: str) -> None:
    """Best-effort Turso mirror; data/equibles_short_crowding.json is the fallback."""
    try:
        from db import writer
    except ImportError:
        return
    try:
        writer.ensure_no_replica_for_writers()
        _chunked_upsert(SHORT_INTEREST_UPSERT_SQL, _short_interest_args(payload, scan_time))
        _write_squeeze_rows(payload, scan_time)
        writer.upsert_scan_snapshot(SERVICE, scan_time, payload)
    except Exception as exc:  # noqa: BLE001 — best-effort mirror
        print(f"[short-crowding] db cache non-fatal: {exc}", file=sys.stderr)


def _write_squeeze_rows(payload: dict[str, Any], scan_time: str) -> None:
    rows = _squeeze_args(payload, scan_time)
    if not rows:
        return
    from db.client import get_db

    db = get_db()
    for args in rows:
        db.execute(SQUEEZE_SCORE_UPSERT_SQL, args)
    db.commit()


def _record_health(state: str, scan_time: str, error: Optional[dict[str, Any]] = None) -> None:
    """Heartbeat EVERY cycle — a writer that only writes on error latches error."""
    try:
        from db import writer
        writer.record_service_health(SERVICE, state, finished_at=scan_time, error=error)
    except Exception as exc:  # noqa: BLE001 — health write is best-effort
        print(f"[short-crowding] health write non-fatal: {exc}", file=sys.stderr)


def _write_json_cache(payload: dict[str, Any]) -> None:
    SHORT_CROWDING_JSON.parent.mkdir(parents=True, exist_ok=True)
    SHORT_CROWDING_JSON.write_text(json.dumps(payload, indent=2))


# ── orchestration ─────────────────────────────────────────────────

def _fetch_entry(client: Any, ticker: str, today: date) -> Optional[dict[str, Any]]:
    """One ticker's scoreboard row. A single failure must not abort the cycle.

    A rate limit or a rejected key is NOT a single failure — every remaining
    ticker fails for the same reason — so those propagate. R-295.
    """
    # No swallow here: the caller's loop does the accounting, so the REASON a
    # ticker dropped out survives instead of becoming an indistinguishable
    # `None`. Cycle-fatal errors pass through it untouched.
    interest = client.get_short_interest(ticker, limit=SETTLEMENT_HISTORY_LIMIT)

    try:
        squeeze = normalize_squeeze_score(client.get_short_squeeze_scores(ticker=ticker))
    except Exception as exc:  # noqa: BLE001 — score is optional context
        print(f"[short-crowding] {ticker} squeeze score failed: {exc}", file=sys.stderr)
        squeeze = None

    return build_ticker_entry(ticker, normalize_short_interest_rows(interest), squeeze, today=today)


def _fetch_board(client: Any) -> list[dict[str, Any]]:
    try:
        snapshot = client.get_short_interest_snapshot(
            min_average_daily_volume=BOARD_MIN_AVERAGE_DAILY_VOLUME,
            sort_by="daysToCover",
            limit=BOARD_ROW_LIMIT,
        )
    except Exception as exc:  # noqa: BLE001 — the board is supplementary
        print(f"[short-crowding] market-wide board failed: {exc}", file=sys.stderr)
        return []
    return normalize_board_rows(snapshot)


def run(
    client: Optional[Any] = None,
    *,
    tickers: Optional[Iterable[str]] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Fetch, normalize, gate, cache, and return the short-crowding payload."""
    now = now or datetime.now(timezone.utc)
    scan_time = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    today = now.astimezone(timezone.utc).date()
    universe: list[str] = []
    errors: list[dict[str, Any]] = []

    from clients.equibles_client import EquiblesAuthError, EquiblesRateLimitError

    # Both subclass EquiblesAPIError; neither is a per-ticker condition.
    _CYCLE_FATAL = (EquiblesAuthError, EquiblesRateLimitError)

    # Client construction is INSIDE the health-reporting try: an absent or
    # rejected EQUIBLES_API_KEY raises EquiblesAuthError from
    # EquiblesClient.__init__, and outside the try that killed the oneshot
    # before any _record_health call, so NO service_health row was written at
    # all. The service is registered for freshness, so a row that never
    # arrives is silent rather than stale and nothing alerts.
    try:
        if client is None:
            from clients.equibles_client import EquiblesClient
            client = EquiblesClient()

        requests_before = getattr(client, "request_count", 0)
        universe = resolve_universe(tickers)
        # Error accounting the loop did not have AT ALL: a comprehension that
        # drops every falsy entry cannot tell "this ticker has no short
        # interest" from "this ticker's fetch failed". R-295.
        entries = []
        for ticker in universe:
            try:
                entry = _fetch_entry(client, ticker, today)
            except _CYCLE_FATAL:
                raise
            except Exception as exc:  # noqa: BLE001 — per-ticker isolation
                print(f"[short-crowding] {ticker} failed: {exc}", file=sys.stderr)
                errors.append({"ticker": ticker, "error": str(exc)})
                continue
            if entry:
                entries.append(entry)

        board = _fetch_board(client)

        payload = build_payload(
            entries=entries,
            board=board,
            scan_time=scan_time,
            requests_used=getattr(client, "request_count", 0) - requests_before,
        )
        payload["errors"] = errors
    except Exception as exc:  # noqa: BLE001 — re-raised; the row must exist first
        _record_health(
            "error",
            scan_time,
            error={
                "message": f"cycle aborted: {exc}",
                "requested": len(universe),
                "failed": len(errors),
            },
        )
        raise

    if not is_payload_valid(payload):
        _record_health(
            "error",
            scan_time,
            error={"message": "no short-interest rows returned", "tickers": len(universe)},
        )
        return {**payload, "missing": True}

    # Coverage gate: `is_payload_valid` passes on ONE entry out of forty, so a
    # cycle that served one ticker was written as `ok` AND replaced the
    # complete scoreboard underneath it. R-295.
    covered = len(payload.get("entries") or [])
    if not has_sufficient_coverage(covered, len(universe)):
        _record_health(
            "error",
            scan_time,
            error={
                "message": (
                    f"partial cycle: {covered}/{len(universe)} tickers covered, "
                    f"below the {MIN_COVERAGE_RATIO:.0%} floor"
                ),
                "requested": len(universe),
                "covered": covered,
                "failed": len(errors),
            },
        )
        return {**payload, "partial": True}

    _write_db_cache(payload, scan_time)
    _write_json_cache(payload)
    _record_health(
        "ok",
        scan_time,
        error={"requested": len(universe), "covered": covered, "failed": len(errors)}
        if errors
        else None,
    )
    return payload


# ── CLI ───────────────────────────────────────────────────────────

def _print_summary(payload: dict[str, Any]) -> None:
    print(
        f"\nSHORT CROWDING — {payload['count']} tickers, settlement "
        f"{payload.get('latest_settlement_date') or 'n/a'}",
        file=sys.stderr,
    )
    for entry in payload["entries"][:10]:
        score = entry["squeeze_score"]
        cover = entry["days_to_cover"]
        print(
            f"  {entry['ticker']:<6} score {score if score is not None else '---':>5}  "
            f"dtc {cover if cover is not None else '---':>5}  {entry['crowding_tier']}",
            file=sys.stderr,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Equibles short crowding / squeeze scoreboard")
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    parser.add_argument("--tickers", help="Comma-separated ticker override")
    args = parser.parse_args()

    payload = run(tickers=args.tickers.split(",") if args.tickers else None)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_summary(payload)
    if payload.get("missing"):
        sys.exit(1)


if __name__ == "__main__":
    main()
