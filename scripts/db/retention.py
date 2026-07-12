"""Retention policies for append-only Turso snapshot tables.

Most scan tables are written with a new time PK every run while readers only
ever take ORDER BY time DESC LIMIT 1. Without a keep-latest sweep they grow
unbounded and dominate nightly dumps. Portfolio snapshots are intentionally
NOT handled here — they have a crash-safe cold-archive pipeline
(``scripts/archive_portfolio_snapshots.py``).

Called from the daily ``db_retention_sweep`` oneshot (VPS timer), never from
the per-scan write path: libsql_experimental has no client timeouts, so a
large DELETE during a Turso degradation window must not hang a market-hours
scan subprocess.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

# Default keep counts: enough history for short diagnostics, not months of
# superseded JSON. Tune per table if a consumer starts reading deeper history.
DEFAULT_KEEP = 30
GEX_KEEP_PER_TICKER = 30
ANALYST_KEEP_PER_TICKER = 5
CRI_KEEP = 60  # multi-day cri/breadth can have several intraday rows
FORECAST_KEEP_PER_TICKER = 30
RECONCILIATION_KEEP = 30
BACKTEST_KEEP_PER_STRATEGY = 20
# Matches get_ticker_flow_history(lookback_days=120) so the forecasting
# series is not truncated under the reader.
TICKER_FLOW_KEEP_DAYS = 120


@dataclass(frozen=True)
class KeepLatestPolicy:
    """Keep the newest ``keep`` rows by ``time_col`` (table-wide)."""

    table: str
    time_col: str
    keep: int = DEFAULT_KEEP

    def delete_sql(self) -> str:
        # time_col is a fixed identifier from this module, never user input.
        return (
            f'DELETE FROM "{self.table}" WHERE "{self.time_col}" NOT IN ('
            f'SELECT "{self.time_col}" FROM "{self.table}" '
            f'ORDER BY "{self.time_col}" DESC LIMIT ?)'
        )

    def delete_args(self) -> tuple[Any, ...]:
        return (self.keep,)


@dataclass(frozen=True)
class KeepLatestPerPartitionPolicy:
    """Keep the newest ``keep`` rows per distinct ``partition_col`` value."""

    table: str
    partition_col: str
    time_col: str
    keep: int = DEFAULT_KEEP

    def delete_sql(self) -> str:
        # Window function: delete rows ranked older than keep within each
        # partition. libSQL/SQLite support ROW_NUMBER over partitions.
        return (
            f'DELETE FROM "{self.table}" WHERE rowid IN ('
            f"SELECT rowid FROM ("
            f'SELECT rowid, ROW_NUMBER() OVER ('
            f'PARTITION BY "{self.partition_col}" '
            f'ORDER BY "{self.time_col}" DESC'
            f") AS rn FROM \"{self.table}\""
            f") ranked WHERE rn > ?)"
        )

    def delete_args(self) -> tuple[Any, ...]:
        return (self.keep,)


@dataclass(frozen=True)
class KeepDaysPolicy:
    """Delete rows whose date-like ``time_col`` is older than ``days``.

    Uses SQLite ``date('now', ?)`` so YYYY-MM-DD columns compare correctly
    (full datetime() strings would not).
    """

    table: str
    time_col: str
    days: int

    def delete_sql(self) -> str:
        return (
            f'DELETE FROM "{self.table}" '
            f'WHERE "{self.time_col}" < date(\'now\', ?)'
        )

    def delete_args(self) -> tuple[Any, ...]:
        return (f"-{self.days} days",)


Policy = KeepLatestPolicy | KeepLatestPerPartitionPolicy | KeepDaysPolicy

# Ordered list applied by the daily sweep. Portfolio is excluded (R1 archive).
# service_health_events is pruned by flex_token_check (DUR-11).
# host_metrics is pruned by the sampler itself.
SNAPSHOT_RETENTION_POLICIES: Sequence[Policy] = (
    KeepLatestPolicy("scanner_snapshots", "scan_time", DEFAULT_KEEP),
    KeepLatestPolicy("theta_harvester_snapshots", "scan_time", DEFAULT_KEEP),
    KeepLatestPolicy("strength_confirmation_snapshots", "scan_time", DEFAULT_KEEP),
    KeepLatestPolicy("flow_analysis_snapshots", "scan_time", DEFAULT_KEEP),
    KeepLatestPolicy("performance_snapshots", "taken_at", DEFAULT_KEEP),
    KeepLatestPolicy("discover_snapshots", "scan_time", DEFAULT_KEEP),
    KeepLatestPolicy("discover_sp500_snapshots", "scan_time", DEFAULT_KEEP),
    KeepLatestPolicy("oi_changes", "scan_time", DEFAULT_KEEP),
    KeepLatestPolicy("vcg_snapshots", "scan_time", DEFAULT_KEEP),
    KeepLatestPolicy("gamma_rotation_snapshots", "scan_time", DEFAULT_KEEP),
    KeepLatestPolicy("reconciliation_log", "snapshot_at", RECONCILIATION_KEEP),
    KeepLatestPolicy("cri_snapshots", "taken_at", CRI_KEEP),
    KeepLatestPolicy("breadth_snapshots", "taken_at", CRI_KEEP),
    KeepLatestPerPartitionPolicy(
        "gex_snapshots", "ticker", "scan_time", GEX_KEEP_PER_TICKER
    ),
    KeepLatestPerPartitionPolicy(
        "analyst_ratings", "ticker", "fetched_at", ANALYST_KEEP_PER_TICKER
    ),
    KeepLatestPerPartitionPolicy(
        "forecast_snapshots", "ticker", "scan_time", FORECAST_KEEP_PER_TICKER
    ),
    KeepLatestPerPartitionPolicy(
        "forecast_calibration", "ticker", "scan_time", FORECAST_KEEP_PER_TICKER
    ),
    KeepLatestPerPartitionPolicy(
        "backtest_runs", "strategy", "run_at", BACKTEST_KEEP_PER_STRATEGY
    ),
    KeepDaysPolicy("ticker_flow_history", "date", TICKER_FLOW_KEEP_DAYS),
)


def _rowcount(cursor: Any) -> int:
    deleted = getattr(cursor, "rowcount", None)
    return deleted if isinstance(deleted, int) and deleted >= 0 else 0


def apply_policy(db: Any, policy: Policy) -> int:
    """Execute one policy's DELETE and commit. Returns rows deleted (best-effort)."""
    cursor = db.execute(policy.delete_sql(), policy.delete_args())
    db.commit()
    return _rowcount(cursor)


def run_retention_sweep(
    db: Any,
    policies: Sequence[Policy] = SNAPSHOT_RETENTION_POLICIES,
    *,
    on_policy: Optional[Callable[[Policy, int], None]] = None,
) -> dict[str, int]:
    """Apply every policy; return {table: rows_deleted}. Raises on DB errors."""
    results: dict[str, int] = {}
    for policy in policies:
        deleted = apply_policy(db, policy)
        results[policy.table] = results.get(policy.table, 0) + deleted
        if on_policy is not None:
            on_policy(policy, deleted)
    return results
