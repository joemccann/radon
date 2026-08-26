"""Unit tests for the portfolio correlation risk-budget guard (F8).

Synthetic price series with known correlation structure drive every assertion.
No live IB/UW/Turso/network access — all inputs are in-memory dicts.
"""

import importlib
import json
import math
import re
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from portfolio_risk import (
    DEFAULT_CORR_THRESHOLD,
    DEFAULT_MIN_OVERLAP,
    build_correlation_matrix,
    build_risk_budget_report,
    position_underlying,
)


# ── Synthetic price-series helpers ───────────────────────────────────────────


def _series(start: float, steps, dates=None):
    """Build a {date: price} series from successive multiplicative steps."""
    prices = [start]
    for step in steps:
        prices.append(prices[-1] * (1.0 + step))
    if dates is None:
        dates = [f"2026-01-{i + 1:02d}" for i in range(len(prices))]
    return {d: round(p, 4) for d, p in zip(dates, prices)}


def _correlated_pair(returns_a, beta, noise):
    """series_b return = beta * series_a return + noise, both anchored at 100."""
    a = _series(100.0, returns_a)
    b_steps = [beta * r + n for r, n in zip(returns_a, noise)]
    b = _series(100.0, b_steps)
    return a, b


_RET_A = [0.02, -0.01, 0.03, -0.02, 0.015, -0.005, 0.025, -0.018, 0.012, 0.008]
_ZERO_NOISE = [0.0] * len(_RET_A)
_ANTI = [-r for r in _RET_A]


# ── position_underlying ──────────────────────────────────────────────────────


class TestPositionUnderlying:
    def test_stock_maps_to_ticker(self):
        assert position_underlying({"ticker": "AAPL", "structure_type": "Stock"}) == "AAPL"

    def test_option_maps_to_underlying_ticker(self):
        pos = {"ticker": "CRCL", "structure_type": "Long Call", "expiry": "2026-06-18"}
        assert position_underlying(pos) == "CRCL"

    def test_symbol_key_fallback(self):
        assert position_underlying({"symbol": "MSFT"}) == "MSFT"


# ── build_correlation_matrix ─────────────────────────────────────────────────


class TestCorrelationMatrix:
    def test_perfectly_correlated_series(self):
        a, b = _correlated_pair(_RET_A, beta=1.0, noise=_ZERO_NOISE)
        matrix = build_correlation_matrix(
            {"AAA": a, "BBB": b}, min_overlap=DEFAULT_MIN_OVERLAP
        )
        assert matrix["AAA"]["BBB"] == pytest.approx(1.0, abs=1e-6)
        assert matrix["AAA"]["AAA"] == pytest.approx(1.0, abs=1e-9)

    def test_anti_correlated_series(self):
        a = _series(100.0, _RET_A)
        b = _series(100.0, _ANTI)
        matrix = build_correlation_matrix(
            {"AAA": a, "INV": b}, min_overlap=DEFAULT_MIN_OVERLAP
        )
        # Inverted simple-return moves are not perfectly symmetric in log space,
        # so the correlation is very strongly negative but not exactly -1.0.
        assert matrix["AAA"]["INV"] < -0.99

    def test_insufficient_overlap_marked_none(self):
        a = _series(100.0, _RET_A)
        # Only three overlapping dates with the others — below min_overlap.
        short = {"2026-01-01": 10.0, "2026-01-02": 10.2, "2026-01-03": 10.1}
        matrix = build_correlation_matrix(
            {"AAA": a, "THIN": short}, min_overlap=DEFAULT_MIN_OVERLAP
        )
        assert matrix["AAA"]["THIN"] is None
        assert matrix["THIN"]["AAA"] is None


# ── build_risk_budget_report ─────────────────────────────────────────────────


def _portfolio(positions, bankroll=1_000_000.0):
    return {"bankroll": bankroll, "positions": positions}


