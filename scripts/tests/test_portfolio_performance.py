"""Tests for reconstructed portfolio performance analytics."""

from __future__ import annotations

import math
import os
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest

# Prevent price_cache from creating dirs on import
with patch("os.makedirs"):
    from portfolio_performance import (
        TradeFill,
        build_option_id,
        build_payload,
        compute_performance_metrics,
        extract_fill_marks,
        load_blotter_fallback,
        load_portfolio_snapshot,
        parse_flex_trade_rows,
        reconstruct_equity_curve,
        select_option_mark,
        _get_worker_count,
        _fetch_stock_history_ib_only,
        _fetch_stock_history_fallback,
        _fetch_option_history_safe,
        _fetch_all_histories,
    )


def test_build_option_id_formats_occ_style_identifier():
    assert build_option_id("BRZE", "20260320", "C", 25.0) == "BRZE260320C00025000"
    assert build_option_id("SPY", "2026-03-20", "p", 572.5) == "SPY260320P00572500"


def test_load_portfolio_snapshot_reads_latest_db_snapshot():
    with patch("db.readers.read_latest_portfolio_snapshot", return_value={"positions": [{"ticker": "AAPL"}]}) as reader:
        assert load_portfolio_snapshot() == {"positions": [{"ticker": "AAPL"}]}
    reader.assert_called_once_with()


def test_load_blotter_fallback_reconstructs_from_executed_orders():
    rows = [
        {
            "exec_id": "exec-1",
            "fill_time": "2026-06-01T14:30:00Z",
            "payload": {
                "execId": "exec-1",
                "side": "BOT",
                "quantity": 2,
                "avgPrice": 1.25,
                "commission": 1.0,
                "time": "2026-06-01T14:30:00Z",
                "contract": {
                    "symbol": "AAPL",
                    "secType": "OPT",
                    "strike": 200,
                    "right": "C",
                    "lastTradeDateOrContractMonth": "20260717",
                },
            },
        }
    ]
    with patch("db.readers.read_executed_orders", return_value=rows):
        fills = load_blotter_fallback()

    assert len(fills) == 1
    assert fills[0].trade_date == "2026-06-01"
    assert fills[0].contract_key == "AAPL260717C00200000"
    assert fills[0].quantity == 2
    assert fills[0].net_cash == pytest.approx(-251.0)


def test_extract_fill_marks_reads_executed_order_prices():
    rows = [
        {
            "exec_id": "exec-1",
            "fill_time": "2026-06-01T14:30:00Z",
            "payload": {
                "execId": "exec-1",
                "side": "SLD",
                "quantity": 1,
                "avgPrice": 2.5,
                "time": "2026-06-01T14:30:00Z",
                "contract": {
                    "symbol": "MSFT",
                    "secType": "OPT",
                    "strike": 400,
                    "right": "P",
                    "lastTradeDateOrContractMonth": "20260821",
                },
            },
        }
    ]
    with patch("db.readers.read_executed_orders", return_value=rows):
        marks = extract_fill_marks()

    assert marks == {"MSFT260821P00400000": {"2026-06-01": 2.5}}


def test_select_option_mark_prefers_nbbo_mid_then_avg_then_last():
    assert select_option_mark({
        "nbbo_bid": "1.00",
        "nbbo_ask": "1.40",
        "avg_price": "1.35",
        "last_price": "1.20",
    }) == pytest.approx(1.20)

    assert select_option_mark({
        "nbbo_bid": "0",
        "nbbo_ask": "0",
        "avg_price": "1.35",
        "last_price": "1.20",
    }) == pytest.approx(1.35)

    assert select_option_mark({
        "nbbo_bid": None,
        "nbbo_ask": None,
        "avg_price": None,
        "last_price": "1.20",
    }) == pytest.approx(1.20)

    assert select_option_mark({
        "nbbo_bid": None,
        "nbbo_ask": None,
        "avg_price": None,
        "last_price": None,
    }) is None


