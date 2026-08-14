#!/usr/bin/env python3
"""SKEW Indicator — change in SPX 1-month 25-delta put/call IV ratio.

Per session, the SPX option chain of the monthly expiry nearest 30 DTE is
interpolated in delta to the 25-delta put and call IVs; the indicator is the
day-over-day change of put_iv / call_iv. Sharp drops mean put skew collapsing
or call demand spiking; sharp rises mean downside fear getting bid.

Source: Unusual Whales /api/stock/SPX/greeks (Bearer UW_TOKEN). History floor
for this token: 2023-09-06. Spec: docs/indicators/skew.md.

Output is dual-written to Turso skew_history + data/skew.json.

Usage:
    python3 scripts/fetch_skew.py                    # gap-filling daily run
    python3 scripts/fetch_skew.py --json             # JSON to stdout
    python3 scripts/fetch_skew.py --backfill         # one-shot from 2023-09-06
    python3 scripts/fetch_skew.py --backfill 2025-01-02
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

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

from clients.uw_client import UWRateLimitError  # noqa: E402 — needs the sys.path insert
from utils.market_calendar import (  # noqa: E402 — needs the sys.path insert
    get_last_n_trading_days,
    market_state,
    last_completed_session_date,
    load_holidays,
)

# ── constants ─────────────────────────────────────────────────────
SKEW_JSON = _PROJECT_DIR / "data" / "skew.json"
# Local sidecar so a daily UW cap does not keep calling the API every minute
# (4xx does not burn quota, but the uncaught raise left the writer silent).
_UW_DAILY_LIMIT_MARKER = "daily request limit"
_UW_DAILY_RESET_HOUR_ET = 20

_TICKER = "SPX"
_TARGET_DTE = 30
_PUT_TARGET_DELTA = -0.25
_CALL_TARGET_DELTA = 0.25
# Plausibility bounds for the 25d put/call ratio; outside = upstream garbage.
_RATIO_SANE_MIN = 0.8
_RATIO_SANE_MAX = 3.0
_MAX_GAP_SESSIONS = 10
_BACKFILL_START = "2023-09-06"  # UW greeks history floor for this token
_BACKFILL_THROTTLE_S = 0.3
_BACKFILL_CHECKPOINT_EVERY = 50
_FINALIZATION_GRACE = timedelta(minutes=45)
_ET = ZoneInfo("America/New_York")
_BASE_ROW_KEYS = ("date", "expiry", "dte", "expiry_far", "dte_far", "put_iv", "call_iv", "ratio")


# ── chain parsing + delta interpolation ───────────────────────────

def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_greek_rows(payload: dict) -> list[dict[str, Any]]:
    """UW greeks response → per-strike rows with numeric fields coerced.

    date/expiry stay strings; deltas/IVs arrive as strings and become floats
    (None when missing or unparseable).
    """
    rows: list[dict[str, Any]] = []
    for raw in payload.get("data") or []:
        rows.append(
            {
                "date": raw.get("date"),
                "expiry": raw.get("expiry"),
                "strike": _to_float(raw.get("strike")),
                "call_delta": _to_float(raw.get("call_delta")),
                "call_volatility": _to_float(raw.get("call_volatility")),
                "put_delta": _to_float(raw.get("put_delta")),
                "put_volatility": _to_float(raw.get("put_volatility")),
            }
        )
    return rows


def interpolate_iv_at_delta(
    rows: list[dict[str, Any]], side: str, target: float
) -> Optional[float]:
    """IV linearly interpolated in delta to ``target`` on one wing.

    Collects (delta, iv) points with both fields present and iv > 0, sorts by
    delta, and interpolates between the bracketing pair; an exact delta hit
    short-circuits. None when the target is not bracketed.
    """
    delta_key, iv_key = f"{side}_delta", f"{side}_volatility"
    points: list[tuple[float, float]] = []
    for row in rows:
        delta, iv = _to_float(row.get(delta_key)), _to_float(row.get(iv_key))
        if delta is None or iv is None or iv <= 0:
            continue
        points.append((delta, iv))
    points.sort()

    for delta, iv in points:
        if delta == target:
            return iv
    for (d0, v0), (d1, v1) in zip(points, points[1:]):
        if d0 < target < d1:
            return v0 + (v1 - v0) * (target - d0) / (d1 - d0)
    return None


# ── monthly expiry selection ──────────────────────────────────────

def third_friday(year: int, month: int) -> date:
    """Third Friday of the month (standard monthly option expiration)."""
    first_friday_offset = (4 - date(year, month, 1).weekday()) % 7
    return date(year, month, 1 + first_friday_offset + 14)


def _monthly_candidates(as_of: date) -> list[date]:
    """Third Fridays of the as-of month and the next two months."""
    candidates = []
    year, month = as_of.year, as_of.month
    for _ in range(3):
        candidates.append(third_friday(year, month))
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return candidates


def bracketing_monthly_expiries(as_of: date) -> tuple[date, date]:
    """The monthly pair straddling the 30d target: near = longest-dated
    monthly at or under 30 DTE (or the shortest listed when none are),
    far = shortest-dated monthly beyond 30 DTE (or near when none are).
    Interpolating between the pair gives a constant-maturity 30d surface —
    a single rolling monthly jumps structurally on roll day (near-dated
    skew ratios run much steeper), which contaminates the change series."""
    candidates = _monthly_candidates(as_of)
    under = [e for e in candidates if (e - as_of).days <= _TARGET_DTE]
    over = [e for e in candidates if (e - as_of).days > _TARGET_DTE]
    near = max(under) if under else min(candidates)
    far = min(over) if over else near
    return near, far


def constant_maturity_leg(
    iv_near: float, dte_near: int, iv_far: float, dte_far: int, target: int = _TARGET_DTE
) -> float:
    """IV interpolated linearly in DTE to the target tenor, clamped to the
    bracket edges (never extrapolate beyond the listed monthlies)."""
    if dte_far == dte_near:
        return iv_near
    weight = (target - dte_near) / (dte_far - dte_near)
    weight = min(1.0, max(0.0, weight))
    return iv_near + weight * (iv_far - iv_near)


# ── change series + stats ─────────────────────────────────────────

def compute_change_series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ascending by date; attaches change = ratio_t - ratio_{t-1} (None first)."""
    ordered = sorted(rows, key=lambda r: r["date"])
    series: list[dict[str, Any]] = []
    prior_ratio: Optional[float] = None
    for row in ordered:
        change = row["ratio"] - prior_ratio if prior_ratio is not None else None
        series.append({**row, "change": change})
        prior_ratio = row["ratio"]
    return series


