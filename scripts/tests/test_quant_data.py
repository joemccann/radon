"""Quant sleeve price loader: backfill policy and IB>RH>UW>Yahoo ladder.

align_closes is covered in test_quant_run.py. The scheduled path injects a
fake loader, so a Yahoo-first swap in quant.data would not fail those tests.
Nothing here opens IB, UW, Robinhood, or Turso.
"""

from __future__ import annotations

from datetime import date, timedelta

from quant import data as qd


TODAY = date(2026, 11, 10)
BARS = {"2026-01-02": 100.0, "2026-01-05": 101.0}


def _weekdays(n: int, last: date, value: float = 100.0) -> dict[str, float]:
    out: dict[str, float] = {}
    day = last
    while len(out) < n:
        if day.weekday() < 5:
            out[day.isoformat()] = value
        day -= timedelta(days=1)
    return out


def _wire(monkeypatch, *, ib=None, rh=None, uw=None, yahoo=None, persist=None):
    """Stub every network and Turso seam. Order of calls is the proof."""
    calls: list[str] = []
    persisted: list[tuple[str, list]] = []

    monkeypatch.setattr(
        qd,
        "_fetch_ib_closes_batch",
        lambda symbols, duration: calls.append(("ib", list(symbols), duration)) or dict(ib or {}),
    )

    def rung(tag, payload):
        def fetch(symbol):
            calls.append((tag, symbol))
            return dict(payload) if payload else {}

        return fetch

    import db.writer as writer
    import portfolio_risk

    monkeypatch.setattr(portfolio_risk, "_fetch_rh_closes", rung("rh", rh))
    monkeypatch.setattr(portfolio_risk, "_fetch_uw_closes", rung("uw", uw))
    monkeypatch.setattr(portfolio_risk, "_fetch_yahoo_closes", rung("yahoo", yahoo))
    monkeypatch.setattr(writer, "ensure_no_replica_for_writers", lambda: None)
    monkeypatch.setattr(
        writer,
        "upsert_price_history_rows",
        persist
        or (lambda symbol, rows: persisted.append((symbol, rows))),
    )
    return calls, persisted


class TestNeedsBackfill:
    def test_empty_and_thin_series_need_a_ladder_pass(self):
        assert qd._needs_backfill(None, TODAY) is True
        assert qd._needs_backfill({}, TODAY) is True
        assert qd._needs_backfill(_weekdays(qd.MIN_SESSIONS - 1, TODAY), TODAY) is True

    def test_full_fresh_series_is_left_alone(self):
        assert qd._needs_backfill(_weekdays(qd.MIN_SESSIONS, TODAY), TODAY) is False

    def test_ten_day_old_full_series_is_still_fresh_eleven_is_stale(self):
        ten_ago = TODAY - timedelta(days=qd.STALE_DAYS)
        eleven_ago = TODAY - timedelta(days=qd.STALE_DAYS + 1)
        ten_series = _weekdays(qd.MIN_SESSIONS - 1, ten_ago - timedelta(days=1))
        ten_series[ten_ago.isoformat()] = 100.0
        eleven_series = _weekdays(qd.MIN_SESSIONS - 1, eleven_ago - timedelta(days=1))
        eleven_series[eleven_ago.isoformat()] = 100.0
        assert qd._needs_backfill(ten_series, TODAY) is False
        assert qd._needs_backfill(eleven_series, TODAY) is True


class TestIbDuration:
    def test_clamps_between_one_and_twenty_years(self):
        assert qd._ib_duration("2026-11-10", TODAY) == "1 Y"
        assert qd._ib_duration("2026-01-01", TODAY) == "2 Y"
        assert qd._ib_duration("2000-01-01", TODAY) == "20 Y"
        assert qd._ib_duration("2007-01-03", TODAY) == "20 Y"


