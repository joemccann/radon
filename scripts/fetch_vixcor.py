#!/usr/bin/env python3
"""VIXCOR Indicator — 20-session correlation between VIX and Cboe COR3M.

Descriptive regime read, not a forecast: a low reading marks a quiet,
range-bound VIX tape. See docs/indicators/vixcor.md section 0 for the
validation study that rejected the predictive framing.

Source: Cboe CDN VIX_History.csv + Turso cor_history.cor3m.
Output is dual-written to Turso vixcor_history + data/vixcor.json;
VIX closes also land in price_history_daily as symbol='VIX', source='cboe'.

Usage:
    python3 scripts/fetch_vixcor.py              # human summary (stderr)
    python3 scripts/fetch_vixcor.py --json       # JSON to stdout
    python3 scripts/fetch_vixcor.py --backfill   # full VIX history write
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

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

# The indicator's math and its named constants both live in the pure module;
# they are re-exported here because docs/indicators/vixcor.md section B.3
# specifies them as this job's module constants.
from scripts.lib.vixcor_math import (  # noqa: E402,F401
    BREAKDOWN_EXIT,
    BREAKDOWN_TRIGGER,
    COUPLED_COV_FLOOR,
    DRAWUP_MATERIAL_THRESHOLD,
    EPISODE_DEBOUNCE_SESSIONS,
    EPISODE_MERGE_SESSIONS,
    FORWARD_HORIZONS,
    LOOSENING_FLOOR,
    MIN_OBSERVATIONS,
    WINDOW,
    attach_forward_drawups,
    build_current,
    classify_regime,
    compute_corr_series,
    compute_forward_stats,
    compute_stats,
    corr_window,
    count_vix_sessions_between,
    detect_episodes,
    episode_membership,
    join_series,
)

# ── constants ─────────────────────────────────────────────────────
VIXCOR_JSON = _PROJECT_DIR / "data" / "vixcor.json"
COR_JSON = _PROJECT_DIR / "data" / "cor.json"

VIX_SYMBOL = "VIX"
VIX_SOURCE = "cboe"
SERVICE = "vixcor"

PARENT_LAG_GRACE_SESSIONS = 2   # cor3m may be this far behind before it is an error
PARENT_READ_PAGE_ROWS = 2000    # keyset page size for the cor_history read

# Mirrors radon-vixcor.timer (OnCalendar=*-*-* 02:35:00 UTC) so a stale-parent
# heartbeat can name the next attempt instead of hardcoding cadence copy.
TIMER_HOUR_UTC = 2
TIMER_MINUTE_UTC = 35

STATUS_OK = "ok"
STATUS_HOLDING = "holding"
STATUS_STALE_PARENT = "stale_parent"

# ── parsing ───────────────────────────────────────────────────────

def parse_index_csv(text: str) -> list[dict[str, Any]]:
    """Cboe daily-prices CSV → ascending [{date: "YYYY-MM-DD", value: float}].

    Dates arrive MM/DD/YYYY; header and malformed rows (bad date, missing
    or empty CLOSE) are skipped. CLOSE only.
    """
    rows: list[dict[str, Any]] = []
    for raw in csv.DictReader(io.StringIO(text)):
        try:
            date = datetime.strptime((raw.get("DATE") or "").strip(), "%m/%d/%Y")
            value = float(raw["CLOSE"])
        except (KeyError, TypeError, ValueError):
            continue
        rows.append({"date": date.date().isoformat(), "value": value})
    rows.sort(key=lambda r: r["date"])
    return rows


# ── parent (cor_history.cor3m) ────────────────────────────────────

def _load_cor3m_from_json() -> list[dict[str, Any]]:
    """data/cor.json fallback: non-null cor3m closes, ascending."""
    try:
        payload = json.loads(COR_JSON.read_text())
    except (OSError, ValueError):
        return []
    rows = [
        {"date": row["date"], "value": float(row["cor3m"])}
        for row in (payload.get("series") or [])
        if row.get("cor3m") is not None
    ]
    rows.sort(key=lambda r: r["date"])
    return rows


def load_cor3m_rows() -> list[dict[str, Any]]:
    """Turso cor_history first; data/cor.json series fallback when empty.

    Keyset-paginated on date (Hrana I/O bounding): ~5,190 rows is three pages
    instead of one unbounded stream read.
    """
    try:
        from db.client import get_db

        db = get_db()
        rows: list[dict[str, Any]] = []
        cursor = ""
        while True:
            page = db.execute(
                "SELECT date, cor3m FROM cor_history "
                "WHERE date > ? AND cor3m IS NOT NULL "
                "ORDER BY date LIMIT ?",
                (cursor, PARENT_READ_PAGE_ROWS),
            ).fetchall()
            if not page:
                break
            rows.extend({"date": row[0], "value": float(row[1])} for row in page)
            cursor = page[-1][0]
            if len(page) < PARENT_READ_PAGE_ROWS:
                break
        if rows:
            return rows
    except Exception as exc:  # noqa: BLE001 — JSON fallback still works
        print(f"[vixcor] turso cor3m rehydrate non-fatal: {exc}", file=sys.stderr)
    return _load_cor3m_from_json()


def latest_stored_vix_date() -> Optional[str]:
    """MAX(date) already stored for symbol='VIX', or None on a first install."""
    try:
        from db.client import get_db

        row = get_db().execute(
            "SELECT MAX(date) FROM price_history_daily WHERE symbol = ?",
            (VIX_SYMBOL,),
        ).fetchone()
    except Exception as exc:  # noqa: BLE001 — an unknown max means write it all
        print(f"[vixcor] stored VIX max-date probe non-fatal: {exc}", file=sys.stderr)
        return None
    return row[0] if row and row[0] else None


# ── persistence ───────────────────────────────────────────────────

def load_prior_payload() -> Optional[dict[str, Any]]:
    """Last dual-written payload from data/vixcor.json, if present."""
    try:
        return json.loads(VIXCOR_JSON.read_text())
    except (OSError, ValueError):
        return None


def persist_json(payload: dict[str, Any]) -> None:
    """Atomic write of data/vixcor.json."""
    VIXCOR_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = VIXCOR_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, VIXCOR_JSON)


def _vix_tail(vix_rows: list[dict[str, Any]], *, backfill: bool) -> list[dict[str, Any]]:
    """price_history_daily rows to write: the bounded tail, or everything."""
    tail = vix_rows
    if not backfill:
        stored_max = latest_stored_vix_date()
        if stored_max:
            tail = [row for row in vix_rows if row["date"] >= stored_max]
    return [
        {"date": row["date"], "close": row["value"], "source": VIX_SOURCE} for row in tail
    ]


def _write_db(
    payload: dict[str, Any],
    scan_time: str,
    *,
    rows_changed: bool,
    vix_tail: Optional[list[dict[str, Any]]] = None,
    health_error: Optional[dict[str, Any]] = None,
) -> None:
    """Snapshot + heartbeat every cycle; row upserts only when the source moved.

    The snapshot and heartbeat run even on the 304 path so a silently dead
    writer trips the staleness banner (feedback_service_health_heartbeat).
    """
    if writer is None:
        return
    row_error: Optional[dict[str, Any]] = None
    try:
        writer.ensure_no_replica_for_writers()
        if rows_changed:
            if vix_tail:
                writer.upsert_price_history_rows(VIX_SYMBOL, vix_tail)
            writer.upsert_vixcor_rows(payload["series"], recorded_at=scan_time)
    except Exception as exc:  # noqa: BLE001
        # R-192: see fetch_ivrank._write_db — a failed row upsert must not
        # take the snapshot and the heartbeat down with it and still exit 0.
        print(f"[vixcor] row upsert failed: {exc}", file=sys.stderr)
        row_error = {
            "message": f"vixcor row upsert failed: {exc}",
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
        print(f"[vixcor] db cache non-fatal: {exc}", file=sys.stderr)


# ── degradation ───────────────────────────────────────────────────

def _market_status(now: datetime) -> str:
    try:
        from utils.market_calendar import market_state

        return "open" if market_state(now).get("is_open") else "closed"
    except Exception:  # noqa: BLE001 — daily post-close job defaults closed
        return "closed"


def _expected_session(now: datetime) -> str:
    from utils.market_calendar import last_completed_session_date

    return last_completed_session_date(now)


def _next_scheduled_run(now: datetime) -> str:
    """The next radon-vixcor.timer fire, as the heartbeat's next_attempt_at."""
    stamp = now.astimezone(timezone.utc).replace(
        hour=TIMER_HOUR_UTC, minute=TIMER_MINUTE_UTC, second=0, microsecond=0
    )
    if stamp <= now:
        stamp += timedelta(days=1)
    return stamp.isoformat().replace("+00:00", "Z")


