"""Tests for scripts/db/demo_seed.py — prod-URL guard + dataset reconciliation."""

import importlib
from pathlib import Path

import pytest

demo_seed = importlib.import_module("db.demo_seed")


def test_assert_not_prod_refuses_prod_looking_url():
    with pytest.raises(SystemExit) as exc:
        demo_seed.assert_not_prod(
            "libsql://radon-joemccann.aws-us-west-2.turso.io"
        )
    assert "REFUSING TO SEED" in str(exc.value)


def test_assert_not_prod_accepts_demo_url():
    # Demo URL does not contain the prod marker → no raise.
    demo_seed.assert_not_prod("libsql://radon-demo-abc.aws-us-west-2.turso.io")


class _FakeDb:
    """Minimal libsql-shaped fake: records executed SQL, supports fetchall."""

    def __init__(self):
        self.statements: list[str] = []
        self._applied_versions: list[int] = []

    def execute(self, sql, params=None):
        self.statements.append(sql.strip())
        return self

    def fetchall(self):
        # apply_demo_migrations queries already-applied versions; pretend none.
        return []

    def commit(self):
        pass


def test_seed_synthetic_dataset_writes_expected_tables(monkeypatch):
    db = _FakeDb()
    demo_seed.seed_synthetic_dataset(db)
    joined = " ".join(db.statements)
    assert "INSERT OR REPLACE INTO portfolio_snapshots" in joined
    assert "INSERT OR REPLACE INTO journal" in joined
    assert "INSERT OR REPLACE INTO open_orders" in joined


def test_portfolio_payload_reconciles():
    payload = demo_seed.build_portfolio_payload()
    # MV - initial == unrealized for each position; sum matches account summary.
    total_initial = sum(p["entry_cost"] for p in payload["positions"])
    total_mv = sum(p["market_value"] for p in payload["positions"])
    assert payload["account_summary"]["unrealized_pnl"] == round(
        total_mv - total_initial, 2
    )
    assert payload["position_count"] == len(payload["positions"]) == 4
    # Each single-leg option position: MV = last_per_contract * contracts.
    for pos in payload["positions"]:
        leg_mv = sum(leg["market_value"] for leg in pos["legs"])
        assert round(leg_mv, 2) == round(pos["market_value"], 2)


def test_main_aborts_on_prod_env(monkeypatch):
    monkeypatch.setenv("TURSO_DEMO_DB_URL", "libsql://radon-joemccann.turso.io")
    monkeypatch.setenv("TURSO_DEMO_AUTH_TOKEN", "tok")
    # Avoid touching dotenv / real network: the guard fires before connect.
    monkeypatch.setattr(demo_seed, "_load_env", lambda: None)
    with pytest.raises(SystemExit) as exc:
        demo_seed.main()
    assert "REFUSING TO SEED" in str(exc.value)


def test_market_mirror_excludes_and_purges_account_derived_flow_rows():
    source = (Path(__file__).parents[1] / "db" / "mirror_market_snapshots_to_demo.js").read_text()
    latest_block = source.split("const LATEST_ONE = [", 1)[1].split("];", 1)[0]
    assert "flow_analysis_snapshots" not in latest_block
    assert 'const PURGED_ACCOUNT_TABLES = ["flow_analysis_snapshots"]' in source
    assert "DELETE FROM ${table}" in source


def test_market_mirror_fails_run_and_prunes_destination_windows():
    source = (Path(__file__).parents[1] / "db" / "mirror_market_snapshots_to_demo.js").read_text()
    assert "required table failures" in source
    assert "DELETE FROM ${table} WHERE ${orderCol} NOT IN" in source
    assert "DELETE FROM ${table} WHERE (${key}, ${orderCol}) NOT IN" in source
