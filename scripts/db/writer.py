"""libSQL writer helpers for Python schedulers.

Symmetric to scripts/db/writer.js. Each function takes a ready-to-write
payload (dict, list, etc.) and returns once the row has reached the
embedded replica (sync to cloud is async, single-digit-second).

Schedulers should use these helpers from inside their existing
file-write code paths so the migration is dual-write — the JSON file
remains authoritative until Phase 6 retires it.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

try:
    # When imported as `scripts.db.writer` from project root.
    from .client import get_db
    from ..clients.journal_basis import normalize_expiry_compact
except ImportError:  # pragma: no cover
    # When imported flat after sys.path.insert(scripts/) like the existing
    # services do (cta_sync_service.py et al).
    from db.client import get_db  # type: ignore[no-redef]
    from clients.journal_basis import normalize_expiry_compact  # type: ignore[no-redef]


def _hrana_execute(sql: str, args: tuple = (), *, timeout: float | None = None) -> None:
    """Bounded single-statement write via ``db.hrana_http`` (real socket timeout).

    Lazy import keeps stripped envs importable and lets tests monkeypatch
    ``db.hrana_http.hrana_execute`` without reloading this module.
    """
    try:
        from .hrana_http import HRANA_TIMEOUT_S, hrana_execute
    except ImportError:  # pragma: no cover
        from db.hrana_http import HRANA_TIMEOUT_S, hrana_execute  # type: ignore[no-redef]

    hrana_execute(sql, args, timeout=HRANA_TIMEOUT_S if timeout is None else timeout)


# Shared SQL for the daemon journal path (fill_monitor / journal_sync). Kept
# as a module constant so mock tests assert shape without parsing source.
JOURNAL_UPSERT_SQL = """
INSERT INTO journal (trade_id, payload, filled_at, written_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(trade_id) DO UPDATE SET
  payload    = excluded.payload,
  filled_at  = excluded.filled_at,
  written_at = excluded.written_at
"""

PORTFOLIO_SNAPSHOT_UPSERT_SQL = """
INSERT OR REPLACE INTO portfolio_snapshots (taken_at, payload)
VALUES (?, ?)
"""


def ensure_no_replica_for_writers() -> None:
    """Belt-and-suspenders replica kill switch for writer entry points.

    Since DUR-07 the embedded replica is OFF by default (opt-in only via
    RADON_DB_USE_REPLICA=1 in db.client), so this is normally redundant.
    Kept because writers must never open the replica even if a future
    operator opts a host in: RADON_DB_NO_REPLICA beats RADON_DB_USE_REPLICA.
    No-op if already set.
    """
    os.environ.setdefault("RADON_DB_NO_REPLICA", "1")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def upsert_menthorq_cta(date_str: str, payload: dict[str, Any], fetched_at: Optional[str] = None) -> None:
    """Persist a CTA cache row keyed by ET trading day."""
    db = get_db()
    db.execute(
        """
        INSERT INTO menthorq_cta (date, payload, fetched_at)
        VALUES (?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
          payload    = excluded.payload,
          fetched_at = excluded.fetched_at
        """,
        (date_str, json.dumps(payload), fetched_at or _now_iso()),
    )
    db.commit()


def upsert_cri_snapshot(date_str: str, taken_at: str, payload: dict[str, Any]) -> None:
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO cri_snapshots (date, taken_at, payload)
        VALUES (?, ?, ?)
        """,
        (date_str, taken_at, json.dumps(payload)),
    )
    db.commit()


def upsert_breadth_snapshot(date_str: str, taken_at: str, payload: dict[str, Any]) -> None:
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO breadth_snapshots (date, taken_at, payload)
        VALUES (?, ?, ?)
        """,
        (date_str, taken_at, json.dumps(payload)),
    )
    db.commit()


def upsert_gex_snapshot(ticker: str, scan_time: str, payload: dict[str, Any]) -> None:
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO gex_snapshots (ticker, scan_time, payload)
        VALUES (?, ?, ?)
        """,
        (ticker, scan_time, json.dumps(payload)),
    )
    db.commit()


