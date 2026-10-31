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

MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0106_quant_strategies.sql"


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

        def fake_targets(strategy, as_of, rows, recorded_at=None):
            calls["targets"] = (strategy, as_of, rows)

        def fake_health(service, state, **kw):
            calls.setdefault("health", []).append((service, state))

        monkeypatch.setattr(quant_run, "_DATA_DIR", tmp_path)
        monkeypatch.setattr(quant_run, "_writer_funcs", lambda: (fake_run, fake_targets, fake_health))
        rc = quant_run.main(
            ["--strategy", "monthly_rotation", "--mode", "targets", "--sleeve-nav", "50000", "--persist"],
            load_prices=_fake_prices,
        )
        assert rc == 0
        strategy, as_of, rows = calls["targets"]
        assert strategy == "monthly_rotation" and len(rows) == 8
        assert {r["symbol"] for r in rows} == set(quant_run.get_strategy("monthly_rotation").universe)
        assert calls["health"] == [("quant-monthly-rotation", "ok")]
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


# --------------------------------------------------------------------------- #
# Turso contract (sqlite3 stand-in for libsql)                                 #
# --------------------------------------------------------------------------- #
class TestStorage:
    def _db(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
        db.executescript(MIGRATION.read_text())
        return db

    def test_migration_registers_version_106_and_tables(self):
        db = self._db()
        assert db.execute("SELECT version FROM schema_migrations").fetchone()[0] == 106
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
        args = ("inverse_vol", "2026-10-30", "SPY", 0.25, 25000.0, "2026-10-30T21:00:00Z")
        db.execute(writer.QUANT_TARGET_UPSERT_SQL, args)
        db.execute(writer.QUANT_TARGET_UPSERT_SQL, args[:3] + (0.30, 30000.0, "2026-10-30T21:05:00Z"))
        rows = db.execute("SELECT symbol, weight, notional FROM quant_targets").fetchall()
        assert rows == [("SPY", 0.30, 30000.0)]

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
