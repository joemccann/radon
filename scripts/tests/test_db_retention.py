"""Unit tests for scripts/db/retention.py (R2 snapshot keep-latest policies)."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_PKG = "_r2_real_db"


def _load_retention():
    db_dir = _SCRIPTS_DIR / "db"
    if f"{_PKG}.retention" in sys.modules:
        return sys.modules[f"{_PKG}.retention"]
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [str(db_dir)]
    sys.modules[_PKG] = pkg
    spec = importlib.util.spec_from_file_location(
        f"{_PKG}.retention", db_dir / "retention.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{_PKG}.retention"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def retention():
    return _load_retention()


class TestPolicies:
    def test_snapshot_policies_exclude_portfolio(self, retention):
        tables = {p.table for p in retention.SNAPSHOT_RETENTION_POLICIES}
        assert "portfolio_snapshots" not in tables
        assert "service_health_events" not in tables
        assert "journal" not in tables
        assert "scanner_snapshots" in tables
        assert "gex_snapshots" in tables
        assert "vcg_snapshots" in tables

    def test_keep_latest_sql_is_parameterized(self, retention):
        p = retention.KeepLatestPolicy("scanner_snapshots", "scan_time", 30)
        sql = p.delete_sql()
        assert "DELETE FROM" in sql
        assert "scanner_snapshots" in sql
        assert "LIMIT ?" in sql
        assert p.delete_args() == (30,)

    def test_keep_latest_per_partition_uses_window(self, retention):
        p = retention.KeepLatestPerPartitionPolicy(
            "gex_snapshots", "ticker", "scan_time", 30
        )
        sql = p.delete_sql()
        assert "ROW_NUMBER()" in sql
        assert "PARTITION BY" in sql
        assert "rn > ?" in sql
        assert p.delete_args() == (30,)

    def test_keep_days_uses_date_modifier(self, retention):
        p = retention.KeepDaysPolicy("ticker_flow_history", "date", 120)
        assert "date('now', ?)" in p.delete_sql()
        assert p.delete_args() == ("-120 days",)


class TestRunSweep:
    def test_applies_each_policy_and_commits(self, retention):
        cursor = MagicMock()
        cursor.rowcount = 3
        db = MagicMock()
        db.execute.return_value = cursor

        policies = [
            retention.KeepLatestPolicy("scanner_snapshots", "scan_time", 10),
            retention.KeepLatestPolicy("vcg_snapshots", "scan_time", 10),
        ]
        results = retention.run_retention_sweep(db, policies)

        assert results == {"scanner_snapshots": 3, "vcg_snapshots": 3}
        assert db.execute.call_count == 2
        assert db.commit.call_count == 2

    def test_unknown_rowcount_reports_zero(self, retention):
        cursor = MagicMock()
        cursor.rowcount = -1
        db = MagicMock()
        db.execute.return_value = cursor
        policies = [retention.KeepLatestPolicy("oi_changes", "scan_time", 5)]
        assert retention.run_retention_sweep(db, policies) == {"oi_changes": 0}

    def test_db_errors_propagate(self, retention):
        db = MagicMock()
        db.execute.side_effect = ValueError("Hrana: stream closed")
        with pytest.raises(ValueError, match="stream closed"):
            retention.run_retention_sweep(
                db, [retention.KeepLatestPolicy("oi_changes", "scan_time", 5)]
            )

    def test_http_sweep_invokes_each_policy(self, retention, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(
            retention,
            "apply_policy_http",
            lambda policy, timeout=60.0: (calls.append(policy.table) or 0),
        )
        policies = [
            retention.KeepLatestPolicy("scanner_snapshots", "scan_time", 10),
            retention.KeepLatestPolicy("vcg_snapshots", "scan_time", 10),
        ]
        results, failed = retention.run_retention_sweep_http(policies)
        assert results == {"scanner_snapshots": 0, "vcg_snapshots": 0}
        assert failed == []
        assert calls == ["scanner_snapshots", "vcg_snapshots"]

    def test_http_sweep_collects_failed_tables(self, retention, monkeypatch):
        def boom(policy, timeout=60.0):
            if policy.table == "vcg_snapshots":
                raise TimeoutError("timed out")
            return 0

        monkeypatch.setattr(retention, "apply_policy_http", boom)
        policies = [
            retention.KeepLatestPolicy("scanner_snapshots", "scan_time", 10),
            retention.KeepLatestPolicy("vcg_snapshots", "scan_time", 10),
        ]
        results, failed = retention.run_retention_sweep_http(policies)
        assert results == {"scanner_snapshots": 0, "vcg_snapshots": 0}
        assert failed == ["vcg_snapshots"]


class TestR076NewTableCoverage:
    """R-076: the durable history tables added in migrations 0039-0049 must
    carry retention policies, with horizons that never truncate under their
    deepest live reader."""

    REQUIRED = {
        "cor_history",
        "vol_cone_history",
        "skew2d_history",
        "equibles_ats_venue_share",
        "equibles_short_interest",
        "equibles_13f_holders",
        "equibles_13f_snapshots",
        "cot_positioning",
        "watchdog_pages",
    }

    def test_every_unbounded_new_table_has_a_policy(self, retention):
        tables = {p.table for p in retention.SNAPSHOT_RETENTION_POLICIES}
        assert not (self.REQUIRED - tables)

    def test_bounded_or_append_only_tables_stay_out(self, retention):
        tables = {p.table for p in retention.SNAPSHOT_RETENTION_POLICIES}
        assert "app_preference_events" not in tables  # intentionally append-only
        # ticker-PK upsert-in-place tables are already bounded by the universe
        assert "equibles_squeeze_scores" not in tables
        assert "equibles_filing_forensics" not in tables

    def test_horizons_never_truncate_under_a_live_reader(self, retention):
        by_table = {p.table: p for p in retention.SNAPSHOT_RETENTION_POLICIES}
        # cot payload percentile-ranks against 3 years of weekly reports
        assert by_table["cot_positioning"].days >= 3 * 365
        # ats venue z-scores ride a 104-week lookback
        assert by_table["equibles_ats_venue_share"].days >= 104 * 7
        # 13f QoQ ownership series spans 8 quarters
        assert by_table["equibles_13f_holders"].days >= 8 * 92
        assert by_table["equibles_13f_snapshots"].days >= 8 * 92
        # short-interest history carries ~26 bi-monthly settlements (~1 year)
        assert by_table["equibles_short_interest"].days >= 365
        # fetch_vol_cone backfills an 80-session (~116 calendar day) lookback
        assert by_table["vol_cone_history"].days >= 120
        # vixcor derives from the FULL cor3m series (~5,190 sessions today);
        # a count cap must sit comfortably above it so the product never
        # truncates before the source CSV could re-backfill anyway
        assert by_table["cor_history"].keep >= 6000

    def test_policies_delete_old_rows_and_keep_reader_depth(self, retention):
        import sqlite3
        from datetime import date, timedelta

        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE watchdog_pages (page_id TEXT PRIMARY KEY, paged_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE equibles_short_interest ("
            "ticker TEXT, settlement_date TEXT, PRIMARY KEY (ticker, settlement_date))"
        )
        today = date.today()
        ancient = (today - timedelta(days=2000)).isoformat()
        fresh = (today - timedelta(days=10)).isoformat()
        conn.execute(
            "INSERT INTO watchdog_pages VALUES (?, ?)", ("old", f"{ancient}T12:00:00Z")
        )
        conn.execute(
            "INSERT INTO watchdog_pages VALUES (?, ?)", ("new", f"{fresh}T12:00:00Z")
        )
        conn.execute("INSERT INTO equibles_short_interest VALUES (?, ?)", ("MU", ancient))
        conn.execute("INSERT INTO equibles_short_interest VALUES (?, ?)", ("MU", fresh))
        conn.commit()

        by_table = {p.table: p for p in retention.SNAPSHOT_RETENTION_POLICIES}
        retention.apply_policy(conn, by_table["watchdog_pages"])
        retention.apply_policy(conn, by_table["equibles_short_interest"])

        pages = [r[0] for r in conn.execute("SELECT page_id FROM watchdog_pages")]
        settlements = [
            r[0] for r in conn.execute("SELECT settlement_date FROM equibles_short_interest")
        ]
        assert pages == ["new"]
        assert settlements == [fresh]