def upsert_vcg_snapshot(scan_time: str, payload: dict[str, Any]) -> None:
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO vcg_snapshots (scan_time, payload)
        VALUES (?, ?)
        """,
        (scan_time, json.dumps(payload)),
    )
    db.commit()


def upsert_gamma_rotation_snapshot(scan_time: str, payload: dict[str, Any]) -> None:
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO gamma_rotation_snapshots (scan_time, payload)
        VALUES (?, ?)
        """,
        (scan_time, json.dumps(payload)),
    )
    db.commit()


# Only the newest row is ever read (ORDER BY taken_at DESC LIMIT 1). Keep a
# small buffer for safety/diagnostics and prune the rest so the table can't grow
# unbounded — it had reached ~82k rows (~800MB, 25-min nightly backups) before
# this was added. taken_at is the ISO-timestamp PK, so the cutoff DELETE is an
# indexed range scan.
PORTFOLIO_SNAPSHOT_RETENTION = 2000


def prune_portfolio_snapshots(retention: int = PORTFOLIO_SNAPSHOT_RETENTION) -> int:
    """Delete all but the newest ``retention`` portfolio_snapshots rows.

    Returns rows deleted (0 when the driver doesn't report a rowcount). Call
    from the daily monitor-daemon slot (flex_token_check) — NOT inline in the
    per-sync write path: the libsql client has no timeout, so a large DELETE
    during a Turso write-degradation window could hang save_portfolio and get
    the sync subprocess SIGKILL'd.
    """
    db = get_db()
    cursor = db.execute(
        """
        DELETE FROM portfolio_snapshots
        WHERE taken_at < (
            SELECT MIN(taken_at) FROM (
                SELECT taken_at FROM portfolio_snapshots
                ORDER BY taken_at DESC LIMIT ?
            )
        )
        """,
        (retention,),
    )
    db.commit()
    deleted = getattr(cursor, "rowcount", None)
    return deleted if isinstance(deleted, int) and deleted >= 0 else 0


# libsql_experimental has NO client timeout and holds the GIL while blocked.
# Fleet archive 2026-07-12 lessons:
#   * multi-row DELETE IN (...) of fat JSON can hang Turso under load
#   * concurrent archive + retention oneshots saturate Turso → every op
#     times out for ~1h until systemd kills the unit
#   * single-key DELETE is ~40ms when the DB is quiet
# Prefer a small subquery batch first; fall back to single-key on timeout.
PORTFOLIO_DELETE_BATCH = 50
_DELETE_HTTP_TIMEOUT_S = 20.0
_DELETE_MAX_ATTEMPTS = 2
_DELETE_TRANSPORT_MARKERS = (
    "hrana",
    "connection",
    "stream",
    "timeout",
    "timed out",
    "closed",
    "reset",
    "urlerror",
    "brokenpipe",
)


def _is_delete_transport_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _DELETE_TRANSPORT_MARKERS)


def _hrana_with_retry(fn, *, attempts: int = _DELETE_MAX_ATTEMPTS):
    import time

    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — transport class only retried
            last = exc
            try:
                from .hrana_http import HranaHttpError
            except ImportError:  # pragma: no cover
                from db.hrana_http import HranaHttpError  # type: ignore[no-redef]
            if (
                not isinstance(exc, HranaHttpError)
                and not _is_delete_transport_error(exc)
            ) or attempt + 1 >= attempts:
                raise
            time.sleep(0.5 * (attempt + 1))
    assert last is not None
    raise last


