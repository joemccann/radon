#!/usr/bin/env python3
"""13F smart-money depth layer — Equibles institutional positioning.

Complements ``scripts/fetch_informed_flow.py``, which already carries the THIN
UW institutional summary (ownership_pct / total_institutions / report_date).
This fetcher adds the position-level depth UW does not publish: who holds the
name, how much, what they did last quarter, and which curated super investors
are among them.

⚠ 13F IS CONTEXT, NEVER A TRIGGER. Filings are quarterly and land up to 45 days
after quarter end, so on their own they fail Gate 2 ("a specific, data-backed
signal that hasn't moved price"). The vintage is encoded in the PAYLOAD
(``disclosure``: quarter, statutory filing deadline, age in days, staleness
tier, ``context_only: true``) so the honesty travels with the data instead of
living only in a component.

Sources (EquiblesClient methods, one billed request each):
  - get_institutional_holders(ticker)   -> position-level holders
  - get_institutional_ownership(ticker) -> aggregate ownership by quarter
  - get_institutional_activity(ticker)  -> largest buyers / sellers QoQ
  - get_13f_most_held(sort="filersDelta")            -> market-wide, cached
  - get_13f_market_activity(bucket="new-positions")  -> market-wide, cached
  - get_13f_market_activity(bucket="sold-out-positions")
  - get_super_investors()                            -> market-wide, cached

The four market-wide calls are fetched ONCE per cycle and shared across every
ticker (``run_many``) — per-ticker they would be pure waste against the daily
allowance. Per-source failures are swallowed so one dead endpoint degrades a
section instead of the surface.

Percent fields arrive already normalized: EquiblesClient rewrites the API's
fraction fields to unambiguous '...Pct' keys at its boundary, so every percent
here is 0-100 and no fraction ever reaches this module.

Output is dual-written to Turso (equibles_13f_holders + equibles_13f_snapshots)
and data/equibles_13f/{TICKER}.json. A structurally empty payload is NOT
cached — that is the signature of a swallowed upstream failure.

Usage:
    python3 scripts/fetch_equibles_smart_money_13f.py AAPL MSFT
    python3 scripts/fetch_equibles_smart_money_13f.py AAPL --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
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

SERVICE = "equibles-13f"
CACHE_DIR = _PROJECT_DIR / "data" / "equibles_13f"

FILING_LAG_DAYS = 45          # SEC statutory 13F deadline after quarter end
FRESH_MAX_AGE_DAYS = 60       # newest possible vintage, just filed
CURRENT_MAX_AGE_DAYS = 135    # still newest until the next quarter's deadline

GATE_NOTE = (
    "Quarterly, filed up to 45 days after quarter end. Fails Gate 2 on its own "
    "(the price has already absorbed it). Read as positioning context, never as "
    "an entry trigger."
)

HOLDERS_PER_PAGE = 500        # endpoint maximum; one request covers most names
OWNERSHIP_PERIODS = 8         # two years of quarters for the QoQ series
ACTIVITY_LIMIT = 10
MARKET_LIST_LIMIT = 100
MAX_HOLDERS_IN_PAYLOAD = 25
TOP_HOLDERS_IN_HEADLINE = 5

NEW_POSITIONS_BUCKET = "new-positions"
SOLD_OUT_BUCKET = "sold-out-positions"

_HOLDER_COLUMNS = (
    "ticker, report_date, cik, institution, shares, value_usd, "
    "pct_of_institutional_total, position_type, change_in_shares, change_type, recorded_at"
)
_HOLDER_ROW_PLACEHOLDER = "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
_HOLDER_CONFLICT_CLAUSE = """
ON CONFLICT(ticker, report_date, cik) DO UPDATE SET
  institution                = excluded.institution,
  shares                     = excluded.shares,
  value_usd                  = excluded.value_usd,
  pct_of_institutional_total = excluded.pct_of_institutional_total,
  position_type              = excluded.position_type,
  change_in_shares           = excluded.change_in_shares,
  change_type                = excluded.change_type,
  recorded_at                = excluded.recorded_at
"""

SNAPSHOT_UPSERT_SQL = """
INSERT INTO equibles_13f_snapshots (ticker, report_date, scan_time, payload)
VALUES (?, ?, ?, ?)
ON CONFLICT(ticker, report_date) DO UPDATE SET
  scan_time = excluded.scan_time,
  payload   = excluded.payload