def test_reconstruct_equity_curve_calibrates_cash_to_final_equity():
    calendar = pd.Index(["2026-01-02", "2026-01-05", "2026-01-06"], name="date")
    trades = [
        TradeFill(
            trade_date="2025-12-31",
            contract_key="STK:AAA",
            quantity=10,
            net_cash=-1000,
            multiplier=1,
        ),
        TradeFill(
            trade_date="2026-01-05",
            contract_key="STK:BBB",
            quantity=5,
            net_cash=-250,
            multiplier=1,
        ),
    ]
    marks = {
        "STK:AAA": {"2026-01-02": 100.0, "2026-01-05": 110.0, "2026-01-06": 120.0},
        "STK:BBB": {"2026-01-05": 50.0, "2026-01-06": 60.0},
    }

    curve = reconstruct_equity_curve(
        calendar=calendar,
        trades=trades,
        marks_by_contract=marks,
        final_equity=2000.0,
    )

    assert list(curve.index.astype(str)) == list(calendar)
    assert curve.loc["2026-01-02", "cash"] == pytest.approx(750.0)
    assert curve.loc["2026-01-02", "equity"] == pytest.approx(1750.0)
    assert curve.loc["2026-01-05", "equity"] == pytest.approx(1850.0)
    assert curve.loc["2026-01-06", "equity"] == pytest.approx(2000.0)
    assert curve.loc["2026-01-06", "daily_return"] == pytest.approx((2000.0 / 1850.0) - 1.0)


def test_reconstruct_equity_curve_normalizes_compact_trade_dates():
    calendar = pd.Index(["2026-01-02", "2026-01-05", "2026-01-06"], name="date")
    trades = [
        TradeFill(
            trade_date="20251231",
            contract_key="STK:AAA",
            quantity=10,
            net_cash=-1000,
            multiplier=1,
        ),
        TradeFill(
            trade_date="20260105",
            contract_key="STK:BBB",
            quantity=5,
            net_cash=-250,
            multiplier=1,
        ),
    ]
    marks = {
        "STK:AAA": {"2026-01-02": 100.0, "2026-01-05": 110.0, "2026-01-06": 120.0},
        "STK:BBB": {"2026-01-05": 50.0, "2026-01-06": 60.0},
    }

    curve = reconstruct_equity_curve(
        calendar=calendar,
        trades=trades,
        marks_by_contract=marks,
        final_equity=2000.0,
    )

    assert curve.loc["2026-01-05", "equity"] == pytest.approx(1850.0)
    assert curve.loc["2026-01-06", "equity"] == pytest.approx(2000.0)


def test_parse_flex_trade_rows_normalizes_trade_dates_to_iso():
    df = pd.DataFrame([
        {
            "assetCategory": "STK",
            "tradeDate": "20260311",
            "symbol": "AAPL",
            "quantity": 5,
            "netCash": -1000,
            "multiplier": 1,
        }
    ])

    fills = parse_flex_trade_rows(df)

    assert fills[0].trade_date == "2026-03-11"


def test_compute_performance_metrics_matches_core_manual_statistics():
    index = pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"])
    equity = pd.Series([100.0, 110.0, 105.0, 120.0], index=index)
    benchmark = pd.Series([100.0, 105.0, 103.0, 108.0], index=index)

    metrics = compute_performance_metrics(equity, benchmark)

    portfolio_returns = np.array([0.10, (105.0 / 110.0) - 1.0, (120.0 / 105.0) - 1.0])
    benchmark_returns = np.array([0.05, (103.0 / 105.0) - 1.0, (108.0 / 103.0) - 1.0])
    expected_beta = float(np.cov(portfolio_returns, benchmark_returns, ddof=1)[0, 1] / np.var(benchmark_returns, ddof=1))
    expected_corr = float(np.corrcoef(portfolio_returns, benchmark_returns)[0, 1])
    expected_mdd = min(0.0, (105.0 / 110.0) - 1.0)

    assert metrics["hit_rate"] == pytest.approx(2 / 3)
    assert metrics["max_drawdown"] == pytest.approx(expected_mdd)
    assert metrics["beta"] == pytest.approx(expected_beta)
    assert metrics["correlation"] == pytest.approx(expected_corr)
    assert metrics["best_day"] == pytest.approx(max(portfolio_returns))
    assert metrics["worst_day"] == pytest.approx(min(portfolio_returns))
    assert "trading_days" not in metrics
    assert math.isfinite(metrics["sharpe_ratio"])
    assert math.isfinite(metrics["sortino_ratio"])


