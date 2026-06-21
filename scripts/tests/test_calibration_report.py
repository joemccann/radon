"""SECTION B — calibration report harness coverage (no torch / chronos).

build_calibration_report is exercised with monkeypatched flow_series_for and
run_backtest so the heavy forecasting deps never load. The writer test runs the
real upsert against in-memory sqlite (migrations 0001 + 0016).
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path
from typing import Iterator

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_MIGRATIONS = [
    _SCRIPTS_DIR / "db" / "migrations" / "0001_init.sql",
    _SCRIPTS_DIR / "db" / "migrations" / "0016_forecast_calibration.sql",
]


def _split_statements(sql: str) -> list[str]:
    stripped = "\n".join(re.sub(r"^\s*--.*$", "", line) for line in sql.splitlines())
    return [s.strip() for s in re.split(r";\s*$", stripped, flags=re.MULTILINE) if s.strip()]


# --- fixtures ---------------------------------------------------------------

# series long enough to pass the MIN_CONTEXT_LEN + 2 gate (MIN_CONTEXT_LEN=8)
LONG_SERIES = [float(i) for i in range(20)]
SHORT_SERIES = [1.0, 2.0, 3.0]

CHRONOS_WINS = {
    "series_len": 20,
    "baseline": {
        "mean_pinball": 0.5,
        "calibration": [
            {"level": 0.1, "empirical": 0.2, "calibration_error": 0.1},
            {"level": 0.9, "empirical": 0.6, "calibration_error": 0.3},
        ],
    },
    "chronos": {"mean_pinball": 0.2},
    "verdict": "chronos beats baseline by 60.0%",
}

ENGINE_UNAVAILABLE = {
    "series_len": 20,
    "baseline": {
        "mean_pinball": 0.4,
        "calibration": [
            {"level": 0.5, "empirical": 0.45, "calibration_error": 0.05},
        ],
    },
    "chronos": {"status": "engine_unavailable"},
    "verdict": "chronos engine unavailable; baseline only",
}


@pytest.fixture
def patched_flow(monkeypatch: pytest.MonkeyPatch):
    """Per-ticker flow series: WIN/UNAVAIL get the long series, SHORT gets a
    too-short one (triggers insufficient_history)."""
    from forecasting import flow_history

    series_by_ticker = {
        "WIN": LONG_SERIES,
        "UNAVAIL": LONG_SERIES,
        "SHORT": SHORT_SERIES,
    }

    def fake_series(ticker, *, metric="flow_strength", lookback_days=250):
        values = series_by_ticker[ticker.upper()]
        dates = [f"2026-06-{i + 1:02d}" for i in range(len(values))]
        return dates, list(values)

    monkeypatch.setattr(flow_history, "flow_series_for", fake_series)
    return series_by_ticker


@pytest.fixture
def patched_backtest(monkeypatch: pytest.MonkeyPatch):
    from forecasting import backtest

    def fake_run_backtest(series, *, horizon=1, **kwargs):
        # length distinguishes which canned result to return
        if len(series) >= 20:
            # disambiguate WIN vs UNAVAIL by a marker we can't see here; the
            # report builder runs per-ticker, so we key on a module-level dict
            return fake_run_backtest.next_result.pop(0)
        return {"series_len": len(series)}

    fake_run_backtest.next_result = []
    monkeypatch.setattr(backtest, "run_backtest", fake_run_backtest)
    return fake_run_backtest


# --- aggregate counts -------------------------------------------------------

def test_aggregate_counts_and_engine_selection(patched_flow, patched_backtest):
    from forecasting import calibration_report

    # WIN then UNAVAIL consume the long-series results in order; SHORT is short.
    patched_backtest.next_result = [CHRONOS_WINS, ENGINE_UNAVAILABLE]

    out = calibration_report.build_calibration_report(
        tickers=["WIN", "UNAVAIL", "SHORT"],
        metric="flow_strength",
        persist=False,
    )

    assert out["n_ok"] == 2
    assert out["n_insufficient"] == 1
    assert out["chronos_available"] is True
    assert out["chronos_beats_baseline"] == 1
    assert out["metric"] == "flow_strength"
    assert "scan_time" in out

    rows = {r["ticker"]: r for r in out["tickers"]}

    assert rows["WIN"]["status"] == "ok"
    assert rows["WIN"]["engine"] == "chronos"
    assert rows["WIN"]["mean_pinball_chronos"] == 0.2
    assert rows["WIN"]["mean_pinball_baseline"] == 0.5
    assert rows["WIN"]["verdict"] == "chronos beats baseline by 60.0%"
    assert rows["WIN"]["max_calibration_error"] == 0.3

    assert rows["UNAVAIL"]["status"] == "ok"
    assert rows["UNAVAIL"]["engine"] == "baseline"
    assert rows["UNAVAIL"]["mean_pinball_chronos"] is None
    assert rows["UNAVAIL"]["mean_pinball_baseline"] == 0.4

    assert rows["SHORT"]["status"] == "insufficient_history"


def test_chronos_available_false_when_none_have_chronos(patched_flow, patched_backtest):
    from forecasting import calibration_report

    patched_backtest.next_result = [ENGINE_UNAVAILABLE, ENGINE_UNAVAILABLE]

    out = calibration_report.build_calibration_report(
        tickers=["WIN", "UNAVAIL"],
        persist=False,
    )
    assert out["chronos_available"] is False
    assert out["chronos_beats_baseline"] == 0


# --- persistence ------------------------------------------------------------

def test_persist_true_calls_recorder(patched_flow, patched_backtest, monkeypatch):
    from db import writer
    from forecasting import calibration_report

    patched_backtest.next_result = [CHRONOS_WINS]

    recorded = []

    def recorder(ticker, metric, scan_time, **kwargs):
        recorded.append((ticker, metric, scan_time, kwargs))

    monkeypatch.setattr(writer, "upsert_forecast_calibration", recorder)

    calibration_report.build_calibration_report(
        tickers=["WIN"], persist=True
    )

    assert len(recorded) == 1
    ticker, metric, _scan_time, kwargs = recorded[0]
    assert ticker == "WIN"
    assert metric == "flow_strength"
    assert kwargs["payload"] == CHRONOS_WINS
    assert kwargs["engine"] == "chronos"
    assert kwargs["verdict"] == "chronos beats baseline by 60.0%"


def test_persist_false_does_not_call_recorder(patched_flow, patched_backtest, monkeypatch):
    from db import writer
    from forecasting import calibration_report

    patched_backtest.next_result = [CHRONOS_WINS]

    recorded = []
    monkeypatch.setattr(
        writer,
        "upsert_forecast_calibration",
        lambda *a, **k: recorded.append((a, k)),
    )

    calibration_report.build_calibration_report(tickers=["WIN"], persist=False)
    assert recorded == []


# --- writer + in-memory sqlite ---------------------------------------------

@pytest.fixture
def db_conn(monkeypatch: pytest.MonkeyPatch) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(":memory:")
    for migration in _MIGRATIONS:
        sql = migration.read_text(encoding="utf-8")
        for stmt in _split_statements(sql):
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as exc:
                if "duplicate column" in str(exc):
                    continue
                raise
    conn.commit()

    import db.client as client_mod
    import db.writer as writer_mod

    monkeypatch.setattr(client_mod, "_cached", conn, raising=False)
    monkeypatch.setattr(client_mod, "get_db", lambda: conn)
    monkeypatch.setattr(writer_mod, "get_db", lambda: conn)

    try:
        yield conn
    finally:
        conn.close()


def test_upsert_and_read_forecast_calibration(db_conn: sqlite3.Connection) -> None:
    from db.writer import upsert_forecast_calibration

    payload = {"series_len": 20, "verdict": "chronos beats baseline"}
    upsert_forecast_calibration(
        "spy",
        "flow_strength",
        "2026-06-20T12:00:00Z",
        engine="chronos",
        series_len=20,
        mean_pinball_chronos=0.2,
        mean_pinball_baseline=0.5,
        verdict="chronos beats baseline",
        payload=payload,
    )

    rows = db_conn.execute(
        "SELECT ticker, metric, scan_time, engine, series_len, "
        "mean_pinball_chronos, mean_pinball_baseline, verdict, payload "
        "FROM forecast_calibration"
    ).fetchall()
    assert len(rows) == 1
    (
        ticker, metric, scan_time, engine, series_len,
        mp_chronos, mp_baseline, verdict, stored_payload,
    ) = rows[0]
    assert ticker == "SPY"
    assert metric == "flow_strength"
    assert scan_time == "2026-06-20T12:00:00Z"
    assert engine == "chronos"
    assert series_len == 20
    assert mp_chronos == 0.2
    assert mp_baseline == 0.5
    assert verdict == "chronos beats baseline"

    import json

    assert json.loads(stored_payload) == payload


def test_upsert_forecast_calibration_replaces_same_key(db_conn: sqlite3.Connection) -> None:
    from db.writer import upsert_forecast_calibration

    upsert_forecast_calibration(
        "SPY", "flow_strength", "2026-06-20T12:00:00Z", payload={"v": 1}
    )
    upsert_forecast_calibration(
        "SPY", "flow_strength", "2026-06-20T12:00:00Z", payload={"v": 2}
    )

    rows = db_conn.execute("SELECT payload FROM forecast_calibration").fetchall()
    assert len(rows) == 1

    import json

    assert json.loads(rows[0][0]) == {"v": 2}