def delete_portfolio_snapshots_before(cutoff: str, batch_size: int = PORTFOLIO_DELETE_BATCH) -> int:
    """Delete portfolio_snapshots rows with ``taken_at < cutoff``.

    Owned by the archive pipeline (export + verify off-box BEFORE calling this).
    Payload-free: only selects/deletes ``taken_at`` keys in batches so catch-up
    ``--delete-only`` runs stay memory-cheap after B2 already holds the archive.
    Strategy (quiet Turso, measured 2026-07-12):
      1. DELETE ... WHERE taken_at IN (SELECT ... LIMIT N)  — fast path
      2. on transport timeout, fall back to single-key DELETEs for that page
    Bounded Hrana HTTP only. Resumable across process restarts.
    """
    try:
        from .hrana_http import hrana_execute, hrana_query
    except ImportError:  # pragma: no cover
        from db.hrana_http import hrana_execute, hrana_query  # type: ignore[no-redef]

    total = 0
    use_batch = True
    while True:
        # Probe remaining work cheaply — also the loop exit when empty.
        probe = _hrana_with_retry(
            lambda: hrana_query(
                "SELECT taken_at FROM portfolio_snapshots "
                "WHERE taken_at < ? ORDER BY taken_at LIMIT 1",
                (cutoff,),
                timeout=_DELETE_HTTP_TIMEOUT_S,
            )
        )
        if not probe:
            return total

        if use_batch:
            try:
                _hrana_with_retry(
                    lambda: hrana_execute(
                        "DELETE FROM portfolio_snapshots WHERE taken_at IN ("
                        "SELECT taken_at FROM portfolio_snapshots "
                        "WHERE taken_at < ? ORDER BY taken_at LIMIT ?)",
                        (cutoff, batch_size),
                        timeout=_DELETE_HTTP_TIMEOUT_S,
                    )
                )
                # Best-effort progress accounting (Hrana has no rowcount).
                total += batch_size
                continue
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[portfolio-delete] batch={batch_size} failed ({exc}); "
                    "falling back to single-key for this page",
                    flush=True,
                )
                use_batch = False

        # Single-key fallback page.
        rows = _hrana_with_retry(
            lambda: hrana_query(
                "SELECT taken_at FROM portfolio_snapshots "
                "WHERE taken_at < ? ORDER BY taken_at LIMIT ?",
                (cutoff, batch_size),
                timeout=_DELETE_HTTP_TIMEOUT_S,
            )
        )
        ids = [str(r[0]) for r in rows if r and r[0] is not None]
        if not ids:
            return total
        page_deleted = 0
        for taken_at in ids:
            try:
                _hrana_with_retry(
                    lambda ta=taken_at: hrana_execute(
                        "DELETE FROM portfolio_snapshots WHERE taken_at = ?",
                        (ta,),
                        timeout=_DELETE_HTTP_TIMEOUT_S,
                    )
                )
                total += 1
                page_deleted += 1
            except Exception as exc:  # noqa: BLE001 — leave for next run
                print(f"[portfolio-delete] skip {taken_at}: {exc}", flush=True)
        if page_deleted == 0:
            # Entire page wedged — stop so the unit does not thrash for 1h.
            print(
                f"[portfolio-delete] abort: 0/{len(ids)} keys deleted in page "
                f"(cutoff={cutoff}); will retry next run",
                flush=True,
            )
            return total
        # If single-key is working, try batch again next loop.
        if page_deleted == len(ids):
            use_batch = True
    return total


def upsert_portfolio_snapshot(taken_at: str, payload: dict[str, Any]) -> None:
    """Single-row portfolio snapshot upsert over bounded Hrana HTTP.

    High-volume path (``ib_sync`` dual-write). The bulk DELETE / prune paths
    already ride hrana; keeping the INSERT on the same transport means a slow
    Turso cannot hang the sync subprocess without a socket bound.
    """
    _hrana_execute(
        PORTFOLIO_SNAPSHOT_UPSERT_SQL,
        (taken_at, json.dumps(payload)),
    )


def upsert_cash_flow(
    txn_id: str,
    date_str: str,
    txn_type: str,
    amount: float,
    currency: str = "USD",
    description: Optional[str] = None,
    raw_type: Optional[str] = None,
) -> None:
    """Persist one cash transaction (deposit / withdrawal / dividend / etc).

    `amount` is signed (positive = inflow into account, negative = outflow).
    Idempotent on `txn_id` (IB transactionID), so re-running the Flex pull
    after a partial-day refresh is a no-op for already-seen rows.
    """
    db = get_db()
    db.execute(
        """
        INSERT INTO cash_flows (id, date, type, amount, currency, description, raw_type, synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          date        = excluded.date,
          type        = excluded.type,
          amount      = excluded.amount,
          currency    = excluded.currency,
          description = excluded.description,
          raw_type    = excluded.raw_type,
          synced_at   = excluded.synced_at
        """,
        (txn_id, date_str, txn_type, float(amount), currency, description, raw_type, _now_iso()),
    )
    db.commit()


