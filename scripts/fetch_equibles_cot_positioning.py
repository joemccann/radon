#!/usr/bin/env python3
"""CFTC COT cross-asset positioning — Equibles /v1/cftc into Turso.

Radon's mandate names cross-asset positioning, but /regime is otherwise all
equity/vol. This is the futures-positioning leg: the weekly Commitments of
Traders report for a focus basket spanning equity indices, rates, the dollar,
gold, and crude.

THE METRIC
    Net non-commercial (speculator) position, ranked against its OWN trailing
    history (3 years of weekly reports). Extremes are CONTRARIAN: a speculative
    net in the top decile of its own range is crowded long, which reads bearish.
    Commercials are the hedgers and take the other side by construction, so both
    series are carried - the contrast is the information.

CADENCE
    The CFTC publishes Friday afternoon ET for the PRIOR Tuesday, so every row
    is structurally ~3 days stale on arrival. Callers render report_date and
    derive freshness from it; nothing here hardcodes a cadence string.

DEFENSIVE PARSING
    Built against the documented camelCase schema without a live key to verify
    against, so every field is resolved through a case-insensitive candidate
    list and nets fall back to long-minus-short. A renamed or absent field
    degrades one number to null instead of crashing the ingest.

Output is dual-written to Turso cot_positioning + scan_snapshots and to
data/equibles_cot_positioning.json (disk fallback).

Usage:
    python3 scripts/fetch_equibles_cot_positioning.py           # human summary
    python3 scripts/fetch_equibles_cot_positioning.py --json    # JSON to stdout
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

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

SERVICE = "equibles-cot-positioning"
COT_JSON = _PROJECT_DIR / "data" / "equibles_cot_positioning.json"

# Cross-asset basket, keyed to the CFTC market codes observed live 2026-08-13
# on /cftc/contracts. The endpoint returns ONLY marketCode / marketName /
# category, so an alias is not resolvable from the payload alone - the mapping
# has to be explicit. There is no dollar-index contract in the roster, so DX is
# deliberately absent rather than silently mismatched.
FOCUS_CONTRACTS: dict[str, str] = {
    "ES": "13874A",   # E-mini S&P 500 (CME)
    "NQ": "209742",   # E-mini Nasdaq-100 (CME)
    "RTY": "239742",  # E-mini Russell 2000 (CME)
    "VIX": "1170E1",  # VIX Futures (CBOE)
    "TY": "043602",   # 10-Year T-Notes (CBOT)
    "GC": "088691",   # Gold (COMEX)
    "WTI": "067651",  # Crude Oil, Light Sweet (NYMEX)
}

FOCUS_ALIASES: tuple[str, ...] = tuple(FOCUS_CONTRACTS)

HISTORY_LOOKBACK_DAYS = 365 * 3
HISTORY_PAGE_LIMIT = 500
HISTORY_MAX_PAGES = 2

# Below a year of weekly reports a percentile says more about the sample than
# about the market, so the statistics are withheld rather than guessed.
MIN_STATS_WEEKS = 52

EXTREME_PERCENTILE_HIGH = 90.0
EXTREME_PERCENTILE_LOW = 10.0

CROWDING_LONG = "CROWDED LONG"
CROWDING_SHORT = "CROWDED SHORT"
CROWDING_NEUTRAL = "NEUTRAL"
CROWDING_UNKNOWN = "UNKNOWN"

BIAS_BEARISH = "BEARISH"
BIAS_BULLISH = "BULLISH"
BIAS_NEUTRAL = "NEUTRAL"

_CONTRARIAN_BIAS = {CROWDING_LONG: BIAS_BEARISH, CROWDING_SHORT: BIAS_BULLISH}

_COT_UPSERT_HEAD = """
INSERT INTO cot_positioning
  (market_code, report_date, contract_name, category, open_interest,
   commercial_long, commercial_short, noncommercial_long, noncommercial_short,
   noncommercial_spread, net_commercial, net_noncommercial,
   net_noncommercial_pct_oi, recorded_at)
VALUES """

_COT_ROW_PLACEHOLDER = "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"

_COT_UPSERT_TAIL = """
ON CONFLICT(market_code, report_date) DO UPDATE SET
  contract_name            = excluded.contract_name,
  category                 = excluded.category,
  open_interest            = excluded.open_interest,
  commercial_long          = excluded.commercial_long,
  commercial_short         = excluded.commercial_short,
  noncommercial_long       = excluded.noncommercial_long,
  noncommercial_short      = excluded.noncommercial_short,
  noncommercial_spread     = excluded.noncommercial_spread,
  net_commercial           = excluded.net_commercial,
  net_noncommercial        = excluded.net_noncommercial,
  net_noncommercial_pct_oi = excluded.net_noncommercial_pct_oi,
  recorded_at              = excluded.recorded_at
