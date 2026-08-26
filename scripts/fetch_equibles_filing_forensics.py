#!/usr/bin/env python3
"""Pre-trade filing-forensics dossier — the Equibles SEC filing surface per ticker.

Answers the question that kills convex long structures silently: what has this
issuer already told the SEC it is about to do?

  proposed-sales      Form 144. FORWARD-LOOKING insider supply, filed BEFORE
                      the sale. (Form 4 — what informed-flow already carries —
                      is filed AFTER. That difference is the whole feature.)
  atm-programs        Unused at-the-market shelf capacity: dilution overhang.
  buyback-programs    Remaining repurchase authorization: a standing bid.
  executive-changes   8-K Item 5.02 departures.
  screener/stocks     hasGoingConcernDoubt membership.

Every check resolves to exactly one of four verdicts, and the last two are
NOT interchangeable with the first two:

  flagged      a filing exists AND breaches a named threshold
  clear        filings exist, none breach            -> genuinely no overhang
  no-filings   the source answered with zero rows    -> nothing on file
  unavailable  the source failed / could not answer  -> we do NOT know

A fetch failure never renders as an all-clear. Every flagged check cites the
filing it came from (form + URL); that traceability is the deliverable.

Flags are framed as gate inputs: Gate 2 (edge) for signals that change what the
flow means, Gate 3 (risk) for signals that change what the position can lose.

Output is dual-written to Turso equibles_filing_forensics +
data/equibles_filing_forensics/<TICKER>.json.

Usage:
    python3 scripts/fetch_equibles_filing_forensics.py AAPL TSLA
    python3 scripts/fetch_equibles_filing_forensics.py --json      # watchlist
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

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

SERVICE_NAME = "equibles-filing-forensics"
CACHE_DIR = _PROJECT_DIR / "data" / "equibles_filing_forensics"
WATCHLIST_JSON = _PROJECT_DIR / "data" / "watchlist.json"

STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_ERROR = "error"

VERDICT_FLAGGED = "flagged"
VERDICT_CLEAR = "clear"
VERDICT_NO_FILINGS = "no-filings"
VERDICT_UNAVAILABLE = "unavailable"

CHECK_DILUTION_OVERHANG = "dilution_overhang"
CHECK_INSIDER_SUPPLY = "insider_supply_pending"
CHECK_BUYBACK_SUPPORT = "buyback_support"
CHECK_EXEC_CHURN = "exec_churn"
CHECK_GOING_CONCERN = "going_concern_doubt"

GATE_EDGE = 2
GATE_RISK = 3

TONE_ADVERSE = "adverse"
TONE_SUPPORTIVE = "supportive"

# Thresholds. Named so a reader never has to reverse-engineer a bare literal.
ATM_REMAINING_MATERIAL_USD = 10_000_000.0
FORM_144_LOOKBACK_DAYS = 90
FORM_144_MATERIAL_USD = 1_000_000.0
BUYBACK_REMAINING_MATERIAL_USD = 10_000_000.0
EXEC_CHURN_LOOKBACK_DAYS = 180
EXEC_CHURN_MIN_DEPARTURES = 2
EXEC_DEPARTURE_ACTIONS = ("resigned", "terminated", "retired")

# One screener walk per cycle covers every ticker. Live 2026-08-11: the
# screener caps a page at 200 rows and ~1,300 issuers carry the flag.
GOING_CONCERN_PAGE_LIMIT = 200
GOING_CONCERN_MAX_PAGES = 12
PROPOSED_SALES_PAGE_LIMIT = 100
EXECUTIVE_CHANGES_PAGE_LIMIT = 100

NO_FILINGS_DETAIL = "No filings found"
UNAVAILABLE_DETAIL = "Could not verify: the filing source did not answer"

EQUIBLES_FILING_FORENSICS_UPSERT_SQL = """
INSERT INTO equibles_filing_forensics
  (ticker, as_of, flag_count, data_complete, payload, recorded_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(ticker) DO UPDATE SET
  as_of         = excluded.as_of,
  flag_count    = excluded.flag_count,
  data_complete = excluded.data_complete,
  payload       = excluded.payload,
  recorded_at   = excluded.recorded_at
"""


# ── source results ────────────────────────────────────────────────


@dataclass
class SourceResult:
    """One endpoint's answer: rows plus whether it answered at all.

    ``envelope`` keeps the top-level response for endpoints that carry figures
    outside the row list (buyback-programs reports remainingAuthorizedAmount on
    the envelope while the per-program rows may leave remainingAmount null).
    """

    status: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    envelope: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "count": len(self.rows), "error": self.error}


@dataclass
class GoingConcernUniverse:
    """Screener membership set. ``truncated`` means absence proves nothing."""

    status: str
    tickers: set[str] = field(default_factory=set)
    truncated: bool = False
    error: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "count": len(self.tickers),
            "truncated": self.truncated,
            "error": self.error,
        }


# ── defensive field access ────────────────────────────────────────


def _first_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if row.get(key) is not None:
            return row[key]
    return None


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, dict):
        return _as_float(_first_value(value, "usd", "currency", "amount", "value"))
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_date(value: Any) -> Optional[date]:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _is_within(value: Any, today: date, days: int) -> bool:
    filed = _as_date(value)
    return filed is not None and 0 <= (today - filed).days <= days


def _filing_citation(row: dict[str, Any], default_form: Optional[str] = None) -> dict[str, Any]:
    return {
        "form": _first_value(row, "latestForm", "form", "formType") or default_form,
        "filed_at": _first_value(row, "filingDate", "lastAmendedDate", "announcedDate", "effectiveDate"),
        "url": _first_value(row, "latestFilingUrl", "filingUrl", "url"),
    }


def _usd(amount: float) -> str:
    if abs(amount) >= 1_000_000_000:
        return f"${amount / 1_000_000_000:,.1f}B"
    if abs(amount) >= 1_000_000:
        return f"${amount / 1_000_000:,.1f}M"
    return f"${amount:,.0f}"


# ── check construction ────────────────────────────────────────────


def _check(
    code: str,
    label: str,
    gate: int,
    tone: str,
    source: SourceResult,
    evaluate: Callable[[list[dict[str, Any]]], Optional[dict[str, Any]]],
    clear_detail: str,
) -> dict[str, Any]:
    """Resolve one check, keeping absence and failure distinguishable."""
    base = {"code": code, "label": label, "gate": gate, "tone": tone}
    if source.status == STATUS_ERROR:
        return {**base, "verdict": VERDICT_UNAVAILABLE, "detail": UNAVAILABLE_DETAIL,
                "figure": None, "filing": None, "error": source.error}
    if source.status == STATUS_EMPTY or not source.rows:
        return {**base, "verdict": VERDICT_NO_FILINGS, "detail": NO_FILINGS_DETAIL,
                "figure": None, "filing": None}

    breach = evaluate(source.rows)
    if breach is None:
        return {**base, "verdict": VERDICT_CLEAR, "detail": clear_detail,
                "figure": None, "filing": None}
    # A breach is flagged unless the evaluator names its own verdict (the
    # filings exist but do not quantify the exposure -> unavailable).
    return {**base, "verdict": VERDICT_FLAGGED, **breach}


def _dilution_overhang_check(source: SourceResult) -> dict[str, Any]:
    def evaluate(rows: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        live = [r for r in rows if not r.get("isFullyUtilized") and not r.get("isExpired")]
        remaining = [(_as_float(r.get("remainingAmount")) or 0.0, r) for r in live]
        total = sum(amount for amount, _ in remaining)
        if total < ATM_REMAINING_MATERIAL_USD:
            return None
        largest = max(remaining, key=lambda pair: pair[0])[1]
        return {
            "detail": (
                f"{_usd(total)} of unused at-the-market shelf capacity across "
                f"{len(live)} live program(s). Shares can be issued into any rally."
            ),
            "figure": {"value": total, "unit": "usd", "label": "UNUSED ATM CAPACITY"},
            "filing": _filing_citation(largest, default_form="424B5"),
        }

    return _check(
        CHECK_DILUTION_OVERHANG,
        "Dilution overhang",
        GATE_RISK,
        TONE_ADVERSE,
        source,
        evaluate,
        clear_detail="ATM shelves on file are expired, fully utilized, or immaterial",
    )


def _insider_supply_check(source: SourceResult, today: date) -> dict[str, Any]:
    def evaluate(rows: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        recent = [r for r in rows if _is_within(r.get("filingDate"), today, FORM_144_LOOKBACK_DAYS)]
        total = sum(_as_float(r.get("aggregateMarketValue")) or 0.0 for r in recent)
        if not recent or total < FORM_144_MATERIAL_USD:
            return None
        largest = max(recent, key=lambda r: _as_float(r.get("aggregateMarketValue")) or 0.0)
        sellers = {r.get("sellerName") for r in recent if r.get("sellerName")}
        return {
            "detail": (
                f"{len(recent)} Form 144 notice(s) from {len(sellers) or 1} insider(s) "
                f"totalling {_usd(total)} in the last {FORM_144_LOOKBACK_DAYS} days. "
                "Form 144 is filed before the sale, so this supply is still pending."
            ),
            "figure": {"value": total, "unit": "usd", "label": "PENDING INSIDER SUPPLY"},
            "filing": _filing_citation(largest, default_form="144"),
        }

    return _check(
        CHECK_INSIDER_SUPPLY,
        "Insider supply pending",
        GATE_EDGE,
        TONE_ADVERSE,
        source,
        evaluate,
        clear_detail=f"No material Form 144 supply in the last {FORM_144_LOOKBACK_DAYS} days",
    )


def _buyback_support_check(source: SourceResult) -> dict[str, Any]:
    """Remaining authorization, which issuers often decline to quantify.

    A program on file whose remaining size is nowhere disclosed is UNKNOWN, not
    clear: reading it as "no standing bid" would be the same all-clear mistake
    the feature exists to prevent.
    """
    def evaluate(rows: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        remaining = [(_as_float(row.get("remainingAmount")), row) for row in rows]
        disclosed = [(amount, row) for amount, row in remaining if amount is not None]
        envelope_total = _as_float(source.envelope.get("remainingAuthorizedAmount"))
        total = sum(amount for amount, _ in disclosed) or envelope_total or 0.0
        if not disclosed and envelope_total is None:
            return _undisclosed_buyback(rows)
        if total < BUYBACK_REMAINING_MATERIAL_USD:
            return None
        largest = max(disclosed, key=lambda pair: pair[0])[1] if disclosed else rows[0]
        return {
            "detail": (
                f"{_usd(total)} of repurchase authorization remains. A standing bid "
                "cushions downside and can squeeze a crowded short."
            ),
            "figure": {"value": total, "unit": "usd", "label": "REMAINING AUTHORIZATION"},
            "filing": _filing_citation(largest, default_form="8-K"),
        }

    return _check(
        CHECK_BUYBACK_SUPPORT,
        "Buyback support",
        GATE_EDGE,
        TONE_SUPPORTIVE,
        source,
        evaluate,
        clear_detail="Repurchase authorization on file is exhausted or immaterial",
    )


def _undisclosed_buyback(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "verdict": VERDICT_UNAVAILABLE,
        "detail": (
            f"{len(rows)} repurchase program(s) on file, none disclosing a remaining "
            "authorization. Size of the standing bid is unknown."
        ),
        "figure": None,
        "filing": _filing_citation(rows[0], default_form="8-K"),
    }


def _exec_churn_check(source: SourceResult, today: date) -> dict[str, Any]:
    def evaluate(rows: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        # Window on the FILING date: a departure disclosed today can carry a
        # future effective date, and that is exactly the supply-side news.
        departures = [
            r for r in rows
            if str(r.get("action") or "").strip().lower() in EXEC_DEPARTURE_ACTIONS
            and _is_within(
                _first_value(r, "filedDate", "filingDate", "effectiveDate"),
                today,
                EXEC_CHURN_LOOKBACK_DAYS,
            )
        ]
        if len(departures) < EXEC_CHURN_MIN_DEPARTURES:
            return None
        roles = ", ".join(str(r.get("role") or r.get("roleText") or "officer") for r in departures[:3])
        return {
            "detail": (
                f"{len(departures)} executive departures disclosed under 8-K Item 5.02 "
                f"in the last {EXEC_CHURN_LOOKBACK_DAYS} days ({roles})."
            ),
            "figure": {"value": len(departures), "unit": "count", "label": "DEPARTURES"},
            "filing": _filing_citation(departures[0], default_form="8-K"),
        }

    return _check(
        CHECK_EXEC_CHURN,
        "Executive churn",
        GATE_EDGE,
        TONE_ADVERSE,
        source,
        evaluate,
        clear_detail=f"Fewer than {EXEC_CHURN_MIN_DEPARTURES} departures in the last {EXEC_CHURN_LOOKBACK_DAYS} days",
    )


def _going_concern_check(ticker: str, universe: GoingConcernUniverse) -> dict[str, Any]:
    """Screener membership, where a truncated universe cannot prove absence."""
    base = {
        "code": CHECK_GOING_CONCERN,
        "label": "Going-concern doubt",
        "gate": GATE_RISK,
        "tone": TONE_ADVERSE,
    }
    if ticker in universe.tickers:
        return {
            **base,
            "verdict": VERDICT_FLAGGED,
            "detail": "Auditor has raised substantial doubt about the issuer continuing as a going concern.",
            "figure": None,
            "filing": {"form": "10-K", "filed_at": None, "url": None},
        }
    if universe.status == STATUS_ERROR or universe.truncated:
        return {**base, "verdict": VERDICT_UNAVAILABLE, "detail": UNAVAILABLE_DETAIL,
                "figure": None, "filing": None, "error": universe.error}
    return {**base, "verdict": VERDICT_CLEAR, "detail": "No going-concern doubt in the latest screener vintage",
            "figure": None, "filing": None}


# ── dossier assembly ──────────────────────────────────────────────


def build_dossier(
    ticker: str,
    sources: dict[str, SourceResult],
    going_concern: GoingConcernUniverse,
    *,
    as_of: str,
    today: date,
) -> dict[str, Any]:
    """Assemble the per-ticker dossier from already-fetched source results."""
    symbol = ticker.upper()
    checks = [
        _dilution_overhang_check(sources["atm_programs"]),
        _insider_supply_check(sources["proposed_sales"], today),
        _buyback_support_check(sources["buyback_programs"]),
        _exec_churn_check(sources["executive_changes"], today),
        _going_concern_check(symbol, going_concern),
    ]
    flags = [c for c in checks if c["verdict"] == VERDICT_FLAGGED]
    source_status = {name: result.as_dict() for name, result in sources.items()}
    source_status["going_concern"] = going_concern.as_dict()

    return {
        "ticker": symbol,
        "as_of": as_of,
        "data_complete": all(s["status"] != STATUS_ERROR for s in source_status.values())
        and not going_concern.truncated,
        "flag_count": len(flags),
        "flags": flags,
        "checks": checks,
        "sources": source_status,
        "thresholds": {
            "atm_remaining_material_usd": ATM_REMAINING_MATERIAL_USD,
            "form_144_lookback_days": FORM_144_LOOKBACK_DAYS,
            "form_144_material_usd": FORM_144_MATERIAL_USD,
            "buyback_remaining_material_usd": BUYBACK_REMAINING_MATERIAL_USD,
            "exec_churn_lookback_days": EXEC_CHURN_LOOKBACK_DAYS,
            "exec_churn_min_departures": EXEC_CHURN_MIN_DEPARTURES,
        },
    }


def has_usable_data(dossier: dict[str, Any]) -> bool:
    """At least one source answered. Gate every cache write on this."""
    return any(s["status"] != STATUS_ERROR for s in dossier["sources"].values())


# ── fetching ──────────────────────────────────────────────────────


def _rows_of(payload: Any) -> list[dict[str, Any]]:
    """Rows from a response whose list lives under ``data`` or ``programs``.

    Verified live 2026-08-11: proposed-sales / executive-changes answer with
    ``data``; atm-programs / buyback-programs answer with ``programs`` beside
    envelope-level fields.
    """
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "programs"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            return [value]
    return []


def _fetch_source(call: Callable[[], Any]) -> SourceResult:
    try:
        payload = call()
    except Exception as exc:  # noqa: BLE001 — an unknown fault is still "unknown", not "clear"
        return SourceResult(STATUS_ERROR, [], error=str(exc))
    rows = _rows_of(payload)
    envelope = payload if isinstance(payload, dict) else {}
    return SourceResult(STATUS_OK if rows else STATUS_EMPTY, rows, envelope=envelope)


def _screener_has_more(payload: Any, page_rows: int, pages_walked: int, rows_seen: int) -> bool:
    """Whether rows remain beyond the pages already walked.

    Prefers the screener's own counters and falls back to a full page meaning
    "probably more" — an unrecognised envelope must never read as exhausted,
    because a short universe is what turns "could not verify" into a false
    all-clear downstream.
    """
    envelope = payload if isinstance(payload, dict) else {}
    total_pages = envelope.get("totalPages")
    if isinstance(total_pages, int) and total_pages > 0:
        return pages_walked < total_pages
    total_count = envelope.get("totalCount")
    if isinstance(total_count, int) and total_count >= 0:
        return rows_seen < total_count
    return page_rows >= GOING_CONCERN_PAGE_LIMIT


def fetch_going_concern_universe(client: Any) -> GoingConcernUniverse:
    """The set of tickers flagged with going-concern doubt, walked page by page.

    The screener caps a page at 200 rows and reports totalPages / totalCount;
    ~1,300 issuers carry the flag, so the walk is bounded at
    GOING_CONCERN_MAX_PAGES and reports truncation when it stops early.
    Truncation matters: absence from a partial universe proves nothing.
    """
    tickers: set[str] = set()
    pages_walked = 0
    rows_seen = 0
    has_more = True

    while has_more and pages_walked < GOING_CONCERN_MAX_PAGES:
        try:
            payload = client.get_stock_screener(
                hasGoingConcernDoubt=True,
                limit=GOING_CONCERN_PAGE_LIMIT,
                page=pages_walked + 1,
            )
        except Exception as exc:  # noqa: BLE001 — captured, never raised into the cycle
            return GoingConcernUniverse(STATUS_ERROR, tickers, True, str(exc))

        pages_walked += 1
        rows = _rows_of(payload)
        rows_seen += len(rows)
        tickers.update(
            str(_first_value(row, "ticker", "symbol")).upper()
            for row in rows
            if _first_value(row, "ticker", "symbol")
        )
        has_more = _screener_has_more(payload, len(rows), pages_walked, rows_seen)

    return GoingConcernUniverse(STATUS_OK, tickers, truncated=has_more)


def fetch_dossier(
    client: Any,
    ticker: str,
    going_concern: GoingConcernUniverse,
    *,
    today: Optional[date] = None,
    as_of: Optional[str] = None,
) -> dict[str, Any]:
    """Fetch every filing source for one ticker and assemble its dossier."""
    symbol = ticker.upper()
    today = today or datetime.now(timezone.utc).date()
    window_start = (today - timedelta(days=EXEC_CHURN_LOOKBACK_DAYS)).isoformat()

    sources = {
        # /proposed-sales rejects startDate (live 2026-08-11: "Supported query
        # parameters: limit, offset"), so the lookback is applied in the check.
        "proposed_sales": _fetch_source(
            lambda: client.get_proposed_sales(symbol, limit=PROPOSED_SALES_PAGE_LIMIT)
        ),
        "atm_programs": _fetch_source(lambda: client.get_atm_programs(symbol)),
        "buyback_programs": _fetch_source(lambda: client.get_buyback_programs(symbol)),
        "executive_changes": _fetch_source(
            lambda: client.get_executive_changes(
                symbol, start_date=window_start, limit=EXECUTIVE_CHANGES_PAGE_LIMIT
            )
        ),
    }
    return build_dossier(
        symbol, sources, going_concern, as_of=as_of or _now_iso(), today=today
    )


# ── persistence ───────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_db_row(dossier: dict[str, Any]) -> None:
    """Best-effort Turso mirror; the per-ticker JSON file is the disk fallback."""
    try:
        from db import writer
        from db.client import get_db
    except ImportError:
        return
    try:
        writer.ensure_no_replica_for_writers()
        db = get_db()
        db.execute(
            EQUIBLES_FILING_FORENSICS_UPSERT_SQL,
            (
                dossier["ticker"],
                dossier["as_of"],
                int(dossier["flag_count"]),
                1 if dossier["data_complete"] else 0,
                json.dumps(dossier),
                _now_iso(),
            ),
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001 — best-effort mirror
        print(f"[filing-forensics] db write non-fatal for {dossier['ticker']}: {exc}", file=sys.stderr)


def _record_health(state: str, error: Optional[dict[str, Any]] = None) -> None:
    """Heartbeat EVERY cycle so a silent writer surfaces as stale, not healthy."""
    try:
        from db import writer
    except ImportError:
        return
    try:
        writer.record_service_health(SERVICE_NAME, state, finished_at=_now_iso(), error=error)
    except Exception as exc:  # noqa: BLE001 — best-effort heartbeat
        print(f"[filing-forensics] health write non-fatal: {exc}", file=sys.stderr)


def _write_json_cache(dossier: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{dossier['ticker']}.json").write_text(json.dumps(dossier, indent=2))


# ── orchestration ─────────────────────────────────────────────────


def load_watchlist_tickers() -> list[str]:
    """Watchlist symbols, Turso first with the JSON file as fallback.

    data/*.json is an ephemeral fallback on the VPS, so a disk-only read makes
    the scheduled run fail every cycle on a host where the file was never
    written. Turso is the canonical store.
    """
    try:
        from db.readers import read_watchlist_tickers

        symbols = read_watchlist_tickers()
        if symbols:
            return [str(s).upper() for s in symbols]
    except Exception:  # noqa: BLE001 - fall through to the disk copy
        pass

    try:
        raw = json.loads(WATCHLIST_JSON.read_text())
    except (OSError, ValueError):
        return []
    entries = raw.get("tickers", raw) if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        return []
    symbols = []
    for entry in entries:
        symbol = entry if isinstance(entry, str) else _first_value(entry, "ticker", "symbol") if isinstance(entry, dict) else None
        if symbol:
            symbols.append(str(symbol).upper())
    return symbols


def run(
    tickers: list[str],
    client: Optional[Any] = None,
    *,
    today: Optional[date] = None,
) -> dict[str, Any]:
    """Refresh every ticker's dossier, persisting only the usable ones."""
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

        as_of = _now_iso()
        today = today or datetime.now(timezone.utc).date()
        going_concern = fetch_going_concern_universe(client)

        dossiers: list[dict[str, Any]] = []
        skipped: list[str] = []
        for ticker in tickers:
            dossier = fetch_dossier(client, ticker, going_concern, today=today, as_of=as_of)
            if not has_usable_data(dossier):
                skipped.append(dossier["ticker"])
                continue
            _write_db_row(dossier)
            _write_json_cache(dossier)
            dossiers.append(dossier)
    except Exception as exc:  # noqa: BLE001 — re-raised; the row must exist first
        _record_health("error", {"message": f"cycle aborted: {exc}"})
        raise

    persisted = len(dossiers)
    if persisted or not tickers:
        _record_health("ok")
    else:
        _record_health("error", {"message": f"no usable dossier for {len(skipped)} ticker(s)"})

    return {
        "as_of": as_of,
        "persisted": persisted,
        "skipped": skipped,
        "flagged": [d["ticker"] for d in dossiers if d["flag_count"]],
        "dossiers": dossiers,
    }


# ── CLI ───────────────────────────────────────────────────────────


def _print_summary(result: dict[str, Any]) -> None:
    print(f"\nFILING FORENSICS — {result['persisted']} dossier(s) @ {result['as_of']}", file=sys.stderr)
    for dossier in result["dossiers"]:
        codes = ", ".join(f["code"] for f in dossier["flags"]) or "no flags"
        complete = "" if dossier["data_complete"] else "  (PARTIAL DATA)"
        print(f"  {dossier['ticker']:<6} {codes}{complete}", file=sys.stderr)
    for ticker in result["skipped"]:
        print(f"  {ticker:<6} skipped: every filing source failed", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Equibles pre-trade filing forensics dossier")
    parser.add_argument("tickers", nargs="*", help="Tickers (default: data/watchlist.json)")
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    args = parser.parse_args()

    tickers = [t.upper() for t in args.tickers] or load_watchlist_tickers()
    if not tickers:
        # Exit 2 without a health row is invisible: the service is registered
        # for freshness, so the watchdog only ever sees a row that never came.
        _record_health("error", {"message": "no tickers given and the watchlist is empty"})
        parser.error("no tickers given and data/watchlist.json is empty or absent")

    result = run(tickers)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_summary(result)


if __name__ == "__main__":
    main()