def _journal_payload_with_compact_expiry(payload: dict[str, Any]) -> dict[str, Any]:
    """Journal option rows must persist compact ``YYYYMMDD`` expiry.

    Writer-level chokepoint covering every Python emitter (journal_sync,
    journal_rehydrate, fill_monitor, ib_execute, the backfill script) — an
    ISO ``YYYY-MM-DD`` row does not lot-match in fromJournal.ts.
    """
    expiry = payload.get("expiry")
    compact = normalize_expiry_compact(expiry)
    if compact == expiry:
        return payload
    return {**payload, "expiry": compact}


def upsert_journal_entry(trade_id: str, payload: dict[str, Any], filled_at: Optional[str] = None) -> None:
    """Upsert one journal row over bounded Hrana HTTP (real socket timeout).

    Used by the long-lived monitor daemon (fill_monitor / journal_sync) and
    oneshot rehydrate/backfill scripts. Sync ``libsql_experimental`` has no
    client timeouts and holds the GIL while blocked — a hung Turso call here
    would stall the entire daemon cycle.
    """
    payload = _journal_payload_with_compact_expiry(payload)
    _hrana_execute(
        JOURNAL_UPSERT_SQL,
        (trade_id, json.dumps(payload), filled_at, _now_iso()),
    )


def upsert_discover_snapshot(scan_time: str, payload: dict[str, Any]) -> None:
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO discover_snapshots (scan_time, payload)
        VALUES (?, ?)
        """,
        (scan_time, json.dumps(payload)),
    )
    db.commit()


def upsert_analyst_ratings(ticker: str, fetched_at: str, payload: dict[str, Any]) -> None:
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO analyst_ratings (ticker, fetched_at, payload)
        VALUES (?, ?, ?)
        """,
        (ticker, fetched_at, json.dumps(payload)),
    )
    db.commit()


def upsert_oi_changes(scan_time: str, payload: dict[str, Any]) -> None:
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO oi_changes (scan_time, payload)
        VALUES (?, ?)
        """,
        (scan_time, json.dumps(payload)),
    )
    db.commit()


SCAN_SNAPSHOT_KEEP = 30


def upsert_scan_snapshot(service: str, scan_time: str, payload: dict[str, Any]) -> None:
    """Generic latest-scan mirror for whole-file scans (scan_snapshots table:
    leap-scan, garch-scan, flow-surprise). Prunes to the newest
    SCAN_SNAPSHOT_KEEP rows per service so the table stays bounded."""
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO scan_snapshots (service, scan_time, payload)
        VALUES (?, ?, ?)
        """,
        (service, scan_time, json.dumps(payload)),
    )
    db.execute(
        """
        DELETE FROM scan_snapshots
        WHERE service = ? AND scan_time NOT IN (
          SELECT scan_time FROM scan_snapshots
          WHERE service = ?
          ORDER BY scan_time DESC
          LIMIT ?
        )
        """,
        (service, service, SCAN_SNAPSHOT_KEEP),
    )
    db.commit()


def upsert_scanner_snapshot(scan_time: str, payload: dict[str, Any]) -> None:
    """Phase 2.1 — store the watchlist signal snapshot from scanner.py."""
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO scanner_snapshots (scan_time, payload)
        VALUES (?, ?)
        """,
        (scan_time, json.dumps(payload)),
    )
    db.commit()


def upsert_theta_harvester_snapshot(scan_time: str, payload: dict[str, Any]) -> None:
    """Mirror the Theta Harvester scan into Turso so every host reads the same
    latest scan (the file cache is host-local; there is no theta auto-timer)."""
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO theta_harvester_snapshots (scan_time, payload)
        VALUES (?, ?)
        """,
        (scan_time, json.dumps(payload)),
    )
    db.commit()