"""

COT_UPSERT_SQL = _COT_UPSERT_HEAD + _COT_ROW_PLACEHOLDER + _COT_UPSERT_TAIL

# 14 bindings per row; 200 rows stays far under SQLite's variable ceiling while
# keeping a 3-year six-contract backfill to a handful of Hrana statements.
_DB_INSERT_CHUNK_ROWS = 200


def cot_upsert_sql(row_count: int) -> str:
    """Multi-row upsert statement — one Hrana round-trip per chunk, not per row."""
    if row_count < 1:
        raise ValueError("cot_upsert_sql needs at least one row")
    values = ", ".join(_COT_ROW_PLACEHOLDER for _ in range(row_count))
    return _COT_UPSERT_HEAD + values + _COT_UPSERT_TAIL


class CotIngestError(RuntimeError):
    """No usable COT positioning could be built — never cache this."""


# ── field resolution (defensive against renamed keys) ─────────────

_FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    "report_date": ("reportdate", "date", "asofdate", "weekdate", "reportweek"),
    "open_interest": ("openinterest", "openinterestall", "totalopeninterest"),
    # The wire uses the abbreviated forms (commLong / nonCommLong /
    # nonCommSpreads), verified live 2026-08-13; the spelled-out variants stay
    # as fallbacks.
    "commercial_long": (
        "commlong", "commerciallong", "commerciallongpositions", "commerciallongall",
    ),
    "commercial_short": (
        "commshort", "commercialshort", "commercialshortpositions", "commercialshortall",
    ),
    "noncommercial_long": (
        "noncommlong", "noncommerciallong", "noncommerciallongpositions", "speculativelong",
    ),
    "noncommercial_short": (
        "noncommshort", "noncommercialshort", "noncommercialshortpositions", "speculativeshort",
    ),
    "noncommercial_spread": (
        "noncommspreads", "noncommspread", "noncommercialspread",
        "noncommercialspreading", "spreadpositions",
    ),
    "net_commercial": ("netcommercial", "commercialnet", "netcommercialpositions"),
    "net_noncommercial": (
        "netnoncommercial", "noncommercialnet", "netnoncommercialpositions", "netspeculative",
    ),
    "market_code": ("marketcode", "contractcode", "cftccontractmarketcode", "code", "id"),
    "contract_name": ("contractname", "marketname", "name", "contract", "title"),
    "category": ("category", "sector", "group", "commoditygroup"),
    "alias": ("alias", "symbol", "ticker", "shortcode"),
}


def _flat_keys(raw: dict[str, Any]) -> dict[str, Any]:
    """Key map with case and separators stripped, so renames still resolve."""
    return {str(k).replace("_", "").replace("-", "").lower(): v for k, v in raw.items()}


def _field(flat: dict[str, Any], name: str) -> Any:
    for candidate in _FIELD_CANDIDATES[name]:
        if flat.get(candidate) is not None:
            return flat[candidate]
    return None


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_day(value: Any) -> Optional[str]:
    if not isinstance(value, str) or len(value) < 10:
        return None
    return value[:10]


def _net(published: Optional[float], long: Optional[float], short: Optional[float]) -> Optional[float]:
    if published is not None:
        return published
    if long is None or short is None:
        return None
    return long - short


def _share_of(value: Optional[float], total: Optional[float]) -> Optional[float]:
    if value is None or not total:
        return None
    return value / total * 100.0


def normalize_position_row(raw: dict[str, Any]) -> Optional[dict[str, Any]]:
    """One weekly COT row in Radon's snake_case shape, or None if unusable."""
    flat = _flat_keys(raw)
    report_date = _as_day(_field(flat, "report_date"))
    if not report_date:
        return None

    open_interest = _as_float(_field(flat, "open_interest"))
    commercial_long = _as_float(_field(flat, "commercial_long"))
    commercial_short = _as_float(_field(flat, "commercial_short"))
    noncommercial_long = _as_float(_field(flat, "noncommercial_long"))
    noncommercial_short = _as_float(_field(flat, "noncommercial_short"))
    net_noncommercial = _net(
        _as_float(_field(flat, "net_noncommercial")), noncommercial_long, noncommercial_short
    )

    return {
        "report_date": report_date,
        "open_interest": open_interest,
        "commercial_long": commercial_long,
        "commercial_short": commercial_short,
        "noncommercial_long": noncommercial_long,
        "noncommercial_short": noncommercial_short,
        "noncommercial_spread": _as_float(_field(flat, "noncommercial_spread")),
        "net_commercial": _net(
            _as_float(_field(flat, "net_commercial")), commercial_long, commercial_short
        ),
        "net_noncommercial": net_noncommercial,
        "net_noncommercial_pct_oi": _share_of(net_noncommercial, open_interest),
    }