def test_build_payload_exposes_expected_top_level_contract(monkeypatch):
    trades = [
        TradeFill(
            trade_date="2025-12-31",
            contract_key="STK:AAA",
            quantity=10,
            net_cash=-1000,
            multiplier=1,
            security_type="STK",
            symbol="AAA",
        ),
        TradeFill(
            trade_date="2026-01-05",
            contract_key="STK:BBB",
            quantity=5,
            net_cash=-250,
            multiplier=1,
            security_type="STK",
            symbol="BBB",
        ),
    ]

    class DummyIBClient:
        def connect(self, **kwargs):
            return None

        def disconnect(self):
            return None

    def fake_stock_history(symbol, start_date, end_date, ib_client, uw_client):
        histories = {
            "SPY": {"2026-01-02": 100.0, "2026-01-05": 101.0, "2026-01-06": 102.0},
            "AAA": {"2026-01-02": 100.0, "2026-01-05": 110.0, "2026-01-06": 120.0},
            "BBB": {"2026-01-05": 50.0, "2026-01-06": 60.0},
        }
        return histories.get(symbol, {})

    def fake_fetch_all(trades, start, end, ib_client, warnings, seed_marks=None):
        marks = {
            "STK:AAA": {"2026-01-02": 100.0, "2026-01-05": 110.0, "2026-01-06": 120.0},
            "STK:BBB": {"2026-01-05": 50.0, "2026-01-06": 60.0},
        }
        return marks, []

    monkeypatch.setattr("portfolio_performance.load_portfolio_snapshot", lambda: {
        "last_sync": "2026-01-06T16:00:00",
        "account_summary": {"net_liquidation": 2000.0},
        "bankroll": 2000.0,
    })
    monkeypatch.setattr("portfolio_performance.fetch_ib_nav_series", lambda: None)
    monkeypatch.setattr("portfolio_performance.load_ib_nav_cache", lambda: None)
    monkeypatch.setattr("portfolio_performance.fetch_flex_trade_fills", lambda: (trades, "ib_flex"))
    monkeypatch.setattr("portfolio_performance.extract_fill_marks", lambda *a, **kw: {})
    monkeypatch.setattr("portfolio_performance.fetch_stock_history", fake_stock_history)
    monkeypatch.setattr("portfolio_performance._fetch_all_histories", fake_fetch_all)
    monkeypatch.setattr("portfolio_performance._fetch_stock_history_fallback", lambda s, st, en: (s, {}, "none"))
    monkeypatch.setattr("portfolio_performance.IBClient", DummyIBClient)
    monkeypatch.setattr("portfolio_performance.read_cache", lambda *a: None)
    monkeypatch.setattr("portfolio_performance.write_cache", lambda *a, **kw: None)

    payload = build_payload()

    assert payload["period_label"] == "YTD"
    assert payload["benchmark"] == "SPY"
    assert payload["trades_source"] == "ib_flex"
    assert payload["price_sources"]["options"] == "unusual_whales_option_contract_historic"
    # starting_equity is curve["equity"].iloc[0], anchored to net_liquidation
    assert payload["summary"]["starting_equity"] == pytest.approx(2000.0)
    assert payload["summary"]["ending_equity"] == pytest.approx(2000.0)
    assert payload["summary"]["trading_days"] == 3
    assert len(payload["series"]) == 3
    assert payload["series"][0]["date"] == "2026-01-02"