def upsert_strength_confirmation_snapshot(scan_time: str, payload: dict[str, Any]) -> None:
    """Mirror the 7-Step Strength Confirmation scan into Turso so every host
    reads the same latest scan (the file cache is host-local; no auto-timer)."""
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO strength_confirmation_snapshots (scan_time, payload)
        VALUES (?, ?)
        """,
        (scan_time, json.dumps(payload)),
    )
    db.commit()


def upsert_flow_analysis_snapshot(scan_time: str, payload: dict[str, Any]) -> None:
    """Phase 2.2 — flow_analysis.py output (intraday dark-pool interp)."""
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO flow_analysis_snapshots (scan_time, payload)
        VALUES (?, ?)
        """,
        (scan_time, json.dumps(payload)),
    )
    db.commit()


def upsert_performance_snapshot(taken_at: str, payload: dict[str, Any]) -> None:
    """Phase 2.3 — portfolio_performance.py output."""
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO performance_snapshots (taken_at, payload)
        VALUES (?, ?)
        """,
        (taken_at, json.dumps(payload)),
    )
    db.commit()


def upsert_nav_history(date_str: str, net_liq: float, daily_pnl: Optional[float]) -> None:
    """Phase 2.3 — append-only NAV history (one row per trading day)."""
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO nav_history (date, net_liq, daily_pnl, recorded_at)
        VALUES (?, ?, ?, ?)
        """,
        (date_str, float(net_liq), float(daily_pnl) if daily_pnl is not None else None, _now_iso()),
    )
    db.commit()


def upsert_twr_history(date_str: str, twr: float) -> None:
    """Phase 2.3 — time-weighted return series (one row per trading day)."""
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO twr_history (date, twr, recorded_at)
        VALUES (?, ?, ?)
        """,
        (date_str, float(twr), _now_iso()),
    )
    db.commit()


MARGIN_DEBT_UPSERT_SQL = """
INSERT INTO margin_debt_history
  (date, level, level_yoy_pct, free_credit_cash, free_credit_margin, source, recorded_at)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(date) DO UPDATE SET
  level              = excluded.level,
  level_yoy_pct      = excluded.level_yoy_pct,
  free_credit_cash   = excluded.free_credit_cash,
  free_credit_margin = excluded.free_credit_margin,
  source             = excluded.source,
  recorded_at        = excluded.recorded_at
"""


def upsert_margin_debt_rows(rows: list[dict[str, Any]], recorded_at: Optional[str] = None) -> None:
    """Margin Debt indicator — one row per calendar month, idempotent on date.

    Rows carry the RAW published level ($ millions) plus a source tag
    ('nyse_legacy' | 'finra'); display splice-adjustment stays out of the DB.
    """
    stamp = recorded_at or _now_iso()
    db = get_db()
    for row in rows:
        db.execute(
            MARGIN_DEBT_UPSERT_SQL,
            (
                row["date"],
                float(row["level"]),
                row.get("level_yoy_pct"),
                row.get("free_credit_cash"),
                row.get("free_credit_margin"),
                row["source"],
                stamp,
            ),
        )
    db.commit()


def upsert_option_close(
    symbol: str,
    expiry: str,
    strike: float,
    right: str,
    close_date: str,
    close_price: float,
) -> None:
    """Phase 2.5 — end-of-day option closing prices.

    Sources: ib_realtime_server.js Node-side path. This Python helper
    exists for symmetry / test setup; the production writer is the JS
    file using @libsql/client directly.
    """
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO option_close_cache
          (symbol, expiry, strike, right, close_date, close_price, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (symbol.upper(), expiry, float(strike), right.upper()[:1], close_date, float(close_price), _now_iso()),
    )
    db.commit()


def upsert_discover_sp500_snapshot(scan_time: str, payload: dict[str, Any]) -> None:
    """Phase 2.4 — sp500-scoped discover.py output (separate table to avoid
    ALTER TABLE partial-migration risk)."""
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO discover_sp500_snapshots (scan_time, payload)
        VALUES (?, ?)
        """,
        (scan_time, json.dumps(payload)),
    )
    db.commit()


