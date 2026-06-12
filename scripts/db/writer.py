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
    from .service_health_sql import SERVICE_HEALTH_UPSERT_SQL, service_health_upsert_args
except ImportError:  # pragma: no cover
    # When imported flat after sys.path.insert(scripts/) like the existing
    # services do (cta_sync_service.py et al).
    from db.client import get_db  # type: ignore[no-redef]
    from db.service_health_sql import (  # type: ignore[no-redef]
        SERVICE_HEALTH_UPSERT_SQL,
        service_health_upsert_args,
    )


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


def upsert_portfolio_snapshot(taken_at: str, payload: dict[str, Any]) -> None:
    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO portfolio_snapshots (taken_at, payload)
        VALUES (?, ?)
        """,
        (taken_at, json.dumps(payload)),
    )
    db.commit()


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


def upsert_journal_entry(trade_id: str, payload: dict[str, Any], filled_at: Optional[str] = None) -> None:
    db = get_db()
    db.execute(
        """
        INSERT INTO journal (trade_id, payload, filled_at, written_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(trade_id) DO UPDATE SET
          payload    = excluded.payload,
          filled_at  = excluded.filled_at,
          written_at = excluded.written_at
        """,
        (trade_id, json.dumps(payload), filled_at, _now_iso()),
    )
    db.commit()


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
    """Phase 4 — replaces data/watchlist.json (one row per ticker)."""
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

    The statement + arg serialization live in ``db.service_health_sql`` so
    the FastAPI heal path (which runs the same upsert over the bounded hrana
    HTTP pipeline — sync libsql is banned in scripts/api) stays in lockstep.
    """
    db = get_db()
    db.execute(
        SERVICE_HEALTH_UPSERT_SQL,
        service_health_upsert_args(
            service,
            state,
            started_at=started_at,
            finished_at=finished_at,
            error=error,
        ),
    )
    db.commit()


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
