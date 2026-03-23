"""Tests for pure functions in ib_orders.py and ib_sync.py."""
import math
import pytest
from unittest.mock import MagicMock

from ib_orders import (
    safe_float,
    format_contract,
    serialize_contract,
    build_orders_data,
    IB_SENTINEL,
)
from ib_sync import (
    detect_structure_type,
    format_structure_description,
    _normalize_market_price,
    _resolve_market_price,
)


# ── safe_float ──────────────────────────────────────────────────────

class TestSafeFloat:
    def test_none_returns_none(self):
        assert safe_float(None) is None

    def test_nan_returns_none(self):
        assert safe_float(float('nan')) is None

    def test_inf_returns_none(self):
        assert safe_float(float('inf')) is None

    def test_neg_inf_returns_none(self):
        assert safe_float(float('-inf')) is None

    def test_sentinel_returns_none(self):
        assert safe_float(IB_SENTINEL) is None

    def test_normal_value_rounded(self):
        assert safe_float(1.23456789) == 1.2346

    def test_zero_returns_zero(self):
        assert safe_float(0) == 0.0

    def test_string_returns_none(self):
        assert safe_float("not a number") is None

    def test_negative_value(self):
        assert safe_float(-5.5) == -5.5

    def test_large_but_valid(self):
        result = safe_float(999999.99)
        assert result == 999999.99


# ── format_contract ─────────────────────────────────────────────────

class TestFormatContract:
    def _mock_contract(self, **kwargs):
        c = MagicMock()
        c.symbol = kwargs.get("symbol", "AAPL")
        c.secType = kwargs.get("secType", "STK")
        c.right = kwargs.get("right", "")
        c.strike = kwargs.get("strike", 0)
        c.lastTradeDateOrContractMonth = kwargs.get("expiry", "")
        return c

    def test_stock_format(self):
        c = self._mock_contract(symbol="AAPL", secType="STK")
        assert format_contract(c) == "AAPL"

    def test_call_option_format(self):
        c = self._mock_contract(symbol="AAPL", secType="OPT", right="C", strike=200)
        assert format_contract(c) == "AAPL C200"

    def test_put_option_format(self):
        c = self._mock_contract(symbol="AAPL", secType="OPT", right="P", strike=180.5)
        assert format_contract(c) == "AAPL P180.5"

    def test_option_int_strike(self):
        c = self._mock_contract(symbol="NVDA", secType="OPT", right="C", strike=800.0)
        assert format_contract(c) == "NVDA C800"

    def test_futures_format(self):
        c = self._mock_contract(symbol="ES", secType="FUT", expiry="20260320")
        assert format_contract(c) == "ES 20260320"

    def test_unknown_sectype(self):
        c = self._mock_contract(symbol="XYZ", secType="WAR")
        assert format_contract(c) == "XYZ (WAR)"


# ── serialize_contract ──────────────────────────────────────────────

class TestSerializeContract:
    def test_option_serialization(self):
        c = MagicMock()
        c.symbol = "AAPL"
        c.secType = "OPT"
        c.strike = 200
        c.right = "C"
        c.lastTradeDateOrContractMonth = "20260320"
        result = serialize_contract(c)
        assert result["symbol"] == "AAPL"
        assert result["secType"] == "OPT"
        assert result["strike"] == 200
        assert result["right"] == "C"
        assert result["expiry"] == "2026-03-20"

    def test_stock_serialization(self):
        c = MagicMock()
        c.symbol = "AAPL"
        c.secType = "STK"
        c.strike = None
        c.right = None
        c.lastTradeDateOrContractMonth = ""
        result = serialize_contract(c)
        assert result["expiry"] is None


# ── build_orders_data ───────────────────────────────────────────────

class TestBuildOrdersData:
    def test_structure(self):
        result = build_orders_data(
            open_orders=[{"orderId": 1}],
            executed_orders=[{"execId": "a"}, {"execId": "b"}],
        )
        assert result["open_count"] == 1
        assert result["executed_count"] == 2
        assert "last_sync" in result

    def test_empty_orders(self):
        result = build_orders_data([], [])
        assert result["open_count"] == 0
        assert result["executed_count"] == 0


# ── detect_structure_type ───────────────────────────────────────────

