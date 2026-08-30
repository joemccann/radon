"""DISPERSION indicator — VIX vs single-stock vs cross-sector dispersion tests.

Ground truth is the checked-in IB capture
`fixtures/dispersion_ib_bars_sample.json` (13 symbols, `6 M` daily bars ending
2026-08-28; XOM carries three extra February sessions). Every expected number
is computed FROM that fixture inside the test (numpy / statistics), never typed
by hand.

Contract these tests pin (the implementer builds to it):

  lib.dispersion_math
    master_sessions(closes) -> list[str]                       sorted VIX dates
    daily_returns(closes, sessions) -> {date: {symbol: r}}     date-keyed, no ffill
    cross_sectional_spread(values) -> float                    p95 - p5, linear interp
    build_raw_rows(closes, stock_symbols, sector_symbols)      raw per-session rows
    rolling_mean(values, window) -> [float | None]             full window only
    zscore_series(rows, base_start) -> [dict]                  UNROUNDED floats
    classify_regime(z_vix, z_stock, z_sector) -> str
    surface_gap(z_vix, z_stock, z_sector) -> float
    compute_stats(series) -> dict
    ensure_plausible_rows(rows, *, backfill) -> None           raises ValueError
    build_payload(rows, *, scan_time, status, source, universe, fetch) -> dict

  WINDOW / ZSCORE_BASE_START / MIN_STOCKS / MIN_SECTORS / MIN_SERIES_ROWS are
  read from the module at CALL time (tests monkeypatch them), never bound as
  default arguments.

  fetch_dispersion
    run(*, backfill=False, fetch_closes=None, now=None, universe=None) -> dict
    _write_db(payload, new_rows, scan_time, *, rows_changed) -> None

Spec: docs/indicators/dispersion.md (§C, §E, §F, §M).
"""
from __future__ import annotations

import json
import sqlite3
import statistics
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

import fetch_dispersion
from lib import dispersion_math
from lib.dispersion_math import (
    COMPRESSED_Z,
    MIN_SECTORS,
    MIN_SERIES_ROWS,
    MIN_STOCKS,
    SECTOR_ETFS,
    SECTOR_SPREAD_MAX,
    SECTOR_SPREAD_MIN,
    STOCK_SPREAD_MAX,
    STOCK_SPREAD_MIN,
    STRESS_Z,
    VIX_MAX,
    VIX_MIN,
    VIX_SYMBOL,
    WINDOW,
    ZSCORE_BASE_START,
    build_payload,
    build_raw_rows,
    classify_regime,
    compute_stats,
    cross_sectional_spread,
    daily_returns,
    ensure_plausible_rows,
    master_sessions,
    rolling_mean,
    surface_gap,
    zscore_series,
)

# ONE anchor for every window-relative date (feedback_import_time_vs_call_time_test_dates).
_TODAY = date.today()

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = json.loads((FIXTURES / "dispersion_ib_bars_sample.json").read_text())
MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0061_dispersion.sql"

FIXTURE_STOCKS = ["AAPL", "MSFT", "NVDA", "JPM", "XOM", "UNH", "PG"]
FIXTURE_SECTORS = ["XLK", "XLF", "XLE", "XLV", "XLC"]
FIXTURE_UNIVERSE = {"stocks": FIXTURE_STOCKS, "sectors": FIXTURE_SECTORS}

# The fixture's own floors: 7 stocks / 5 sectors, so the production 300 / 9
# floors are lowered to 5 / 3 wherever a cross-section is built.
FIXTURE_MIN_STOCKS = 5
FIXTURE_MIN_SECTORS = 3


def _closes() -> dict[str, dict[str, float]]:
    return {
        symbol: {bar["date"]: float(bar["close"]) for bar in bars}
        for symbol, bars in FIXTURE["symbols"].items()
    }


CLOSES = _closes()
VIX_DATES = sorted(CLOSES[VIX_SYMBOL])


def _shift_date(iso: str, days: int) -> str:
    return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()


def _shift_closes(closes: dict[str, dict[str, float]], days: int) -> dict[str, dict[str, float]]:
    return {
        symbol: {_shift_date(d, days): c for d, c in series.items()}
        for symbol, series in closes.items()
    }


def _shift_rows(rows: list[dict], days: int) -> list[dict]:
    return [{**row, "date": _shift_date(row["date"], days)} for row in rows]


def _fixture_returns_on(session: str, symbols: list[str]) -> list[float]:
    """r_i(session) for the given symbols, straight from the fixture closes."""
    prev = VIX_DATES[VIX_DATES.index(session) - 1]
    return [CLOSES[s][session] / CLOSES[s][prev] - 1 for s in symbols]


def _numpy_spread(values: list[float]) -> float:
    return float(np.percentile(values, 95) - np.percentile(values, 5))


@pytest.fixture
def fixture_floors(monkeypatch):
    monkeypatch.setattr(dispersion_math, "MIN_STOCKS", FIXTURE_MIN_STOCKS)
    monkeypatch.setattr(dispersion_math, "MIN_SECTORS", FIXTURE_MIN_SECTORS)
    monkeypatch.setattr(fetch_dispersion, "MIN_STOCKS", FIXTURE_MIN_STOCKS, raising=False)
    monkeypatch.setattr(fetch_dispersion, "MIN_SECTORS", FIXTURE_MIN_SECTORS, raising=False)


def _raw_rows() -> list[dict]:
    return build_raw_rows(CLOSES, FIXTURE_STOCKS, FIXTURE_SECTORS)


# ── constants ─────────────────────────────────────────────────────


class TestConstants:
    def test_window_and_base(self):
        assert WINDOW == 60
        assert ZSCORE_BASE_START == "2017-01-01"

    def test_cross_section_floors(self):
        assert MIN_STOCKS == 300
        assert MIN_SECTORS == 9
        assert MIN_SERIES_ROWS == 400

    def test_universe_symbols(self):
        assert SECTOR_ETFS == ("XLK", "XLF", "XLV", "XLY", "XLP", "XLE", "XLI", "XLB", "XLU", "XLRE", "XLC")
        assert len(SECTOR_ETFS) == 11
        assert VIX_SYMBOL == "VIX"

    def test_regime_edges(self):
        assert STRESS_Z == 1.0
        assert COMPRESSED_Z == -1.0

    def test_plausibility_bands(self):
        assert (STOCK_SPREAD_MIN, STOCK_SPREAD_MAX) == (0.005, 0.60)
        assert (SECTOR_SPREAD_MIN, SECTOR_SPREAD_MAX) == (0.001, 0.30)
        assert (VIX_MIN, VIX_MAX) == (5.0, 100.0)


