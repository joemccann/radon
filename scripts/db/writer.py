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
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

try:
    # When imported as `scripts.db.writer` from project root.
    from .client import get_db
    from .order_events_sql import ORDER_EVENT_INSERT_SQL, order_event_args
    from ..clients.journal_basis import normalize_expiry_compact
except ImportError:  # pragma: no cover
    # When imported flat after sys.path.insert(scripts/) like the existing
    # services do (cta_sync_service.py et al).
    from db.client import get_db  # type: ignore[no-redef]
    from db.order_events_sql import ORDER_EVENT_INSERT_SQL, order_event_args  # type: ignore[no-redef]
    from clients.journal_basis import normalize_expiry_compact  # type: ignore[no-redef]

logger = logging.getLogger("radon.db.writer")


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


# Multi-row chunk size: 5 params/row keeps 400 rows at 2,000 bound params,
# far under SQLite's variable limit. Over the Hrana HTTP transport,
# executemany is one round-trip PER ROW — a 12y backfill (~3,000 closes)
# spent minutes writing and blew the scan subprocess timeout (2026-07-21);
# chunked multi-row INSERTs make it ~8 round-trips.
_PRICE_HISTORY_INSERT_CHUNK_ROWS = 400


def upsert_price_history_rows(symbol: str, rows: list[dict[str, Any]]) -> None:
    """Batched upsert of daily closes into price_history_daily.

    Each row: {"date": "YYYY-MM-DD", "close": float, "source": "ib"|"uw"|"yahoo"}.
    fetched_at is stamped at write time. Writes go as chunked multi-row
    INSERT statements, never per-row.
    """
    upsert_price_history_symbol_rows([{**row, "symbol": symbol} for row in rows])


def upsert_price_history_symbol_rows(rows: list[dict[str, Any]]) -> None:
    """Cross-symbol variant for bulk member ingest (BPI scan).

    Each row additionally carries "symbol". Same chunked multi-row INSERT
    discipline — one statement per ~400 rows regardless of symbol count,
    instead of one per-symbol statement each (2,000 members x 1 new close
    would otherwise be 2,000 statements on one Hrana stream).
    """
    if not rows:
        return
    fetched_at = _now_iso()
    db = get_db()
    for start in range(0, len(rows), _PRICE_HISTORY_INSERT_CHUNK_ROWS):
        chunk = rows[start:start + _PRICE_HISTORY_INSERT_CHUNK_ROWS]
        placeholders = ", ".join("(?, ?, ?, ?, ?)" for _ in chunk)
        params: list[Any] = []
        for row in chunk:
            params.extend(
                (row["symbol"], row["date"], float(row["close"]), row["source"], fetched_at)
            )
        db.execute(
            "INSERT OR REPLACE INTO price_history_daily "
            f"(symbol, date, close, source, fetched_at) VALUES {placeholders}",
            tuple(params),
        )
    db.commit()


def delete_price_history(symbol: str) -> None:
    """Lineage reset for one symbol (splice-mismatch re-backfill path)."""
    db = get_db()
    db.execute("DELETE FROM price_history_daily WHERE symbol = ?", (symbol,))
    db.commit()


def touch_price_history_row(symbol: str, date_str: str) -> None:
    """Refresh one row's fetched_at (the deep-backfill re-check marker)."""
    db = get_db()
    db.execute(
        "UPDATE price_history_daily SET fetched_at = ? WHERE symbol = ? AND date = ?",
        (_now_iso(), symbol, date_str),
    )
    db.commit()