def test_build_payload_warns_and_continues_when_option_history_is_rate_limited(monkeypatch):
    trades = [
        TradeFill(
            trade_date="2025-12-31",
            contract_key="SPY260320C00570000",
            quantity=1,
            net_cash=-1000,
            multiplier=100,
            security_type="OPT",
            symbol="SPY",
            option_id="SPY260320C00570000",
            expiry="20260320",
        ),
    ]

    class DummyIBClient:
        def connect(self, **kwargs):
            return None

        def disconnect(self):
            return None

    def fake_fetch_all(trades_arg, start, end, ib_client, warnings, seed_marks=None):
        warnings.append("Option history unavailable for SPY260320C00570000: rate limited")
        return {}, ["SPY260320C00570000"]

    monkeypatch.setattr("portfolio_performance.load_portfolio_snapshot", lambda: {
        "last_sync": "2026-01-06T16:00:00",
        "account_summary": {"net_liquidation": 1000.0},
        "bankroll": 1000.0,
    })
    monkeypatch.setattr("portfolio_performance.fetch_ib_nav_series", lambda: None)
    monkeypatch.setattr("portfolio_performance.load_ib_nav_cache", lambda: None)
    monkeypatch.setattr("portfolio_performance.fetch_flex_trade_fills", lambda: (trades, "ib_flex"))
    monkeypatch.setattr("portfolio_performance.extract_fill_marks", lambda *a, **kw: {})
    monkeypatch.setattr("portfolio_performance.fetch_stock_history", lambda symbol, start_date, end_date, ib_client, uw_client: {
        "2026-01-02": 100.0,
        "2026-01-05": 101.0,
        "2026-01-06": 102.0,
    })
    monkeypatch.setattr("portfolio_performance._fetch_all_histories", fake_fetch_all)
    monkeypatch.setattr("portfolio_performance._fetch_stock_history_fallback", lambda s, st, en: (s, {}, "none"))
    monkeypatch.setattr("portfolio_performance.IBClient", DummyIBClient)
    monkeypatch.setattr("portfolio_performance.read_cache", lambda *a: None)
    monkeypatch.setattr("portfolio_performance.write_cache", lambda *a, **kw: None)

    payload = build_payload()

    assert payload["summary"]["ending_equity"] == pytest.approx(1000.0)
    assert payload["contracts_missing_history"] == ["SPY260320C00570000"]
    assert any("option history unavailable" in warning.lower() for warning in payload["warnings"])


# ---- New tests for parallel fetch + cache integration ----


class TestGetWorkerCount:
    def test_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PERF_FETCH_WORKERS", None)
            assert _get_worker_count() == 8

    def test_valid_value(self):
        with patch.dict(os.environ, {"PERF_FETCH_WORKERS": "12"}):
            assert _get_worker_count() == 12

    def test_empty_string(self):
        with patch.dict(os.environ, {"PERF_FETCH_WORKERS": ""}):
            assert _get_worker_count() == 8

    def test_zero(self):
        with patch.dict(os.environ, {"PERF_FETCH_WORKERS": "0"}):
            assert _get_worker_count() == 8

    def test_negative(self):
        with patch.dict(os.environ, {"PERF_FETCH_WORKERS": "-5"}):
            assert _get_worker_count() == 8

    def test_too_large(self):
        with patch.dict(os.environ, {"PERF_FETCH_WORKERS": "999"}):
            assert _get_worker_count() == 8

    def test_non_numeric(self):
        with patch.dict(os.environ, {"PERF_FETCH_WORKERS": "abc"}):
            assert _get_worker_count() == 8

    def test_min_valid(self):
        with patch.dict(os.environ, {"PERF_FETCH_WORKERS": "1"}):
            assert _get_worker_count() == 1

    def test_max_valid(self):
        with patch.dict(os.environ, {"PERF_FETCH_WORKERS": "20"}):
            assert _get_worker_count() == 20


class TestFetchStockHistoryIBOnly:
    def test_successful_ib_fetch(self):
        mock_bar = MagicMock()
        mock_bar.date = "2026-01-02"
        mock_bar.close = 230.5

        ib_client = MagicMock()
        ib_client.get_historical_data.return_value = [mock_bar]

        sym, history = _fetch_stock_history_ib_only("SPY", "2026-01-01", "2026-12-31", ib_client)
        assert sym == "SPY"
        assert history == {"2026-01-02": 230.5}

    def test_ib_fetch_exception(self):
        ib_client = MagicMock()
        ib_client.get_historical_data.side_effect = Exception("timeout")

        sym, history = _fetch_stock_history_ib_only("SPY", "2026-01-01", "2026-12-31", ib_client)
        assert sym == "SPY"
        assert history == {}

    def test_ib_fetch_empty_bars(self):
        ib_client = MagicMock()
        ib_client.get_historical_data.return_value = []

        sym, history = _fetch_stock_history_ib_only("SPY", "2026-01-01", "2026-12-31", ib_client)
        assert history == {}


