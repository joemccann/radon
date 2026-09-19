"""TradingView alert drain (docs/tradingview-integration.md, Phase 1).

Runs the drain's real SQL against an in-memory SQLite built from the
migration, with Turso, Pushover and service_health stubbed at the seam.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import tv_alerts_drain as drain  # noqa: E402

NOW = datetime(2026, 9, 19, 15, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


@pytest.fixture
def env(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.executescript((SCRIPTS / "db" / "migrations" / "0081_tv_alert_events.sql").read_text())
    pushes: list[dict] = []
    health: list[tuple] = []
    monkeypatch.setattr(drain, "_query", lambda sql, args=(): conn.execute(sql, args).fetchall())

    def _execute(sql, args=()):
        conn.execute(sql, args)
        conn.commit()

    monkeypatch.setattr(drain, "_execute", _execute)
    monkeypatch.setattr(drain, "_send_pushover", lambda payload: pushes.append(payload))
    monkeypatch.setattr(
        drain, "_record_health", lambda state, error=None: health.append((state, error))
    )
    monkeypatch.setenv("PUSHOVER_USER", "u")
    monkeypatch.setenv("PUSHOVER_TOKEN", "t")
    return conn, pushes, health


def _insert(conn, *, symbol, exchange="NASDAQ", received=NOW, bar_time="2026-09-19T14:30:00Z",
            price=100.0, alert="a", interval="15"):
    cur = conn.execute(
        "INSERT INTO tv_alert_events (received_at, raw_body, symbol, exchange, price, interval,"
        " alert_name, bar_time) VALUES (?, '{}', ?, ?, ?, ?, ?, ?)",
        (_iso(received), symbol, exchange, price, interval, alert, bar_time),
    )
    conn.commit()
    return cur.lastrowid


def test_n_rows_in_one_digest_out(env):
    conn, pushes, health = env
    for sym, n in (("NVDA", 3), ("SPX", 2), ("ES1!", 2)):
        for i in range(n):
            _insert(conn, symbol=sym, exchange="CME_MINI" if sym == "ES1!" else "NASDAQ",
                    bar_time=f"2026-09-19T14:{i:02d}:00Z")

    summary = drain.run(now=NOW)

    assert len(pushes) == 1
    assert pushes[0]["message"].startswith("7 TradingView alerts: NVDA x3, SPX x2, ES1! x2")
    assert pushes[0]["priority"] == 0
    assert summary["processed"] == 7
    stamped = conn.execute(
        "SELECT COUNT(*) FROM tv_alert_events WHERE processed_at IS NOT NULL AND digest_sent_at IS NOT NULL"
    ).fetchone()[0]
    assert stamped == 7
    assert health == [("ok", None)]


def test_no_new_rows_sends_nothing_but_heartbeats(env):
    conn, pushes, health = env
    drain.run(now=NOW)
    assert pushes == []
    assert health == [("ok", None)]


def test_second_cycle_does_not_resend(env):
    conn, pushes, _ = env
    _insert(conn, symbol="NVDA")
    drain.run(now=NOW)
    drain.run(now=NOW + timedelta(minutes=5))
    assert len(pushes) == 1


def test_ticker_resolution_and_unresolved_count(env):
    conn, pushes, _ = env
    a = _insert(conn, symbol="NASDAQ:AAPL", exchange=None)
    b = _insert(conn, symbol="ES1!", exchange="CME_MINI", bar_time="x2")
    c = _insert(conn, symbol="EURUSD/GBP", exchange="FX", bar_time="x3")
    d = _insert(conn, symbol=None, exchange=None, bar_time="x4")
    drain.run(now=NOW)
    tickers = dict(conn.execute("SELECT id, ticker FROM tv_alert_events").fetchall())
    assert tickers[a] == "AAPL"
    assert tickers[b] == "ES"
    assert tickers[c] is None
    assert tickers[d] is None
    assert "2 unresolved" in pushes[0]["message"]


@pytest.mark.parametrize(
    "symbol, exchange, expected",
    [
        ("NVDA", "NASDAQ", "NVDA"),
        ("BRK.B", "NYSE", "BRK.B"),
        ("SPX", "TVC", "SPX"),
        ("CME_MINI:NQ1!", None, "NQ"),
        ("ES1!", "CME_MINI", "ES"),
        ("BTCUSDT", "BINANCE", None),
        ("", None, None),
        (None, None, None),
    ],
)
def test_resolve_ticker(symbol, exchange, expected):
    assert drain.resolve_ticker(symbol, exchange) == expected


def test_exact_repeat_within_5s_is_marked_duplicate(env):
    conn, pushes, _ = env
    first = _insert(conn, symbol="NVDA", received=NOW)
    echo = _insert(conn, symbol="NVDA", received=NOW + timedelta(seconds=3))
    later = _insert(conn, symbol="NVDA", received=NOW + timedelta(seconds=9))
    drain.run(now=NOW + timedelta(minutes=1))
    dup = dict(conn.execute("SELECT id, duplicate_of FROM tv_alert_events").fetchall())
    assert dup[first] is None
    assert dup[echo] == first
    assert dup[later] is None
    assert pushes[0]["message"].startswith("2 TradingView alerts: NVDA x2")


def test_prune_at_180_day_boundary(env):
    conn, _, _ = env
    old = _insert(conn, symbol="OLD", received=NOW - timedelta(days=180, seconds=1))
    edge = _insert(conn, symbol="EDGE", received=NOW - timedelta(days=180) + timedelta(seconds=1))
    drain.run(now=NOW)
    ids = {r[0] for r in conn.execute("SELECT id FROM tv_alert_events")}
    assert old not in ids
    assert edge in ids


def test_reads_on_an_id_cursor(env, monkeypatch):
    conn, pushes, _ = env
    monkeypatch.setattr(drain, "PAGE_SIZE", 2)
    for i in range(5):
        _insert(conn, symbol="NVDA", bar_time=f"b{i}")
    summary = drain.run(now=NOW)
    assert summary["processed"] == 5
    assert len(pushes) == 1


def test_db_failure_records_error_health_and_exits_nonzero(env, monkeypatch):
    _, pushes, health = env

    def boom(sql, args=()):
        raise RuntimeError("hrana down")

    monkeypatch.setattr(drain, "_query", boom)
    assert drain.main([]) == 1
    assert pushes == []
    assert health and health[-1][0] == "error"


def test_missing_pushover_creds_does_not_stamp_digest(env, monkeypatch):
    conn, pushes, health = env
    monkeypatch.delenv("PUSHOVER_USER")
    _insert(conn, symbol="NVDA")
    drain.run(now=NOW)
    row = conn.execute("SELECT processed_at, digest_sent_at FROM tv_alert_events").fetchone()
    assert row[0] is not None
    assert row[1] is None
    assert pushes == []
    assert health[-1][0] == "error"