def upsert_rv_ratio_snapshot(symbol: str, taken_at: str, payload: dict[str, Any]) -> None:
    """Latest RV-ratio snapshot per symbol (single-row replace)."""
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO rv_ratio_snapshots (symbol, taken_at, payload)
        VALUES (?, ?, ?)
        """,
        (symbol, taken_at, json.dumps(payload)),
    )
    db.commit()


def upsert_bpi_history_rows(index_symbol: str, rows: list[dict[str, Any]]) -> None:
    """Batched upsert of per-session BPI rows into bpi_history.

    Each row: {"date": "YYYY-MM-DD", "bpi": float, "members": int,
    "bullish": int}. Chunked multi-row INSERTs per the Hrana rules —
    never per-row executemany (upsert_price_history_rows precedent).
    """
    if not rows:
        return
    db = get_db()
    for start in range(0, len(rows), _PRICE_HISTORY_INSERT_CHUNK_ROWS):
        chunk = rows[start:start + _PRICE_HISTORY_INSERT_CHUNK_ROWS]
        placeholders = ", ".join("(?, ?, ?, ?, ?)" for _ in chunk)
        params: list[Any] = []
        for row in chunk:
            params.extend(
                (index_symbol, row["date"], float(row["bpi"]), int(row["members"]), int(row["bullish"]))
            )
        db.execute(
            "INSERT OR REPLACE INTO bpi_history "
            f"(index_symbol, date, bpi, members, bullish) VALUES {placeholders}",
            tuple(params),
        )
    db.commit()


def upsert_bpi_snapshot(index_symbol: str, taken_at: str, payload: dict[str, Any]) -> None:
    """Latest BPI snapshot per index (single-row replace)."""
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO bpi_snapshots (index_symbol, taken_at, payload)
        VALUES (?, ?, ?)
        """,
        (index_symbol, taken_at, json.dumps(payload)),
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

    ⚠️ NOT wired into any scheduled path. Snapshot retention is owned by the B2
    archive pipeline (``archive_portfolio_snapshots.py`` exports to Backblaze
    BEFORE deleting via ``delete_portfolio_snapshots_before``). This function is
    an UNCONDITIONAL keep-newest-``retention`` DELETE with no archive
    coordination — rewiring it into ``flex_token_check`` (as the old docstring
    instructed, now deliberately removed there) would destroy history that has
    not been archived. Retained only for manual/emergency use.

    Returns rows deleted (0 when the driver doesn't report a rowcount). Never
    call inline in the per-sync write path: the libsql client has no timeout, so
    a large DELETE during a Turso write-degradation window could hang
    save_portfolio and get the sync subprocess SIGKILL'd.
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
                # Hrana has no rowcount — count the page BEFORE deleting it.
                # `total += batch_size` reported 200 deletions for a 7-row
                # tail and the inflated figure went verbatim into the
                # archive job's service_health detail (T-046).
                count_rows = _hrana_with_retry(
                    lambda: hrana_query(
                        "SELECT COUNT(*) FROM ("
                        "SELECT taken_at FROM portfolio_snapshots "
                        "WHERE taken_at < ? ORDER BY taken_at LIMIT ?)",
                        (cutoff, batch_size),
                        timeout=_DELETE_HTTP_TIMEOUT_S,
                    )
                )
                page_count = int(count_rows[0][0]) if count_rows and count_rows[0] else 0
                _hrana_with_retry(
                    lambda: hrana_execute(
                        "DELETE FROM portfolio_snapshots WHERE taken_at IN ("
                        "SELECT taken_at FROM portfolio_snapshots "
                        "WHERE taken_at < ? ORDER BY taken_at LIMIT ?)",
                        (cutoff, batch_size),
                        timeout=_DELETE_HTTP_TIMEOUT_S,
                    )
                )
                total += page_count
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


def append_order_event(
    event_type: str,
    *,
    order_ref: Optional[str] = None,
    order_id: Optional[int] = None,
    perm_id: Optional[int] = None,
    symbol: Optional[str] = None,
    action: Optional[str] = None,
    quantity: Optional[float] = None,
    limit_price: Optional[float] = None,
    status: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
) -> bool:
    """Best-effort append to the order_events audit trail (REL-019).

    Rides the bounded hrana transport — an order path must never gain a
    hang risk from its own audit write. NEVER raises: a failed audit write
    logs a warning and returns False; the order outcome already happened
    and the caller's response must not change.
    """
    try:
        _hrana_execute(
            ORDER_EVENT_INSERT_SQL,
            order_event_args(
                event_type,
                order_ref=order_ref,
                order_id=order_id,
                perm_id=perm_id,
                symbol=symbol,
                action=action,
                quantity=quantity,
                limit_price=limit_price,
                status=status,
                detail=detail,
            ),
        )
        return True
    except Exception as exc:
        logger.warning(
            "order_events append failed (%s orderRef=%s orderId=%s): %s",
            event_type, order_ref, order_id, exc,
        )
        return False


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


# 8 params/row keeps 400 rows at 3,200 bound params, well under SQLite's
# variable limit.
_CASH_FLOW_INSERT_CHUNK_ROWS = 400

CASH_FLOW_UPSERT_COLUMNS = (
    "id", "date", "type", "amount", "currency", "description", "raw_type", "synced_at",
)


def upsert_cash_flow_rows(rows: list[dict[str, Any]]) -> int:
    """Batched upsert of parsed CashTransaction rows.

    Each row: the dict emitted by ``cash_flow_sync.parse_cash_transactions``
    (id / date / type / amount / currency / description / raw_type).

    Chunked multi-row INSERTs, never one statement per row: 264 sequential
    single-row upserts is 264 Hrana round trips on one stream, which is the
    shape that 502s and then keeps 502ing (scripts/CLAUDE.md, Turso I/O
    bounding). Precedent: ``upsert_price_history_rows``.

    Returns the number of rows discarded because a LATER row in the same
    input carried the same ``id``. SQLite refuses to UPSERT one conflict
    target twice inside a single INSERT, so the dedupe is required; the
    surviving row is the last one, which is byte-identical to what the
    old per-row loop produced. IBKR really does issue one transactionID
    per posting batch with one row per sub-category, so a non-zero return
    means real data was overwritten — the caller is expected to surface it.
    """
    if not rows:
        return 0

    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        deduped[str(row["id"])] = row
    dropped = len(rows) - len(deduped)

    synced_at = _now_iso()
    ordered = list(deduped.values())
    db = get_db()
    placeholder = "(" + ", ".join("?" * len(CASH_FLOW_UPSERT_COLUMNS)) + ")"
    for start in range(0, len(ordered), _CASH_FLOW_INSERT_CHUNK_ROWS):
        chunk = ordered[start:start + _CASH_FLOW_INSERT_CHUNK_ROWS]
        params: list[Any] = []
        for row in chunk:
            params.extend((
                str(row["id"]),
                row["date"],
                row["type"],
                float(row["amount"]),
                row.get("currency") or "USD",
                row.get("description"),
                row.get("raw_type"),
                synced_at,
            ))
        db.execute(
            "INSERT INTO cash_flows "
            f"({', '.join(CASH_FLOW_UPSERT_COLUMNS)}) VALUES "
            + ", ".join(placeholder for _ in chunk)
            + """
            ON CONFLICT(id) DO UPDATE SET
              date        = excluded.date,
              type        = excluded.type,
              amount      = excluded.amount,
              currency    = excluded.currency,
              description = excluded.description,
              raw_type    = excluded.raw_type,
              synced_at   = excluded.synced_at
            """,
            tuple(params),
        )
    db.commit()
    return dropped


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


def delete_journal_entry(trade_id: str) -> None:
    """Delete one journal row over bounded Hrana HTTP.

    Used by journal_sync to supersede fill_monitor's provisional
    ``fill-monitor:…`` mirror rows once the real execId rows cover the
    mirrored fill count (REL-011). Same transport rationale as
    ``upsert_journal_entry``.
    """
    _hrana_execute("DELETE FROM journal WHERE trade_id = ?", (trade_id,))


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


YIELD_CURVE_UPSERT_SQL = """
INSERT INTO yield_curve_history
  (date, y3m, y2, y10, spread, recorded_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(date) DO UPDATE SET
  y3m         = excluded.y3m,
  y2          = excluded.y2,
  y10         = excluded.y10,
  spread      = excluded.spread,
  recorded_at = excluded.recorded_at
"""


def upsert_yield_curve_rows(rows: list[dict[str, Any]], recorded_at: Optional[str] = None) -> None:
    """Yield Curve indicator — one row per business day, idempotent on date.

    Chunked multi-row INSERTs (Hrana I/O bounding): the one-time --backfill
    passes ~9,000 rows, which per-row would be thousands of statements on one
    stream (the rv-ratio 2026-07-21 502 incident). Daily runs pass 0-2 rows.
    """
    if not rows:
        return
    stamp = recorded_at or _now_iso()
    db = get_db()
    for start in range(0, len(rows), _PRICE_HISTORY_INSERT_CHUNK_ROWS):
        chunk = rows[start:start + _PRICE_HISTORY_INSERT_CHUNK_ROWS]
        placeholders = ", ".join("(?, ?, ?, ?, ?, ?)" for _ in chunk)
        params: list[Any] = []
        for row in chunk:
            params.extend(
                (
                    row["date"],
                    row.get("y3m"),
                    float(row["y2"]),
                    float(row["y10"]),
                    float(row["spread"]),
                    stamp,
                )
            )
        db.execute(
            "INSERT INTO yield_curve_history (date, y3m, y2, y10, spread, recorded_at) "
            f"VALUES {placeholders} "
            "ON CONFLICT(date) DO UPDATE SET "
            "y3m = excluded.y3m, y2 = excluded.y2, y10 = excluded.y10, "
            "spread = excluded.spread, recorded_at = excluded.recorded_at",
            tuple(params),
        )
    db.commit()


def upsert_credit_spread_rows(rows: list[dict[str, Any]], recorded_at: Optional[str] = None) -> None:
    """CREDIT indicator — one row per common HYG/SPX session, idempotent on date.

    Chunked multi-row INSERTs (Hrana I/O bounding): a first-run backfill
    writes the full ~4,800-session series, which per-row would be thousands
    of statements on one stream (the rv-ratio 2026-07-21 502 incident).
    Daily runs pass 0-2 rows.
    """
    if not rows:
        return
    stamp = recorded_at or _now_iso()
    db = get_db()
    for start in range(0, len(rows), _PRICE_HISTORY_INSERT_CHUNK_ROWS):
        chunk = rows[start:start + _PRICE_HISTORY_INSERT_CHUNK_ROWS]
        placeholders = ", ".join("(?, ?, ?, ?)" for _ in chunk)
        params: list[Any] = []
        for row in chunk:
            params.extend(
                (
                    row["date"],
                    float(row["hyg_close"]),
                    float(row["spx_close"]),
                    stamp,
                )
            )
        db.execute(
            "INSERT INTO credit_spread_history (date, hyg_close, spx_close, recorded_at) "
            f"VALUES {placeholders} "
            "ON CONFLICT(date) DO UPDATE SET "
            "hyg_close = excluded.hyg_close, spx_close = excluded.spx_close, "
            "recorded_at = excluded.recorded_at",
            tuple(params),
        )
    db.commit()


DIVYIELD_UPSERT_SQL = """
INSERT INTO divyield_history
  (date, pct_above, count_above, total, y10, approximate, recorded_at)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(date) DO UPDATE SET
  pct_above   = excluded.pct_above,
  count_above = excluded.count_above,
  total       = excluded.total,
  y10         = excluded.y10,
  approximate = excluded.approximate,
  recorded_at = excluded.recorded_at
"""


def upsert_divyield_rows(rows: list[dict[str, Any]], recorded_at: Optional[str] = None) -> None:
    """DIVYIELD indicator — one row per date, idempotent on date.

    Chunked multi-row INSERTs (Hrana I/O bounding): the one-time --backfill
    passes ~440 approximate monthly rows, which per-row would be hundreds of
    statements on one stream (the rv-ratio 2026-07-21 502 incident). Daily
    runs pass 0-1 rows.
    """
    if not rows:
        return
    stamp = recorded_at or _now_iso()
    db = get_db()
    for start in range(0, len(rows), _PRICE_HISTORY_INSERT_CHUNK_ROWS):
        chunk = rows[start:start + _PRICE_HISTORY_INSERT_CHUNK_ROWS]
        placeholders = ", ".join("(?, ?, ?, ?, ?, ?, ?)" for _ in chunk)
        params: list[Any] = []
        for row in chunk:
            params.extend(
                (
                    row["date"],
                    float(row["pct_above"]),
                    int(row["count_above"]),
                    int(row["total"]),
                    float(row["y10"]),
                    int(row.get("approximate", 0)),
                    stamp,
                )
            )
        db.execute(
            "INSERT INTO divyield_history "
            "(date, pct_above, count_above, total, y10, approximate, recorded_at) "
            f"VALUES {placeholders} "
            "ON CONFLICT(date) DO UPDATE SET "
            "pct_above = excluded.pct_above, count_above = excluded.count_above, "
            "total = excluded.total, y10 = excluded.y10, "
            "approximate = excluded.approximate, recorded_at = excluded.recorded_at",
            tuple(params),
        )
    db.commit()


HYAD_UPSERT_SQL = """
INSERT INTO hyad_history
  (date, advances, declines, unchanged, total, recorded_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(date) DO UPDATE SET
  advances    = excluded.advances,
  declines    = excluded.declines,
  unchanged   = excluded.unchanged,
  total       = excluded.total,
  recorded_at = excluded.recorded_at
"""


def upsert_hyad_rows(rows: list[dict[str, Any]], recorded_at: Optional[str] = None) -> None:
    """HYAD indicator — one row per date, idempotent on date.

    Chunked multi-row INSERTs (Hrana I/O bounding): the one-time --backfill
    passes ~2,150 daily rows, which per-row would be thousands of statements
    on one stream (the rv-ratio 2026-07-21 502 incident). Daily runs pass
    0-10 rows.
    """
    if not rows:
        return
    stamp = recorded_at or _now_iso()
    db = get_db()
    for start in range(0, len(rows), _PRICE_HISTORY_INSERT_CHUNK_ROWS):
        chunk = rows[start:start + _PRICE_HISTORY_INSERT_CHUNK_ROWS]
        placeholders = ", ".join("(?, ?, ?, ?, ?, ?)" for _ in chunk)
        params: list[Any] = []
        for row in chunk:
            params.extend(
                (
                    row["date"],
                    int(row["advances"]),
                    int(row["declines"]),
                    int(row["unchanged"]),
                    int(row["total"]),
                    stamp,
                )
            )
        db.execute(
            "INSERT INTO hyad_history "
            "(date, advances, declines, unchanged, total, recorded_at) "
            f"VALUES {placeholders} "
            "ON CONFLICT(date) DO UPDATE SET "
            "advances = excluded.advances, declines = excluded.declines, "
            "unchanged = excluded.unchanged, total = excluded.total, "
            "recorded_at = excluded.recorded_at",
            tuple(params),
        )
    db.commit()


HHLEV_UPSERT_SQL = """
INSERT INTO hhlev_history
  (date, leverage_pct, liabilities_musd, net_worth_musd, recorded_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(date) DO UPDATE SET
  leverage_pct     = excluded.leverage_pct,
  liabilities_musd = excluded.liabilities_musd,
  net_worth_musd   = excluded.net_worth_musd,
  recorded_at      = excluded.recorded_at
"""


def upsert_hhlev_rows(rows: list[dict[str, Any]], recorded_at: Optional[str] = None) -> None:
    """HHLEV indicator — one row per quarter, idempotent on date.

    Chunked multi-row INSERTs (Hrana I/O bounding): Z.1 revises full
    history each release, so EVERY daily run passes all ~304 quarterly
    rows, which per-row would be hundreds of statements on one stream
    (the rv-ratio 2026-07-21 502 incident).
    """
    if not rows:
        return
    stamp = recorded_at or _now_iso()
    db = get_db()
    for start in range(0, len(rows), _PRICE_HISTORY_INSERT_CHUNK_ROWS):
        chunk = rows[start:start + _PRICE_HISTORY_INSERT_CHUNK_ROWS]
        placeholders = ", ".join("(?, ?, ?, ?, ?)" for _ in chunk)
        params: list[Any] = []
        for row in chunk:
            liabilities = float(row["liabilities_musd"])
            net_worth = float(row["net_worth_musd"])
            params.extend(
                (
                    row["date"],
                    float(
                        row.get("leverage_pct", 100.0 * liabilities / net_worth)
                    ),
                    liabilities,
                    net_worth,
                    stamp,
                )
            )
        db.execute(
            "INSERT INTO hhlev_history "
            "(date, leverage_pct, liabilities_musd, net_worth_musd, recorded_at) "
            f"VALUES {placeholders} "
            "ON CONFLICT(date) DO UPDATE SET "
            "leverage_pct = excluded.leverage_pct, "
            "liabilities_musd = excluded.liabilities_musd, "
            "net_worth_musd = excluded.net_worth_musd, "
            "recorded_at = excluded.recorded_at",
            tuple(params),
        )
    db.commit()


def _iei_hyg_params(row: dict[str, Any], stamp: str) -> tuple:
    dxy = row.get("dxy_close")
    return (
        row["date"],
        float(row["iei_close"]),
        float(row["hyg_close"]),
        None if dxy is None else float(dxy),
        stamp,
    )


def upsert_iei_hyg_rows(rows: list[dict[str, Any]], recorded_at: Optional[str] = None) -> None:
    """IEI/HYG indicator — one row per common IEI/HYG session, idempotent on date.

    Chunked multi-row INSERTs (Hrana I/O bounding), same shape as the
    credit-spread writer: a first-run backfill is ~4,800 sessions, daily
    runs pass 0-2 rows. dxy_close is nullable (ICE gaps on US holidays).
    """
    if not rows:
        return
    stamp = recorded_at or _now_iso()
    db = get_db()
    for start in range(0, len(rows), _PRICE_HISTORY_INSERT_CHUNK_ROWS):
        chunk = rows[start:start + _PRICE_HISTORY_INSERT_CHUNK_ROWS]
        placeholders = ", ".join("(?, ?, ?, ?, ?)" for _ in chunk)
        params: list[Any] = []
        for row in chunk:
            params.extend(_iei_hyg_params(row, stamp))
        db.execute(
            "INSERT INTO iei_hyg_history (date, iei_close, hyg_close, dxy_close, recorded_at) "
            f"VALUES {placeholders} "
            "ON CONFLICT(date) DO UPDATE SET "
            "iei_close = excluded.iei_close, hyg_close = excluded.hyg_close, "
            "dxy_close = excluded.dxy_close, recorded_at = excluded.recorded_at",
            tuple(params),
        )
    db.commit()


_TRIN_SAMPLE_COLUMNS = "(ts, session_date, trin, adv, dec, up_vol, down_vol, source, recorded_at)"
_TRIN_SAMPLE_ON_CONFLICT = (
    "ON CONFLICT(ts) DO UPDATE SET "
    "session_date = excluded.session_date, trin = excluded.trin, "
    "adv = excluded.adv, dec = excluded.dec, "
    "up_vol = excluded.up_vol, down_vol = excluded.down_vol, "
    "source = excluded.source, recorded_at = excluded.recorded_at"
)
TRIN_SAMPLE_UPSERT_SQL = (
    f"INSERT INTO trin_samples {_TRIN_SAMPLE_COLUMNS} "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
    f"{_TRIN_SAMPLE_ON_CONFLICT}"
)

_TRIN_DAILY_ON_CONFLICT = (
    "ON CONFLICT(date) DO UPDATE SET close = excluded.close, recorded_at = excluded.recorded_at"
)
TRIN_DAILY_UPSERT_SQL = (
    f"INSERT INTO trin_daily (date, close, recorded_at) VALUES (?, ?, ?) {_TRIN_DAILY_ON_CONFLICT}"
)


def _trin_sample_params(row: dict[str, Any], stamp: str) -> tuple:
    def _optional_float(value: Any) -> Optional[float]:
        return None if value is None else float(value)

    def _optional_int(value: Any) -> Optional[int]:
        return None if value is None else int(value)

    return (
        row["ts"],
        row["session_date"],
        float(row["trin"]),
        _optional_int(row.get("adv")),
        _optional_int(row.get("dec")),
        _optional_float(row.get("up_vol")),
        _optional_float(row.get("down_vol")),
        row.get("source") or "ib",
        stamp,
    )


def upsert_trin_samples(rows: list[dict[str, Any]], recorded_at: Optional[str] = None) -> None:
    """TRIN indicator — one row per five-minute RTH sample, idempotent on ts.
    Chunked multi-row INSERTs (Hrana I/O bounding); a live run passes one row."""
    if not rows:
        return
    stamp = recorded_at or _now_iso()
    db = get_db()
    for start in range(0, len(rows), _PRICE_HISTORY_INSERT_CHUNK_ROWS):
        chunk = rows[start:start + _PRICE_HISTORY_INSERT_CHUNK_ROWS]
        placeholders = ", ".join("(?, ?, ?, ?, ?, ?, ?, ?, ?)" for _ in chunk)
        params: list[Any] = []
        for row in chunk:
            params.extend(_trin_sample_params(row, stamp))
        db.execute(
            f"INSERT INTO trin_samples {_TRIN_SAMPLE_COLUMNS} VALUES {placeholders} "
            f"{_TRIN_SAMPLE_ON_CONFLICT}",
            tuple(params),
        )
    db.commit()


def upsert_trin_daily_rows(rows: list[tuple[str, float]], recorded_at: Optional[str] = None) -> None:
    """TRIN daily closes (StockCharts $TRIN) — (date, close) tuples, idempotent on date."""
    if not rows:
        return
    stamp = recorded_at or _now_iso()
    db = get_db()
    for start in range(0, len(rows), _PRICE_HISTORY_INSERT_CHUNK_ROWS):
        chunk = rows[start:start + _PRICE_HISTORY_INSERT_CHUNK_ROWS]
        placeholders = ", ".join("(?, ?, ?)" for _ in chunk)
        params: list[Any] = []
        for date, close in chunk:
            params.extend((date, float(close), stamp))
        db.execute(
            f"INSERT INTO trin_daily (date, close, recorded_at) VALUES {placeholders} "
            f"{_TRIN_DAILY_ON_CONFLICT}",
            tuple(params),
        )
    db.commit()


STRADDLE_UPSERT_SQL = """
INSERT INTO straddle_history
  (date, spx_close, vix1d_close, ratio, recorded_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(date) DO UPDATE SET
  spx_close   = excluded.spx_close,
  vix1d_close = excluded.vix1d_close,
  ratio       = excluded.ratio,
  recorded_at = excluded.recorded_at
"""


def upsert_straddle_rows(rows: list[dict[str, Any]], recorded_at: Optional[str] = None) -> None:
    """Straddle indicator — one row per common SPX/VIX1D session, idempotent
    on date. ratio is NULL on the series' first session (no prior close).

    Chunked multi-row INSERTs (Hrana I/O bounding): a changed-source run
    rewrites the full ~1,060-session series, which per-row would be a
    thousand statements on one stream (the rv-ratio 2026-07-21 502
    incident). ~3 chunked round-trips instead.
    """
    if not rows:
        return
    stamp = recorded_at or _now_iso()
    db = get_db()
    for start in range(0, len(rows), _PRICE_HISTORY_INSERT_CHUNK_ROWS):
        chunk = rows[start:start + _PRICE_HISTORY_INSERT_CHUNK_ROWS]
        placeholders = ", ".join("(?, ?, ?, ?, ?)" for _ in chunk)
        params: list[Any] = []
        for row in chunk:
            params.extend(
                (
                    row["date"],
                    float(row["spx_close"]),
                    float(row["vix1d_close"]),
                    row.get("ratio"),
                    stamp,
                )
            )
        db.execute(
            "INSERT INTO straddle_history (date, spx_close, vix1d_close, ratio, recorded_at) "
            f"VALUES {placeholders} "
            "ON CONFLICT(date) DO UPDATE SET "
            "spx_close = excluded.spx_close, vix1d_close = excluded.vix1d_close, "
            "ratio = excluded.ratio, recorded_at = excluded.recorded_at",
            tuple(params),
        )
    db.commit()


COR_UPSERT_SQL = """
INSERT INTO cor_history
  (date, cor1m, cor3m, cor6m, cor1y, recorded_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(date) DO UPDATE SET
  cor1m       = excluded.cor1m,
  cor3m       = excluded.cor3m,
  cor6m       = excluded.cor6m,
  cor1y       = excluded.cor1y,
  recorded_at = excluded.recorded_at
"""


def upsert_cor_rows(rows: list[dict[str, Any]], recorded_at: Optional[str] = None) -> None:
    """COR indicator — one row per session across the four Cboe implied
    correlation tenors, idempotent on date. Tenor columns are NULL where
    that tenor is missing the date (COR3M's scattered gaps).

    Chunked multi-row INSERTs (Hrana I/O bounding): a changed-source run
    rewrites the full ~5,180-session series, which per-row would be
    thousands of statements on one stream (the rv-ratio 2026-07-21 502
    incident). ~13 chunked round-trips instead.
    """
    if not rows:
        return
    stamp = recorded_at or _now_iso()
    db = get_db()
    for start in range(0, len(rows), _PRICE_HISTORY_INSERT_CHUNK_ROWS):
        chunk = rows[start:start + _PRICE_HISTORY_INSERT_CHUNK_ROWS]
        placeholders = ", ".join("(?, ?, ?, ?, ?, ?)" for _ in chunk)
        params: list[Any] = []
        for row in chunk:
            params.extend(
                (
                    row["date"],
                    row.get("cor1m"),
                    row.get("cor3m"),
                    row.get("cor6m"),
                    row.get("cor1y"),
                    stamp,
                )
            )
        db.execute(
            "INSERT INTO cor_history (date, cor1m, cor3m, cor6m, cor1y, recorded_at) "
            f"VALUES {placeholders} "
            "ON CONFLICT(date) DO UPDATE SET "
            "cor1m = excluded.cor1m, cor3m = excluded.cor3m, "
            "cor6m = excluded.cor6m, cor1y = excluded.cor1y, "
            "recorded_at = excluded.recorded_at",
            tuple(params),
        )
    db.commit()


def upsert_vixcor_rows(rows: list[dict[str, Any]], recorded_at: Optional[str] = None) -> None:
    """VIXCOR indicator — one row per joined session (VIX x COR3M), idempotent
    on date. corr20 is NULL for the first WINDOW-1 rows and for degenerate
    windows where one leg is constant.

    Chunked multi-row INSERTs (Hrana I/O bounding): a changed-source run
    rewrites the full ~5,170-session series, which per-row would be thousands
    of statements on one stream (the rv-ratio 2026-07-21 502 incident).
    ~13 chunked round-trips instead.
    """
    if not rows:
        return
    stamp = recorded_at or _now_iso()
    db = get_db()
    for start in range(0, len(rows), _PRICE_HISTORY_INSERT_CHUNK_ROWS):
        chunk = rows[start:start + _PRICE_HISTORY_INSERT_CHUNK_ROWS]
        placeholders = ", ".join("(?, ?, ?, ?, ?)" for _ in chunk)
        params: list[Any] = []
        for row in chunk:
            params.extend(
                (row["date"], row["vix_close"], row["cor3m_close"], row.get("corr20"), stamp)
            )
        db.execute(
            "INSERT INTO vixcor_history (date, vix_close, cor3m_close, corr20, recorded_at) "
            f"VALUES {placeholders} "
            "ON CONFLICT(date) DO UPDATE SET "
            "vix_close = excluded.vix_close, cor3m_close = excluded.cor3m_close, "
            "corr20 = excluded.corr20, recorded_at = excluded.recorded_at",
            tuple(params),
        )
    db.commit()


def upsert_ivrank_rows(rows: list[dict[str, Any]], recorded_at: Optional[str] = None) -> None:
    """IV RANK indicator — one row per SPY session, idempotent on date.
    iv_rank / iv_pct are NULL for the first RANK_WINDOW-1 rows and for
    degenerate windows.

    Chunked multi-row INSERTs (Hrana I/O bounding): every run rewrites the
    full stored series (~1,260 sessions under the 5Y seed), which per-row
    would be thousands of statements on one stream (the rv-ratio 2026-07-21
    502 incident). ~4 chunked round-trips instead.
    """
    if not rows:
        return
    stamp = recorded_at or _now_iso()
    db = get_db()
    for start in range(0, len(rows), _PRICE_HISTORY_INSERT_CHUNK_ROWS):
        chunk = rows[start:start + _PRICE_HISTORY_INSERT_CHUNK_ROWS]
        placeholders = ", ".join("(?, ?, ?, ?, ?, ?)" for _ in chunk)
        params: list[Any] = []
        for row in chunk:
            params.extend(
                (row["date"], row["iv"], row.get("iv_rank"), row.get("iv_pct"), row["source"], stamp)
            )
        db.execute(
            "INSERT INTO ivrank_history (date, iv, iv_rank, iv_pct, source, recorded_at) "
            f"VALUES {placeholders} "
            "ON CONFLICT(date) DO UPDATE SET "
            "iv = excluded.iv, iv_rank = excluded.iv_rank, "
            "iv_pct = excluded.iv_pct, source = excluded.source, "
            "recorded_at = excluded.recorded_at",
            tuple(params),
        )
    db.commit()


SKEW_UPSERT_SQL = """
INSERT INTO skew_history
  (date, expiry, dte, put_iv, call_iv, ratio, change, recorded_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(date) DO UPDATE SET
  expiry      = excluded.expiry,
  dte         = excluded.dte,
  put_iv      = excluded.put_iv,
  call_iv     = excluded.call_iv,
  ratio       = excluded.ratio,
  change      = excluded.change,
  recorded_at = excluded.recorded_at
"""


def upsert_skew_rows(rows: list[dict[str, Any]], recorded_at: Optional[str] = None) -> None:
    """Skew indicator — one row per completed SPX session, idempotent on
    date. change is NULL on the series' first session (no prior ratio).

    Chunked multi-row INSERTs (Hrana I/O bounding): the one-time --backfill
    rewrites the full ~730-session series, which per-row would be hundreds
    of statements on one stream (the rv-ratio 2026-07-21 502 incident).
    Daily gap-filling runs pass the same full series in ~2 chunked
    round-trips.
    """
    if not rows:
        return
    stamp = recorded_at or _now_iso()
    db = get_db()
    for start in range(0, len(rows), _PRICE_HISTORY_INSERT_CHUNK_ROWS):
        chunk = rows[start:start + _PRICE_HISTORY_INSERT_CHUNK_ROWS]
        placeholders = ", ".join("(?, ?, ?, ?, ?, ?, ?, ?)" for _ in chunk)
        params: list[Any] = []
        for row in chunk:
            params.extend(
                (
                    row["date"],
                    row["expiry"],
                    int(row["dte"]),
                    float(row["put_iv"]),
                    float(row["call_iv"]),
                    float(row["ratio"]),
                    row.get("change"),
                    stamp,
                )
            )
        db.execute(
            "INSERT INTO skew_history (date, expiry, dte, put_iv, call_iv, ratio, change, recorded_at) "
            f"VALUES {placeholders} "
            "ON CONFLICT(date) DO UPDATE SET "
            "expiry = excluded.expiry, dte = excluded.dte, "
            "put_iv = excluded.put_iv, call_iv = excluded.call_iv, "
            "ratio = excluded.ratio, change = excluded.change, "
            "recorded_at = excluded.recorded_at",
            tuple(params),
        )
    db.commit()


SKEW2D_UPSERT_SQL = """
INSERT INTO skew2d_history
  (date, expiry, dte, put_iv, call_iv, ratio, change, recorded_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(date) DO UPDATE SET
  expiry      = excluded.expiry,
  dte         = excluded.dte,
  put_iv      = excluded.put_iv,
  call_iv     = excluded.call_iv,
  ratio       = excluded.ratio,
  change      = excluded.change,
  recorded_at = excluded.recorded_at
"""


def upsert_skew2d_rows(rows: list[dict[str, Any]], recorded_at: Optional[str] = None) -> None:
    """Skew 2D indicator — one row per completed SPX session, idempotent on
    date. change is NULL on the first two sessions (no two-session lag).

    Chunked multi-row INSERTs (same Hrana I/O bounding as upsert_skew_rows).
    """
    if not rows:
        return
    stamp = recorded_at or _now_iso()
    db = get_db()
    for start in range(0, len(rows), _PRICE_HISTORY_INSERT_CHUNK_ROWS):
        chunk = rows[start:start + _PRICE_HISTORY_INSERT_CHUNK_ROWS]
        placeholders = ", ".join("(?, ?, ?, ?, ?, ?, ?, ?)" for _ in chunk)
        params: list[Any] = []
        for row in chunk:
            params.extend(
                (
                    row["date"],
                    row["expiry"],
                    int(row["dte"]),
                    float(row["put_iv"]),
                    float(row["call_iv"]),
                    float(row["ratio"]),
                    row.get("change"),
                    stamp,
                )
            )
        db.execute(
            "INSERT INTO skew2d_history (date, expiry, dte, put_iv, call_iv, ratio, change, recorded_at) "
            f"VALUES {placeholders} "
            "ON CONFLICT(date) DO UPDATE SET "
            "expiry = excluded.expiry, dte = excluded.dte, "
            "put_iv = excluded.put_iv, call_iv = excluded.call_iv, "
            "ratio = excluded.ratio, change = excluded.change, "
            "recorded_at = excluded.recorded_at",
            tuple(params),
        )
    db.commit()


VOL_CONE_UPSERT_SQL = """
INSERT INTO vol_cone_history
  (ticker, date, expiry, dte, spot, atm_iv, call_10_iv, put_10_iv,
   call_10_strike, put_10_strike, recorded_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(ticker, date, expiry) DO UPDATE SET
  dte            = excluded.dte,
  spot           = excluded.spot,
  atm_iv         = excluded.atm_iv,
  call_10_iv     = excluded.call_10_iv,
  put_10_iv      = excluded.put_10_iv,
  call_10_strike = excluded.call_10_strike,
  put_10_strike  = excluded.put_10_strike,
  recorded_at    = excluded.recorded_at
"""


def upsert_vol_cone_rows(rows: list[dict[str, Any]], recorded_at: Optional[str] = None) -> None:
    """VOL CONE indicator — one row per (ticker, date, expiry), idempotent
    on that triple. Chunked multi-row INSERTs (same Hrana I/O bounding as
    upsert_skew_rows): first-run backfill is ~80 sessions x ~25 names.
    """
    if not rows:
        return
    stamp = recorded_at or _now_iso()
    db = get_db()
    for start in range(0, len(rows), _PRICE_HISTORY_INSERT_CHUNK_ROWS):
        chunk = rows[start:start + _PRICE_HISTORY_INSERT_CHUNK_ROWS]
        placeholders = ", ".join("(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)" for _ in chunk)
        params: list[Any] = []
        for row in chunk:
            params.extend(
                (
                    row["ticker"],
                    row["date"],
                    row["expiry"],
                    int(row["dte"]),
                    float(row["spot"]),
                    float(row["atm_iv"]),
                    float(row["call_10_iv"]),
                    float(row["put_10_iv"]),
                    row.get("call_10_strike"),
                    row.get("put_10_strike"),
                    stamp,
                )
            )
        db.execute(
            "INSERT INTO vol_cone_history "
            "(ticker, date, expiry, dte, spot, atm_iv, call_10_iv, put_10_iv, "
            "call_10_strike, put_10_strike, recorded_at) "
            f"VALUES {placeholders} "
            "ON CONFLICT(ticker, date, expiry) DO UPDATE SET "
            "dte = excluded.dte, spot = excluded.spot, "
            "atm_iv = excluded.atm_iv, call_10_iv = excluded.call_10_iv, "
            "put_10_iv = excluded.put_10_iv, "
            "call_10_strike = excluded.call_10_strike, "
            "put_10_strike = excluded.put_10_strike, "
            "recorded_at = excluded.recorded_at",
            tuple(params),
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


# The execution-identity contract: exactly these normalized fields define a
# fill's lifecycle identity, and the stored payload_sha256 is computed over
# them. This is PINNED on purpose (test_position_return_capital.py asserts the
# tuple) — it must never be derived from "whatever normalize_execution returns".
#
# 2026-08-13: it was `{k: v for k, v in item.items() if k != "commission"}`,
# so when commit 4eaaf5e9 added `revision` and `source_exec_id` to
# normalize_execution for reversal splitting, every one of the 76 stored facts
# silently hashed differently and orders-sync aborted with "execution fact
# conflict" on the first pre-existing exec_id it re-synced, freezing
# executed_orders / open_orders.
#
# `commission` is excluded because commission/realized-P&L reports arrive
# after execDetails; late enrichment is not an execution correction.
# `revision` is excluded because it is already part of the row's primary key,
# and `source_exec_id` because it merely defaults back to exec_id.
_LIFECYCLE_HASH_FIELDS: tuple[str, ...] = (
    "account_id",
    "con_id",
    "currency",
    "exec_id",
    "filled_at",
    "multiplier",
    "order_ref",
    "perm_id",
    "price",
    "quantity",
    "sec_type",
    "side",
    "signed_quantity",
    "symbol",
)


def _execution_identity_hash(item: dict[str, Any]) -> str:
    """Hash the pinned lifecycle identity of a normalized execution.

    Only the fields in ``_LIFECYCLE_HASH_FIELDS`` participate. A missing field
    is a hard error rather than a silently different hash, so renaming one in
    normalize_execution fails loudly instead of invalidating stored history.
    """
    import hashlib

    lifecycle_payload = {key: item[key] for key in _LIFECYCLE_HASH_FIELDS}
    return hashlib.sha256(
        json.dumps(lifecycle_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _row_value(row: Any, key: str, index: int) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        return row[index]


def _same_number(left: Any, right: Any) -> bool:
    try:
        return abs(float(left) - float(right)) <= 1e-10
    except (TypeError, ValueError):
        return False


def _execution_economically_conflicts(
    row: Any,
    item: dict[str, Any],
    payload: dict[str, Any],
    *,
    iso_ts,
) -> bool:
    """True when stored columns disagree on the fill itself.

    Used only after the pinned hash already differs, so leftover hashes
    and late metadata (perm_id, avgPrice fallback, timezone form) do
    not abort orders-sync. A real price/qty/side/time change still raises.
    """
    if int(_row_value(row, "con_id", 1)) != int(item["con_id"]):
        return True
    if str(_row_value(row, "side", 2)).upper() != str(item["side"]).upper():
        return True
    if not _same_number(_row_value(row, "quantity", 3), item["quantity"]):
        return True
    if iso_ts(_row_value(row, "filled_at", 5)) != item["filled_at"]:
        return True
    explicit_price = payload.get("price")
    if explicit_price is not None and not _same_number(_row_value(row, "price", 4), explicit_price):
        return True
    return False


def _execution_fact_columns(item: dict[str, Any]) -> tuple[Any, ...]:
    """Denormalized ledger columns derived from a normalized execution.

    Single source for INSERT and the tolerated-drift UPDATE so a converged
    row is column-for-column what a fresh insert of the same payload would
    produce (T-098).
    """
    return (
        item["perm_id"], item["order_ref"] or None, item["con_id"],
        item["side"], item["quantity"], item["price"], item["multiplier"],
        item["currency"], item["filled_at"],
    )


def upsert_position_execution_fact(payload: dict[str, Any], *, db: Any = None) -> bool:
    """Persist a lossless IB execution fact without overwriting its identity.

    Repeated syncs are idempotent. New rows hash the pinned lifecycle
    field set. A hash mismatch only raises when stored economic columns
    also disagree, so leftover hashes and late IB enrichment do not
    freeze orders-sync.
    """
    try:
        from ..position_return_capital import _iso, normalize_execution
    except ImportError:  # pragma: no cover - flat scripts/ import mode
        from position_return_capital import _iso, normalize_execution  # type: ignore[no-redef]

    item = normalize_execution(payload)
    revision = int(payload.get("revision") or 1)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    payload_hash = _execution_identity_hash(item)
    target = db or get_db()
    row = target.execute(
        """SELECT payload_sha256, con_id, side, quantity, price, filled_at
           FROM position_execution_facts
           WHERE account_id = ? AND exec_id = ? AND revision = ?""",
        (item["account_id"], item["exec_id"], revision),
    ).fetchone()
    if row is not None:
        existing_hash = _row_value(row, "payload_sha256", 0)
        if existing_hash != payload_hash:
            if not _execution_economically_conflicts(row, item, payload, iso_ts=_iso):
                # Tolerated drift: same fill, newer metadata. Converge the
                # pinned hash, the stored payload AND every denormalized
                # column so late IB enrichment (perm_id, order_ref) and gate-
                # tolerated restatements (avgPrice-derived price, multiplier,
                # currency) reach the ledger instead of leaving the row
                # permanently self-contradictory (R-077, T-098).
                target.execute(
                    """UPDATE position_execution_facts
                       SET payload_sha256 = ?, payload = ?,
                           perm_id = ?, order_ref = ?, con_id = ?, side = ?,
                           quantity = ?, price = ?, multiplier = ?, currency = ?,
                           filled_at = ?
                       WHERE account_id = ? AND exec_id = ? AND revision = ?""",
                    (
                        payload_hash, canonical, *_execution_fact_columns(item),
                        item["account_id"], item["exec_id"], revision,
                    ),
                )
                target.commit()
                return True
            logger.warning(
                "execution fact conflict for %s:%s:%s",
                item["account_id"], item["exec_id"], revision,
            )
            raise ValueError(
                f"execution fact conflict for {item['account_id']}:{item['exec_id']}:{revision}"
            )
        return False

    target.execute(
        """
        INSERT INTO position_execution_facts
          (account_id, exec_id, revision, payload_sha256, perm_id, order_ref,
           con_id, side, quantity, price, multiplier, currency, filled_at,
           payload, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item["account_id"], item["exec_id"], revision, payload_hash,
            *_execution_fact_columns(item), canonical, _now_iso(),
        ),
    )
    target.commit()
    return True


def insert_account_margin_sample(
    account_id: str,
    *,
    observed_from: str,
    observed_through: str,
    initial_margin: float,
    maintenance_margin: Optional[float],
    currency: str,
    positions: dict[str, float],
    db: Any = None,
) -> Optional[str]:
    """Append an acquisition-bounded margin + signed conId position vector."""
    import hashlib

    account = str(account_id or "").strip()
    ccy = str(currency or "").upper().strip()
    try:
        initial = float(initial_margin)
        maintenance = float(maintenance_margin) if maintenance_margin is not None else None
    except (TypeError, ValueError):
        return None
    if (
        not account
        or not ccy
        or not observed_from
        or not observed_through
        or observed_from > observed_through
        or initial < 0
        or (maintenance is not None and maintenance < 0)
    ):
        return None
    clean_positions = {
        str(int(key)): float(value)
        for key, value in positions.items()
        if abs(float(value)) > 1e-9
    }
    positions_json = json.dumps(clean_positions, sort_keys=True, separators=(",", ":"))
    positions_hash = hashlib.sha256(positions_json.encode()).hexdigest()
    identity = json.dumps(
        [account, observed_from, observed_through, initial, maintenance, ccy, positions_hash],
        separators=(",", ":"),
    )
    identity_hash = hashlib.sha256(identity.encode()).hexdigest()
    sample_id = f"margin-{identity_hash[:24]}"
    target = db or get_db()
    target.execute(
        """
        INSERT OR IGNORE INTO account_margin_samples
          (sample_id, account_id, observed_from, observed_through, initial_margin,
           maintenance_margin, currency, positions, positions_sha256, source,
           idempotency_key, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ib-account-values', ?, ?)
        """,
        (
            sample_id, account, observed_from, observed_through, initial, maintenance, ccy,
            positions_json, positions_hash, identity_hash, _now_iso(),
        ),
    )
    target.commit()
    return sample_id



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
    """Phase 3 — replace the open_orders snapshot without an empty window.

    Used by ib_orders.py after a full sync since IB returns the full
    open-orders snapshot. The direct-to-cloud pipeline autocommits per
    statement, so the old DELETE-all-then-insert shape left the table
    EMPTY (or partial) when any statement failed mid-replace — /orders
    rendered a flat book over real working orders (T-024). Order of
    operations now: multi-row UPSERT the new snapshot FIRST (chunked per
    the Hrana bounding rules), THEN delete rows not in it. A failure at
    any point leaves old rows, new rows, or their union — never an empty
    table while orders are working — and the next 60s sync converges.
    Errors propagate to the caller.
    """
    db = get_db()
    now = _now_iso()
    if not open_orders:
        db.execute("DELETE FROM open_orders")
        db.commit()
        return

    rows = [(int(perm_id), json.dumps(payload), now) for perm_id, payload in open_orders]
    chunk_size = 200
    for start in range(0, len(rows), chunk_size):
        chunk = rows[start:start + chunk_size]
        placeholders = ", ".join(["(?, ?, ?)"] * len(chunk))
        # libsql_experimental's PyO3 binding accepts TUPLE positional params
        # only — a list raises "'list' object cannot be converted to
        # 'PyTuple'" (2026-08-09 orders-sync incident; only detonates when
        # working orders exist, the empty path takes the bare DELETE).
        params = tuple(value for row in chunk for value in row)
        db.execute(
            f"""
            INSERT INTO open_orders (perm_id, payload, updated_at)
            VALUES {placeholders}
            ON CONFLICT(perm_id) DO UPDATE SET
              payload    = excluded.payload,
              updated_at = excluded.updated_at
            """,
            params,
        )

    id_placeholders = ", ".join(["?"] * len(rows))
    db.execute(
        f"DELETE FROM open_orders WHERE perm_id NOT IN ({id_placeholders})",
        tuple(row[0] for row in rows),
    )
    db.commit()


def upsert_daemon_state(
    handler: str,
    *,
    last_run: Optional[str] = None,
    last_status: Optional[str] = None,
    last_error: Optional[str] = None,
) -> None:
    """Phase 4 — replaces data/daemon_state.json per-handler tick log.

    Bounded Hrana transport (REL-008 / R-013): this write runs after EVERY
    daemon cycle — on native libsql a Turso stall held the GIL and froze
    the whole loop, exit orders included.
    """
    _hrana_execute(
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
        SELECT * FROM (
          SELECT * FROM ticker_flow_history
          WHERE ticker = ?
          ORDER BY date DESC
          LIMIT ?
        ) newest
        ORDER BY date ASC
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


# ── Performance TWR (plan §4.2) ─────────────────────────────────────────────
# Daily NAV snapshots from Flex EquitySummaryInBase, external flows from
# CashTransactions+Transfers, and chained TWR subperiods derived from (B,E,C).
# Tables are created via migration 0035_perf_twr.sql; ensure helper below
# keeps first-run idempotent when the migration hasn't applied yet (plan §4.2:
# "Ensure tables are created via dbExecute on first performance build").

_PERF_TWR_DDL = """
CREATE TABLE IF NOT EXISTS nav_snapshots (
  account_id    TEXT NOT NULL,
  report_date   TEXT NOT NULL,
  total_net_liq REAL NOT NULL,
  cash          REAL,
  stock         REAL,
  options       REAL,
  accrued_fees  REAL,
  PRIMARY KEY (account_id, report_date)
);
CREATE INDEX IF NOT EXISTS idx_nav_snapshots_date ON nav_snapshots (report_date DESC);
CREATE TABLE IF NOT EXISTS external_flows (
  account_id  TEXT NOT NULL,
  report_date TEXT NOT NULL,
  amount      REAL NOT NULL,
  flow_type   TEXT NOT NULL,
  note        TEXT,
  PRIMARY KEY (account_id, report_date, flow_type)
);
CREATE INDEX IF NOT EXISTS idx_external_flows_date ON external_flows (report_date DESC);
CREATE TABLE IF NOT EXISTS twr_subperiods (
  account_id  TEXT NOT NULL,
  report_date TEXT NOT NULL,
  b           REAL NOT NULL,
  e           REAL NOT NULL,
  c           REAL NOT NULL,
  r           REAL NOT NULL,
  cum_r       REAL NOT NULL,
  PRIMARY KEY (account_id, report_date)
);
CREATE INDEX IF NOT EXISTS idx_twr_subperiods_date ON twr_subperiods (report_date DESC);
"""


_perf_twr_tables_ensured = False


def ensure_perf_twr_tables() -> None:
    """Idempotent: CREATE TABLE IF NOT EXISTS for the three TWR tables.

    Called by the performance builder on first build and by the migration
    script. Uses the synchronous libsql path (the builder is a short-lived
    subprocess with TimeoutStartSec, so no bounded-HTTP needed; the tables
    are tiny DDL). Once-per-process memo keeps batch upserts from issuing
    DDL on every row (plan §4.2: ensure via dbExecute on first build)."""
    global _perf_twr_tables_ensured
    if _perf_twr_tables_ensured:
        return
    db = get_db()
    for stmt in [s.strip() for s in _PERF_TWR_DDL.strip().split(";") if s.strip()]:
        db.execute(stmt)
    db.commit()
    _perf_twr_tables_ensured = True


def _ensure_perf_twr_tables_if_missing(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "no such table" in msg and ("nav_snapshots" in msg or "external_flows" in msg or "twr_subperiods" in msg)


NAV_SNAPSHOT_UPSERT_SQL = """
INSERT INTO nav_snapshots (account_id, report_date, total_net_liq, cash, stock, options, accrued_fees)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(account_id, report_date) DO UPDATE SET
  total_net_liq = excluded.total_net_liq,
  cash          = excluded.cash,
  stock         = excluded.stock,
  options       = excluded.options,
  accrued_fees  = excluded.accrued_fees
"""

EXTERNAL_FLOW_UPSERT_SQL = """
INSERT INTO external_flows (account_id, report_date, amount, flow_type, note)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(account_id, report_date, flow_type) DO UPDATE SET
  amount = excluded.amount,
  note   = excluded.note
"""

TWR_SUBPERIOD_UPSERT_SQL = """
INSERT INTO twr_subperiods (account_id, report_date, b, e, c, r, cum_r)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(account_id, report_date) DO UPDATE SET
  b     = excluded.b,
  e     = excluded.e,
  c     = excluded.c,
  r     = excluded.r,
  cum_r = excluded.cum_r
"""


def upsert_nav_snapshot(
    account_id: str,
    report_date: str,
    total_net_liq: float,
    *,
    cash: Optional[float] = None,
    stock: Optional[float] = None,
    options: Optional[float] = None,
    accrued_fees: Optional[float] = None,
) -> None:
    """Upsert one daily NAV snapshot (Flex EquitySummaryInBase)."""
    db = get_db()
    try:
        db.execute(
            NAV_SNAPSHOT_UPSERT_SQL,
            (account_id, report_date, float(total_net_liq), cash, stock, options, accrued_fees),
        )
    except Exception as exc:  # noqa: BLE001 — first-build ensure (plan §4.2)
        if _ensure_perf_twr_tables_if_missing(exc):
            ensure_perf_twr_tables()
            db.execute(
                NAV_SNAPSHOT_UPSERT_SQL,
                (account_id, report_date, float(total_net_liq), cash, stock, options, accrued_fees),
            )
        else:
            raise
    db.commit()


def upsert_nav_snapshot_rows(rows: list[dict[str, Any]]) -> None:
    """Batch upsert of NAV snapshots (chunked multi-row INSERTs per Hrana I/O bounding).

    Each row: {account_id, report_date, total_net_liq, cash?, stock?, options?, accrued_fees?}
    """
    if not rows:
        return
    db = get_db()
    for start in range(0, len(rows), _PRICE_HISTORY_INSERT_CHUNK_ROWS):
        chunk = rows[start : start + _PRICE_HISTORY_INSERT_CHUNK_ROWS]
        placeholders = ", ".join("(?, ?, ?, ?, ?, ?, ?)" for _ in chunk)
        params: list[Any] = []
        for row in chunk:
            params.extend(
                (
                    row["account_id"],
                    row["report_date"],
                    float(row["total_net_liq"]),
                    row.get("cash"),
                    row.get("stock"),
                    row.get("options"),
                    row.get("accrued_fees"),
                )
            )
        try:
            db.execute(
                "INSERT INTO nav_snapshots (account_id, report_date, total_net_liq, cash, stock, options, accrued_fees) "
                f"VALUES {placeholders} "
                "ON CONFLICT(account_id, report_date) DO UPDATE SET "
                "total_net_liq = excluded.total_net_liq, cash = excluded.cash, "
                "stock = excluded.stock, options = excluded.options, accrued_fees = excluded.accrued_fees",
                tuple(params),
            )
        except Exception as exc:  # noqa: BLE001 — first-build ensure
            if _ensure_perf_twr_tables_if_missing(exc):
                ensure_perf_twr_tables()
                db.execute(
                    "INSERT INTO nav_snapshots (account_id, report_date, total_net_liq, cash, stock, options, accrued_fees) "
                    f"VALUES {placeholders} "
                    "ON CONFLICT(account_id, report_date) DO UPDATE SET "
                    "total_net_liq = excluded.total_net_liq, cash = excluded.cash, "
                    "stock = excluded.stock, options = excluded.options, accrued_fees = excluded.accrued_fees",
                    tuple(params),
                )
            else:
                raise
    db.commit()


def upsert_external_flow(
    account_id: str,
    report_date: str,
    amount: float,
    flow_type: str,
    note: Optional[str] = None,
) -> None:
    """Upsert one external flow (deposit|withdrawal|acats|internal) per account/day/type."""
    db = get_db()
    try:
        db.execute(
            EXTERNAL_FLOW_UPSERT_SQL,
            (account_id, report_date, float(amount), flow_type, note),
        )
    except Exception as exc:  # noqa: BLE001 — first-build ensure
        if _ensure_perf_twr_tables_if_missing(exc):
            ensure_perf_twr_tables()
            db.execute(
                EXTERNAL_FLOW_UPSERT_SQL,
                (account_id, report_date, float(amount), flow_type, note),
            )
        else:
            raise
    db.commit()


def upsert_external_flow_rows(rows: list[dict[str, Any]]) -> None:
    """Batch upsert of external flows (chunked multi-row INSERTs).

    Each row: {account_id, report_date, amount, flow_type, note?}
    """
    if not rows:
        return
    db = get_db()
    for start in range(0, len(rows), _PRICE_HISTORY_INSERT_CHUNK_ROWS):
        chunk = rows[start : start + _PRICE_HISTORY_INSERT_CHUNK_ROWS]
        placeholders = ", ".join("(?, ?, ?, ?, ?)" for _ in chunk)
        params: list[Any] = []
        for row in chunk:
            params.extend(
                (
                    row["account_id"],
                    row["report_date"],
                    float(row["amount"]),
                    row["flow_type"],
                    row.get("note"),
                )
            )
        try:
            db.execute(
                "INSERT INTO external_flows (account_id, report_date, amount, flow_type, note) "
                f"VALUES {placeholders} "
                "ON CONFLICT(account_id, report_date, flow_type) DO UPDATE SET "
                "amount = excluded.amount, note = excluded.note",
                tuple(params),
            )
        except Exception as exc:  # noqa: BLE001 — first-build ensure
            if _ensure_perf_twr_tables_if_missing(exc):
                ensure_perf_twr_tables()
                db.execute(
                    "INSERT INTO external_flows (account_id, report_date, amount, flow_type, note) "
                    f"VALUES {placeholders} "
                    "ON CONFLICT(account_id, report_date, flow_type) DO UPDATE SET "
                    "amount = excluded.amount, note = excluded.note",
                    tuple(params),
                )
            else:
                raise
    db.commit()


def upsert_twr_subperiod(
    account_id: str,
    report_date: str,
    b: float,
    e: float,
    c: float,
    r: float,
    cum_r: float,
) -> None:
    """Upsert one TWR subperiod derived from nav_snapshots + external_flows."""
    db = get_db()
    try:
        db.execute(
            TWR_SUBPERIOD_UPSERT_SQL,
            (account_id, report_date, float(b), float(e), float(c), float(r), float(cum_r)),
        )
    except Exception as exc:  # noqa: BLE001 — first-build ensure
        if _ensure_perf_twr_tables_if_missing(exc):
            ensure_perf_twr_tables()
            db.execute(
                TWR_SUBPERIOD_UPSERT_SQL,
                (account_id, report_date, float(b), float(e), float(c), float(r), float(cum_r)),
            )
        else:
            raise
    db.commit()


def upsert_twr_subperiod_rows(rows: list[dict[str, Any]]) -> None:
    """Batch upsert of TWR subperiods (chunked multi-row INSERTs).

    Each row: {account_id, report_date, b, e, c, r, cum_r}
    """
    if not rows:
        return
    db = get_db()
    for start in range(0, len(rows), _PRICE_HISTORY_INSERT_CHUNK_ROWS):
        chunk = rows[start : start + _PRICE_HISTORY_INSERT_CHUNK_ROWS]
        placeholders = ", ".join("(?, ?, ?, ?, ?, ?, ?)" for _ in chunk)
        params: list[Any] = []
        for row in chunk:
            params.extend(
                (
                    row["account_id"],
                    row["report_date"],
                    float(row["b"]),
                    float(row["e"]),
                    float(row["c"]),
                    float(row["r"]),
                    float(row["cum_r"]),
                )
            )
        try:
            db.execute(
                "INSERT INTO twr_subperiods (account_id, report_date, b, e, c, r, cum_r) "
                f"VALUES {placeholders} "
                "ON CONFLICT(account_id, report_date) DO UPDATE SET "
                "b = excluded.b, e = excluded.e, c = excluded.c, r = excluded.r, cum_r = excluded.cum_r",
                tuple(params),
            )
        except Exception as exc:  # noqa: BLE001 — first-build ensure
            if _ensure_perf_twr_tables_if_missing(exc):
                ensure_perf_twr_tables()
                db.execute(
                    "INSERT INTO twr_subperiods (account_id, report_date, b, e, c, r, cum_r) "
                    f"VALUES {placeholders} "
                    "ON CONFLICT(account_id, report_date) DO UPDATE SET "
                    "b = excluded.b, e = excluded.e, c = excluded.c, r = excluded.r, cum_r = excluded.cum_r",
                    tuple(params),
                )
            else:
                raise
    db.commit()


def _as_float(value: Any) -> Optional[float]:
    return float(value) if value is not None else None