def upsert_open_order(perm_id: int, payload: dict[str, Any]) -> None:
    """Phase 3 — open_orders table. permId is IB's stable identifier."""
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO open_orders (perm_id, payload, updated_at)
        VALUES (?, ?, ?)
        """,
        (int(perm_id), json.dumps(payload), _now_iso()),
    )
    db.commit()


def upsert_executed_order(
    exec_id: str,
    payload: dict[str, Any],
    fill_time: str,
    perm_id: Optional[int] = None,
) -> None:
    """Phase 3 — executed_orders table. execId is IB's per-fill identifier."""
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO executed_orders
          (exec_id, perm_id, payload, fill_time, recorded_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            exec_id,
            int(perm_id) if perm_id is not None else None,
            json.dumps(payload),
            fill_time,
            _now_iso(),
        ),
    )
    db.commit()


def replace_open_orders_for_session(
    open_orders: list[tuple[int, dict[str, Any]]],
) -> None:
    """Phase 3 — atomic replace: delete all open_orders + insert new set.

    Used by ib_orders.py after a full sync since IB returns the full
    open-orders snapshot. Cancelled / filled orders disappear from IB's
    snapshot; this DELETE+INSERT keeps the DB in lockstep without manual
    diff logic.
    """
    db = get_db()
    now = _now_iso()
    db.execute("DELETE FROM open_orders")
    for perm_id, payload in open_orders:
        db.execute(
            """
            INSERT INTO open_orders (perm_id, payload, updated_at)
            VALUES (?, ?, ?)
            """,
            (int(perm_id), json.dumps(payload), now),
        )
    db.commit()


def upsert_daemon_state(
    handler: str,
    *,
    last_run: Optional[str] = None,
    last_status: Optional[str] = None,
    last_error: Optional[str] = None,
) -> None:
    """Phase 4 — replaces data/daemon_state.json per-handler tick log."""
    db = get_db()
    db.execute(
        """
        INSERT INTO daemon_state (handler, last_run, last_status, last_error, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(handler) DO UPDATE SET
          last_run    = COALESCE(excluded.last_run, daemon_state.last_run),
          last_status = COALESCE(excluded.last_status, daemon_state.last_status),
          last_error  = excluded.last_error,
          updated_at  = excluded.updated_at
        """,
        (handler, last_run, last_status, last_error, _now_iso()),
    )
    db.commit()


def upsert_app_config(key: str, value: str) -> None:
    """Phase 4 — generic key/value store for static config."""
    db = get_db()
    db.execute(
        """
        INSERT INTO app_config (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
          value      = excluded.value,
          updated_at = excluded.updated_at
        """,
        (key, value, _now_iso()),
    )
    db.commit()


def get_app_config(key: str) -> Optional[str]:
    """Phase 4 — read a single app_config value."""
    db = get_db()
    rows = db.execute("SELECT value FROM app_config WHERE key = ?", (key,)).fetchall()
    return rows[0][0] if rows else None


def upsert_watchlist_ticker(
    ticker: str,
    *,
    sector: Optional[str] = None,
    source: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
) -> None:
    """Phase 4 — writes one Turso watchlist row per ticker."""
    db = get_db()
    db.execute(
        """
        INSERT INTO watchlist (ticker, sector, source, payload, last_seen)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
          sector    = COALESCE(excluded.sector, watchlist.sector),
          source    = COALESCE(excluded.source, watchlist.source),
          payload   = COALESCE(excluded.payload, watchlist.payload),
          last_seen = excluded.last_seen
        """,
        (
            ticker.upper(),
            sector,
            source,
            json.dumps(payload) if payload is not None else None,
            _now_iso(),
        ),
    )
    db.commit()


def upsert_ticker_lookup_cache(query: str, result: str, expires_at: str) -> None:
    """Phase 4 — TTL cache for ticker validation lookups."""
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO ticker_lookup_cache (query, result, expires_at, cached_at)
        VALUES (?, ?, ?, ?)
        """,
        (query.upper(), result, expires_at, _now_iso()),
    )
    db.commit()


def upsert_reconciliation_log(snapshot_at: str, payload: dict[str, Any]) -> None:
    """Phase 4 — replaces data/reconciliation.json."""
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO reconciliation_log (snapshot_at, payload)
        VALUES (?, ?)
        """,
        (snapshot_at, json.dumps(payload)),
    )
    db.commit()


