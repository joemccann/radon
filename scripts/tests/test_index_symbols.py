"""Tests for scripts/utils/index_symbols.py + contract_resolver.py."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from utils.index_symbols import INDEX_SYMBOLS, index_exchange_for, is_index_symbol


class TestIsIndexSymbol:
    def test_canonical_indices_return_true(self):
        for symbol in ["VIX", "VXN", "VVIX", "SPX", "NDX", "RUT", "DJX"]:
            assert is_index_symbol(symbol) is True, f"{symbol} should be an index"

    def test_case_insensitive(self):
        assert is_index_symbol("vix") is True
        assert is_index_symbol("Spx") is True

    def test_equity_returns_false(self):
        for symbol in ["AAPL", "TSLA", "SPY", "QQQ"]:
            assert is_index_symbol(symbol) is False, f"{symbol} should NOT be an index"

    def test_none_or_empty(self):
        assert is_index_symbol(None) is False
        assert is_index_symbol("") is False


class TestIndexExchangeFor:
    def test_volatility_indices_on_cboe(self):
        assert index_exchange_for("VIX") == "CBOE"
        assert index_exchange_for("VXN") == "CBOE"
        assert index_exchange_for("VVIX") == "CBOE"
        assert index_exchange_for("COR1M") == "CBOE"

    def test_ndx_on_nasdaq(self):
        assert index_exchange_for("NDX") == "NASDAQ"

    def test_rut_on_russell(self):
        assert index_exchange_for("RUT") == "RUSSELL"

    def test_unknown_returns_none(self):
        assert index_exchange_for("AAPL") is None
        assert index_exchange_for(None) is None

    def test_case_normalised(self):
        assert index_exchange_for("vix") == "CBOE"


class TestIndexSymbolsTableShape:
    def test_regime_indices_present(self):
        """WorkspaceShell hardcodes VIX/VVIX/COR1M for the Regime tab —
        the table must include them so the /[ticker] page also handles them."""
        assert "VIX" in INDEX_SYMBOLS
        assert "VXN" in INDEX_SYMBOLS
        assert "VVIX" in INDEX_SYMBOLS
        assert "COR1M" in INDEX_SYMBOLS

    def test_every_value_is_a_known_exchange(self):
        valid_exchanges = {"CBOE", "NASDAQ", "NYSE", "RUSSELL"}
        for symbol, exchange in INDEX_SYMBOLS.items():
            assert exchange in valid_exchanges, (
                f"{symbol} maps to {exchange!r} which is not in the valid set"
            )


class TestContractResolver:
    def test_resolves_index_to_Index_contract(self):
        """VIX should construct an ib_insync Index, not a Stock."""
        try:
            from clients.contract_resolver import resolve_quote_contract
            from ib_insync import Index, Stock
        except ImportError:
            pytest.skip("ib_insync not installed in this env")
        c = resolve_quote_contract("VIX")
        assert isinstance(c, Index), f"VIX must resolve to Index, got {type(c).__name__}"
        assert c.symbol == "VIX"
        assert c.exchange == "CBOE"
        assert c.currency == "USD"

    def test_resolves_equity_to_Stock_contract(self):
        try:
            from clients.contract_resolver import resolve_quote_contract
            from ib_insync import Stock
        except ImportError:
            pytest.skip("ib_insync not installed in this env")
        c = resolve_quote_contract("AAPL")
        assert isinstance(c, Stock)
        assert c.symbol == "AAPL"
        assert c.exchange == "SMART"

    def test_empty_symbol_raises(self):
        try:
            from clients.contract_resolver import resolve_quote_contract
        except ImportError:
            pytest.skip("ib_insync not installed in this env")
        with pytest.raises(ValueError):
            resolve_quote_contract("")
        with pytest.raises(ValueError):
            resolve_quote_contract("   ")

    def test_lowercase_symbol_normalised(self):
        try:
            from clients.contract_resolver import resolve_quote_contract
            from ib_insync import Index
        except ImportError:
            pytest.skip("ib_insync not installed in this env")
        c = resolve_quote_contract("vix")
        assert isinstance(c, Index)
        assert c.symbol == "VIX"
