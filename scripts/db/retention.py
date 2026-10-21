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

# R-076: durable history tables added in migrations 0039-0049. Each horizon
# sits above its deepest live reader (pinned by test_db_retention.py), so the
# sweep bounds growth without ever truncating under a consumer.
# fetch_vixcor derives from the FULL cor3m series (~5,190 sessions today) and
# fetch_cor can re-backfill from the authoritative Cboe CSV, so cor_history is
# count-capped well above the whole series instead of date-bounded.
COR_KEEP_SESSIONS = 7000
VOL_CONE_KEEP_DAYS = 180              # fetch_vol_cone: 80-session lookback
SKEW2D_KEEP_DAYS = 730                # durable mirror, no deep reader yet
EQUIBLES_WEEKLY_KEEP_DAYS = 1096      # ats venue z-scores: 104-week lookback
EQUIBLES_SETTLEMENT_KEEP_DAYS = 730   # ~26 bi-monthly settlements ≈ 1 year
EQUIBLES_13F_KEEP_DAYS = 1096         # 8-quarter QoQ ownership series
COT_KEEP_DAYS = 1461                  # percentiles rank against 3y of weeklies
WATCHDOG_PAGES_KEEP_DAYS = 90         # forensic dispatch trail
SLM_TAGGER_SHADOW_KEEP_DAYS = 90      # newsfeed SLM shadow provenance (I.1)

# R-120: the five tables migrations 0051-0054 added had NO policy, so the
# sweep that bounds every other history table skipped them. Each horizon sits
# above its deepest live reader, same rule as the R-076 block above.
CREDIT_SPREAD_KEEP_SESSIONS = 6000    # 18y+ daily series backs the full chart
IVRANK_KEEP_SESSIONS = 3000           # 252-session rank window + chart history
IEI_HYG_KEEP_SESSIONS = 3000          # 252-session extremes window + chart
CREDIT_VIX_KEEP_SESSIONS = 3000       # 252-session range-gap window + chart
TRIN_DAILY_KEEP_SESSIONS = 1500       # daily $TRIN chart (~6y)
# ~78 intraday samples a weekday; only the CURRENT session's MA(10) and the
# hourly chart read them, so this is deliberately the tightest of the five.
TRIN_SAMPLES_KEEP_ROWS = 4000

# account_margin_samples (~1.2k rows a day, PR #989 follow-up). Only samples
# near a fill can bracket a return-capital event, so old ones are dead weight
# unless an observation cites them or an unreconciled fill could still pair
# them. 121 s = the reconciler's 120 s max window + its 1 s text-order slack
# (capture_position_return_capital._merged_windows).
MARGIN_SAMPLES_KEEP_DAYS = 90
MARGIN_SAMPLES_BRACKET_WINDOW_S = 121
MARGIN_SAMPLES_DELETE_BATCH = 500
MARGIN_SAMPLES_MAX_BATCHES = 20  # <= 10k rows a run; steady state is ~1.2k


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


@dataclass(frozen=True)
class MarginSampleRetentionPolicy:
    """Delete ``account_margin_samples`` older than ``days`` in bounded batches.

    Kept regardless of age: any sample a ``position_capital_observations`` row
    cites (VALID or VOID), and any sample whose ``observed_from`` lies within
    ``bracket_window_s`` of a fill that no VALID observation covers yet, so
    the reconciler's plan cannot change under it. Each statement deletes at
    most ``batch_size`` rows and a run issues at most ``max_batches``
    (Hrana bounding rules, scripts/CLAUDE.md).
    """

    table: str = "account_margin_samples"
    days: int = MARGIN_SAMPLES_KEEP_DAYS
    bracket_window_s: int = MARGIN_SAMPLES_BRACKET_WINDOW_S
    batch_size: int = MARGIN_SAMPLES_DELETE_BATCH
    max_batches: int = MARGIN_SAMPLES_MAX_BATCHES

    def delete_sql(self) -> str:
        # Text timestamps: '%SZ' truncates the fraction, and a fractional fill
        # ('...:01.9Z') sorts below its whole second ('...:01Z'), so the bounds
        # cover at least bracket_window_s on both sides. Fact exec_ids carry
        # the IB correction suffix the ledger strips (`root.NN`).
        # NOT IN must never see a NULL reference: one would delete nothing.
        return """DELETE FROM account_margin_samples WHERE sample_id IN (
  SELECT s.sample_id FROM account_margin_samples s
  WHERE s.observed_from < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)
    AND s.sample_id NOT IN (
      SELECT before_sample_id FROM position_capital_observations
       WHERE before_sample_id IS NOT NULL
      UNION
      SELECT after_sample_id FROM position_capital_observations
       WHERE after_sample_id IS NOT NULL)
    AND NOT EXISTS (
      SELECT 1 FROM position_execution_facts f
       WHERE f.account_id = s.account_id
         AND f.filled_at BETWEEN strftime('%Y-%m-%dT%H:%M:%SZ', s.observed_from, ?)
                             AND strftime('%Y-%m-%dT%H:%M:%SZ', s.observed_from, ?)
         AND NOT EXISTS (
           SELECT 1 FROM position_event_executions e
             JOIN position_capital_observations o
               ON o.through_event_id = e.event_id AND o.status = 'VALID'
            WHERE e.account_id = f.account_id
              AND e.con_id = f.con_id
              AND (e.exec_id = f.exec_id
                   OR substr(f.exec_id, 1, length(e.exec_id) + 1) = e.exec_id || '.')))
  LIMIT ?)
RETURNING sample_id"""

    def delete_args(self) -> tuple[Any, ...]:
        # Tuple, never list: libsql's execute() raises TypeError on a list.
        return (
            f"-{self.days} days",
            f"-{self.bracket_window_s} seconds",
            f"+{self.bracket_window_s} seconds",
            self.batch_size,
        )