class TestFetchStockHistoryFallback:
    @patch("portfolio_performance.read_cache")
    def test_cache_hit(self, mock_read):
        mock_read.return_value = {"2026-01-02": 230.5}
        sym, history, source = _fetch_stock_history_fallback("SPY", "2026-01-01", "2026-03-17")
        assert history == {"2026-01-02": 230.5}
        assert source == "cache"

    @patch("portfolio_performance.write_cache")
    @patch("portfolio_performance.read_cache", return_value=None)
    @patch("portfolio_performance.UWClient")
    def test_uw_success(self, mock_uw_cls, mock_read, mock_write):
        mock_uw = MagicMock()
        mock_uw.get_stock_ohlc.return_value = {
            "data": [{"date": "2026-01-02", "close": 230.5}]
        }
        mock_uw_cls.return_value = mock_uw

        sym, history, source = _fetch_stock_history_fallback("SPY", "2026-01-01", "2026-03-17")
        assert history == {"2026-01-02": 230.5}
        assert source == "uw"

    @patch("portfolio_performance.write_cache")
    @patch("portfolio_performance.read_cache", return_value=None)
    @patch("portfolio_performance.UWClient", side_effect=Exception("no token"))
    @patch("portfolio_performance._fetch_yahoo_chart")
    def test_yahoo_fallback(self, mock_yahoo, mock_uw_cls, mock_read, mock_write):
        mock_yahoo.return_value = [("2026-01-02", 230.5)]
        sym, history, source = _fetch_stock_history_fallback("SPY", "2026-01-01", "2026-03-17")
        assert history == {"2026-01-02": 230.5}
        assert source == "yahoo"

    @patch("portfolio_performance.read_cache", return_value=None)
    @patch("portfolio_performance.UWClient", side_effect=Exception("no token"))
    @patch("portfolio_performance._fetch_yahoo_chart", side_effect=Exception("network"))
    def test_all_fail(self, mock_yahoo, mock_uw_cls, mock_read):
        sym, history, source = _fetch_stock_history_fallback("SPY", "2026-01-01", "2026-03-17")
        assert history == {}
        assert source == "none"


class TestFetchOptionHistorySafe:
    @patch("portfolio_performance.read_cache")
    def test_cache_hit(self, mock_read):
        mock_read.return_value = {"2026-02-15": 5.50}
        oid, history, warning = _fetch_option_history_safe("AAPL260321C00230000", "2026-01-01", "2026-03-17")
        assert history == {"2026-02-15": 5.50}
        assert warning is None

    @patch("portfolio_performance.write_cache")
    @patch("portfolio_performance.read_cache", return_value=None)
    @patch("portfolio_performance.UWClient")
    def test_uw_success(self, mock_uw_cls, mock_read, mock_write):
        mock_uw = MagicMock()
        mock_uw.get_option_contract_historic.return_value = {
            "chains": [{"date": "2026-02-15", "nbbo_bid": 5.0, "nbbo_ask": 6.0}]
        }
        mock_uw_cls.return_value = mock_uw

        oid, history, warning = _fetch_option_history_safe("AAPL260321C00230000", "2026-01-01", "2026-03-17")
        assert history == {"2026-02-15": 5.5}
        assert warning is None

    @patch("portfolio_performance.read_cache", return_value=None)
    @patch("portfolio_performance.UWClient")
    def test_rate_limit_skips(self, mock_uw_cls, mock_read):
        from clients.uw_client import UWRateLimitError
        mock_uw = MagicMock()
        mock_uw.get_option_contract_historic.side_effect = UWRateLimitError("429")
        mock_uw_cls.return_value = mock_uw

        oid, history, warning = _fetch_option_history_safe("AAPL260321C00230000", "2026-01-01", "2026-03-17")
        assert history == {}
        assert "Rate limited" in warning

    @patch("portfolio_performance.read_cache", return_value=None)
    @patch("portfolio_performance.UWClient")
    def test_generic_exception(self, mock_uw_cls, mock_read):
        mock_uw = MagicMock()
        mock_uw.get_option_contract_historic.side_effect = Exception("network error")
        mock_uw_cls.return_value = mock_uw

        oid, history, warning = _fetch_option_history_safe("AAPL260321C00230000", "2026-01-01", "2026-03-17")
        assert history == {}
        assert "unavailable" in warning