"""

_HOLDER_INSERT_CHUNK_ROWS = 200


def _holder_upsert_sql(row_count: int) -> str:
    """Idempotent multi-row holder upsert for ``row_count`` rows."""
    values = ", ".join(_HOLDER_ROW_PLACEHOLDER for _ in range(row_count))
    return (
        f"INSERT INTO equibles_13f_holders ({_HOLDER_COLUMNS}) VALUES {values}"
        f"{_HOLDER_CONFLICT_CLAUSE}"
    )


# Single-row form, for tests and one-off writes.
HOLDER_UPSERT_SQL = _holder_upsert_sql(1)


# ── defensive field access ────────────────────────────────────────


def _rows(payload: Any) -> list[dict[str, Any]]:
    """Row list from an Equibles envelope, a bare list, or nothing."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "results", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)]
    return []


def _first(row: dict[str, Any], *keys: str) -> Any:
    """First present, non-null value among renamed field candidates."""
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return None


def _to_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    number = _to_float(value)
    return int(number) if number is not None else None


def _to_date_string(value: Any) -> Optional[str]:
    if not isinstance(value, str) or len(value) < 10:
        return None
    candidate = value[:10]
    try:
        date.fromisoformat(candidate)
    except ValueError:
        return None
    return candidate


_PUNCTUATION = re.compile(r"[^A-Z0-9 ]+")
_WHITESPACE = re.compile(r"\s+")


def _match_key(name: Any) -> str:
    """Punctuation- and case-insensitive institution key ('Vanguard, Inc.')."""
    if not isinstance(name, str):
        return ""
    return _WHITESPACE.sub(" ", _PUNCTUATION.sub(" ", name.upper())).strip()


def _now_iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# ── disclosure vintage ────────────────────────────────────────────


def _quarter_label(report_date: str) -> str:
    quarter = (int(report_date[5:7]) - 1) // 3 + 1
    return f"Q{quarter} {report_date[:4]}"


def _staleness(age_days: int) -> str:
    if age_days <= FRESH_MAX_AGE_DAYS:
        return "fresh"
    if age_days <= CURRENT_MAX_AGE_DAYS:
        return "current"
    return "stale"


def build_disclosure(
    report_date: Optional[str],
    *,
    now: datetime,
    filing_window_open: bool = False,
    combined_quarter: bool = False,
) -> Optional[dict[str, Any]]:
    """Vintage + staleness envelope carried in the payload, not just the view.

    ``filing_window_open`` means filers are still submitting for this quarter,
    so the counts below are a partial tally that will keep rising until the
    deadline. ``combined_quarter`` is Equibles' flag for a vintage assembled
    from more than one filing period.
    """
    quarter_end = _to_date_string(report_date)
    if quarter_end is None:
        return None

    age_days = (now.astimezone(timezone.utc).date() - date.fromisoformat(quarter_end)).days
    return {
        "report_date": quarter_end,
        "quarter": _quarter_label(quarter_end),
        "filed_by": (date.fromisoformat(quarter_end) + timedelta(days=FILING_LAG_DAYS)).isoformat(),
        "age_days": age_days,
        "staleness": _staleness(age_days),
        "cadence": "quarterly",
        "lag_days": FILING_LAG_DAYS,
        "filing_window_open": filing_window_open,
        "combined_quarter": combined_quarter,
        "context_only": True,
        "gate_note": GATE_NOTE,
    }


# ── normalization ─────────────────────────────────────────────────


def _institution_name(row: dict[str, Any]) -> str:
    name = _first(row, "institutionName", "institution", "filerName", "holderName", "name")
    return str(name) if name else ""


def _cik(row: dict[str, Any]) -> str:
    """Filer CIK with leading zeros stripped — Equibles pads inconsistently."""
    cik = _first(row, "cik", "filerCik", "institutionCik")
    return str(cik).lstrip("0") if cik else ""