class TestRiskBudgetReport:
    def test_correlated_stack_breaches_budget(self):
        """Two highly-correlated names each at the 2.5% cap stack to 5% > budget."""
        a, b = _correlated_pair(_RET_A, beta=1.0, noise=_ZERO_NOISE)
        bankroll = 1_000_000.0
        cap = bankroll * 0.025
        portfolio = _portfolio(
            [
                {"ticker": "AAA", "structure_type": "Stock", "market_value": cap},
                {"ticker": "BBB", "structure_type": "Stock", "market_value": cap},
            ],
            bankroll=bankroll,
        )
        report = build_risk_budget_report(
            portfolio,
            {"AAA": a, "BBB": b},
            corr_threshold=DEFAULT_CORR_THRESHOLD,
        )

        assert len(report["clusters"]) == 1
        cluster = report["clusters"][0]
        assert set(cluster["tickers"]) == {"AAA", "BBB"}
        # Aggregate exposure is the sum of the two weights (~5% of book).
        assert cluster["aggregate_exposure"] == pytest.approx(0.05, abs=1e-6)
        assert cluster["budget"] == pytest.approx(0.025, abs=1e-9)
        assert cluster["breached"] is True

        assert len(report["breaches"]) == 1
        assert set(report["breaches"][0]["tickers"]) == {"AAA", "BBB"}

    def test_uncorrelated_positions_do_not_cluster(self):
        a = _series(100.0, _RET_A)
        b = _series(100.0, _ANTI)  # anti-correlated → not a cluster
        bankroll = 1_000_000.0
        cap = bankroll * 0.025
        portfolio = _portfolio(
            [
                {"ticker": "AAA", "structure_type": "Stock", "market_value": cap},
                {"ticker": "INV", "structure_type": "Stock", "market_value": cap},
            ],
            bankroll=bankroll,
        )
        report = build_risk_budget_report(
            portfolio, {"AAA": a, "INV": b}, corr_threshold=DEFAULT_CORR_THRESHOLD
        )
        assert report["clusters"] == []
        assert report["breaches"] == []

    def test_correlated_but_under_budget_does_not_breach(self):
        a, b = _correlated_pair(_RET_A, beta=1.0, noise=_ZERO_NOISE)
        bankroll = 1_000_000.0
        small = bankroll * 0.005
        portfolio = _portfolio(
            [
                {"ticker": "AAA", "structure_type": "Stock", "market_value": small},
                {"ticker": "BBB", "structure_type": "Stock", "market_value": small},
            ],
            bankroll=bankroll,
        )
        report = build_risk_budget_report(
            portfolio, {"AAA": a, "BBB": b}, corr_threshold=DEFAULT_CORR_THRESHOLD
        )
        # They cluster (highly correlated) but 1% aggregate < 2.5% budget.
        assert len(report["clusters"]) == 1
        assert report["clusters"][0]["breached"] is False
        assert report["breaches"] == []

    def test_option_position_maps_to_underlying_series(self):
        """An option position correlates via its underlying's price history."""
        a, b = _correlated_pair(_RET_A, beta=1.0, noise=_ZERO_NOISE)
        bankroll = 1_000_000.0
        cap = bankroll * 0.025
        portfolio = _portfolio(
            [
                {
                    "ticker": "AAA",
                    "structure_type": "Long Call",
                    "expiry": "2026-06-18",
                    "market_value": cap,
                    "max_risk": cap,
                },
                {"ticker": "BBB", "structure_type": "Stock", "market_value": cap},
            ],
            bankroll=bankroll,
        )
        report = build_risk_budget_report(
            portfolio, {"AAA": a, "BBB": b}, corr_threshold=DEFAULT_CORR_THRESHOLD
        )
        assert len(report["clusters"]) == 1
        assert set(report["clusters"][0]["tickers"]) == {"AAA", "BBB"}
        assert report["clusters"][0]["breached"] is True

    def test_insufficient_data_path_surfaced(self):
        """A position with too-thin history is reported, not silently dropped."""
        a, b = _correlated_pair(_RET_A, beta=1.0, noise=_ZERO_NOISE)
        thin = {"2026-01-01": 10.0, "2026-01-02": 10.2}
        bankroll = 1_000_000.0
        cap = bankroll * 0.025
        portfolio = _portfolio(
            [
                {"ticker": "AAA", "structure_type": "Stock", "market_value": cap},
                {"ticker": "BBB", "structure_type": "Stock", "market_value": cap},
                {"ticker": "THIN", "structure_type": "Stock", "market_value": cap},
            ],
            bankroll=bankroll,
        )
        report = build_risk_budget_report(
            portfolio,
            {"AAA": a, "BBB": b, "THIN": thin},
            corr_threshold=DEFAULT_CORR_THRESHOLD,
        )
        assert "THIN" in report["insufficient_data"]
        # AAA/BBB still cluster and breach despite THIN being unmeasurable.
        assert len(report["clusters"]) == 1
        assert "THIN" not in report["clusters"][0]["tickers"]

    def test_aggregate_exposure_is_book_fraction(self):
        a, b = _correlated_pair(_RET_A, beta=1.0, noise=_ZERO_NOISE)
        bankroll = 2_000_000.0
        portfolio = _portfolio(
            [
                {"ticker": "AAA", "structure_type": "Stock", "market_value": 60_000.0},
                {"ticker": "BBB", "structure_type": "Stock", "market_value": 40_000.0},
            ],
            bankroll=bankroll,
        )
        report = build_risk_budget_report(
            portfolio, {"AAA": a, "BBB": b}, corr_threshold=DEFAULT_CORR_THRESHOLD
        )
        # (60k + 40k) / 2M = 0.05
        assert report["aggregate_exposure"] == pytest.approx(0.05, abs=1e-9)

    def test_empty_portfolio_is_graceful(self):
        report = build_risk_budget_report(_portfolio([]), {})
        assert report["clusters"] == []
        assert report["breaches"] == []
        assert report["aggregate_exposure"] == 0.0
        assert report["insufficient_data"] == []


