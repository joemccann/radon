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