def _change_type(row: dict[str, Any], change_in_shares: Optional[float]) -> Optional[str]:
    declared = _first(row, "changeType", "positionChange", "activityType")
    if isinstance(declared, str) and declared.strip():
        return declared.strip().upper()
    if _first(row, "isNewPosition") is True:
        return "NEW"
    if _first(row, "isSoldOut") is True:
        return "EXITED"
    if change_in_shares is None:
        return None
    if change_in_shares > 0:
        return "ADDED"
    if change_in_shares < 0:
        return "TRIMMED"
    return "HELD"


def normalize_holders(payload: Any) -> list[dict[str, Any]]:
    """Position-level holders, ranked by position value descending.

    ``pct_of_institutional_total`` is Equibles' ``percentOfTotal``: the filer's
    share of the REPORTED 13F position, not of shares outstanding. The holders
    endpoint carries no quarter-over-quarter delta — that is joined in from
    institutional-activity by ``attach_holder_activity``.
    """
    meta = payload.get("meta") if isinstance(payload, dict) else None
    meta_report_date = _to_date_string(_first(meta or {}, "reportDate"))

    holders: list[dict[str, Any]] = []
    for row in _rows(payload):
        holders.append(
            {
                "cik": _cik(row),
                "institution": _institution_name(row),
                "shares": _to_float(_first(row, "shares", "sharesHeld", "position")),
                "value_usd": _to_float(_first(row, "value", "marketValue", "valueUsd", "positionValue")),
                "pct_of_institutional_total": _to_float(_first(row, "percentOfTotal", "pctOfTotal")),
                "position_type": _first(row, "positionType", "securityType"),
                "change_in_shares": None,
                "change_type": None,
                "report_date": _to_date_string(_first(row, "reportDate", "periodOfReport")) or meta_report_date,
            }
        )
    holders.sort(key=lambda h: h["value_usd"] if h["value_usd"] is not None else float("-inf"), reverse=True)
    return holders