# ── Turso price source (Gate-3 measurement input) ────────────────────────────


def _dated_closes(returns, start=100.0, first_day=None):
    """{date: close} over consecutive calendar days built from ``returns``.

    Anchored so the series ends today unless ``first_day`` says otherwise —
    the loader rejects series that are deep but stale.
    """
    origin = first_day or (date.today() - timedelta(days=len(returns)))
    prices = [start]
    for r in returns:
        prices.append(prices[-1] * (1.0 + r))
    return {
        (origin + timedelta(days=i)).isoformat(): round(p, 4)
        for i, p in enumerate(prices)
    }


_DEEP_RET = _RET_A * 4


@pytest.fixture
def price_db(monkeypatch, tmp_path):
    """In-memory sqlite carrying migration 0029's ``price_history_daily``.

    ``db.client.get_db`` is patched so the backfill's persistence leg writes
    into the same connection the reader sees. No network, no real Turso.
    """
    scripts_dir = Path(__file__).resolve().parent.parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    migration = (scripts_dir / "db" / "migrations" / "0029_rv_ratio.sql").read_text()
    stripped = "\n".join(re.sub(r"^\s*--.*$", "", line) for line in migration.splitlines())
    for stmt in [s.strip() for s in re.split(r";\s*$", stripped, flags=re.MULTILINE) if s.strip()]:
        conn.execute(stmt)
    conn.commit()

    import db.client as client_mod

    monkeypatch.setattr(client_mod, "_cached", conn, raising=False)
    monkeypatch.setattr(client_mod, "get_db", lambda: conn)

    import db.writer as writer_mod

    importlib.reload(writer_mod)

    import portfolio_risk

    monkeypatch.setattr(
        portfolio_risk, "_BACKFILL_MARKER_PATH", tmp_path / "backfill_attempts.json"
    )
    try:
        yield conn
    finally:
        conn.close()


def _store_closes(conn, symbol, closes, source="ib"):
    conn.executemany(
        "INSERT OR REPLACE INTO price_history_daily "
        "(symbol, date, close, source, fetched_at) VALUES (?, ?, ?, ?, ?)",
        [(symbol, d, c, source, "2026-08-25T00:00:00Z") for d, c in closes.items()],
    )
    conn.commit()


