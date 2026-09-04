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
    # T-108: behavioral coverage lives in scripts/lib/demoMirrorReliability.test.js
    # (purge issued by default, retried on 502, run fails when it persists).
    # This grep only pins the wiring shape so the loop cannot vanish silently.
    source = (Path(__file__).parents[1] / "db" / "mirror_market_snapshots_to_demo.js").read_text()
    latest_block = source.split("const LATEST_ONE = [", 1)[1].split("];", 1)[0]
    assert "flow_analysis_snapshots" not in latest_block
    assert 'PURGED_ACCOUNT_TABLES = ["flow_analysis_snapshots"]' in source

    run_fn = source.split("export async function runMarketMirror({", 1)[1]
    purge_loop = run_fn.split("for (const table of purgedAccountTables) {", 1)[1].split("\n  }\n", 1)[0]
    assert "retryOperation({" in purge_loop
    assert "phase: `${table}:account_purge`" in purge_loop
    assert "dst.execute(`DELETE FROM ${table}`)" in purge_loop
    assert "throw new Error(" in purge_loop
    assert "SKIP purge" not in source


def test_market_mirror_fails_run_and_prunes_destination_windows():
    source = (Path(__file__).parents[1] / "db" / "mirror_market_snapshots_to_demo.js").read_text()
    assert "required table failures" in source
    assert "DELETE FROM ${table} WHERE ${orderCol} NOT IN" in source
    assert "DELETE FROM ${table} WHERE (${key}, ${orderCol}) NOT IN" in source


def test_market_mirror_retries_transient_turso_502():
    """ba86fe0a: oneshot paged P1 on a single scan_snapshots HTTP 502."""
    source = (Path(__file__).parents[1] / "db" / "mirror_market_snapshots_to_demo.js").read_text()
    assert "isTransientTursoError" in source
    assert "http status 5\\d\\d" in source
    assert "MIRROR_MAX_ATTEMPTS" in source
    assert "source_read" in source


# ── demo migration ledger (2026-09-03 demo outage) ───────────────────────
#
# The demo Turso DB shares ONE `schema_migrations` table with the MAIN
# migration series (versions 1..69). demo_migrations/0003 declared version 3,
# which the main series claimed on 2026-06-29, so the version-keyed applier
# skipped it forever and `demo_webhook_events` was never created. Every Clerk
# user.created webhook then threw and no trial was provisioned for 22 days.
# The demo series gets its own NAME-keyed ledger so a version collision with
# the main series is structurally impossible.


class _LedgerFakeDb:
    """libsql-shaped fake with a shared, already-populated schema_migrations."""

    def __init__(self, applied_demo_names=(), main_versions=range(1, 70)):
        self.statements: list[str] = []
        self.recorded: list[str] = []
        self._applied_demo_names = list(applied_demo_names)
        self._main_versions = list(main_versions)
        self._last_rows: list[tuple] = []

    def execute(self, sql, params=None):
        self.statements.append(sql.strip())
        lowered = " ".join(sql.lower().split())
        if "insert or replace into demo_schema_migrations" in lowered:
            self.recorded.append(params[0])
        if "from demo_schema_migrations" in lowered:
            self._last_rows = [(n,) for n in self._applied_demo_names]
        elif "from schema_migrations" in lowered:
            self._last_rows = [(v,) for v in self._main_versions]
        else:
            self._last_rows = []
        return self

    def fetchall(self):
        return self._last_rows

    def commit(self):
        pass


def test_demo_migrations_apply_despite_main_series_version_collision():
    db = _LedgerFakeDb()
    demo_seed.apply_demo_migrations(db)
    joined = " ".join(db.statements)
    assert "CREATE TABLE IF NOT EXISTS demo_webhook_events" in joined
    assert "CREATE TABLE IF NOT EXISTS demo_users" in joined
    assert "CREATE TABLE IF NOT EXISTS demo_ai_usage" in joined