Policy = (
    KeepLatestPolicy
    | KeepLatestPerPartitionPolicy
    | KeepDaysPolicy
    | MarginSampleRetentionPolicy
)

# Ordered list applied by the daily sweep. Portfolio is excluded (R1 archive).
# service_health_events is pruned by flex_token_check (DUR-11).
# host_metrics is pruned by the sampler itself.
SNAPSHOT_RETENTION_POLICIES: Sequence[Policy] = (
    KeepLatestPolicy("scanner_snapshots", "scan_time", DEFAULT_KEEP),
    KeepLatestPolicy("theta_harvester_snapshots", "scan_time", DEFAULT_KEEP),
    KeepLatestPolicy("strength_confirmation_snapshots", "scan_time", DEFAULT_KEEP),
    KeepLatestPolicy("vol_skew_mr_snapshots", "scan_time", DEFAULT_KEEP),
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
    # R-076 history tables. equibles_squeeze_scores / equibles_filing_forensics
    # are ticker-PK upsert-in-place (bounded by the universe) and
    # app_preference_events is intentionally append-only — none get a policy.
    KeepLatestPolicy("cor_history", "date", COR_KEEP_SESSIONS),
    KeepDaysPolicy("vol_cone_history", "date", VOL_CONE_KEEP_DAYS),
    KeepDaysPolicy("skew2d_history", "date", SKEW2D_KEEP_DAYS),
    KeepDaysPolicy(
        "equibles_ats_venue_share", "week_start_date", EQUIBLES_WEEKLY_KEEP_DAYS
    ),
    KeepDaysPolicy(
        "equibles_short_interest", "settlement_date", EQUIBLES_SETTLEMENT_KEEP_DAYS
    ),
    KeepDaysPolicy("equibles_13f_holders", "report_date", EQUIBLES_13F_KEEP_DAYS),
    KeepDaysPolicy("equibles_13f_snapshots", "report_date", EQUIBLES_13F_KEEP_DAYS),
    KeepDaysPolicy("cot_positioning", "report_date", COT_KEEP_DAYS),
    # paged_at is a full ISO datetime; against date('now', ...) the boundary
    # day resolves one day late, which a 90-day forensic horizon absorbs.
    KeepDaysPolicy("watchdog_pages", "paged_at", WATCHDOG_PAGES_KEEP_DAYS),
    KeepDaysPolicy("watchdog_page_attempts", "attempted_at", WATCHDOG_PAGES_KEEP_DAYS),
    KeepDaysPolicy("slm_tagger_shadow", "observed_at", SLM_TAGGER_SHADOW_KEEP_DAYS),
    # R-120: migrations 0051-0054.
    KeepLatestPolicy("credit_spread_history", "date", CREDIT_SPREAD_KEEP_SESSIONS),
    KeepLatestPolicy("ivrank_history", "date", IVRANK_KEEP_SESSIONS),
    KeepLatestPolicy("iei_hyg_history", "date", IEI_HYG_KEEP_SESSIONS),
    KeepLatestPolicy("credit_vix_history", "date", CREDIT_VIX_KEEP_SESSIONS),
    KeepLatestPolicy("trin_daily", "date", TRIN_DAILY_KEEP_SESSIONS),
    KeepLatestPolicy("trin_samples", "ts", TRIN_SAMPLES_KEEP_ROWS),
    MarginSampleRetentionPolicy(),
)