def compute_stats(series: list[dict[str, Any]]) -> Optional[dict[str, float]]:
    """{high, low, avg, stddev (population)} over non-null changes."""
    changes = [row["change"] for row in series if row.get("change") is not None]
    if not changes:
        return None
    return {
        "high": max(changes),
        "low": min(changes),
        "avg": statistics.fmean(changes),
        "stddev": statistics.pstdev(changes),
    }


# ── UW client (reuses the shared scripts/clients/uw_client.py) ────

class _DefaultClient:
    """fetch_greeks(expiry, as_of) adapter over the shared UWClient."""

    def __init__(self) -> None:
        from clients.uw_client import UWClient

        # web/.env stores the token double-quoted; dotenv usually strips the
        # quotes but a stale shell export may not — strip defensively.
        token = (os.environ.get("UW_TOKEN") or "").strip().strip('"')
        self._uw = UWClient(token=token or None)

    def fetch_greeks(self, expiry: str, as_of: str) -> dict:
        return self._uw.get_greeks(_TICKER, expiry=expiry, date=as_of)


# ── persistence ───────────────────────────────────────────────────

def _write_db_cache(
    payload: dict[str, Any], scan_time: str, *, rows_changed: bool
) -> None:
    """Best-effort Turso mirror; data/skew.json is the disk fallback.

    Durable per-session skew_history rows are skipped when no session was
    added; the snapshot + heartbeat run EVERY cycle so the staleness banner
    notices a silent writer (cf. feedback_service_health_heartbeat).
    """
    try:
        from db import writer
    except ImportError:
        return
    try:
        writer.ensure_no_replica_for_writers()
        if rows_changed:
            # Intraday points are display-only. Persisting one would make the
            # post-close run believe today's final session already exists.
            durable_rows = [
                row for row in payload["series"] if not row.get("is_intraday")
            ]
            writer.upsert_skew_rows(durable_rows, recorded_at=scan_time)
        writer.upsert_scan_snapshot("skew", scan_time, payload)
        writer.record_service_health("skew", "ok", finished_at=scan_time)
    except Exception as exc:  # noqa: BLE001 — best-effort mirror
        print(f"[skew] db cache non-fatal: {exc}", file=sys.stderr)