class TestBackfillLadder:
    def test_ib_hit_never_calls_later_rungs_and_persists_ib_rows(self, monkeypatch):
        calls, persisted = _wire(monkeypatch, ib={"SPY": BARS}, rh=BARS, uw=BARS, yahoo=BARS)
        out = qd.backfill_closes(["SPY"], since="2007-01-03", today=TODAY)
        assert out == {"SPY": BARS}
        assert calls == [("ib", ["SPY"], "20 Y")]
        assert persisted == [
            ("SPY", [{"date": "2026-01-02", "close": 100.0, "source": "ib"}, {"date": "2026-01-05", "close": 101.0, "source": "ib"}])
        ]

    def test_empty_ib_walks_rh_then_uw_then_yahoo(self, monkeypatch):
        calls, _persisted = _wire(monkeypatch, yahoo=BARS)
        out = qd.backfill_closes(["SPY"], since="2026-01-01", today=TODAY)
        assert out == {"SPY": BARS}
        assert calls == [
            ("ib", ["SPY"], "2 Y"),
            ("rh", "SPY"),
            ("uw", "SPY"),
            ("yahoo", "SPY"),
        ]

    def test_rh_hit_skips_uw_and_yahoo(self, monkeypatch):
        calls, persisted = _wire(monkeypatch, rh=BARS, uw=BARS, yahoo=BARS)
        out = qd.backfill_closes(["QQQ"], since="2026-01-01", today=TODAY)
        assert out == {"QQQ": BARS}
        assert calls == [("ib", ["QQQ"], "2 Y"), ("rh", "QQQ")]
        assert persisted[0][1][0]["source"] == "rh"

    def test_uw_hit_skips_yahoo(self, monkeypatch):
        calls, persisted = _wire(monkeypatch, uw=BARS, yahoo=BARS)
        out = qd.backfill_closes(["TLT"], since="2026-01-01", today=TODAY)
        assert out == {"TLT": BARS}
        assert calls == [("ib", ["TLT"], "2 Y"), ("rh", "TLT"), ("uw", "TLT")]
        assert persisted[0][1][0]["source"] == "uw"

    def test_ib_partial_batch_falls_through_only_the_missing_symbol(self, monkeypatch):
        calls, persisted = _wire(monkeypatch, ib={"SPY": BARS}, rh=BARS)
        out = qd.backfill_closes(["SPY", "GLD"], since="2026-01-01", today=TODAY)
        assert out == {"SPY": BARS, "GLD": BARS}
        assert calls[0] == ("ib", ["SPY", "GLD"], "2 Y")
        assert ("rh", "SPY") not in calls
        assert ("rh", "GLD") in calls
        sources = {symbol: rows[0]["source"] for symbol, rows in persisted}
        assert sources == {"SPY": "ib", "GLD": "rh"}

    def test_persist_failure_still_returns_the_fetched_closes(self, monkeypatch):
        def boom(symbol, rows):
            raise RuntimeError("turso down")

        calls, _ = _wire(monkeypatch, ib={"SPY": BARS}, persist=boom)
        out = qd.backfill_closes(["SPY"], since="2026-01-01", today=TODAY)
        assert out == {"SPY": BARS}
        assert calls == [("ib", ["SPY"], "2 Y")]


class TestLoadPriceMatrix:
    def test_fresh_turso_rows_do_not_touch_the_ladder(self, monkeypatch):
        fresh = _weekdays(qd.MIN_SESSIONS, TODAY)
        monkeypatch.setattr(
            "db.readers.read_price_history_closes",
            lambda symbols, since=None, db=None: {"SPY": fresh},
        )
        called = []
        monkeypatch.setattr(qd, "backfill_closes", lambda *a, **k: called.append((a, k)) or {})
        matrix = qd.load_price_matrix(["SPY"], since="2025-01-01", today=TODAY, allow_backfill=True)
        assert called == []
        assert matrix.sources == {"SPY": "turso"}
        assert matrix.n_sessions == qd.MIN_SESSIONS

    def test_thin_turso_rows_backfill_and_merge(self, monkeypatch):
        thin = {"2026-11-10": 99.0}
        monkeypatch.setattr(
            "db.readers.read_price_history_closes",
            lambda symbols, since=None, db=None: {"SPY": dict(thin)},
        )
        monkeypatch.setattr(
            qd,
            "backfill_closes",
            lambda symbols, *, since, today: {"SPY": BARS} if symbols == ["SPY"] else {},
        )
        matrix = qd.load_price_matrix(["SPY"], since="2026-01-01", today=TODAY, allow_backfill=True)
        assert matrix.sources == {"SPY": "ladder"}
        assert matrix.dates == ["2026-01-02", "2026-01-05", "2026-11-10"]
        assert matrix.closes[:, 0].tolist() == [100.0, 101.0, 99.0]

    def test_allow_backfill_false_keeps_a_thin_column(self, monkeypatch):
        monkeypatch.setattr(
            "db.readers.read_price_history_closes",
            lambda symbols, since=None, db=None: {"SPY": {"2026-11-10": 99.0}},
        )
        called = []
        monkeypatch.setattr(qd, "backfill_closes", lambda *a, **k: called.append(1) or {})
        matrix = qd.load_price_matrix(["SPY"], since="2026-01-01", today=TODAY, allow_backfill=False)
        assert called == []
        assert matrix.sources == {"SPY": "turso"}
        assert matrix.closes[0, 0] == 99.0
