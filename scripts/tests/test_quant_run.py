"""quant_run CLI + data alignment + Turso contract (sqlite3 stand-in).

The price loader is injected, so nothing here touches IB, UW or Turso.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest
import quant_run
from quant.data import PriceMatrix, align_closes

MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0108_quant_strategies.sql"


# --------------------------------------------------------------------------- #
# alignment                                                                    #
# --------------------------------------------------------------------------- #
class TestAlignCloses:
    def test_union_of_dates_forward_fill_and_leading_nan(self):
        series = {
            "SPY": {"2026-01-05": 100.0, "2026-01-06": 101.0, "2026-01-08": 103.0},
            "TLT": {"2026-01-06": 90.0, "2026-01-07": 91.0, "2026-01-08": 92.0},
        }
        pm = align_closes(series, ["SPY", "TLT"])
        assert pm.dates == ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
        assert pm.symbols == ["SPY", "TLT"]
        spy, tlt = pm.closes[:, 0].tolist(), pm.closes[:, 1].tolist()
        assert spy == [100.0, 101.0, 101.0, 103.0]  # 01-07 forward-filled
        assert np.isnan(tlt[0]) and tlt[1:] == [90.0, 91.0, 92.0]

    def test_since_filter_and_missing_symbol_column(self):
        series = {"SPY": {"2025-12-31": 99.0, "2026-01-05": 100.0}}
        pm = align_closes(series, ["SPY", "GLD"], since="2026-01-01")
        assert pm.dates == ["2026-01-05"]
        assert np.isnan(pm.closes[0, 1])


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def _fake_prices(symbols, *, since=None, **_):
    rng = np.random.default_rng(11)
    n = 320
    import datetime as dt

    dates, day = [], dt.date(2025, 1, 6)
    while len(dates) < n:
        if day.weekday() < 5:
            dates.append(day.isoformat())
        day += dt.timedelta(days=1)
    closes = 100.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, (n, len(symbols))), axis=0))
    return PriceMatrix(dates=dates, symbols=list(symbols), closes=closes, sources={s: "test" for s in symbols})


class TestCli:
    def test_list_prints_registry(self, capsys):
        assert quant_run.main(["--list"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert {e["key"] for e in out} >= {"trend_inverse_vol", "inverse_vol"}

    def test_unknown_strategy_is_exit_2(self, capsys):
        assert quant_run.main(["--strategy", "nope"], load_prices=_fake_prices) == 2
        assert "unknown strategy" in capsys.readouterr().err

    def test_backtest_json_on_stdout_summary_on_stderr(self, capsys):
        rc = quant_run.main(
            ["--strategy", "inverse_vol", "--mode", "backtest", "--since", "2025-01-01", "--json"],
            load_prices=_fake_prices,
        )
        assert rc == 0
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        assert out["strategy"] == "inverse_vol" and out["mode"] == "backtest"
        assert out["n_sessions"] == 320 and out["n_rebalances"] >= 10
        assert set(out["metrics"]) >= {"sharpe", "max_drawdown", "annualized_return", "ann_vol"}
        assert out["since"] == "2025-01-06" and out["until"] == out["dates"][-1]
        assert "inverse_vol" in captured.err

    def test_targets_mode_scales_weights_by_sleeve_nav(self, capsys):
        rc = quant_run.main(
            ["--strategy", "inverse_vol", "--mode", "targets", "--sleeve-nav", "200000", "--json"],
            load_prices=_fake_prices,
        )
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["mode"] == "targets" and out["sleeve_nav"] == 200000.0
        assert out["as_of"] == out["dates"][-1]
        for row in out["targets"]:
            assert row["notional"] == pytest.approx(row["weight"] * 200000.0)
        assert sum(r["weight"] for r in out["targets"]) <= 1.0 + 1e-9
        assert "rebalance_today" in out

    def test_persist_writes_turso_rows_and_disk_fallback(self, tmp_path, monkeypatch, capsys):
        calls = {}

        def fake_run(strategy, run_at, summary, payload):
            calls["run"] = (strategy, run_at, summary, payload)

        def fake_targets(strategy, as_of, rows, *, rebalance_today=False, recorded_at=None):
            calls["targets"] = (strategy, as_of, rows, rebalance_today)

        def fake_health(state, **kw):
            calls.setdefault("health", []).append(("quant-targets", state))

        monkeypatch.setattr(quant_run, "_DATA_DIR", tmp_path)
        monkeypatch.setattr(quant_run, "_writer_funcs", lambda: (fake_run, fake_targets))
        monkeypatch.setattr(quant_run, "_heartbeat", fake_health)
        rc = quant_run.main(
            ["--strategy", "monthly_rotation", "--mode", "targets", "--sleeve-nav", "50000", "--persist"],
            load_prices=_fake_prices,
        )
        assert rc == 0
        strategy, as_of, rows, rebalance_today = calls["targets"]
        assert strategy == "monthly_rotation" and len(rows) == 8
        assert {r["symbol"] for r in rows} == set(quant_run.get_strategy("monthly_rotation").universe)
        assert all(isinstance(r["close"], float) for r in rows) and isinstance(rebalance_today, bool)
        assert "health" not in calls  # manual runs are on-demand: no heartbeat
        disk = json.loads((tmp_path / "quant" / "monthly_rotation_targets.json").read_text())
        assert disk["as_of"] == as_of

        rc = quant_run.main(
            ["--strategy", "monthly_rotation", "--mode", "backtest", "--persist"],
            load_prices=_fake_prices,
        )
        assert rc == 0
        strategy, run_at, summary, payload = calls["run"]
        assert strategy == "monthly_rotation" and summary["mode"] == "backtest"
        assert "equity" in payload and summary["n_sessions"] == 320


class TestScheduled:
    def _writers(self, monkeypatch, calls):
        def fake_run(strategy, run_at, summary, payload):
            calls.setdefault("runs", []).append(strategy)

        def fake_targets(strategy, as_of, rows, *, rebalance_today=False, recorded_at=None):
            calls.setdefault("targets", []).append((strategy, rows))

        def fake_health(state, **kw):
            calls.setdefault("health", []).append(("quant-targets", state, kw))

        monkeypatch.setattr(quant_run, "_writer_funcs", lambda: (fake_run, fake_targets))
        monkeypatch.setattr(quant_run, "_heartbeat", fake_health)

    def test_enabled_strategies_parse_and_drop_unknown_keys(self, capsys):
        env = {"RADON_QUANT_STRATEGIES": " inverse_vol, nope ,dual_momentum,"}
        assert quant_run.enabled_strategies(env) == ["inverse_vol", "dual_momentum"]
        assert "nope" in capsys.readouterr().err
        assert quant_run.enabled_strategies({}) == []
        assert quant_run.sleeve_pct({"RADON_QUANT_SLEEVE_PCT": "0.2"}) == 0.2
        assert quant_run.sleeve_pct({"RADON_QUANT_SLEEVE_PCT": "1.5"}) == 0.0
        assert quant_run.sleeve_nav_from_snapshot(0.2, 1_000_000.0) == 200_000.0
        assert quant_run.sleeve_nav_from_snapshot(0.0, 1_000_000.0) is None

    def test_scheduled_persists_each_strategy_with_sleeve_notional_and_one_heartbeat(self, tmp_path, monkeypatch):
        calls = {}
        self._writers(monkeypatch, calls)
        monkeypatch.setattr(quant_run, "_DATA_DIR", tmp_path)
        rc = quant_run.run_scheduled(
            heartbeat=quant_run._heartbeat,
            load_prices=_fake_prices,
            env={"RADON_QUANT_STRATEGIES": "inverse_vol,monthly_rotation", "RADON_QUANT_SLEEVE_PCT": "0.2"},
            net_liq=1_000_000.0,
        )
        assert rc == 0
        assert [t[0] for t in calls["targets"]] == ["inverse_vol", "monthly_rotation"]
        for row in calls["targets"][0][1]:
            assert row["notional"] == pytest.approx(row["weight"] * 200_000.0)
        assert [h[:2] for h in calls["health"]] == [("quant-targets", "ok")]
        assert (tmp_path / "quant" / "inverse_vol_targets.json").exists()

    def test_scheduled_with_nothing_enabled_heartbeats_ok_and_touches_nothing(self, monkeypatch):
        calls = {}
        self._writers(monkeypatch, calls)
        assert (
            quant_run.run_scheduled(heartbeat=quant_run._heartbeat, load_prices=_fake_prices, env={}, net_liq=None) == 0
        )
        assert "targets" not in calls and [h[:2] for h in calls["health"]] == [("quant-targets", "ok")]

    def test_scheduled_strategy_failure_heartbeats_error_and_continues(self, tmp_path, monkeypatch):
        calls = {}
        self._writers(monkeypatch, calls)
        monkeypatch.setattr(quant_run, "_DATA_DIR", tmp_path)

        def flaky(symbols, *, since=None, **_):
            if "QQQ" in symbols:  # the futures-proxy universe
                raise RuntimeError("IB unreachable")
            return _fake_prices(symbols, since=since)

        rc = quant_run.run_scheduled(
            heartbeat=quant_run._heartbeat,
            load_prices=flaky,
            env={"RADON_QUANT_STRATEGIES": "trend_inverse_vol,inverse_vol", "RADON_QUANT_SLEEVE_PCT": "0.1"},
            net_liq=500_000.0,
        )
        assert rc == 1
        assert [t[0] for t in calls["targets"]] == ["inverse_vol"]
        service, state, kw = calls["health"][0]
        assert (service, state) == ("quant-targets", "error") and "trend_inverse_vol" in kw["error"]["message"]

    def test_cli_scheduled_flag_dispatches(self, monkeypatch):
        calls = {}
        self._writers(monkeypatch, calls)
        monkeypatch.delenv("RADON_QUANT_STRATEGIES", raising=False)
        assert quant_run.main(["--scheduled"], load_prices=_fake_prices) == 0
        assert [h[:2] for h in calls["health"]] == [("quant-targets", "ok")]


# --------------------------------------------------------------------------- #
# Turso contract (sqlite3 stand-in for libsql)                                 #
# --------------------------------------------------------------------------- #
class TestStorage:
    def _db(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
        db.executescript(MIGRATION.read_text())
        db.executescript((MIGRATION.parent / "0109_quant_execution.sql").read_text())
        return db

    def test_migration_registers_version_108_and_tables(self):
        db = self._db()
        assert {r[0] for r in db.execute("SELECT version FROM schema_migrations")} == {108, 109}
        runs = {r[1] for r in db.execute("PRAGMA table_info(quant_runs)")}
        assert {
            "strategy",
            "run_at",
            "mode",
            "since",
            "until",
            "n_sessions",
            "n_rebalances",
            "ann_return",
            "ann_vol",
            "sharpe",
            "max_drawdown",
            "payload",
        } <= runs
        targets = {r[1] for r in db.execute("PRAGMA table_info(quant_targets)")}
        assert {"strategy", "as_of", "symbol", "weight", "notional", "recorded_at"} <= targets

    def test_upserts_are_idempotent(self):
        from db import writer

        db = self._db()
        args = ("inverse_vol", "2026-10-30", "SPY", 0.25, 25000.0, 500.0, 0, "2026-10-30T21:00:00Z")
        db.execute(writer.QUANT_TARGET_UPSERT_SQL, args)
        db.execute(writer.QUANT_TARGET_UPSERT_SQL, args[:3] + (0.30, 30000.0, 501.0, 1, "2026-10-30T21:05:00Z"))
        rows = db.execute("SELECT symbol, weight, notional, close, rebalance_today FROM quant_targets").fetchall()
        assert rows == [("SPY", 0.30, 30000.0, 501.0, 1)]

        run_args = (
            "inverse_vol",
            "2026-10-30T21:00:00Z",
            "backtest",
            "2007-01-03",
            "2026-10-30",
            4990,
            237,
            0.055,
            0.082,
            0.67,
            -0.204,
            "{}",
        )
        db.execute(writer.QUANT_RUN_UPSERT_SQL, run_args)
        db.execute(writer.QUANT_RUN_UPSERT_SQL, run_args[:9] + (0.70, -0.20, '{"a":1}'))
        rows = db.execute("SELECT sharpe, payload FROM quant_runs").fetchall()
        assert rows == [(0.70, '{"a":1}')]