def attach_holder_activity(
    holders: list[dict[str, Any]], activity: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Join each holder's QoQ move in from the largest-movers lists, by CIK.

    Holders outside the movers page keep a null change — an unknown move is
    not a HELD.
    """
    moves = {
        participant["cik"]: participant
        for side in ("buyers", "sellers")
        for participant in activity.get(side, [])
        if participant["cik"]
    }
    for holder in holders:
        move = moves.get(holder["cik"])
        if move is None:
            continue
        holder["change_in_shares"] = move["shares_changed"]
        holder["change_type"] = move["change_type"]
    return holders


def normalize_ownership_series(payload: Any) -> list[dict[str, Any]]:
    """Aggregate ownership by quarter, ascending, with the QoQ filer delta.

    Equibles publishes ``institutions`` per quarter but no filer delta, so the
    diff of consecutive quarters is computed here — the whole reason to ask for
    a series rather than a snapshot.
    """
    series: list[dict[str, Any]] = []
    for row in _rows(payload):
        series.append(
            {
                "report_date": _to_date_string(_first(row, "reportDate", "periodOfReport")),
                "filer_count": _to_int(
                    _first(row, "institutions", "filerCount", "filers", "institutionCount", "totalInstitutions")
                ),
                "filer_count_delta": _to_int(_first(row, "filerCountDelta", "filersDelta", "filerDelta")),
                "shares_held": _to_float(_first(row, "totalShares", "sharesHeld", "shares")),
                "value_usd": _to_float(_first(row, "totalValue", "value", "marketValue", "valueUsd")),
                "shares_qoq_pct": _to_float(_first(row, "changePercent", "sharesChangePercent")),
                "new_positions": _to_int(_first(row, "newPositions", "newPositionCount")),
                "exited_positions": _to_int(_first(row, "exitedPositions", "soldOutPositions", "closedPositions")),
                "combined_quarter": _first(row, "isCombinedQuarter") is True,
            }
        )
    series.sort(key=lambda r: r["report_date"] or "")
    return _fill_filer_deltas(series)


def _fill_filer_deltas(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for index, row in enumerate(series):
        if row["filer_count_delta"] is not None or index == 0:
            continue
        prior = series[index - 1]["filer_count"]
        if prior is not None and row["filer_count"] is not None:
            row["filer_count_delta"] = row["filer_count"] - prior
    return series


def _normalize_participants(payload: Any, *keys: str) -> list[dict[str, Any]]:
    source = payload if isinstance(payload, dict) else {}
    participants: list[dict[str, Any]] = []
    for row in _rows(_first(source, *keys)):
        shares_changed = _to_float(_first(row, "deltaShares", "changeInShares", "sharesChange"))
        participants.append(
            {
                "cik": _cik(row),
                "institution": _institution_name(row),
                "shares_changed": shares_changed,
                "value_usd": _to_float(_first(row, "deltaValue", "value", "marketValue", "valueUsd")),
                "current_shares": _to_float(_first(row, "currentShares")),
                "previous_shares": _to_float(_first(row, "previousShares")),
                "change_type": _change_type(row, shares_changed),
            }
        )
    return participants


def normalize_activity(payload: Any) -> dict[str, Any]:
    """Largest buyers and sellers versus the prior quarter.

    ``buyer_count`` / ``seller_count`` are the endpoint's authoritative totals
    across every filer. ``new_positions`` / ``exited_positions`` are counted
    from the returned movers page unless the source supplies its own aggregate,
    so ``positions_scope`` says which — reporting a page-scoped count as a
    universe count would be a quiet lie.
    """
    source = payload if isinstance(payload, dict) else {}
    buyers = _normalize_participants(source, "buyers", "topBuyers", "largestBuyers")
    sellers = _normalize_participants(source, "sellers", "topSellers", "largestSellers")

    filed_new = _to_int(_first(source, "totalNewPositions", "newPositions"))
    filed_exited = _to_int(_first(source, "totalSoldOutPositions", "exitedPositions"))
    is_filed = filed_new is not None or filed_exited is not None

    return {
        "report_date": _to_date_string(_first(source, "reportDate")),
        "previous_report_date": _to_date_string(_first(source, "previousReportDate")),
        "buyers": buyers,
        "sellers": sellers,
        "buyer_count": _to_int(_first(source, "totalBuyers")),
        "seller_count": _to_int(_first(source, "totalSellers")),
        "new_positions": filed_new if is_filed else _count_flagged(buyers, "NEW"),
        "exited_positions": filed_exited if is_filed else _count_flagged(sellers, "EXITED"),
        "positions_scope": "filed" if is_filed else "top-movers",
    }


def _count_flagged(participants: list[dict[str, Any]], change_type: str) -> Optional[int]:
    if not participants:
        return None
    return sum(1 for p in participants if p["change_type"] == change_type)


def super_investor_positions(roster: Any, holders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Curated managers that actually appear in this ticker's holder list.

    Matched on CIK where both sides publish one, else on a punctuation- and
    case-insensitive firm name. A roster manager with no matching position is
    omitted — never invented as a zero. The roster carries its OWN ``asOf``
    quarter, which commonly trails the holder vintage, so it travels with the
    position instead of being silently attributed to the newer quarter.
    """
    by_cik = {h["cik"]: h for h in holders if h["cik"]}
    by_name = {_match_key(h["institution"]): h for h in holders if h["institution"]}

    named: list[dict[str, Any]] = []
    for row in _rows(roster):
        investor = _first(row, "managerName", "name", "investorName", "manager")
        firm = _first(row, "firmName", "institutionName", "institution", "fundName", "name")
        holder = by_cik.get(_cik(row)) or by_name.get(_match_key(firm))
        if holder is None:
            continue
        named.append(
            {
                "investor": str(investor) if investor else holder["institution"],
                "institution": holder["institution"],
                "cik": holder["cik"],
                "shares": holder["shares"],
                "value_usd": holder["value_usd"],
                "pct_of_institutional_total": holder["pct_of_institutional_total"],
                "change_type": holder["change_type"],
                "roster_as_of": _to_date_string(_first(row, "asOf")),
                "roster_is_stale": _first(row, "isStale") is True,
            }
        )
    return named


# ── market-wide context (fetched once per cycle) ──────────────────


def _cycle_fatal_errors() -> tuple[type[BaseException], ...]:
    """Errors that kill the whole cycle rather than one section.

    A rejected key or an exhausted daily allowance makes EVERY call fail;
    swallowing them would write an empty payload and heartbeat 'ok', hiding a
    dead integration behind a plausible-looking empty surface.
    """
    try:
        from clients.equibles_client import EquiblesAuthError, EquiblesRateLimitError
    except ImportError:  # pragma: no cover — stripped env
        return ()
    return (EquiblesAuthError, EquiblesRateLimitError)


def _safe_call(fetch, label: str) -> Any:
    try:
        return fetch()
    except _cycle_fatal_errors():
        raise
    except Exception as exc:  # noqa: BLE001 — one dead endpoint degrades one section
        print(f"[{SERVICE}] {label} unavailable (non-fatal): {exc}", file=sys.stderr)
        return None


def fetch_market_snapshot(client: Any) -> dict[str, Any]:
    """Market-wide 13F lists, shared by every ticker in the cycle."""
    most_held = _safe_call(
        lambda: client.get_13f_most_held(sort="filersDelta", limit=MARKET_LIST_LIMIT),
        "13f/most-held",
    )
    new_positions = _safe_call(
        lambda: client.get_13f_market_activity(bucket=NEW_POSITIONS_BUCKET, limit=MARKET_LIST_LIMIT),
        f"13f/market-activity {NEW_POSITIONS_BUCKET}",
    )
    sold_out = _safe_call(
        lambda: client.get_13f_market_activity(bucket=SOLD_OUT_BUCKET, limit=MARKET_LIST_LIMIT),
        f"13f/market-activity {SOLD_OUT_BUCKET}",
    )
    super_investors = _safe_call(client.get_super_investors, "super-investors")

    return {
        "report_date": _market_report_date(most_held, new_positions, sold_out),
        # The newest quarter keeps accumulating filings until the 45-day
        # deadline passes; an open window means the vintage is INCOMPLETE.
        "filing_window_open": _first(most_held if isinstance(most_held, dict) else {}, "isFilingWindowOpen") is True,
        "most_held": _rows(most_held),
        "new_positions": _rows(new_positions),
        "sold_out": _rows(sold_out),
        "super_investors": _rows(super_investors),
    }


def _market_report_date(*payloads: Any) -> Optional[str]:
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        stamp = _to_date_string(_first(meta, "reportDate")) or _to_date_string(
            _first(payload, "reportDate")
        )
        if stamp:
            return stamp
    return None


def _rank_of(ticker: str, rows: list[dict[str, Any]]) -> Optional[int]:
    for index, row in enumerate(rows):
        if str(_first(row, "ticker", "symbol") or "").upper() == ticker:
            return index + 1
    return None


def build_market_context(ticker: str, market: dict[str, Any]) -> dict[str, Any]:
    """Where this ticker sits in the market-wide 13F flow buckets."""
    upper = ticker.upper()
    most_held = market.get("most_held") or []
    rank = _rank_of(upper, most_held)
    entry = most_held[rank - 1] if rank else {}
    return {
        "report_date": market.get("report_date"),
        "most_held_rank": rank,
        "most_held_filers": _to_int(_first(entry, "filerCount", "filers")) if rank else None,
        "most_held_filers_delta": (
            _to_int(_first(entry, "deltaFilerCount", "filersDelta", "filerCountDelta")) if rank else None
        ),
        "in_new_positions_bucket": _rank_of(upper, market.get("new_positions") or []) is not None,
        "in_sold_out_bucket": _rank_of(upper, market.get("sold_out") or []) is not None,
    }


# ── payload assembly ──────────────────────────────────────────────


def _resolve_report_date(
    holders: list[dict[str, Any]],
    ownership: list[dict[str, Any]],
    activity_payload: Any,
    market: dict[str, Any],
) -> Optional[str]:
    """Newest vintage any source agrees on; the payload is keyed by it."""
    candidates = [h["report_date"] for h in holders if h["report_date"]]
    candidates += [r["report_date"] for r in ownership if r["report_date"]]
    stamp = _to_date_string(
        _first(activity_payload if isinstance(activity_payload, dict) else {}, "reportDate")
    )
    if stamp:
        candidates.append(stamp)
    if market.get("report_date"):
        candidates.append(market["report_date"])
    return max(candidates) if candidates else None


def _build_headline(
    ownership: list[dict[str, Any]],
    activity: dict[str, Any],
    holders: list[dict[str, Any]],
    named_investors: list[dict[str, Any]],
    holders_meta: dict[str, Any],
) -> dict[str, Any]:
    """The five named metrics, each taken from its most authoritative source."""
    latest = ownership[-1] if ownership else {}
    return {
        "filer_count": latest.get("filer_count") or _to_int(_first(holders_meta, "totalInstitutions")),
        "filer_count_delta": latest.get("filer_count_delta"),
        "buyer_count": activity.get("buyer_count"),
        "seller_count": activity.get("seller_count"),
        "new_positions": latest.get("new_positions") or activity.get("new_positions"),
        "exited_positions": latest.get("exited_positions") or activity.get("exited_positions"),
        "positions_scope": activity.get("positions_scope"),
        "shares_qoq_pct": latest.get("shares_qoq_pct"),
        "top_holders": holders[:TOP_HOLDERS_IN_HEADLINE],
        "super_investors": named_investors,
    }


def build_payload(
    ticker: str,
    *,
    client: Any,
    market: dict[str, Any],
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Fetch the three per-ticker surfaces and assemble one normalized payload."""
    now = now or datetime.now(timezone.utc)
    upper = ticker.upper()

    holders_payload = _safe_call(
        lambda: client.get_institutional_holders(upper, limit=HOLDERS_PER_PAGE),
        f"institutional-holders {upper}",
    )
    ownership = normalize_ownership_series(
        _safe_call(
            lambda: client.get_institutional_ownership(upper, periods=OWNERSHIP_PERIODS),
            f"institutional-ownership {upper}",
        )
    )
    activity_payload = _safe_call(
        lambda: client.get_institutional_activity(upper, limit=ACTIVITY_LIMIT),
        f"institutional-activity {upper}",
    )

    activity = normalize_activity(activity_payload)
    holders = attach_holder_activity(normalize_holders(holders_payload), activity)
    named_investors = super_investor_positions(market.get("super_investors"), holders)
    report_date = _resolve_report_date(holders, ownership, activity_payload, market)
    holders_meta = (holders_payload or {}).get("meta") if isinstance(holders_payload, dict) else None
    holders_meta = holders_meta if isinstance(holders_meta, dict) else {}

    return {
        "ticker": upper,
        "scan_time": _now_iso(now),
        "report_date": report_date,
        "disclosure": build_disclosure(
            report_date,
            now=now,
            filing_window_open=bool(market.get("filing_window_open")),
            combined_quarter=bool(ownership[-1]["combined_quarter"]) if ownership else False,
        ),
        "headline": _build_headline(ownership, activity, holders, named_investors, holders_meta),
        "holders": holders[:MAX_HOLDERS_IN_PAYLOAD],
        "holder_count": _to_int(_first(holders_meta, "totalInstitutions")) or len(holders),
        "ownership_series": ownership,
        "activity": activity,
        "super_investors": named_investors,
        "market_context": build_market_context(upper, market),
    }


def has_signal(payload: dict[str, Any]) -> bool:
    """True when the ticker's own 13F surfaces carried data worth caching.

    Market-wide context deliberately does not count — it is present for every
    cycle and would mask a swallowed per-ticker failure.
    """
    activity = payload.get("activity") or {}
    return bool(
        payload.get("holders")
        or payload.get("ownership_series")
        or activity.get("buyers")
        or activity.get("sellers")
    )


def _empty_payload(ticker: str, now: datetime) -> dict[str, Any]:
    return {
        "ticker": ticker.upper(),
        "scan_time": _now_iso(now),
        "missing": True,
        "report_date": None,
        "disclosure": None,
        "headline": None,
        "holders": [],
        "holder_count": 0,
        "ownership_series": [],
        "activity": {"buyers": [], "sellers": []},
        "super_investors": [],
        "market_context": None,
    }


# ── persistence ───────────────────────────────────────────────────


def _holder_params(
    ticker: str, report_date: str, chunk: list[dict[str, Any]], recorded_at: str
) -> list[Any]:
    params: list[Any] = []
    for holder in chunk:
        params.extend(
            (
                ticker,
                report_date,
                holder["cik"],
                holder["institution"],
                holder["shares"],
                holder["value_usd"],
                holder["pct_of_institutional_total"],
                holder["position_type"],
                holder["change_in_shares"],
                holder["change_type"],
                recorded_at,
            )
        )
    return params


def _upsert_holder_rows(
    ticker: str, report_date: str, holders: list[dict[str, Any]], recorded_at: str
) -> None:
    """Durable position-level rows as chunked multi-row INSERTs.

    A liquid name reports thousands of filers; per-row statements are one Hrana
    round-trip each and 502 the direct-to-cloud pipeline (scripts/CLAUDE.md
    § Turso Hrana I/O Bounding).
    """
    if not holders:
        return
    from db.client import get_db

    db = get_db()
    # One commit covers N chunks, so a failure on chunk k used to leave
    # chunks 0..k-1 applied and k..N absent — a truncated set for that
    # (ticker, report_date), silently mixed with the previous quarter's rows
    # because the upsert is not preceded by a delete. Roll the whole write
    # back instead. R-227.
    try:
        for start in range(0, len(holders), _HOLDER_INSERT_CHUNK_ROWS):
            chunk = holders[start:start + _HOLDER_INSERT_CHUNK_ROWS]
            params = _holder_params(ticker, report_date, chunk, recorded_at)
            db.execute(_holder_upsert_sql(len(chunk)), tuple(params))
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:  # noqa: BLE001 — a dead connection is already rolled back
            pass
        raise


def _upsert_snapshot(ticker: str, report_date: str, scan_time: str, payload: dict[str, Any]) -> None:
    from db.client import get_db

    db = get_db()
    db.execute(SNAPSHOT_UPSERT_SQL, (ticker, report_date, scan_time, json.dumps(payload)))
    db.commit()


def _record_health(state: str) -> None:
    """Heartbeat EVERY cycle — a writer that only writes on error latches error."""
    try:
        from db import writer

        writer.ensure_no_replica_for_writers()
        writer.record_service_health(SERVICE, state, finished_at=_now_iso(datetime.now(timezone.utc)))
    except Exception as exc:  # noqa: BLE001 — best-effort mirror
        print(f"[{SERVICE}] health heartbeat non-fatal: {exc}", file=sys.stderr)


def _write_db_cache(payload: dict[str, Any]) -> None:
    try:
        from db import writer

        writer.ensure_no_replica_for_writers()
    except Exception:  # noqa: BLE001 — stripped env; disk cache still applies
        pass
    # Separate try blocks on purpose. equibles_13f_snapshots is the ONLY table
    # /api/equibles-smart-money-13f reads; the holder rows are depth nothing
    # queries yet. Sharing one try meant a single failed holder chunk (a liquid
    # name writes hundreds of rows) skipped the snapshot upsert and left the
    # ticker blank in the UI.
    holders_persisted = True
    try:
        _upsert_holder_rows(
            payload["ticker"], payload["report_date"], payload["holders"], payload["scan_time"]
        )
    except Exception as exc:  # noqa: BLE001 — best-effort mirror, disk is the fallback
        holders_persisted = False
        print(f"[{SERVICE}] holder rows non-fatal: {exc}", file=sys.stderr)
    # The snapshot carries the full `holders` array and `holder_count`, so
    # without this it asserted completeness the rows table did not have. R-227.
    payload["holders_persisted"] = holders_persisted
    try:
        _upsert_snapshot(
            payload["ticker"], payload["report_date"], payload["scan_time"], payload
        )
    except Exception as exc:  # noqa: BLE001 — best-effort mirror, disk is the fallback
        print(f"[{SERVICE}] snapshot non-fatal: {exc}", file=sys.stderr)


def _write_json_cache(payload: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{payload['ticker']}.json").write_text(json.dumps(payload, indent=2))


def _persist(payload: dict[str, Any]) -> None:
    """Gate every cache write on payload validity and a resolved vintage."""
    if not has_signal(payload) or not payload.get("report_date"):
        return
    _write_db_cache(payload)
    _write_json_cache(payload)


# ── orchestration ─────────────────────────────────────────────────


def _run_ticker(
    ticker: str,
    *,
    client: Any,
    market: dict[str, Any],
    now: datetime,
    persist: bool,
) -> dict[str, Any]:
    requests_before = getattr(client, "request_count", 0)
    payload = build_payload(ticker, client=client, market=market, now=now)
    if not has_signal(payload):
        payload = _empty_payload(ticker, now)
    elif persist:
        _persist(payload)
    payload["requests_used"] = getattr(client, "request_count", 0) - requests_before
    return payload


def _resolve_client(client: Optional[Any]) -> Any:
    if client is not None:
        return client
    from clients.equibles_client import EquiblesClient

    return EquiblesClient()


def run(
    ticker: str,
    *,
    client: Optional[Any] = None,
    market: Optional[dict[str, Any]] = None,
    now: Optional[datetime] = None,
    persist: bool = True,
) -> dict[str, Any]:
    """One ticker's 13F depth payload, persisted, with a cycle heartbeat."""
    now = now or datetime.now(timezone.utc)
    # Client construction is INSIDE the try: an absent or rejected
    # EQUIBLES_API_KEY raises EquiblesAuthError from EquiblesClient.__init__,
    # and outside the try that killed the oneshot before any health row was
    # written at all — silent, not stale, so nothing alerted.
    try:
        resolved = _resolve_client(client)
        requests_before = getattr(resolved, "request_count", 0)
        market = market if market is not None else fetch_market_snapshot(resolved)
        payload = _run_ticker(ticker, client=resolved, market=market, now=now, persist=persist)
    except Exception:
        _record_health("error")
        raise
    # Single-ticker runs bear the market-wide fetch alone; run_many amortizes it
    # across the cycle and leaves each payload with only its own per-ticker cost.
    payload["requests_used"] = getattr(resolved, "request_count", 0) - requests_before
    _record_health("ok")
    return payload


def run_many(
    tickers: Iterable[str],
    *,
    client: Optional[Any] = None,
    now: Optional[datetime] = None,
    persist: bool = True,
) -> list[dict[str, Any]]:
    """Whole cycle: one market-wide fetch shared across every ticker."""
    now = now or datetime.now(timezone.utc)
    try:
        resolved = _resolve_client(client)  # inside the try — see run()
        market = fetch_market_snapshot(resolved)
        payloads = [
            _run_ticker(t, client=resolved, market=market, now=now, persist=persist)
            for t in tickers
        ]
    except Exception:
        _record_health("error")
        raise
    _record_health("ok")
    return payloads


# ── CLI ───────────────────────────────────────────────────────────


def _print_summary(payload: dict[str, Any]) -> None:
    ticker = payload["ticker"]
    if payload.get("missing"):
        print(f"\n13F {ticker} — no institutional depth returned", file=sys.stderr)
        return

    disclosure = payload["disclosure"] or {}
    headline = payload["headline"] or {}
    print(
        f"\n13F {ticker} — {disclosure.get('quarter', '?')} vintage "
        f"({disclosure.get('age_days', '?')}d old, {disclosure.get('staleness', '?')}) "
        f"— CONTEXT ONLY",
        file=sys.stderr,
    )
    print(
        f"  filers   {headline.get('filer_count')} "
        f"(QoQ {headline.get('filer_count_delta')})",
        file=sys.stderr,
    )
    print(
        f"  churn    {headline.get('new_positions')} new / "
        f"{headline.get('exited_positions')} exited",
        file=sys.stderr,
    )
    for holder in headline.get("top_holders", [])[:3]:
        print(f"  holder   {holder['institution']}  {holder['change_type'] or ''}", file=sys.stderr)
    for named in headline.get("super_investors", []):
        print(f"  super    {named['investor']} via {named['institution']}", file=sys.stderr)
    print(f"  requests {payload.get('requests_used')}", file=sys.stderr)


def watchlist_tickers() -> list[str]:
    """Watchlist symbols from Turso, the canonical store."""
    from db.readers import read_watchlist_tickers

    return [str(s).upper() for s in read_watchlist_tickers()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Equibles 13F smart-money depth layer")
    parser.add_argument(
        "tickers", nargs="*", help="Ticker symbols (default: the Turso watchlist)"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    args = parser.parse_args()

    tickers = [t.upper() for t in args.tickers] or watchlist_tickers()
    if not tickers:
        # Exit 2 without a health row is invisible: the service is registered
        # for freshness, so the watchdog only ever sees a row that never came.
        _record_health("error")
        parser.error("no tickers given and the watchlist is empty")

    payloads = run_many(tickers)
    if args.json:
        print(json.dumps(payloads if len(payloads) > 1 else payloads[0], indent=2))
        return
    for payload in payloads:
        _print_summary(payload)


if __name__ == "__main__":
    main()