class TestDetectStructureType:
    def test_single_stock(self):
        legs = [{"secType": "STK", "position": 100}]
        name, risk = detect_structure_type(legs)
        assert name == "Stock"
        assert risk == "equity"

    def test_single_long_call(self):
        legs = [{"secType": "OPT", "position": 1, "right": "C", "strike": 200}]
        name, risk = detect_structure_type(legs)
        assert name == "Long Call"
        assert risk == "defined"

    def test_single_short_put(self):
        """Short Put is undefined risk — shows in undefined risk table"""
        legs = [{"secType": "OPT", "position": -1, "right": "P", "strike": 180}]
        name, risk = detect_structure_type(legs)
        assert name == "Short Put"
        assert risk == "undefined"

    def test_single_short_call_is_undefined(self):
        legs = [{"secType": "OPT", "position": -1, "right": "C", "strike": 200}]
        name, risk = detect_structure_type(legs)
        assert name == "Short Call"
        assert risk == "undefined"

    def test_single_long_put_is_defined(self):
        legs = [{"secType": "OPT", "position": 1, "right": "P", "strike": 100}]
        name, risk = detect_structure_type(legs)
        assert name == "Long Put"
        assert risk == "defined"

    def test_bull_call_spread(self):
        legs = [
            {"secType": "OPT", "position": 1, "right": "C", "strike": 200},
            {"secType": "OPT", "position": -1, "right": "C", "strike": 210},
        ]
        name, risk = detect_structure_type(legs)
        assert name == "Bull Call Spread"
        assert risk == "defined"

    def test_bear_put_spread(self):
        legs = [
            {"secType": "OPT", "position": 1, "right": "P", "strike": 200},
            {"secType": "OPT", "position": -1, "right": "P", "strike": 190},
        ]
        name, risk = detect_structure_type(legs)
        assert name == "Bear Put Spread"
        assert risk == "defined"

    def test_risk_reversal(self):
        legs = [
            {"secType": "OPT", "position": -1, "right": "P", "strike": 180},
            {"secType": "OPT", "position": 1, "right": "C", "strike": 220},
        ]
        name, risk = detect_structure_type(legs)
        assert name == "Risk Reversal"
        assert risk == "undefined"

    def test_straddle(self):
        legs = [
            {"secType": "OPT", "position": 1, "right": "P", "strike": 200},
            {"secType": "OPT", "position": 1, "right": "C", "strike": 200},
        ]
        name, risk = detect_structure_type(legs)
        assert name == "Straddle"

    def test_strangle(self):
        legs = [
            {"secType": "OPT", "position": 1, "right": "P", "strike": 190},
            {"secType": "OPT", "position": 1, "right": "C", "strike": 210},
        ]
        name, risk = detect_structure_type(legs)
        assert name == "Strangle"


# ── format_structure_description ────────────────────────────────────

class TestFormatStructureDescription:
    def test_spread_with_strikes(self):
        legs = [
            {"secType": "OPT", "right": "C", "strike": 200, "structure": ""},
            {"secType": "OPT", "right": "C", "strike": 210, "structure": ""},
        ]
        result = format_structure_description("Bull Call Spread", legs)
        assert "$200/$210" in result

    def test_risk_reversal_with_strikes(self):
        legs = [
            {"secType": "OPT", "right": "P", "strike": 180, "structure": ""},
            {"secType": "OPT", "right": "C", "strike": 220, "structure": ""},
        ]
        result = format_structure_description("Risk Reversal", legs)
        assert "P$180" in result
        assert "C$220" in result

    def test_straddle_single_strike(self):
        legs = [
            {"secType": "OPT", "right": "P", "strike": 200, "structure": ""},
            {"secType": "OPT", "right": "C", "strike": 200, "structure": ""},
        ]
        result = format_structure_description("Straddle", legs)
        assert "$200" in result

    def test_single_leg_short_put_includes_strike(self):
        legs = [{"secType": "OPT", "right": "P", "strike": 85, "structure": ""}]
        result = format_structure_description("Short Put", legs)
        assert result == "Short Put $85"

    def test_single_leg_short_call_includes_strike(self):
        legs = [{"secType": "OPT", "right": "C", "strike": 150, "structure": ""}]
        result = format_structure_description("Short Call", legs)
        assert result == "Short Call $150"

    def test_single_leg_long_put_includes_strike(self):
        legs = [{"secType": "OPT", "right": "P", "strike": 200, "structure": ""}]
        result = format_structure_description("Long Put", legs)
        assert result == "Long Put $200"

    def test_single_leg_long_call_includes_strike(self):
        legs = [{"secType": "OPT", "right": "C", "strike": 300, "structure": ""}]
        result = format_structure_description("Long Call", legs)
        assert result == "Long Call $300"

    def test_stock_returns_structure_field(self):
        legs = [{"secType": "STK", "structure": "Stock (100 shares)"}]
        result = format_structure_description("Stock", legs)
        assert result == "Stock (100 shares)"


# ── _normalize_market_price ─────────────────────────────────────────

class TestNormalizeMarketPrice:
    def test_none_returns_none(self):
        assert _normalize_market_price(None) is None

    def test_nan_returns_none(self):
        assert _normalize_market_price(float('nan')) is None

    def test_negative_returns_none(self):
        assert _normalize_market_price(-1.0) is None

    def test_valid_price(self):
        assert _normalize_market_price(150.5) == 150.5

    def test_zero_returns_zero(self):
        assert _normalize_market_price(0.0) == 0.0


# ── _resolve_market_price ───────────────────────────────────────────

class TestResolveMarketPrice:
    def test_uses_market_price_when_available(self):
        price, is_calc = _resolve_market_price(150.0, 149.0, 151.0)
        assert price == 150.0
        assert is_calc is False

    def test_falls_back_to_midpoint(self):
        price, is_calc = _resolve_market_price(None, 149.0, 151.0)
        assert price == 150.0
        assert is_calc is True

    def test_none_when_no_data(self):
        price, is_calc = _resolve_market_price(None, None, None)
        assert price is None
        assert is_calc is False