class TestFetchAllHistories:
    def _make_trade(self, security_type: str, symbol: str, option_id: str = None):
        return TradeFill(
            trade_date="2026-01-15",
            contract_key=option_id or f"STK:{symbol}",
            quantity=100,
            net_cash=-10000,
            multiplier=100 if security_type == "OPT" else 1,
            security_type=security_type,
            symbol=symbol,
            option_id=option_id,
        )

    @patch("portfolio_performance.prune_cache")
    @patch("portfolio_performance._fetch_option_history_safe")
    @patch("portfolio_performance._fetch_stock_history_ib_only")
    @patch("portfolio_performance.read_cache", return_value=None)
    @patch("portfolio_performance.write_cache")
    def test_parallel_execution(self, mock_wc, mock_read, mock_ib, mock_opt, mock_prune):
        mock_ib.return_value = ("AAPL", {"2026-01-15": 230.0})
        mock_opt.return_value = ("AAPL260321C00230000", {"2026-02-15": 5.5}, None)

        trades = [
            self._make_trade("STK", "AAPL"),
            self._make_trade("OPT", "AAPL", "AAPL260321C00230000"),
        ]
        warnings: list = []

        marks, missing = _fetch_all_histories(trades, "2026-01-01", "2026-03-17", MagicMock(), warnings)

        assert "STK:AAPL" in marks
        assert "AAPL260321C00230000" in marks
        assert len(missing) == 0

    @patch("portfolio_performance.prune_cache")
    @patch("portfolio_performance._fetch_stock_history_fallback")
    @patch("portfolio_performance._fetch_stock_history_ib_only")
    @patch("portfolio_performance.read_cache", return_value=None)
    @patch("portfolio_performance.write_cache")
    def test_ib_fail_triggers_fallback(self, mock_wc, mock_read, mock_ib, mock_fallback, mock_prune):
        mock_ib.return_value = ("AAPL", {})  # IB failure
        mock_fallback.return_value = ("AAPL", {"2026-01-15": 230.0}, "uw")

        trades = [self._make_trade("STK", "AAPL")]
        warnings: list = []

        marks, missing = _fetch_all_histories(trades, "2026-01-01", "2026-03-17", MagicMock(), warnings)

        assert "STK:AAPL" in marks
        mock_fallback.assert_called_once()

    @patch("portfolio_performance.prune_cache")
    @patch("portfolio_performance._fetch_option_history_safe")
    @patch("portfolio_performance.read_cache", return_value=None)
    def test_option_warning_propagated(self, mock_read, mock_opt, mock_prune):
        mock_opt.return_value = ("OPT123", {}, "Rate limited fetching OPT123 — skipped")

        trades = [self._make_trade("OPT", "AAPL", "OPT123")]
        warnings: list = []

        marks, missing = _fetch_all_histories(trades, "2026-01-01", "2026-03-17", None, warnings)

        assert "OPT123" in missing
        assert any("Rate limited" in w for w in warnings)

    @patch("portfolio_performance.prune_cache")
    @patch("portfolio_performance._fetch_stock_history_fallback")
    @patch("portfolio_performance.read_cache", return_value=None)
    def test_no_ib_client(self, mock_read, mock_fb, mock_prune):
        mock_fb.return_value = ("AAPL", {"2026-01-15": 230.0}, "yahoo")
        trades = [self._make_trade("STK", "AAPL")]
        warnings: list = []

        marks, missing = _fetch_all_histories(trades, "2026-01-01", "2026-03-17", None, warnings)

        assert "STK:AAPL" in marks

    @patch("portfolio_performance.read_cache")
    def test_cache_hit_skips_ib(self, mock_read):
        mock_read.return_value = {"2026-01-15": 230.0}

        trades = [self._make_trade("STK", "AAPL")]
        ib_client = MagicMock()
        warnings: list = []

        marks, missing = _fetch_all_histories(trades, "2026-01-01", "2026-03-17", ib_client, warnings)

        assert "STK:AAPL" in marks
        ib_client.get_historical_data.assert_not_called()