def _rowcount(cursor: Any) -> int:
    deleted = getattr(cursor, "rowcount", None)
    return deleted if isinstance(deleted, int) and deleted >= 0 else 0


def _apply_batched(run_batch: Callable[[], int], policy: MarginSampleRetentionPolicy) -> int:
    """Repeat one bounded DELETE until a short batch or ``max_batches``."""
    total = 0
    for _ in range(policy.max_batches):
        deleted = run_batch()
        total += deleted
        if deleted < policy.batch_size:
            break
    return total


def apply_policy(db: Any, policy: Policy) -> int:
    """Execute one policy's DELETE and commit. Returns rows deleted (best-effort).

    Prefer :func:`apply_policy_http` in production oneshots — the sync libsql
    client has no timeout and hung the first fleet retention run (2026-07-12).
    """
    if isinstance(policy, MarginSampleRetentionPolicy):
        def run_batch() -> int:
            rows = db.execute(policy.delete_sql(), policy.delete_args()).fetchall()
            db.commit()
            return len(rows or [])

        return _apply_batched(run_batch, policy)
    cursor = db.execute(policy.delete_sql(), policy.delete_args())
    db.commit()
    return _rowcount(cursor)


# Bounded HTTP deletes. Window-function keep-latest DELETEs on scan tables are
# usually small; 60s absorbs a slow tail without hanging the oneshot forever.
RETENTION_HTTP_TIMEOUT_S = 60.0


def apply_policy_http(policy: Policy, *, timeout: float = RETENTION_HTTP_TIMEOUT_S) -> int:
    """Apply one policy over the bounded Hrana HTTP pipeline.

    Rowcount is unavailable on this transport (returns 0); success is the
    absence of an exception.
    """
    if isinstance(policy, MarginSampleRetentionPolicy):
        try:
            from .hrana_http import hrana_query
        except ImportError:  # pragma: no cover
            from db.hrana_http import hrana_query  # type: ignore[no-redef]

        # RETURNING rows are the only rowcount this transport gives back.
        return _apply_batched(
            lambda: len(hrana_query(policy.delete_sql(), policy.delete_args(), timeout=timeout)),
            policy,
        )

    try:
        from .hrana_http import hrana_execute
    except ImportError:  # pragma: no cover
        from db.hrana_http import hrana_execute  # type: ignore[no-redef]

    hrana_execute(policy.delete_sql(), policy.delete_args(), timeout=timeout)
    return 0


def run_retention_sweep(
    db: Any,
    policies: Sequence[Policy] = SNAPSHOT_RETENTION_POLICIES,
    *,
    on_policy: Optional[Callable[[Policy, int], None]] = None,
) -> dict[str, int]:
    """Apply every policy via ``db``; return {table: rows_deleted}. Raises on DB errors."""
    results: dict[str, int] = {}
    for policy in policies:
        deleted = apply_policy(db, policy)
        results[policy.table] = results.get(policy.table, 0) + deleted
        if on_policy is not None:
            on_policy(policy, deleted)
    return results


def run_retention_sweep_http(
    policies: Sequence[Policy] = SNAPSHOT_RETENTION_POLICIES,
    *,
    timeout: float = RETENTION_HTTP_TIMEOUT_S,
    on_policy: Optional[Callable[[Policy, int], None]] = None,
) -> tuple[dict[str, int], list[str]]:
    """Apply every policy over bounded Hrana HTTP (production oneshot path).

    Returns ``(results, failed_tables)``. Per-policy failures are logged and
    collected so one timed-out table cannot abort the rest of the sweep
    (Turso saturation class, 2026-07-12) while the caller can still fail the
    unit when every policy failed.
    """
    results: dict[str, int] = {}
    failed: list[str] = []
    for policy in policies:
        try:
            deleted = apply_policy_http(policy, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 — continue across tables
            print(f"[db-retention] skip {policy.table}: {exc}", flush=True)
            results[policy.table] = 0
            failed.append(policy.table)
            continue
        results[policy.table] = results.get(policy.table, 0) + deleted
        if on_policy is not None:
            on_policy(policy, deleted)
    return results, failed