def test_demo_migrations_record_into_their_own_ledger_only():
    db = _LedgerFakeDb()
    demo_seed.apply_demo_migrations(db)
    joined = " ".join(db.statements)
    assert "demo_schema_migrations" in joined
    assert db.recorded == [
        "0001_demo_users.sql",
        "0002_demo_ai_usage.sql",
        "0003_demo_webhook_events.sql",
    ]
    # The shared main-series ledger is read by nothing and written by nothing.
    assert "INSERT OR IGNORE INTO schema_migrations" not in joined
    assert "SELECT version FROM schema_migrations" not in joined


def test_demo_migrations_skip_names_already_in_the_demo_ledger():
    db = _LedgerFakeDb(applied_demo_names=[
        "0001_demo_users.sql", "0002_demo_ai_usage.sql",
        "0003_demo_webhook_events.sql",
    ])
    demo_seed.apply_demo_migrations(db)
    joined = " ".join(db.statements)
    assert "CREATE TABLE IF NOT EXISTS demo_webhook_events" not in joined
    assert "CREATE TABLE IF NOT EXISTS demo_users" not in joined


def test_demo_migration_files_never_touch_the_shared_schema_migrations_table():
    for _version, name, path in demo_seed._list_demo_migrations():
        sql = path.read_text(encoding="utf-8")
        assert "INSERT OR IGNORE INTO schema_migrations" not in sql, (
            f"{name} writes the shared main-series ledger — that INSERT is a "
            "silent no-op and misleads any version-keyed applier."
        )


def test_assert_not_prod_requires_the_positive_demo_marker():
    """A URL that is neither prod-marked nor demo-marked must still be refused."""
    with pytest.raises(SystemExit) as exc:
        demo_seed.assert_not_prod("libsql://some-other-db.aws-us-west-2.turso.io")
    assert "REFUSING TO SEED" in str(exc.value)


# ── seed freshness (2026-09-03) ──────────────────────────────────────────
#
# The seeded workspace was frozen at 2026-06-17 with hardcoded expiries, so by
# September every synthetic option had already expired and the demo rendered a
# dead account. Seed dates are anchored to the run date instead.


def _anchored(monkeypatch, iso_day: str):
    monkeypatch.setenv("RADON_DEMO_SEED_TODAY", iso_day)


def test_seeded_positions_never_carry_an_expired_option(monkeypatch):
    from datetime import date

    for anchor in ("2026-09-03", "2027-03-01", "2028-12-15"):
        _anchored(monkeypatch, anchor)
        payload = demo_seed.build_portfolio_payload()
        today = date.fromisoformat(anchor)
        for pos in payload["positions"]:
            expiry = date.fromisoformat(pos["expiry"])
            assert expiry > today, f"{pos['ticker']} expiry {expiry} <= anchor {today}"
            assert date.fromisoformat(pos["entry_date"]) < today


def test_seeded_open_orders_and_journal_track_the_anchor(monkeypatch):
    from datetime import date

    _anchored(monkeypatch, "2027-03-01")
    today = date.fromisoformat("2027-03-01")
    for _perm_id, order in demo_seed.build_open_orders():
        expiry = order["contract"]["expiry"]
        assert len(expiry) == 8, "open_orders use the compact IB YYYYMMDD form"
        assert date(int(expiry[:4]), int(expiry[4:6]), int(expiry[6:])) > today
    for _trade_id, payload, filled_at in demo_seed.build_journal_rows():
        assert date.fromisoformat(payload["expiry"]) > today
        assert date.fromisoformat(filled_at) < today


def test_seed_snapshot_is_taken_at_the_anchor(monkeypatch):
    _anchored(monkeypatch, "2027-03-01")
    assert demo_seed.seed_snapshot_taken_at().startswith("2027-03-01T")
    assert demo_seed.build_portfolio_payload()["last_sync"].startswith("2027-03-01T")


def test_reseeding_replaces_the_snapshot_rather_than_accumulating(monkeypatch):
    _anchored(monkeypatch, "2027-03-01")
    db = _FakeDb()
    demo_seed.seed_synthetic_dataset(db)
    joined = " ".join(db.statements)
    assert "DELETE FROM portfolio_snapshots" in joined
    assert joined.index("DELETE FROM portfolio_snapshots") < joined.index(
        "INSERT OR REPLACE INTO portfolio_snapshots"
    )