def _write_json_cache(payload: dict[str, Any]) -> None:
    SKEW_JSON.parent.mkdir(parents=True, exist_ok=True)
    SKEW_JSON.write_text(json.dumps(payload, indent=2))


def _read_history_rows() -> list[dict[str, Any]]:
    """Turso skew_history -> base rows (Turso-first read; the JSON file is a
    fallback mirror only — a fresh host has no data/skew.json while Turso
    holds the full backfilled series)."""
    try:
        from db.client import get_db

        rows = get_db().execute(
            "SELECT date, expiry, dte, put_iv, call_iv, ratio FROM skew_history ORDER BY date"
        ).fetchall()
        keys = ("date", "expiry", "dte", "put_iv", "call_iv", "ratio")
        return [dict(zip(keys, row)) for row in rows]
    except Exception as exc:  # noqa: BLE001 — the JSON fallback still works
        print(f"[skew] turso rehydrate non-fatal: {exc}", file=sys.stderr)
        return []


def _read_json_cache() -> Optional[dict[str, Any]]:
    try:
        return json.loads(SKEW_JSON.read_text())
    except (OSError, ValueError):
        return None


# ── session fetching ──────────────────────────────────────────────

def _fetch_expiry_wings(
    client: Any, expiry: date, session: str
) -> Optional[tuple[date, float, float]]:
    """(actual_expiry, put_iv_25d, call_iv_25d) for one monthly, trying the
    listed date then the prior calendar day (holiday-shifted expiries trade
    a day early); None when neither chain prices both wings."""
    for candidate in (expiry, expiry - timedelta(days=1)):
        rows = parse_greek_rows(client.fetch_greeks(candidate.isoformat(), session))
        if not rows:
            continue
        put_iv = interpolate_iv_at_delta(rows, "put", _PUT_TARGET_DELTA)
        call_iv = interpolate_iv_at_delta(rows, "call", _CALL_TARGET_DELTA)
        if put_iv is not None and call_iv is not None:
            return candidate, put_iv, call_iv
    return None


def _fetch_session_row(client: Any, session: str) -> Optional[dict[str, Any]]:
    """One session's constant-maturity 30d base row from the bracketing
    monthly pair; a single usable bracket prices the row alone (clamped);
    None (stderr note) when neither bracket is usable."""
    as_of = date.fromisoformat(session)
    near_expiry, far_expiry = bracketing_monthly_expiries(as_of)
    near = _fetch_expiry_wings(client, near_expiry, session)
    far = near if far_expiry == near_expiry else _fetch_expiry_wings(client, far_expiry, session)

    if near is None and far is None:
        print(f"[skew] {session}: no usable chain in the bracketing pair; skipping", file=sys.stderr)
        return None
    near = near or far
    far = far or near

    near_dte = (near[0] - as_of).days
    far_dte = (far[0] - as_of).days
    put_iv = constant_maturity_leg(near[1], near_dte, far[1], far_dte)
    call_iv = constant_maturity_leg(near[2], near_dte, far[2], far_dte)
    ratio = put_iv / call_iv
    if not (_RATIO_SANE_MIN <= ratio <= _RATIO_SANE_MAX):
        # SPX 25d put/call is structurally put-over-call (~1.0-1.9). Outside
        # these bounds the upstream chain is garbage (seen on unfiltered
        # holidays: 25d call IV 60% on Christmas), never a market event.
        print(
            f"[skew] {session}: implausible ratio {ratio:.3f} "
            f"(put {put_iv:.4f} / call {call_iv:.4f}); rejecting chain",
            file=sys.stderr,
        )
        return None
    return {
        "date": session,
        "expiry": near[0].isoformat(),
        "dte": near_dte,
        "expiry_far": far[0].isoformat(),
        "dte_far": far_dte,
        "put_iv": put_iv,
        "call_iv": call_iv,
        "ratio": ratio,
    }