# ── master calendar ───────────────────────────────────────────────


class TestMasterSessions:
    def test_master_calendar_is_the_sorted_vix_dates(self):
        sessions = master_sessions(CLOSES)
        assert sessions == VIX_DATES
        assert len(sessions) == 126
        assert sessions[0] == "2026-03-02"
        assert sessions[-1] == "2026-08-28"

    def test_xom_february_bars_are_not_sessions(self):
        xom_only = sorted(set(CLOSES["XOM"]) - set(CLOSES[VIX_SYMBOL]))
        assert len(xom_only) == 3
        assert all(d < "2026-03-02" for d in xom_only)
        assert not set(xom_only) & set(master_sessions(CLOSES))


# ── daily returns ─────────────────────────────────────────────────


class TestDailyReturns:
    def test_aapl_last_session_return_is_close_over_master_previous(self):
        returns = daily_returns(CLOSES, VIX_DATES)
        prev = VIX_DATES[-2]
        expected = 319.7 / CLOSES["AAPL"][prev] - 1
        assert CLOSES["AAPL"]["2026-08-28"] == 319.7
        assert returns["2026-08-28"]["AAPL"] == pytest.approx(expected, abs=1e-12)

    def test_first_session_has_no_master_previous(self):
        returns = daily_returns(CLOSES, VIX_DATES)
        assert returns.get(VIX_DATES[0], {}) == {}

    def test_xom_february_bars_emit_nothing(self):
        returns = daily_returns(CLOSES, VIX_DATES)
        february = [d for d in CLOSES["XOM"] if d < VIX_DATES[0]]
        assert len(february) == 3
        assert not any(d in returns and returns[d] for d in february)
        # XOM's first master session still has no return: its master-previous
        # session (2026-02-27) is not a fixture bar for XOM either way.
        assert "XOM" in returns[VIX_DATES[1]]

    def test_missing_close_drops_both_adjacent_returns_no_forward_fill(self):
        closes = _closes()
        gap = VIX_DATES[-2]
        del closes["AAPL"][gap]
        returns = daily_returns(closes, VIX_DATES)
        assert "AAPL" not in returns[gap]
        assert "AAPL" not in returns[VIX_DATES[-1]]
        assert "MSFT" in returns[gap]
        assert "MSFT" in returns[VIX_DATES[-1]]
        assert "AAPL" in returns[VIX_DATES[-3]]

    def test_returns_are_keyed_by_master_session_only(self):
        returns = daily_returns(CLOSES, VIX_DATES)
        assert set(returns) <= set(VIX_DATES)


# ── cross-sectional spread ────────────────────────────────────────


class TestCrossSectionalSpread:
    def test_equals_numpy_percentile_95_minus_5_on_fixture_returns(self):
        vals = _fixture_returns_on("2026-08-28", FIXTURE_STOCKS)
        assert len(vals) == 7
        assert cross_sectional_spread(vals) == pytest.approx(_numpy_spread(vals), abs=1e-12)

    def test_sector_spread_matches_numpy_too(self):
        vals = _fixture_returns_on("2026-08-28", FIXTURE_SECTORS)
        assert cross_sectional_spread(vals) == pytest.approx(_numpy_spread(vals), abs=1e-12)

    @pytest.mark.parametrize(
        "vals",
        [
            [0.01, -0.02, 0.03, 0.0, 0.05],
            [0.1, 0.2],
            [-0.03, 0.04, 0.02, -0.01, 0.0, 0.07, -0.05, 0.01, 0.02, 0.03, -0.02],
        ],
    )
    def test_linear_interpolation_matches_numpy_default(self, vals):
        assert cross_sectional_spread(vals) == pytest.approx(_numpy_spread(vals), abs=1e-12)

    def test_order_independent(self):
        vals = _fixture_returns_on("2026-08-27", FIXTURE_STOCKS)
        assert cross_sectional_spread(vals) == pytest.approx(
            cross_sectional_spread(sorted(vals, reverse=True)), abs=1e-15
        )


# ── raw rows ──────────────────────────────────────────────────────


@pytest.mark.usefixtures("fixture_floors")
class TestBuildRawRows:
    def test_one_row_per_session_after_the_first(self):
        rows = _raw_rows()
        assert [r["date"] for r in rows] == VIX_DATES[1:]
        assert len(rows) == 125

    def test_row_shape(self):
        row = _raw_rows()[-1]
        assert set(row) == {"date", "vix_close", "stock_spread", "sector_spread", "n_stocks", "n_sectors"}

    def test_last_row_values_from_fixture(self):
        row = _raw_rows()[-1]
        assert row["date"] == "2026-08-28"
        assert row["vix_close"] == 14.43
        assert row["stock_spread"] == pytest.approx(
            _numpy_spread(_fixture_returns_on("2026-08-28", FIXTURE_STOCKS)), abs=1e-12
        )
        assert row["sector_spread"] == pytest.approx(
            _numpy_spread(_fixture_returns_on("2026-08-28", FIXTURE_SECTORS)), abs=1e-12
        )
        assert row["n_stocks"] == 7
        assert row["n_sectors"] == 5

    def test_xom_february_bars_produce_no_row(self):
        dates = {r["date"] for r in _raw_rows()}
        assert not any(d < "2026-03-02" for d in dates)

    def test_stock_floor_drops_the_row(self, monkeypatch):
        monkeypatch.setattr(dispersion_math, "MIN_STOCKS", 8)
        assert _raw_rows() == []

    def test_sector_floor_drops_the_row(self, monkeypatch):
        monkeypatch.setattr(dispersion_math, "MIN_SECTORS", 6)
        assert _raw_rows() == []

    def test_a_missing_close_lowers_n_stocks_and_a_tight_floor_drops_only_that_row(self, monkeypatch):
        closes = _closes()
        gap = VIX_DATES[-2]
        del closes["AAPL"][gap]
        monkeypatch.setattr(dispersion_math, "MIN_STOCKS", 7)
        rows = build_raw_rows(closes, FIXTURE_STOCKS, FIXTURE_SECTORS)
        dates = [r["date"] for r in rows]
        assert gap not in dates
        assert VIX_DATES[-1] not in dates
        assert VIX_DATES[-3] in dates
        by_date = {r["date"]: r for r in rows}
        assert by_date[VIX_DATES[-3]]["n_stocks"] == 7

    def test_a_session_without_vix_is_never_emitted(self):
        closes = _closes()
        dropped = VIX_DATES[-1]
        del closes[VIX_SYMBOL][dropped]
        rows = build_raw_rows(closes, FIXTURE_STOCKS, FIXTURE_SECTORS)
        assert dropped not in {r["date"] for r in rows}

    def test_rows_are_ascending(self):
        dates = [r["date"] for r in _raw_rows()]
        assert dates == sorted(dates)