# ── crowding statistics ───────────────────────────────────────────


def percentile_rank(values: Sequence[float], value: float) -> Optional[float]:
    """Share of the window at or below `value`, 0-100. None on an empty window."""
    if not values:
        return None
    at_or_below = sum(1 for v in values if v <= value)
    return at_or_below / len(values) * 100.0


def z_score(values: Sequence[float], value: float) -> Optional[float]:
    """Standard scores against the window's mean and population sigma."""
    if len(values) < 2:
        return None
    sigma = statistics.pstdev(values)
    if sigma == 0:
        return None
    return (value - statistics.fmean(values)) / sigma


def classify_crowding(percentile: Optional[float]) -> str:
    if percentile is None:
        return CROWDING_UNKNOWN
    if percentile >= EXTREME_PERCENTILE_HIGH:
        return CROWDING_LONG
    if percentile <= EXTREME_PERCENTILE_LOW:
        return CROWDING_SHORT
    return CROWDING_NEUTRAL


def contrarian_bias(crowding: str) -> str:
    """Positioning extremes fade: crowded long reads bearish, and vice versa."""
    return _CONTRARIAN_BIAS.get(crowding, BIAS_NEUTRAL)


# ── per-contract assembly ─────────────────────────────────────────


def _normalized_series(raw_rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ascending, de-duplicated by report date (revisions win)."""
    by_date: dict[str, dict[str, Any]] = {}
    for raw in raw_rows:
        row = normalize_position_row(raw)
        if row:
            by_date[row["report_date"]] = row
    return [by_date[d] for d in sorted(by_date)]


def build_contract_positioning(
    alias: str, contract: dict[str, Any], raw_rows: Iterable[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    """One contract's history plus where this week sits inside it."""
    series = _normalized_series(raw_rows)
    if not series:
        return None

    latest = series[-1]
    nets = [row["net_noncommercial"] for row in series if row["net_noncommercial"] is not None]
    current_net = latest["net_noncommercial"]
    has_sample = current_net is not None and len(nets) >= MIN_STATS_WEEKS

    percentile = percentile_rank(nets, current_net) if has_sample else None
    crowding = classify_crowding(percentile)

    return {
        "alias": alias,
        "market_code": contract["market_code"],
        "name": contract.get("name"),
        "category": contract.get("category"),
        "report_date": latest["report_date"],
        "weeks": len(series),
        "open_interest": latest["open_interest"],
        "net_noncommercial": current_net,
        "net_commercial": latest["net_commercial"],
        "net_noncommercial_pct_oi": latest["net_noncommercial_pct_oi"],
        "net_noncommercial_change": _week_over_week_change(series),
        "percentile": percentile,
        "z_score": z_score(nets, current_net) if has_sample else None,
        "crowding": crowding,
        "contrarian_bias": contrarian_bias(crowding),
        "series": series,
    }


def _week_over_week_change(series: Sequence[dict[str, Any]]) -> Optional[float]:
    if len(series) < 2:
        return None
    current, prior = series[-1]["net_noncommercial"], series[-2]["net_noncommercial"]
    if current is None or prior is None:
        return None
    return current - prior


def _normalize_market_row(raw: dict[str, Any]) -> Optional[dict[str, Any]]:
    """A latest-week board row: identity plus the two nets."""
    row = normalize_position_row(raw)
    if not row:
        return None
    flat = _flat_keys(raw)
    return {
        "market_code": _field(flat, "market_code"),
        "name": _field(flat, "contract_name"),
        "category": _field(flat, "category"),
        "report_date": row["report_date"],
        "open_interest": row["open_interest"],
        "net_commercial": row["net_commercial"],
        "net_noncommercial": row["net_noncommercial"],
        "net_noncommercial_pct_oi": row["net_noncommercial_pct_oi"],
    }


def build_payload(
    contracts: Sequence[dict[str, Any]], market_rows: Iterable[dict[str, Any]], scan_time: str
) -> dict[str, Any]:
    """Assemble the route payload. Raises rather than emit an empty cache."""
    if not contracts:
        raise CotIngestError("no COT focus contracts resolved; refusing to cache an empty payload")

    market = [row for row in (_normalize_market_row(r) for r in market_rows) if row]
    return {
        "scan_time": scan_time,
        "report_date": max(c["report_date"] for c in contracts),
        "count": len(contracts),
        "lookback_days": HISTORY_LOOKBACK_DAYS,
        "min_stats_weeks": MIN_STATS_WEEKS,
        "extremes": {"high": EXTREME_PERCENTILE_HIGH, "low": EXTREME_PERCENTILE_LOW},
        "contracts": list(contracts),
        "market": market,
    }


# ── roster resolution ─────────────────────────────────────────────


def resolve_market_code(
    roster: Iterable[dict[str, Any]], alias: str
) -> Optional[dict[str, Any]]:
    """Find the roster entry for an alias.

    Resolution order: the explicit CFTC market code from FOCUS_CONTRACTS, then a
    declared alias field, then a name token. Only the first works against the
    live payload, which carries no alias or symbol; the other two are kept so a
    future roster that does declare one still resolves.
    """
    wanted = alias.strip().upper()
    rows = [(_flat_keys(row), row) for row in roster]

    target_code = FOCUS_CONTRACTS.get(wanted)
    if target_code is not None:
        for flat, row in rows:
            code = _field(flat, "market_code")
            if code is not None and str(code).strip().upper() == target_code.upper():
                return _contract_identity(flat)

    for flat, row in rows:
        declared = _field(flat, "alias")
        if isinstance(declared, str) and declared.strip().upper() == wanted:
            return _contract_identity(flat)

    for flat, row in rows:
        name = _field(flat, "contract_name")
        if isinstance(name, str) and wanted in _name_tokens(name):
            return _contract_identity(flat)

    return None


def _name_tokens(name: str) -> set[str]:
    cleaned = "".join(ch if ch.isalnum() else " " for ch in name.upper())
    return set(cleaned.split())


def _contract_identity(flat: dict[str, Any]) -> Optional[dict[str, Any]]:
    market_code = _field(flat, "market_code")
    if market_code is None:
        return None
    return {
        "market_code": str(market_code),
        "name": _field(flat, "contract_name"),
        "category": _field(flat, "category"),
    }


# ── fetch ─────────────────────────────────────────────────────────


def _fetch_roster(client: Any) -> list[dict[str, Any]]:
    return _rows(client.get_cftc_contracts())


def _fetch_market_latest(client: Any) -> list[dict[str, Any]]:
    try:
        return _rows(client.get_cftc_latest_positions())
    except Exception as exc:  # noqa: BLE001 — the board is a secondary view
        print(f"[cot] latest-positions board unavailable (non-fatal): {exc}", file=sys.stderr)
        return []


def _fetch_contract_history(client: Any, market_code: str, start_date: str) -> list[dict[str, Any]]:
    walk = client.paginate(
        client.get_cftc_positions,
        market_code,
        limit=HISTORY_PAGE_LIMIT,
        max_pages=HISTORY_MAX_PAGES,
        start_date=start_date,
    )
    return walk.rows


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "contracts", "positions", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def collect_contracts(client: Any, start_date: str) -> list[dict[str, Any]]:
    """Resolve every focus alias and pull its trailing history."""
    roster = _fetch_roster(client)
    collected: list[dict[str, Any]] = []
    for alias in FOCUS_ALIASES:
        contract = resolve_market_code(roster, alias)
        if contract is None:
            print(f"[cot] {alias}: no contract in the CFTC roster; skipped", file=sys.stderr)
            continue
        history = _fetch_contract_history(client, contract["market_code"], start_date)
        positioning = build_contract_positioning(alias, contract, history)
        if positioning is None:
            print(f"[cot] {alias}: no usable weekly rows; skipped", file=sys.stderr)
            continue
        collected.append(positioning)
    return collected


# ── persistence ───────────────────────────────────────────────────


def cot_rows_for_db(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten every contract-week into a durable cot_positioning row."""
    return [
        {
            **row,
            "market_code": contract["market_code"],
            "contract_name": contract.get("name"),
            "category": contract.get("category"),
        }
        for contract in payload["contracts"]
        for row in contract["series"]
    ]


def _db_args(row: dict[str, Any], recorded_at: str) -> tuple:
    return (
        row["market_code"], row["report_date"], row.get("contract_name"), row.get("category"),
        row.get("open_interest"), row.get("commercial_long"), row.get("commercial_short"),
        row.get("noncommercial_long"), row.get("noncommercial_short"),
        row.get("noncommercial_spread"), row.get("net_commercial"), row.get("net_noncommercial"),
        row.get("net_noncommercial_pct_oi"), recorded_at,
    )


def upsert_cot_rows(rows: Sequence[dict[str, Any]], recorded_at: str) -> None:
    """Chunked multi-row upsert — never one statement per week over Hrana."""
    if not rows:
        return
    try:
        from db.client import get_db
    except ImportError:  # pragma: no cover — stripped env
        return

    db = get_db()
    for start in range(0, len(rows), _DB_INSERT_CHUNK_ROWS):
        chunk = rows[start:start + _DB_INSERT_CHUNK_ROWS]
        params: list[Any] = []
        for row in chunk:
            params.extend(_db_args(row, recorded_at))
        db.execute(cot_upsert_sql(len(chunk)), tuple(params))
    db.commit()


def _write_db_cache(payload: dict[str, Any], scan_time: str) -> None:
    """Turso mirror; data/equibles_cot_positioning.json is the disk fallback.

    Durable rows + the route's snapshot + the writer heartbeat all run on the
    same successful cycle, so a silent writer shows up as staleness instead of
    a latched error row (cf. feedback_service_health_heartbeat).
    """
    try:
        from db import writer
    except ImportError:
        return
    try:
        writer.ensure_no_replica_for_writers()
        upsert_cot_rows(cot_rows_for_db(payload), scan_time)
        writer.upsert_scan_snapshot(SERVICE, scan_time, payload)
        writer.record_service_health(SERVICE, "ok", finished_at=scan_time)
    except Exception as exc:  # noqa: BLE001 — best-effort mirror
        print(f"[cot] db cache non-fatal: {exc}", file=sys.stderr)


def _write_json_cache(payload: dict[str, Any]) -> None:
    COT_JSON.parent.mkdir(parents=True, exist_ok=True)
    COT_JSON.write_text(json.dumps(payload, indent=2))


def _record_failure(error: Exception, scan_time: str) -> None:
    try:
        from db import writer
        writer.record_service_health(
            SERVICE, "error", finished_at=scan_time, error={"message": str(error)}
        )
    except Exception:  # noqa: BLE001 — health reporting must not mask the cause
        pass


# ── orchestration ─────────────────────────────────────────────────


def run(client: Optional[Any] = None, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """Fetch the focus basket, rank this week against its history, cache it."""
    now = now or datetime.now(timezone.utc)
    scan_time = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    start_date = (now.date() - timedelta(days=HISTORY_LOOKBACK_DAYS)).isoformat()

    owns_client = client is None
    if owns_client:
        from clients.equibles_client import EquiblesClient
        client = EquiblesClient()

    try:
        contracts = collect_contracts(client, start_date)
        payload = build_payload(contracts, _fetch_market_latest(client), scan_time)
    finally:
        if owns_client:
            client.close()

    _write_db_cache(payload, scan_time)
    _write_json_cache(payload)
    return payload


# ── CLI ───────────────────────────────────────────────────────────


def _print_summary(payload: dict[str, Any]) -> None:
    print(f"\nCFTC COT POSITIONING — week of {payload['report_date']}", file=sys.stderr)
    for contract in payload["contracts"]:
        percentile = contract["percentile"]
        rank = f"{percentile:5.1f}pct" if percentile is not None else "   ---  "
        net = contract["net_noncommercial"]
        net_text = f"{net:>12,.0f}" if net is not None else "         ---"
        print(
            f"  {contract['alias']:<4} net spec {net_text}  {rank}  "
            f"{contract['crowding']} -> {contract['contrarian_bias']}",
            file=sys.stderr,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="CFTC COT cross-asset positioning (Equibles)")
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    args = parser.parse_args()

    scan_time = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        payload = run()
    except Exception as exc:
        _record_failure(exc, scan_time)
        print(f"[cot] ingest failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_summary(payload)


if __name__ == "__main__":
    main()