def _missing_sessions(last_stored: str, now: datetime) -> list[str]:
    """Trading days after ``last_stored`` up to the last FINALIZABLE ET
    session (16:45 boundary), ascending, bounded to the most recent 10."""
    # UW's 16:00 print can still settle immediately after the bell. Preserve
    # the established 16:45 ET finalization window even though the high-
    # frequency timer also fires through the UTC DST shoulder.
    last_completed = last_completed_session_date(now - _FINALIZATION_GRACE)
    if last_stored >= last_completed:
        return []
    anchor = datetime.strptime(last_completed, "%Y-%m-%d").replace(hour=16, minute=30)
    recent = get_last_n_trading_days(_MAX_GAP_SESSIONS, from_date=anchor)
    return sorted(day for day in recent if day > last_stored)


def _base_rows(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # .get: rows cached before the constant-maturity rework lack the far keys.
    return [{key: row.get(key) for key in _BASE_ROW_KEYS} for row in series]


def _scan_time_iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _embargo_path() -> Path:
    return SKEW_JSON.with_name("skew_uw_embargo.json")


def _is_uw_daily_quota(exc: BaseException) -> bool:
    return isinstance(exc, UWRateLimitError) and _UW_DAILY_LIMIT_MARKER in str(exc).lower()


def _uw_retry_at(exc: BaseException, now: datetime) -> str:
    """UW daily cap resets at 20:00 ET; other 429s get a 5-minute embargo."""
    if _is_uw_daily_quota(exc):
        local = now.astimezone(_ET)
        reset = local.replace(hour=_UW_DAILY_RESET_HOUR_ET, minute=0, second=0, microsecond=0)
        if local >= reset:
            reset = reset + timedelta(days=1)
        return reset.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return (now + timedelta(minutes=5)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_uw_embargo() -> Optional[datetime]:
    try:
        raw = json.loads(_embargo_path().read_text()).get("next_attempt_at")
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _save_uw_embargo(next_attempt_at: str) -> None:
    path = _embargo_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"next_attempt_at": next_attempt_at}))


def _clear_uw_embargo() -> None:
    _embargo_path().unlink(missing_ok=True)


def uw_embargo_until(now: datetime) -> Optional[datetime]:
    """Active UW-cap deadline, or None. Read-only — the parent owns clearing.

    Derived indicators read this to tell an explained parent lag apart from a
    genuinely broken parent.
    """
    deadline = _load_uw_embargo()
    return deadline if deadline is not None and now < deadline else None


def _uw_embargo_active(now: datetime) -> Optional[str]:
    deadline = _load_uw_embargo()
    if deadline is None or now >= deadline:
        if deadline is not None:
            _clear_uw_embargo()
        return None
    return deadline.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _record_cycle_error(exc: BaseException, *, now: datetime, next_attempt_at: str) -> None:
    message = str(exc).splitlines()[0].strip()[:300]
    try:
        from db import writer
    except ImportError:
        return
    try:
        writer.ensure_no_replica_for_writers()
        writer.record_service_health(
            "skew",
            "error",
            finished_at=_scan_time_iso(now),
            error={"message": message, "next_attempt_at": next_attempt_at},
        )
    except Exception as write_exc:  # noqa: BLE001 — telemetry must not mask the cycle
        print(f"[skew] error heartbeat non-fatal: {write_exc}", file=sys.stderr)


def _build_payload(
    scan_time: str,
    series: list[dict[str, Any]],
    *,
    stats_series: Optional[list[dict[str, Any]]] = None,
    market_status: str = "closed",
) -> dict[str, Any]:
    return {
        "scan_time": scan_time,
        "source": "unusual_whales",
        "count": len(series),
        "current": series[-1] if series else None,
        # A provisional move is compared with the completed-session
        # distribution; it must not move its own high/low/stddev baseline.
        "stats": compute_stats(stats_series if stats_series is not None else series),
        "series": series,
        "market_status": market_status,
    }


# ── orchestration ─────────────────────────────────────────────────