# ── rolling mean ──────────────────────────────────────────────────


class TestRollingMean:
    def test_full_window_only(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
        out = rolling_mean(values, 5)
        assert len(out) == 7
        assert out[:4] == [None, None, None, None]
        assert out[4] == pytest.approx(statistics.mean(values[0:5]), abs=1e-12)
        assert out[5] == pytest.approx(statistics.mean(values[1:6]), abs=1e-12)
        assert out[6] == pytest.approx(statistics.mean(values[2:7]), abs=1e-12)

    def test_shorter_than_window_is_all_none(self):
        assert rolling_mean([1.0, 2.0, 3.0], 5) == [None, None, None]

    @pytest.mark.usefixtures("fixture_floors")
    def test_fixture_stock_spread_window_five(self):
        rows = _raw_rows()
        values = [r["stock_spread"] for r in rows]
        out = rolling_mean(values, 5)
        assert out[4] == pytest.approx(statistics.mean(values[0:5]), abs=1e-12)
        assert out[-1] == pytest.approx(statistics.mean(values[-5:]), abs=1e-12)


# ── z-score series ────────────────────────────────────────────────


def _synthetic_rows(values: list[float], start_index: int = 0) -> list[dict]:
    """Raw rows with vix/stock/sector all driven by one value list (dates from
    the fixture calendar so the master order is real)."""
    return [
        {
            "date": VIX_DATES[start_index + i],
            "vix_close": 10.0 + v,
            "stock_spread": 0.05 + v / 1000,
            "sector_spread": 0.02 + v / 1000,
            "n_stocks": 7,
            "n_sectors": 5,
        }
        for i, v in enumerate(values)
    ]


class TestZscoreSeries:
    @pytest.fixture(autouse=True)
    def _window_five(self, monkeypatch):
        monkeypatch.setattr(dispersion_math, "WINDOW", 5)

    @pytest.mark.usefixtures("fixture_floors")
    def test_sixth_session_carries_the_first_metric(self):
        rows = _raw_rows()
        series = zscore_series(rows, VIX_DATES[0])
        # rows[0] is session 2; the window closes on rows[4] = session 6.
        assert series[0]["date"] == rows[4]["date"] == VIX_DATES[5]
        assert len(series) == len(rows) - 4
        for field, key in (("m60_vix", "vix_close"), ("m60_stock", "stock_spread"), ("m60_sector", "sector_spread")):
            assert series[0][field] == pytest.approx(statistics.mean(r[key] for r in rows[0:5]), abs=1e-9)
            assert series[1][field] == pytest.approx(statistics.mean(r[key] for r in rows[1:6]), abs=1e-9)

    @pytest.mark.usefixtures("fixture_floors")
    def test_first_four_sessions_carry_no_metric(self):
        series = zscore_series(_raw_rows(), VIX_DATES[0])
        emitted = {r["date"] for r in series}
        assert not emitted & set(VIX_DATES[:5])

    @pytest.mark.usefixtures("fixture_floors")
    def test_z_uses_sample_stdev_over_the_base(self):
        rows = _raw_rows()
        series = zscore_series(rows, VIX_DATES[0])
        m60 = [statistics.mean(r["vix_close"] for r in rows[i - 4 : i + 1]) for i in range(4, len(rows))]
        mu = statistics.mean(m60)
        sigma = statistics.stdev(m60)
        assert series[-1]["z_vix"] == pytest.approx((m60[-1] - mu) / sigma, abs=1e-9)
        assert series[0]["z_vix"] == pytest.approx((m60[0] - mu) / sigma, abs=1e-9)
        assert series[-1]["vix"] == rows[-1]["vix_close"]
        assert series[-1]["stock_spread"] == rows[-1]["stock_spread"]
        assert series[-1]["sector_spread"] == rows[-1]["sector_spread"]

    def test_base_starts_at_base_start_and_earlier_rows_are_not_emitted(self, monkeypatch):
        monkeypatch.setattr(dispersion_math, "WINDOW", 1)
        rows = _synthetic_rows([1.0, 2.0, 3.0, 4.0])
        base_start = rows[2]["date"]
        series = zscore_series(rows, base_start)
        assert [r["date"] for r in series] == [rows[2]["date"], rows[3]["date"]]
        # Two-point base: z is +/- 1/sqrt(2) whatever the pre-base rows hold.
        assert series[0]["z_vix"] == pytest.approx(-0.7071067811865476, abs=1e-12)
        assert series[1]["z_vix"] == pytest.approx(0.7071067811865476, abs=1e-12)

    def test_pre_base_rows_do_not_move_the_z(self, monkeypatch):
        monkeypatch.setattr(dispersion_math, "WINDOW", 1)
        rows = _synthetic_rows([1.0, 2.0, 3.0, 4.0])
        wild = _synthetic_rows([500.0, -300.0, 900.0, 4.0])
        series = zscore_series(rows, rows[2]["date"])
        moved = zscore_series(wild[:2] + rows[2:], rows[2]["date"])
        assert [r["z_stock"] for r in moved] == pytest.approx([r["z_stock"] for r in series], abs=1e-12)

    def test_constant_series_raises_on_zero_sigma(self, monkeypatch):
        monkeypatch.setattr(dispersion_math, "WINDOW", 1)
        rows = _synthetic_rows([2.0, 2.0, 2.0])
        with pytest.raises(ValueError):
            zscore_series(rows, rows[0]["date"])

    def test_single_point_base_raises(self, monkeypatch):
        monkeypatch.setattr(dispersion_math, "WINDOW", 1)
        rows = _synthetic_rows([1.0, 2.0, 3.0])
        with pytest.raises(ValueError):
            zscore_series(rows, rows[2]["date"])

    def test_series_is_ascending(self):
        rows = _synthetic_rows([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
        dates = [r["date"] for r in zscore_series(rows, rows[0]["date"])]
        assert dates == sorted(dates)


# ── regime + surface gap ──────────────────────────────────────────


class TestClassifyRegime:
    @pytest.mark.parametrize(
        "z_vix,z_stock,z_sector,expected",
        [
            (1.0, 0.0, 0.0, "BROAD STRESS"),
            (0.9999, 1.0, 0.0, "BELOW THE SURFACE"),
            (0.9999, 0.0, 1.0, "BELOW THE SURFACE"),
            (0.0, 0.9999, 0.9999, "NORMAL"),
            (-1.0, -1.0, -1.0, "COMPRESSED"),
            (-1.0, -0.9999, -1.0, "NORMAL"),
            (2.0, 2.5, 2.5, "BROAD STRESS"),
        ],
    )
    def test_boundary_table(self, z_vix, z_stock, z_sector, expected):
        assert classify_regime(z_vix, z_stock, z_sector) == expected

    def test_vix_wins_over_below_the_surface(self):
        assert classify_regime(STRESS_Z, 3.0, 3.0) == "BROAD STRESS"

    def test_compressed_needs_all_three_at_or_below_the_edge(self):
        assert classify_regime(COMPRESSED_Z, COMPRESSED_Z, COMPRESSED_Z + 0.0001) == "NORMAL"
        assert classify_regime(COMPRESSED_Z + 0.0001, COMPRESSED_Z, COMPRESSED_Z) == "NORMAL"


class TestSurfaceGap:
    def test_gap_is_max_of_stock_and_sector_minus_vix(self):
        assert surface_gap(-0.3, 2.4, 2.1) == pytest.approx(2.7, abs=1e-12)
        assert surface_gap(-0.3, 2.1, 2.4) == pytest.approx(2.7, abs=1e-12)
        assert surface_gap(1.5, 0.5, 0.2) == pytest.approx(-1.0, abs=1e-12)

    def test_gap_is_zero_when_all_three_agree(self):
        assert surface_gap(1.0, 1.0, 1.0) == 0.0


# ── stats ─────────────────────────────────────────────────────────


def _series_row(d: str, z_vix: float, z_stock: float, z_sector: float, m_vix: float, m_stock: float, m_sector: float) -> dict:
    return {
        "date": d,
        "z_vix": z_vix,
        "z_stock": z_stock,
        "z_sector": z_sector,
        "vix": 15.0,
        "stock_spread": 0.05,
        "sector_spread": 0.02,
        "m60_vix": m_vix,
        "m60_stock": m_stock,
        "m60_sector": m_sector,
        "n_stocks": 7,
        "n_sectors": 5,
    }


class TestComputeStats:
    def _series(self):
        return [
            _series_row(VIX_DATES[5], -0.6, -0.4, -0.5, 12.0, 0.040, 0.012),
            _series_row(VIX_DATES[6], 0.2, 1.3, 0.9, 16.0, 0.062, 0.020),   # BELOW THE SURFACE
            _series_row(VIX_DATES[7], 1.4, 2.0, 2.2, 22.0, 0.080, 0.030),   # BROAD STRESS
            _series_row(VIX_DATES[8], -0.3, 2.4, 2.1, 15.0, 0.083, 0.030),  # BELOW THE SURFACE
        ]

    def test_base_block(self):
        stats = compute_stats(self._series())
        assert stats["base"] == {"start": VIX_DATES[5], "end": VIX_DATES[8], "n": 4}

    def test_per_series_blocks_use_mean_and_sample_stdev_of_the_rolling_metric(self):
        series = self._series()
        stats = compute_stats(series)
        for key, m_field, z_field in (
            ("vix", "m60_vix", "z_vix"),
            ("stock", "m60_stock", "z_stock"),
            ("sector", "m60_sector", "z_sector"),
        ):
            m = [r[m_field] for r in series]
            z = [r[z_field] for r in series]
            assert stats[key]["mean_60d"] == pytest.approx(statistics.mean(m), abs=1e-9)
            assert stats[key]["stdev_60d"] == pytest.approx(statistics.stdev(m), abs=1e-9)
            assert stats[key]["z_min"] == pytest.approx(min(z), abs=1e-9)
            assert stats[key]["z_max"] == pytest.approx(max(z), abs=1e-9)

    def test_below_surface_count_and_last_date(self):
        stats = compute_stats(self._series())
        assert stats["days_below_surface"] == 2
        assert stats["last_below_surface_date"] == VIX_DATES[8]

    def test_never_below_surface_reports_null_date(self):
        series = [_series_row(VIX_DATES[5], 0.0, 0.0, 0.0, 12.0, 0.04, 0.01),
                  _series_row(VIX_DATES[6], 0.1, 0.2, 0.3, 13.0, 0.05, 0.02)]
        stats = compute_stats(series)
        assert stats["days_below_surface"] == 0
        assert stats["last_below_surface_date"] is None


# ── plausibility guard ────────────────────────────────────────────


@pytest.mark.usefixtures("fixture_floors")
class TestEnsurePlausibleRows:
    def test_fixture_rows_pass_incrementally(self):
        ensure_plausible_rows(_raw_rows(), backfill=False)  # must not raise

    def test_backfill_requires_min_series_rows(self):
        rows = _raw_rows()
        assert len(rows) < MIN_SERIES_ROWS
        with pytest.raises(ValueError, match="rows"):
            ensure_plausible_rows(rows, backfill=True)

    def test_backfill_passes_once_the_floor_is_met(self, monkeypatch):
        monkeypatch.setattr(dispersion_math, "MIN_SERIES_ROWS", 100)
        ensure_plausible_rows(_raw_rows(), backfill=True)

    def test_empty_rows_raise(self):
        with pytest.raises(ValueError):
            ensure_plausible_rows([], backfill=False)

    @pytest.mark.parametrize("bad", [0.004, 0.61])
    def test_stock_spread_out_of_band_raises(self, bad):
        rows = _raw_rows()
        corrupted = rows[:-1] + [{**rows[-1], "stock_spread": bad}]
        with pytest.raises(ValueError, match="stock_spread"):
            ensure_plausible_rows(corrupted, backfill=False)

    @pytest.mark.parametrize("bad", [0.0009, 0.31])
    def test_sector_spread_out_of_band_raises(self, bad):
        rows = _raw_rows()
        corrupted = rows[:-1] + [{**rows[-1], "sector_spread": bad}]
        with pytest.raises(ValueError, match="sector_spread"):
            ensure_plausible_rows(corrupted, backfill=False)

    @pytest.mark.parametrize("bad", [4.99, 100.01])
    def test_vix_out_of_band_raises(self, bad):
        rows = _raw_rows()
        corrupted = rows[:-1] + [{**rows[-1], "vix_close": bad}]
        with pytest.raises(ValueError, match="vix"):
            ensure_plausible_rows(corrupted, backfill=False)

    def test_thin_stock_cross_section_raises(self):
        rows = _raw_rows()
        corrupted = rows[:-1] + [{**rows[-1], "n_stocks": FIXTURE_MIN_STOCKS - 1}]
        with pytest.raises(ValueError, match="n_stocks"):
            ensure_plausible_rows(corrupted, backfill=False)

    def test_thin_sector_cross_section_raises(self):
        rows = _raw_rows()
        corrupted = rows[:-1] + [{**rows[-1], "n_sectors": FIXTURE_MIN_SECTORS - 1}]
        with pytest.raises(ValueError, match="n_sectors"):
            ensure_plausible_rows(corrupted, backfill=False)

    def test_every_row_is_checked_not_just_the_latest(self):
        rows = _raw_rows()
        corrupted = [{**rows[0], "vix_close": 0.5}] + rows[1:]
        with pytest.raises(ValueError, match="vix"):
            ensure_plausible_rows(corrupted, backfill=False)


# ── payload ───────────────────────────────────────────────────────


SCAN_TIME = "2026-08-29T22:21:07Z"
PAYLOAD_UNIVERSE = {"index": "SPX", "n_constituents": 7, "sectors": FIXTURE_SECTORS}
PAYLOAD_FETCH = {"ib_ok": 13, "yahoo_ok": 0, "failed": 0, "failed_symbols": []}


@pytest.mark.usefixtures("fixture_floors")
class TestBuildPayload:
    def _payload(self):
        return build_payload(
            _raw_rows(),
            scan_time=SCAN_TIME,
            status="ok",
            source={"prices": "ib", "vix": "ib"},
            universe=PAYLOAD_UNIVERSE,
            fetch=PAYLOAD_FETCH,
        )

    def test_top_level_shape(self):
        payload = self._payload()
        assert set(payload) >= {
            "scan_time", "status", "source", "data_date", "universe", "fetch",
            "count", "current", "stats", "series",
        }
        assert payload["scan_time"] == SCAN_TIME
        assert payload["status"] == "ok"
        assert payload["source"] == {"prices": "ib", "vix": "ib"}
        assert payload["universe"] == PAYLOAD_UNIVERSE
        assert payload["fetch"] == PAYLOAD_FETCH

    def test_series_starts_at_the_first_full_window_and_counts_every_emitted_session(self):
        rows = _raw_rows()
        payload = self._payload()
        assert payload["count"] == len(payload["series"]) == len(rows) - WINDOW + 1
        assert payload["series"][0]["date"] == rows[WINDOW - 1]["date"]
        assert payload["series"][-1]["date"] == rows[-1]["date"] == payload["data_date"]
        dates = [p["date"] for p in payload["series"]]
        assert dates == sorted(dates)

    def test_series_point_shape(self):
        point = self._payload()["series"][0]
        assert set(point) == {"date", "z_vix", "z_stock", "z_sector", "vix", "stock_spread", "sector_spread"}

    def test_current_is_computed_from_the_fixture(self):
        rows = _raw_rows()
        current = self._payload()["current"]
        m60 = [statistics.mean(r["vix_close"] for r in rows[i - WINDOW + 1 : i + 1]) for i in range(WINDOW - 1, len(rows))]
        mu, sigma = statistics.mean(m60), statistics.stdev(m60)
        assert current["date"] == rows[-1]["date"]
        assert current["vix"] == rows[-1]["vix_close"]
        assert current["stock_spread"] == round(rows[-1]["stock_spread"], 6)
        assert current["sector_spread"] == round(rows[-1]["sector_spread"], 6)
        assert current["m60_vix"] == pytest.approx(m60[-1], abs=1e-6)
        assert current["z_vix"] == pytest.approx((m60[-1] - mu) / sigma, abs=5e-5)
        assert current["n_stocks"] == 7
        assert current["n_sectors"] == 5
        assert current["regime"] == classify_regime(current["z_vix"], current["z_stock"], current["z_sector"])
        assert current["surface_gap"] == pytest.approx(
            max(current["z_stock"], current["z_sector"]) - current["z_vix"], abs=1e-4
        )

    def test_rounding_contract(self):
        payload = self._payload()
        for point in payload["series"]:
            for key in ("z_vix", "z_stock", "z_sector"):
                assert point[key] == round(point[key], 4)
            for key in ("stock_spread", "sector_spread"):
                assert point[key] == round(point[key], 6)

    def test_stats_are_computed_over_the_full_series(self):
        payload = self._payload()
        assert payload["stats"]["base"]["n"] == payload["count"]
        assert payload["stats"]["base"]["start"] == payload["series"][0]["date"]
        assert payload["stats"]["base"]["end"] == payload["data_date"]

    def test_status_passes_through(self):
        payload = build_payload(
            _raw_rows(), scan_time=SCAN_TIME, status="stale_source",
            source={"prices": "yahoo", "vix": "yahoo"}, universe=PAYLOAD_UNIVERSE, fetch=PAYLOAD_FETCH,
        )
        assert payload["status"] == "stale_source"


# ── fetch_dispersion: constants ───────────────────────────────────


class TestFetcherConstants:
    def test_service_key(self):
        assert fetch_dispersion.SERVICE == "dispersion"

    def test_timer_mirror(self):
        assert fetch_dispersion.TIMER_HOUR_UTC == 22
        assert fetch_dispersion.TIMER_MINUTE_UTC == 20

    def test_disk_fallback_path(self):
        assert Path(fetch_dispersion.DISPERSION_JSON).name == "dispersion.json"
        assert Path(fetch_dispersion.DISPERSION_JSON).parent.name == "data"


# ── fetch_dispersion: run() ───────────────────────────────────────


class _FakeWriter:
    def __init__(self, *, rows_raise: bool = False):
        self.rows_raise = rows_raise
        self.replica_checks = 0
        self.rows: list[tuple[list[dict], str | None]] = []
        self.snapshots: list[tuple[str, str]] = []
        self.health: list[tuple[str, str, dict | None]] = []

    def ensure_no_replica_for_writers(self):
        self.replica_checks += 1

    def upsert_dispersion_rows(self, rows, recorded_at=None):
        if self.rows_raise:
            raise RuntimeError("hrana 502")
        self.rows.append((list(rows), recorded_at))

    def upsert_scan_snapshot(self, service, scan_time, payload):
        self.snapshots.append((service, scan_time))

    def record_service_health(self, service, state, *, finished_at=None, error=None):
        self.health.append((service, state, error))


class _StubFetch:
    def __init__(self, closes):
        self.closes = closes
        self.calls: list[tuple[list[str], bool]] = []

    def __call__(self, symbols, backfill):
        self.calls.append((list(symbols), backfill))
        return self.closes


# Shift the fixture calendar so its last session is yesterday relative to
# _TODAY; the math is date-keyed so a constant shift preserves every number.
_SHIFT_DAYS = (_TODAY - timedelta(days=1) - date.fromisoformat(VIX_DATES[-1])).days
SHIFTED_CLOSES = _shift_closes(CLOSES, _SHIFT_DAYS)
SHIFTED_SESSIONS = [_shift_date(d, _SHIFT_DAYS) for d in VIX_DATES]
LAST_SESSION = SHIFTED_SESSIONS[-1]
NOW = datetime.combine(_TODAY, datetime.min.time(), tzinfo=timezone.utc).replace(hour=22, minute=21, second=7)
NOW_ISO = NOW.isoformat().replace("+00:00", "Z")


@pytest.mark.usefixtures("fixture_floors")
class TestRun:
    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        self.fake = _FakeWriter()
        monkeypatch.setattr(fetch_dispersion, "writer", self.fake)
        monkeypatch.setattr(fetch_dispersion, "DISPERSION_JSON", tmp_path / "dispersion.json")
        monkeypatch.setattr(fetch_dispersion, "last_completed_session_date", lambda *a, **k: LAST_SESSION)
        self.stored: list[dict] = []
        monkeypatch.setattr(fetch_dispersion, "_read_stored_rows", lambda *a, **k: list(self.stored))
        self.json_path = tmp_path / "dispersion.json"

    def _all_rows(self) -> list[dict]:
        return _shift_rows(build_raw_rows(CLOSES, FIXTURE_STOCKS, FIXTURE_SECTORS), _SHIFT_DAYS)

    def _run(self, stub, **kwargs):
        return fetch_dispersion.run(fetch_closes=stub, now=NOW, universe=FIXTURE_UNIVERSE, **kwargs)

    def test_no_new_session_makes_no_fetch_calls_and_still_heartbeats(self):
        self.stored = self._all_rows()
        stub = _StubFetch(SHIFTED_CLOSES)
        payload = self._run(stub)

        assert stub.calls == []
        assert self.fake.rows == []
        assert self.fake.snapshots == [("dispersion", NOW_ISO)]
        assert self.fake.health == [("dispersion", "ok", None)]
        assert self.fake.replica_checks >= 1
        assert payload["scan_time"] == NOW_ISO
        assert payload["status"] == "ok"
        assert payload["data_date"] == LAST_SESSION
        assert payload["count"] == len(self.stored) - WINDOW + 1
        assert json.loads(self.json_path.read_text())["data_date"] == LAST_SESSION

    def test_new_session_upserts_only_the_new_rows(self):
        rows = self._all_rows()
        self.stored = rows[:-3]
        stub = _StubFetch(SHIFTED_CLOSES)
        payload = self._run(stub)

        assert len(stub.calls) == 1
        symbols, backfill = stub.calls[0]
        assert backfill is False
        assert set(symbols) == set(FIXTURE_STOCKS) | set(FIXTURE_SECTORS) | {VIX_SYMBOL}

        assert len(self.fake.rows) == 1
        upserted, recorded_at = self.fake.rows[0]
        assert [r["date"] for r in upserted] == [r["date"] for r in rows[-3:]]
        assert recorded_at == NOW_ISO
        for got, want in zip(upserted, rows[-3:]):
            assert got["vix_close"] == want["vix_close"]
            assert got["stock_spread"] == pytest.approx(want["stock_spread"], abs=1e-12)
            assert got["n_stocks"] == 7 and got["n_sectors"] == 5

        assert self.fake.snapshots == [("dispersion", NOW_ISO)]
        assert self.fake.health == [("dispersion", "ok", None)]
        assert payload["data_date"] == LAST_SESSION
        assert payload["count"] == len(rows) - WINDOW + 1
        assert payload["universe"]["n_constituents"] == 7
        assert payload["universe"]["sectors"] == FIXTURE_SECTORS

    def test_vix_empty_reserves_stored_series_as_stale_source(self):
        rows = self._all_rows()
        self.stored = rows[:-1]
        self.json_path.write_text(json.dumps({"scan_time": "old", "status": "ok", "series": [], "current": None}))
        no_vix = {s: c for s, c in SHIFTED_CLOSES.items() if s != VIX_SYMBOL}
        payload = self._run(_StubFetch(no_vix))

        assert payload["status"] == "stale_source"
        assert payload["scan_time"] == NOW_ISO
        assert self.fake.rows == []
        assert self.fake.snapshots == [("dispersion", NOW_ISO)]
        assert len(self.fake.health) == 1
        service, state, error = self.fake.health[0]
        assert (service, state) == ("dispersion", "error")
        assert error is not None
        assert json.loads(self.json_path.read_text())["status"] == "stale_source"

    def test_thin_cross_section_on_the_new_session_is_stale_source(self):
        rows = self._all_rows()
        self.stored = rows[:-1]
        thin = {s: c for s, c in SHIFTED_CLOSES.items() if s in ("AAPL", "MSFT", VIX_SYMBOL) or s in FIXTURE_SECTORS}
        payload = self._run(_StubFetch(thin))

        assert payload["status"] == "stale_source"
        assert self.fake.rows == []
        assert self.fake.health[0][1] == "error"

    def test_gap_beyond_the_incremental_window_raises_and_records_error(self):
        rows = self._all_rows()
        self.stored = rows[:40]
        tail_start = SHIFTED_SESSIONS[100]
        tail = {s: {d: c for d, c in series.items() if d >= tail_start} for s, series in SHIFTED_CLOSES.items()}
        with pytest.raises(Exception, match="backfill"):
            self._run(_StubFetch(tail))

        assert self.fake.rows == []
        assert self.fake.health[-1][1] == "error"

    def test_backfill_replaces_every_row(self, monkeypatch):
        monkeypatch.setattr(dispersion_math, "MIN_SERIES_ROWS", 100)
        rows = self._all_rows()
        self.stored = rows[:50]
        stub = _StubFetch(SHIFTED_CLOSES)
        payload = self._run(stub, backfill=True)

        assert stub.calls[0][1] is True
        assert len(self.fake.rows) == 1
        upserted, _ = self.fake.rows[0]
        assert [r["date"] for r in upserted] == [r["date"] for r in rows]
        assert payload["count"] == len(rows) - WINDOW + 1
        assert self.fake.health == [("dispersion", "ok", None)]

    def test_implausible_fetched_row_raises_before_any_write(self):
        rows = self._all_rows()
        self.stored = rows[:-1]
        corrupted = {s: dict(c) for s, c in SHIFTED_CLOSES.items()}
        corrupted[VIX_SYMBOL][LAST_SESSION] = 0.5
        with pytest.raises(ValueError):
            self._run(_StubFetch(corrupted))
        assert self.fake.rows == []

    # R-434: a sweep IB served nothing on is real data (Yahoo built it) but not
    # a healthy steady state under CLAUDE.md rule 7; the heartbeat stays ok and
    # carries the class so the operator sees the rung is dead.
    def test_ib_rung_dead_heartbeats_ok_with_the_ib_rung_dead_class(self, monkeypatch):
        rows = self._all_rows()
        self.stored = rows[:-1]
        monkeypatch.setattr(fetch_dispersion, "_fetch_ib_closes", lambda symbols, duration, deadline: {})
        monkeypatch.setattr(
            fetch_dispersion,
            "_fetch_yahoo_closes",
            lambda symbols, backfill, deadline, *rest: {s: dict(SHIFTED_CLOSES[s]) for s in symbols},
        )
        payload = fetch_dispersion.run(now=NOW, universe=FIXTURE_UNIVERSE)

        assert payload["status"] == "ok"
        assert payload["source"] == {"prices": "yahoo", "vix": "yahoo"}
        assert payload["fetch"]["ib_ok"] == 0
        assert len(self.fake.rows) == 1
        assert self.fake.snapshots == [("dispersion", NOW_ISO)]
        assert len(self.fake.health) == 1
        service, state, error = self.fake.health[0]
        assert (service, state) == ("dispersion", "ok")
        assert error is not None, "pre-fix: ok with error=None hides a dead IB rung"
        assert error["class"] == "ib_rung_dead"
        assert "IB" in error["message"]

    def test_ib_serving_the_sweep_heartbeats_ok_without_an_error_class(self, monkeypatch):
        rows = self._all_rows()
        self.stored = rows[:-1]
        monkeypatch.setattr(
            fetch_dispersion,
            "_fetch_ib_closes",
            lambda symbols, duration, deadline: {s: dict(SHIFTED_CLOSES[s]) for s in symbols},
        )
        monkeypatch.setattr(
            fetch_dispersion, "_fetch_yahoo_closes", lambda symbols, backfill, deadline, *rest: {}
        )
        payload = fetch_dispersion.run(now=NOW, universe=FIXTURE_UNIVERSE)
        assert payload["source"] == {"prices": "ib", "vix": "ib"}
        assert self.fake.health == [("dispersion", "ok", None)]


# ── fetch_dispersion: _write_db isolation (R-192) ─────────────────


class TestWriteDbIsolation:
    def _write(self, monkeypatch, fake, **kwargs):
        monkeypatch.setattr(fetch_dispersion, "writer", fake)
        new_rows = [{"date": "d", "vix_close": 15.0, "stock_spread": 0.05, "sector_spread": 0.02, "n_stocks": 7, "n_sectors": 5}]
        fetch_dispersion._write_db({"series": [], "current": None}, new_rows, SCAN_TIME, **kwargs)

    def test_happy_path_writes_rows_snapshot_and_ok_heartbeat(self, monkeypatch):
        fake = _FakeWriter()
        self._write(monkeypatch, fake, rows_changed=True)
        assert [(len(r), stamp) for r, stamp in fake.rows] == [(1, SCAN_TIME)]
        assert fake.snapshots == [("dispersion", SCAN_TIME)]
        assert fake.health == [("dispersion", "ok", None)]

    def test_no_new_rows_heartbeats_without_row_upserts(self, monkeypatch):
        fake = _FakeWriter()
        self._write(monkeypatch, fake, rows_changed=False)
        assert fake.rows == []
        assert fake.snapshots == [("dispersion", SCAN_TIME)]
        assert fake.health == [("dispersion", "ok", None)]

    def test_failed_row_upsert_still_snapshots_and_records_error(self, monkeypatch):
        fake = _FakeWriter(rows_raise=True)
        self._write(monkeypatch, fake, rows_changed=True)
        assert fake.snapshots == [("dispersion", SCAN_TIME)]
        service, state, error = fake.health[0]
        assert (service, state) == ("dispersion", "error")
        assert error["class"] == "db_write_failed"


# ── migration + upsert (sqlite3 stand-in for libsql) ──────────────


class TestDispersionStorage:
    def _db(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
        db.executescript(MIGRATION.read_text())
        return db

    def test_migration_applies_and_registers_version_61(self):
        db = self._db()
        assert db.execute("SELECT version FROM schema_migrations").fetchone()[0] == 61
        cols = {r[1] for r in db.execute("PRAGMA table_info(dispersion_history)")}
        assert cols == {"date", "vix_close", "stock_spread", "sector_spread", "n_stocks", "n_sectors", "recorded_at"}

    def test_date_is_the_primary_key(self):
        db = self._db()
        pk = [r[1] for r in db.execute("PRAGMA table_info(dispersion_history)") if r[5]]
        assert pk == ["date"]

    def test_every_column_is_not_null(self):
        db = self._db()
        notnull = {r[1]: r[3] for r in db.execute("PRAGMA table_info(dispersion_history)")}
        assert all(notnull[c] == 1 for c in ("vix_close", "stock_spread", "sector_spread", "n_stocks", "n_sectors", "recorded_at"))

    def test_descending_date_index_exists(self):
        db = self._db()
        names = {r[1] for r in db.execute("PRAGMA index_list(dispersion_history)")}
        assert "idx_dispersion_history_date_desc" in names

    def test_migration_is_idempotent(self):
        db = self._db()
        db.executescript(MIGRATION.read_text())
        assert db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 1

    def _row(self, d: str, vix: float, stock: float = 0.05) -> dict:
        return {"date": d, "vix_close": vix, "stock_spread": stock, "sector_spread": 0.02, "n_stocks": 501, "n_sectors": 11}

    def test_upsert_is_idempotent_per_date(self, monkeypatch):
        from db import writer

        db = self._db()
        monkeypatch.setattr(writer, "get_db", lambda: db)
        writer.upsert_dispersion_rows([self._row("2026-08-28", 14.43)], recorded_at="2026-08-29T22:21:07Z")
        writer.upsert_dispersion_rows([self._row("2026-08-28", 14.50, 0.06)], recorded_at="2026-08-30T22:21:07Z")
        rows = db.execute("SELECT date, vix_close, stock_spread, recorded_at FROM dispersion_history").fetchall()
        assert rows == [("2026-08-28", 14.50, 0.06, "2026-08-30T22:21:07Z")]

    def test_the_emitted_statement_carries_one_row_per_date(self, monkeypatch):
        from db import writer

        calls: list[tuple[str, tuple]] = []

        class _Capture:
            def execute(self, sql, params=()):
                calls.append((sql, params))

            def commit(self):
                pass

        monkeypatch.setattr(writer, "get_db", lambda: _Capture())
        writer.upsert_dispersion_rows(
            [self._row("2026-08-27", 14.0), self._row("2026-08-28", 14.43), self._row("2026-08-28", 14.50)],
            recorded_at="2026-08-29T22:21:07Z",
        )
        assert len(calls) == 1
        sql, params = calls[0]
        assert len(params) % 7 == 0
        dates = [params[i] for i in range(0, len(params), 7)]
        assert dates == ["2026-08-27", "2026-08-28"], f"one row per conflict target; got {dates}"
        vix = [params[i + 1] for i in range(0, len(params), 7)]
        assert vix == [14.0, 14.50], "the LAST row for a date wins"
        assert "ON CONFLICT(date) DO UPDATE" in sql.replace(" (date)", "(date)")

    def test_empty_rows_write_nothing(self, monkeypatch):
        from db import writer

        monkeypatch.setattr(writer, "get_db", lambda: (_ for _ in ()).throw(AssertionError("no db call expected")))
        writer.upsert_dispersion_rows([], recorded_at="2026-08-29T22:21:07Z")


# ── fetch_dispersion: sweep budget (R-446) ────────────────────────


class TestSweepBudget:
    def test_budget_split_covers_the_whole_sweep(self):
        assert fetch_dispersion.SWEEP_BUDGET_S == 600
        assert fetch_dispersion.IB_SWEEP_BUDGET_S == 420
        assert fetch_dispersion.YAHOO_SWEEP_BUDGET_S == 180
        assert fetch_dispersion.IB_SWEEP_BUDGET_S + fetch_dispersion.YAHOO_SWEEP_BUDGET_S == fetch_dispersion.SWEEP_BUDGET_S

    def test_yahoo_rung_keeps_its_own_budget_after_ib_spends_the_deadline(self, monkeypatch):
        """An HMDS-inactive gateway answers every request empty on its own
        timeout and overruns the deadline; the fallback must still get a
        positive budget instead of the spent one (pre-fix: {} at 0/N)."""
        monkeypatch.setattr(fetch_dispersion, "SWEEP_BUDGET_S", 0.2)
        monkeypatch.setattr(fetch_dispersion, "IB_SWEEP_BUDGET_S", 0.05, raising=False)
        monkeypatch.setattr(fetch_dispersion, "YAHOO_SWEEP_BUDGET_S", 0.5, raising=False)
        symbols = ["AAPL", "MSFT", VIX_SYMBOL]
        seen: dict[str, float] = {}

        def ib_spent(requested, duration, deadline):
            time.sleep(max(0.0, deadline - time.monotonic()) + 0.05)
            return {}

        real_incremental = fetch_dispersion._fetch_yahoo_incremental

        def yahoo_incremental(requested, deadline, *rest):
            seen["remaining"] = deadline - time.monotonic()
            return real_incremental(requested, deadline, *rest)

        monkeypatch.setattr(fetch_dispersion, "_fetch_ib_closes", ib_spent)
        monkeypatch.setattr(fetch_dispersion, "_fetch_yahoo_incremental", yahoo_incremental)
        monkeypatch.setattr(
            fetch_dispersion,
            "_fetch_yahoo_spark_batch",
            lambda batch, *rest: {s: {"2026-08-28": 100.0} for s in batch},
        )

        closes = fetch_dispersion.fetch_closes_ladder(symbols, backfill=False)

        assert seen["remaining"] > 0, "the fallback rung was handed a spent deadline"
        assert set(closes) == set(symbols)
        assert closes.sources == {s: "yahoo" for s in symbols}