def _count_calendar_sessions_between(after: str, through: str) -> int:
    """Trading sessions in (after, through] on the US equity calendar.

    The changed-source path counts on the VIX rows it just fetched; a 304 has
    no fresh rows, and VIX trades the equity session calendar, so the
    holiday-aware calendar reaches the same count by another route.
    """
    from utils.market_calendar import _is_trading_day

    cursor = date.fromisoformat(after) + timedelta(days=1)
    last = date.fromisoformat(through)
    sessions = 0
    while cursor <= last:
        if _is_trading_day(datetime(cursor.year, cursor.month, cursor.day)):
            sessions += 1
        cursor += timedelta(days=1)
    return sessions


def _stale_parent_error(
    lag_sessions: Any, *, parent_as_of: Any, expected_session: Any, now: datetime
) -> dict[str, Any]:
    """The service_health error a broken parent writes."""
    return {
        "message": (
            f"cor_history.cor3m is {lag_sessions} sessions behind "
            f"{expected_session}; serving through {parent_as_of}"
        ),
        "next_attempt_at": _next_scheduled_run(now),
    }


def _parent_lag_state(
    lag_sessions: int, *, parent_as_of: str, expected_session: str, now: datetime
) -> tuple[str, Optional[dict[str, Any]]]:
    """Three-state ladder: ok, a routine hold, or a genuinely broken parent.

    A one-session cor3m lag is routine publication timing, so it holds with an
    OK heartbeat: a derived child that pages on parent lag pages every run
    (feedback_derived_indicator_parent_embargo). Beyond the grace band the
    heartbeat goes to error but the job still must NOT raise, because a failed
    unit is a second page for one condition.
    """
    if lag_sessions <= 0:
        return STATUS_OK, None
    if lag_sessions <= PARENT_LAG_GRACE_SESSIONS:
        print(
            f"[vixcor] cor3m {lag_sessions} session(s) behind {expected_session}; "
            f"holding at {parent_as_of}",
            file=sys.stderr,
        )
        return STATUS_HOLDING, None
    return STATUS_STALE_PARENT, _stale_parent_error(
        lag_sessions,
        parent_as_of=parent_as_of,
        expected_session=expected_session,
        now=now,
    )