def record_llm_token_index(
    date_str: str,
    index_value: float,
    raw_avg_usd: float,
    components: dict[str, Any],
    methodology_version: int = 1,
) -> None:
    """Persist one daily LLM Token Expenditure Index row.

    Idempotent on `date` — re-running the AA pull on the same UTC day
    overwrites the row rather than appending. `components` is a dict like
    ``{model_id: {input_per_mtok, output_per_mtok, weight}}`` and is
    serialised to JSON for column storage.
    """
    db = get_db()
    db.execute(
        """
        INSERT INTO llm_token_index
          (date, index_value, raw_avg_usd, components, methodology_version, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
          index_value         = excluded.index_value,
          raw_avg_usd         = excluded.raw_avg_usd,
          components          = excluded.components,
          methodology_version = excluded.methodology_version,
          created_at          = excluded.created_at
        """,
        (
            date_str,
            float(index_value),
            float(raw_avg_usd),
            json.dumps(components),
            int(methodology_version),
            int(datetime.now(timezone.utc).timestamp()),
        ),
    )
    db.commit()


def get_llm_token_index_base_raw() -> Optional[float]:
    """Return the raw_avg_usd of the FIRST persisted row (earliest date).

    Used by ``llm_token_index.py`` to compute the day-N index value as
    ``raw_today / raw_base`` so the series is normalised to 1.0 on the
    first day persisted, matching Silicon Data's index treatment.
    Returns None if the table is empty (caller must establish base).
    """
    db = get_db()
    rows = db.execute(
        "SELECT raw_avg_usd FROM llm_token_index ORDER BY date ASC LIMIT 1"
    ).fetchall()
    return float(rows[0][0]) if rows else None


def record_service_health(
    service: str,
    state: str,
    *,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
    error: Optional[dict[str, Any]] = None,
) -> None:
    """state ∈ {'ok', 'syncing', 'error', 'paused'}.

    Bounded Hrana HTTP via ``write_service_health_http`` (shared SQL in
    ``db.service_health_sql``). Critical path for the monitor daemon,
    ``service_cycle``, and ``scan_mirror`` — must not use sync libsql, which
    has no client timeouts and holds the GIL while blocked.
    """
    try:
        from .hrana_http import write_service_health_http
    except ImportError:  # pragma: no cover
        from db.hrana_http import write_service_health_http  # type: ignore[no-redef]

    write_service_health_http(
        service,
        state,
        started_at=started_at,
        finished_at=finished_at,
        error=error,
    )


SERVICE_HEALTH_EVENTS_RETENTION_DAYS = 90


def prune_service_health_events(
    retention_days: int = SERVICE_HEALTH_EVENTS_RETENTION_DAYS,
) -> int:
    """Delete ``service_health_events`` rows older than the retention window.

    The table (migration 0011) is append-only via triggers on
    ``service_health``; the daily flex_token_check handler calls this once a
    day. Raises on DB errors so the handler's BaseHandler contract retries
    instead of burning the daily slot. Returns rows deleted (0 when the
    driver doesn't report a rowcount).
    """
    cutoff = (
        (datetime.now(timezone.utc) - timedelta(days=retention_days))
        .isoformat()
        .replace("+00:00", "Z")
    )
    db = get_db()
    cursor = db.execute(
        "DELETE FROM service_health_events WHERE created_at < ?", (cutoff,)
    )
    db.commit()
    deleted = getattr(cursor, "rowcount", None)
    return deleted if isinstance(deleted, int) and deleted >= 0 else 0


def upsert_ticker_flow_history(
    ticker: str,
    date: str,
    *,
    flow_strength: Optional[float] = None,
    dp_direction: Optional[str] = None,
    buy_ratio: Optional[float] = None,
    num_prints: Optional[int] = None,
    total_premium: Optional[float] = None,
    total_volume: Optional[int] = None,
) -> None:
    """Chronos-2 — accrue one (ticker, date) daily flow row.

    Idempotent on (ticker, date); re-running the scanner on the same UTC
    day overwrites the row and refreshes recorded_at.
    """
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO ticker_flow_history
          (ticker, date, flow_strength, dp_direction, buy_ratio,
           num_prints, total_premium, total_volume, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ticker.upper(),
            date,
            float(flow_strength) if flow_strength is not None else None,
            dp_direction,
            float(buy_ratio) if buy_ratio is not None else None,
            int(num_prints) if num_prints is not None else None,
            float(total_premium) if total_premium is not None else None,
            int(total_volume) if total_volume is not None else None,
            _now_iso(),
        ),
    )
    db.commit()