def _stub_fetchers(monkeypatch, sink, ib=None, uw=None, yahoo=None):
    """Replace the IB/UW/Yahoo rungs with recorders returning fixed series."""
    import portfolio_risk

    monkeypatch.setattr(portfolio_risk, "_ib_reachable", lambda: True)
    for name, payload in (
        ("_fetch_ib_closes", ib),
        ("_fetch_uw_closes", uw),
        ("_fetch_yahoo_closes", yahoo),
    ):
        rung = name.split("_")[2]

        def _stub(symbol, _rung=rung, _payload=payload):
            sink.append((_rung, symbol))
            return dict(_payload or {})

        monkeypatch.setattr(portfolio_risk, name, _stub)


def _one_stock_portfolio(ticker="AAA"):
    return _portfolio(
        [{"ticker": ticker, "structure_type": "Stock", "market_value": 20_000.0}],
        bankroll=1_000_000.0,
    )


def _two_stock_portfolio():
    bankroll = 1_000_000.0
    cap = bankroll * 0.02
    return _portfolio(
        [
            {"ticker": "AAA", "structure_type": "Stock", "market_value": cap},
            {"ticker": "BBB", "structure_type": "Stock", "market_value": cap},
        ],
        bankroll=bankroll,
    )


class TestTursoPriceSource:
    """Gate-3's measurement input comes from Turso, not an unmaintained cache."""

    def test_turso_history_makes_the_book_measurable(self, price_db, tmp_path):
        """The bug: an empty data/price_history_cache/ made every held symbol
        insufficient even when price_history_daily carried the closes."""
        import portfolio_risk

        _store_closes(price_db, "AAA", _dated_closes(_DEEP_RET))
        _store_closes(price_db, "BBB", _dated_closes(_DEEP_RET, start=50.0))

        series = portfolio_risk.load_price_series_for_portfolio(
            _two_stock_portfolio(),
            stocks_dir=tmp_path / "empty-cache",
            db=price_db,
            allow_backfill=False,
        )

        assert set(series) == {"AAA", "BBB"}
        assert len(series["AAA"]) >= portfolio_risk.MIN_CLOSES_TARGET

        report = build_risk_budget_report(_two_stock_portfolio(), series)
        assert report["insufficient_data"] == []
        assert len(report["clusters"]) == 1
        assert set(report["clusters"][0]["tickers"]) == {"AAA", "BBB"}

    def test_genuinely_missing_history_still_reports_insufficient(
        self, price_db, tmp_path, monkeypatch
    ):
        """No Turso rows and no source that can serve them: say so rather than
        invent a verdict."""
        import portfolio_risk

        _stub_fetchers(monkeypatch, [])

        series = portfolio_risk.load_price_series_for_portfolio(
            _two_stock_portfolio(), stocks_dir=tmp_path / "empty-cache", db=price_db
        )

        assert series == {}
        report = build_risk_budget_report(_two_stock_portfolio(), series)
        assert report["insufficient_data"] == ["AAA", "BBB"]
        assert report["clusters"] == []

    def test_backfill_prefers_ib_and_persists_to_turso(
        self, price_db, tmp_path, monkeypatch
    ):
        """IB first; the fetched series lands in Turso so the next run is a
        pure read."""
        import portfolio_risk

        calls = []
        _stub_fetchers(monkeypatch, calls, ib=_dated_closes(_DEEP_RET))

        series = portfolio_risk.load_price_series_for_portfolio(
            _two_stock_portfolio(), stocks_dir=tmp_path / "empty-cache", db=price_db
        )

        assert {rung for rung, _ in calls} == {"ib"}
        assert set(series) == {"AAA", "BBB"}
        stored = price_db.execute(
            "SELECT DISTINCT symbol, source FROM price_history_daily ORDER BY symbol"
        ).fetchall()
        assert stored == [("AAA", "ib"), ("BBB", "ib")]

    def test_backfill_falls_back_ib_then_uw_then_yahoo(
        self, price_db, tmp_path, monkeypatch
    ):
        """Yahoo is never the only source tried (mandatory rule 7)."""
        import portfolio_risk

        calls = []
        _stub_fetchers(monkeypatch, calls, yahoo=_dated_closes(_DEEP_RET))

        series = portfolio_risk.load_price_series_for_portfolio(
            _one_stock_portfolio(), stocks_dir=tmp_path / "empty-cache", db=price_db
        )

        assert [rung for rung, _ in calls] == ["ib", "uw", "yahoo"]
        assert set(series) == {"AAA"}
        assert price_db.execute(
            "SELECT DISTINCT source FROM price_history_daily"
        ).fetchall() == [("yahoo",)]

    def test_ib_rung_skipped_only_when_gateway_is_not_authenticated(self, monkeypatch):
        """Rule 7: the IB socket is skipped only on an explicit non-authenticated
        auth_state; an unreachable /health proceeds optimistically."""
        import portfolio_risk
        import utils.ib_preflight as preflight

        monkeypatch.setattr(preflight, "ib_auth_state", lambda *a, **k: "awaiting_2fa")
        assert portfolio_risk._ib_reachable() is False

        monkeypatch.setattr(preflight, "ib_auth_state", lambda *a, **k: None)
        assert portfolio_risk._ib_reachable() is True

    def test_backfill_is_throttled_across_runs(self, price_db, tmp_path, monkeypatch):
        """ib_sync calls this loader every minute during RTH — a symbol no
        source can serve must not fire the ladder on every call."""
        import portfolio_risk

        calls = []
        _stub_fetchers(monkeypatch, calls)

        for _ in range(3):
            portfolio_risk.load_price_series_for_portfolio(
                _two_stock_portfolio(), stocks_dir=tmp_path / "empty-cache", db=price_db
            )

        # Three rungs x two symbols, tried once — not once per call.
        assert len(calls) == 6
        assert sorted({symbol for _, symbol in calls}) == ["AAA", "BBB"]

    def test_stale_turso_series_is_refreshed(self, price_db, tmp_path, monkeypatch):
        """Deep but months-old closes are not a current correlation read."""
        import portfolio_risk

        # Inside the 180-day read window but ~2 months old: deep, not current.
        stale_start = date.today() - timedelta(days=100)
        _store_closes(price_db, "AAA", _dated_closes(_DEEP_RET, first_day=stale_start))

        calls = []
        _stub_fetchers(monkeypatch, calls, ib=_dated_closes(_DEEP_RET))

        portfolio_risk.load_price_series_for_portfolio(
            _one_stock_portfolio(), stocks_dir=tmp_path / "empty-cache", db=price_db
        )

        assert [rung for rung, _ in calls] == ["ib"]

    def test_disk_cache_remains_a_fallback(self, price_db, tmp_path, monkeypatch):
        """Turso empty and every source down: the legacy cache still counts."""
        import portfolio_risk

        _stub_fetchers(monkeypatch, [])

        stocks_dir = tmp_path / "stocks"
        stocks_dir.mkdir()
        (stocks_dir / "abc123.json").write_text(
            json.dumps(
                {
                    "key": "AAA|2026-01-01|2026-03-01|v1",
                    "fetched_at": "2026-03-01T00:00:00Z",
                    "data": _dated_closes(_DEEP_RET),
                }
            )
        )

        series = portfolio_risk.load_price_series_for_portfolio(
            _one_stock_portfolio(), stocks_dir=stocks_dir, db=price_db
        )

        assert set(series) == {"AAA"}
        assert len(series["AAA"]) >= portfolio_risk.MIN_CLOSES_TARGET


class TestPriceHistoryReader:
    def test_reader_returns_per_symbol_closes_bounded_by_since(self, price_db):
        from db.readers import read_price_history_closes

        _store_closes(price_db, "AAA", {"2026-01-01": 10.0, "2026-02-01": 11.0})
        _store_closes(price_db, "BBB", {"2026-02-01": 20.0})

        out = read_price_history_closes(
            ["AAA", "BBB", "CCC"], since="2026-01-15", db=price_db
        )

        assert out == {"AAA": {"2026-02-01": 11.0}, "BBB": {"2026-02-01": 20.0}}

    def test_reader_is_empty_for_no_symbols(self, price_db):
        from db.readers import read_price_history_closes

        assert read_price_history_closes([], db=price_db) == {}