def restate_cached_payload(
    cached: dict[str, Any], *, scan_time: str, now: datetime
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    """Re-age the freshness verdict of the payload a 304 reuses.

    A conditional GET reuses the previous numbers, so it must reuse — and
    re-derive — the previous verdict. Writing a bare ``ok`` here clears a
    stale_parent heartbeat and, because service_health upserts
    ``last_error = excluded.last_error``, its stored text as well; weekend and
    holiday runs are 304s by design, and the watchdog only escalates after two
    CONSECUTIVE error cycles, so a parent breakage spanning a weekend would
    silently de-escalate. Re-running the ladder against the cached
    ``parent_as_of`` also stops a frozen source from reading as fresh: only
    ``scan_time`` may advance while the data stands still.
    """
    payload = {**cached, "scan_time": scan_time}
    parent_as_of = cached.get("parent_as_of")
    if not parent_as_of:
        return payload, _cached_health_error(cached, now)

    expected_session = _expected_session(now)
    lag_sessions = _count_calendar_sessions_between(parent_as_of, expected_session)
    status, health_error = _parent_lag_state(
        lag_sessions,
        parent_as_of=parent_as_of,
        expected_session=expected_session,
        now=now,
    )
    payload.update(
        {
            "status": status,
            "expected_session": expected_session,
            "lag_sessions": lag_sessions,
        }
    )
    return payload, health_error


def _cached_health_error(
    cached: dict[str, Any], now: datetime
) -> Optional[dict[str, Any]]:
    """The heartbeat error a payload still implies when its lag cannot be re-derived."""
    if cached.get("status") != STATUS_STALE_PARENT:
        return None
    return _stale_parent_error(
        cached.get("lag_sessions"),
        parent_as_of=cached.get("parent_as_of"),
        expected_session=cached.get("expected_session"),
        now=now,
    )


# ── payload ───────────────────────────────────────────────────────

def build_payload(
    joined: list[dict[str, Any]],
    *,
    scan_time: str,
    source_last_modified: dict[str, Optional[str]],
    status: str,
    expected_session: str,
    lag_sessions: int,
    parent_as_of: str,
    vix_as_of: str,
    market_status: str,
) -> dict[str, Any]:
    """Full API contract for the vixcor snapshot / JSON fallback."""
    series = compute_corr_series(joined)
    episodes = attach_forward_drawups(series, detect_episodes(series))
    stats = compute_stats(series)
    shaded = episode_membership(series, episodes)
    return {
        "scan_time": scan_time,
        "source_last_modified": source_last_modified,
        "status": status,
        "as_of": series[-1]["date"] if series else None,
        "parent_as_of": parent_as_of,
        "vix_as_of": vix_as_of,
        "expected_session": expected_session,
        "lag_sessions": lag_sessions,
        "market_status": market_status,
        "window": WINDOW,
        "count": len(series),
        "corr_count": sum(1 for row in series if row["corr20"] is not None),
        "current": build_current(series, episodes, stats),
        "stats": stats,
        "episodes": episodes,
        "forward_stats": compute_forward_stats(series, episodes),
        "series": [{**row, "episode": row["date"] in shaded} for row in series],
    }


# ── orchestration ─────────────────────────────────────────────────

def run(
    client: Optional[Any] = None,
    *,
    now: Optional[datetime] = None,
    backfill: bool = False,
) -> dict[str, Any]:
    """Fetch the Cboe VIX history, join COR3M, compute, dual-write, return.

    Conditional GET on the VIX file: a 304 with a cached payload reuses that
    payload with a fresh scan_time, re-ages its freshness verdict, and
    refreshes the snapshot + heartbeat only. Any change rebuilds the joined
    series from scratch.
    """
    now = now or datetime.now(timezone.utc)
    scan_time = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if client is None:
        from clients.cboe_client import CboeClient

        client = CboeClient()

    cached = load_prior_payload()
    cached_stamp = ((cached or {}).get("source_last_modified") or {}).get("vix")
    text, last_modified = client.fetch_history(VIX_SYMBOL, if_modified_since=cached_stamp)

    if cached and text is None:
        print("[vixcor] all sources unchanged (304); refreshing snapshot only", file=sys.stderr)
        payload, health_error = restate_cached_payload(cached, scan_time=scan_time, now=now)
        _write_db(payload, scan_time, rows_changed=False, health_error=health_error)
        persist_json(payload)
        return payload

    if text is None:
        text, last_modified = client.fetch_history(VIX_SYMBOL)
    if text is None:
        raise ValueError("vixcor: no VIX history returned and no cached payload")

    vix_rows = parse_index_csv(text)
    cor3m_rows = load_cor3m_rows()
    joined = join_series(vix_rows, cor3m_rows)
    if not joined:
        raise ValueError("vixcor series computed to zero rows")

    expected_session = _expected_session(now)
    parent_as_of = max(row["date"] for row in cor3m_rows)
    vix_as_of = max(row["date"] for row in vix_rows)
    lag_sessions = count_vix_sessions_between(
        [row["date"] for row in vix_rows], parent_as_of, expected_session
    )
    status, health_error = _parent_lag_state(
        lag_sessions,
        parent_as_of=parent_as_of,
        expected_session=expected_session,
        now=now,
    )

    payload = build_payload(
        joined,
        scan_time=scan_time,
        source_last_modified={"vix": last_modified},
        status=status,
        expected_session=expected_session,
        lag_sessions=lag_sessions,
        parent_as_of=parent_as_of,
        vix_as_of=vix_as_of,
        market_status=_market_status(now),
    )
    print(
        f"[vixcor] {payload['count']} joined sessions through {payload['as_of']} "
        f"({payload['corr_count']} with a 20-session reading)",
        file=sys.stderr,
    )
    _write_db(
        payload,
        scan_time,
        rows_changed=True,
        vix_tail=_vix_tail(vix_rows, backfill=backfill),
        health_error=health_error,
    )
    persist_json(payload)
    return payload


# ── CLI ───────────────────────────────────────────────────────────

def _print_summary(payload: dict[str, Any]) -> None:
    current = payload.get("current") or {}
    episodes = payload.get("episodes") or []
    corr20 = current.get("corr20")
    print(
        f"\nVIXCOR — {payload['count']} joined sessions through {payload.get('as_of')} "
        f"[{payload.get('status')}]",
        file=sys.stderr,
    )
    print(
        f"  corr20     {corr20:+.4f}" if corr20 is not None else "  corr20     n/a",
        file=sys.stderr,
    )
    print(f"  regime     {current.get('regime')}", file=sys.stderr)
    print(
        f"  vix/cor3m  {current.get('vix_close')} / {current.get('cor3m_close')}",
        file=sys.stderr,
    )
    print(f"  episodes   {len(episodes)} detected", file=sys.stderr)
    print(f"  lag        {payload.get('lag_sessions')} session(s)", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="VIXCOR — 20-session VIX x Cboe COR3M correlation"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    parser.add_argument(
        "--backfill", action="store_true", help="Write the full Cboe VIX history"
    )
    args = parser.parse_args()

    payload = run(backfill=args.backfill)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_summary(payload)


if __name__ == "__main__":
    main()