def run(client: Optional[Any] = None, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """Gap-filling incremental: fetch every missing completed session since
    the last stored date (bounded to the most recent 10), recompute the
    change series, and dual-write. No missing sessions → the cached payload
    is reused with a fresh scan_time and only the snapshot + heartbeat
    refresh (heartbeat-only fast path)."""
    now = now or datetime.now(timezone.utc)
    embargo_until = _uw_embargo_active(now)
    if embargo_until:
        cached = _read_json_cache()
        if cached:
            print(
                f"[skew] UW daily quota embargo until {embargo_until}; keeping last snapshot",
                file=sys.stderr,
            )
            _record_cycle_error(
                UWRateLimitError("You have hit your daily request limit of 40000 requests."),
                now=now,
                next_attempt_at=embargo_until,
            )
            return cached
    if client is None:
        client = _DefaultClient()

    try:
        return _run_cycle(client, now)
    except UWRateLimitError as exc:
        next_attempt = _uw_retry_at(exc, now)
        if _is_uw_daily_quota(exc):
            _save_uw_embargo(next_attempt)
        _record_cycle_error(exc, now=now, next_attempt_at=next_attempt)
        cached = _read_json_cache()
        if cached:
            print(
                f"[skew] {str(exc).splitlines()[0].strip()}; "
                f"keeping last snapshot until {next_attempt}",
                file=sys.stderr,
            )
            return cached
        raise


def _run_cycle(client: Any, now: datetime) -> dict[str, Any]:
    # Base series is the union of Turso history and the JSON mirror, keyed by
    # date (reads are Turso-first; the JSON file is only a fallback). Trusting
    # the JSON alone restarted the series at the 10-session gap bound on a
    # fresh host and clobbered the full snapshot (VPS first run, 2026-08-06).
    cached_payload = _read_json_cache() or {}
    cached_rows = cached_payload.get("series") or []
    cached_intraday = next(
        (row for row in reversed(cached_rows) if row.get("is_intraday")),
        None,
    )

    by_date = {row["date"]: row for row in _base_rows(_read_history_rows())}
    # Never promote the JSON mirror's provisional row into the durable base.
    # _base_rows intentionally strips metadata, so filter before calling it.
    for row in _base_rows([row for row in cached_rows if not row.get("is_intraday")]):
        by_date[row["date"]] = row
    base = [by_date[day] for day in sorted(by_date)]

    last_stored = base[-1]["date"] if base else ""
    missing = _missing_sessions(last_stored, now)
    scan_time = _scan_time_iso(now)

    if not missing:
        print("[skew] no missing sessions; refreshing snapshot only", file=sys.stderr)

    new_rows: list[dict[str, Any]] = []
    for session in missing:
        print(f"[skew] fetching {session}", file=sys.stderr)
        row = _fetch_session_row(client, session)
        if row:
            new_rows.append(row)

    completed_series = compute_change_series(base + new_rows)
    if not completed_series:
        raise ValueError("skew series computed to zero rows")

    state = market_state(now)
    is_open = bool(state.get("is_open"))
    market_status = "open" if is_open else "closed"
    current_session = now.astimezone(_ET).date().isoformat()
    intraday: Optional[dict[str, Any]] = None

    if is_open and completed_series[-1]["date"] < current_session:
        print(f"[skew] fetching intraday {current_session}", file=sys.stderr)
        live_row = _fetch_session_row(client, current_session)
        if live_row is None:
            # Do not advance scan_time or write a false-ok heartbeat. The last
            # good snapshot remains available and naturally ages to STALE.
            raise ValueError(f"no usable intraday skew chain for {current_session}")
        intraday = {
            **live_row,
            "change": live_row["ratio"] - completed_series[-1]["ratio"],
            "is_intraday": True,
            "as_of": scan_time,
        }
    elif (
        cached_intraday
        and cached_intraday.get("date") == current_session
        and completed_series[-1]["date"] < current_session
    ):
        # Between the close and the 16:45 ET finalization run, retain the last
        # good live sample instead of reverting the UI to yesterday.
        intraday = cached_intraday

    display_series = [*completed_series, intraday] if intraday else completed_series
    payload = _build_payload(
        scan_time,
        display_series,
        stats_series=completed_series,
        market_status=market_status,
    )
    _write_db_cache(payload, scan_time, rows_changed=bool(new_rows))
    _write_json_cache(payload)
    _clear_uw_embargo()
    return payload


# ── backfill ──────────────────────────────────────────────────────

def _existing_dates() -> set[str]:
    """Dates already in Turso skew_history (JSON cache fallback)."""
    try:
        from db.client import get_db

        rows = get_db().execute("SELECT date FROM skew_history").fetchall()
        return {row[0] for row in rows}
    except Exception as exc:  # noqa: BLE001 — fall back to the disk cache
        print(f"[skew] could not read existing dates from Turso: {exc}", file=sys.stderr)
        cached = _read_json_cache()
        return {row["date"] for row in (cached or {}).get("series") or []}


def _trading_days_between(start: str, end: str) -> list[str]:
    days: list[str] = []
    holidays_by_year: dict[int, set] = {}
    current = date.fromisoformat(start)
    last = date.fromisoformat(end)
    while current <= last:
        if current.year not in holidays_by_year:
            holidays_by_year[current.year] = load_holidays(current.year)
        if current.weekday() < 5 and current.isoformat() not in holidays_by_year[current.year]:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def run_backfill(
    start: Optional[str] = None,
    client: Optional[Any] = None,
    *,
    now: Optional[datetime] = None,
    throttle_s: float = _BACKFILL_THROTTLE_S,
) -> dict[str, Any]:
    """One-shot history build: every trading day from ``start`` (default
    2023-09-06, the UW history floor) through the last completed session.
    Resumable — dates already in Turso are skipped; progress is checkpointed
    every 50 sessions so an interrupted run loses at most one batch."""
    now = now or datetime.now(timezone.utc)
    if client is None:
        client = _DefaultClient()

    existing = _existing_dates()
    cached_series = (_read_json_cache() or {}).get("series") or []
    base = [row for row in _base_rows(cached_series) if row["date"] in existing]
    known = existing | {row["date"] for row in base}

    sessions = _trading_days_between(start or _BACKFILL_START, last_completed_session_date(now))
    todo = [day for day in sessions if day not in known]
    print(f"[skew] backfill: {len(todo)} sessions to fetch ({len(known)} already present)", file=sys.stderr)

    def checkpoint() -> dict[str, Any]:
        series = compute_change_series(base)
        scan_time = _scan_time_iso(datetime.now(timezone.utc))
        payload = _build_payload(scan_time, series)
        # Fresh connection per checkpoint: the singleton's Hrana stream dies
        # server-side while the loop spends minutes on UW fetches, and every
        # later statement on it 404s ("stream not found") — Hrana rule 3.
        try:
            from db.client import reset_connection

            reset_connection()
        except ImportError:
            pass
        _write_db_cache(payload, scan_time, rows_changed=True)
        _write_json_cache(payload)
        return payload

    fetched = 0
    for index, session in enumerate(todo, 1):
        row = _fetch_session_row(client, session)
        if row:
            base.append(row)
            fetched += 1
        if index % _BACKFILL_CHECKPOINT_EVERY == 0:
            print(f"[skew] backfill progress: {index}/{len(todo)}", file=sys.stderr)
            checkpoint()
        time.sleep(throttle_s)

    if not base:
        raise ValueError("skew backfill produced zero rows")
    payload = checkpoint()
    print(f"[skew] backfill done: {fetched} sessions fetched, {payload['count']} total", file=sys.stderr)
    return payload


# ── CLI ───────────────────────────────────────────────────────────

def _print_summary(payload: dict[str, Any]) -> None:
    current = payload.get("current") or {}
    stats = payload.get("stats") or {}
    change = current.get("change")
    print(f"\nSKEW — {payload['count']} sessions through {current.get('date')}", file=sys.stderr)
    print(
        f"  change {change:+.4f}  ratio {current.get('ratio'):.4f}"
        if change is not None
        else f"  change n/a  ratio {current.get('ratio')}",
        file=sys.stderr,
    )
    if stats:
        print(
            f"  avg {stats['avg']:+.4f}  pstdev {stats['stddev']:.4f}  "
            f"high {stats['high']:+.4f}  low {stats['low']:+.4f}",
            file=sys.stderr,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SPX 1M 25-delta put/call IV ratio change (Unusual Whales greeks)"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    parser.add_argument(
        "--backfill",
        nargs="?",
        const=_BACKFILL_START,
        default=None,
        metavar="START",
        help=f"One-shot history build from START (default {_BACKFILL_START})",
    )
    args = parser.parse_args()

    payload = run_backfill(args.backfill) if args.backfill else run()
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_summary(payload)


if __name__ == "__main__":
    main()