def upsert_forecast_snapshot(
    ticker: str,
    metric: str,
    scan_time: str,
    horizon: int,
    model_id: str,
    payload: dict[str, Any],
) -> None:
    """Chronos-2 — persist one quantile forecast snapshot.

    Keyed on (ticker, metric, scan_time); payload is the serialised
    QuantileForecast.to_dict() output.
    """
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO forecast_snapshots
          (ticker, metric, scan_time, horizon, model_id, payload)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            ticker.upper(),
            metric,
            scan_time,
            int(horizon),
            model_id,
            json.dumps(payload),
        ),
    )
    db.commit()


def upsert_forecast_calibration(
    ticker: str,
    metric: str,
    scan_time: str,
    *,
    engine: Optional[str] = None,
    series_len: Optional[int] = None,
    mean_pinball_chronos: Optional[float] = None,
    mean_pinball_baseline: Optional[float] = None,
    verdict: Optional[str] = None,
    payload: dict[str, Any],
) -> None:
    """Chronos-2 — persist one per-ticker calibration report row.

    Keyed on (ticker, metric, scan_time); payload is the full serialised
    run_backtest() output. The scalar columns are the headline numbers
    lifted out for indexed queries.
    """
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO forecast_calibration
          (ticker, metric, scan_time, engine, series_len,
           mean_pinball_chronos, mean_pinball_baseline, verdict, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ticker.upper(),
            metric,
            scan_time,
            engine,
            int(series_len) if series_len is not None else None,
            float(mean_pinball_chronos) if mean_pinball_chronos is not None else None,
            float(mean_pinball_baseline) if mean_pinball_baseline is not None else None,
            verdict,
            json.dumps(payload),
        ),
    )
    db.commit()


def get_ticker_flow_history(ticker: str, *, lookback_days: int = 120) -> list[dict[str, Any]]:
    """Chronos-2 — read the daily flow series for a ticker, ascending by date.

    Returns up to ``lookback_days`` rows as dicts keyed by column name.
    """
    db = get_db()
    cursor = db.execute(
        """
        SELECT * FROM ticker_flow_history
        WHERE ticker = ?
        ORDER BY date ASC
        LIMIT ?
        """,
        (ticker.upper(), int(lookback_days)),
    )
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def upsert_backtest_run(
    strategy: str,
    run_at: str,
    *,
    horizon: int,
    payload: dict[str, Any],
) -> None:
    """F12 — persist one strategy backtest run keyed on (strategy, run_at).

    The headline metrics are lifted into indexed columns for cheap listing;
    ``payload`` is the full serialised run (trades + equity curve + metrics).
    """
    metrics = payload.get("metrics", {})
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO backtest_runs
          (strategy, run_at, horizon, n_trades, sharpe, sortino, calmar,
           max_drawdown, hit_rate, expectancy, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            strategy,
            run_at,
            int(horizon),
            int(metrics.get("n_trades", 0)),
            _as_float(metrics.get("sharpe")),
            _as_float(metrics.get("sortino")),
            _as_float(metrics.get("calmar")),
            _as_float(metrics.get("max_drawdown")),
            _as_float(metrics.get("hit_rate")),
            _as_float(metrics.get("expectancy")),
            json.dumps(payload),
        ),
    )
    db.commit()


def get_latest_backtest_run(strategy: str) -> Optional[dict[str, Any]]:
    """F12 — most recent backtest run payload for a strategy, or None."""
    db = get_db()
    cursor = db.execute(
        """
        SELECT payload FROM backtest_runs
        WHERE strategy = ?
        ORDER BY run_at DESC
        LIMIT 1
        """,
        (strategy,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return json.loads(row[0])


def _as_float(value: Any) -> Optional[float]:
    return float(value) if value is not None else None
