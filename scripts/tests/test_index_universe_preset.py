"""Virtual `indexes` preset: Nasdaq-100 ∪ S&P 500 ∪ Russell 2000.

`load_preset('indexes')` is a virtual union of the file-backed ndx100 /
sp500 / r2k presets. There is no committed indexes.json.
"""
from __future__ import annotations

import pytest

from utils.presets import PRESETS_DIR, load_preset

INDEX_SLUGS = ("ndx100", "sp500", "r2k")
FLOORS = {
    "ndx100": (80, 20),
    "sp500": (400, 200),
    "r2k": (1200, 500),
}


def _unique_tickers(*groups):
    seen = set()
    out = []
    for group in groups:
        for ticker in group:
            if ticker not in seen:
                seen.add(ticker)
                out.append(ticker)
    return out


def _unique_pairs(*groups):
    seen = set()
    out = []
    for group in groups:
        for pair in group:
            key = frozenset(pair)
            if key not in seen:
                seen.add(key)
                out.append(list(pair))
    return out


class TestFileBackedIndexPresets:
    def test_slugs_stay_file_backed_and_meet_floors(self):
        for slug, (ticker_floor, pair_floor) in FLOORS.items():
            preset = load_preset(slug)
            assert (PRESETS_DIR / f"{slug}.json").is_file()
            assert preset.ticker_count >= ticker_floor
            assert preset.pair_count >= pair_floor


class TestIndexesVirtualPreset:
    def test_no_committed_indexes_json(self):
        assert not (PRESETS_DIR / "indexes.json").exists()

    def test_load_preset_indexes_unions_three_files(self):
        ndx = load_preset("ndx100")
        spx = load_preset("sp500")
        r2k = load_preset("r2k")
        idx = load_preset("indexes")

        assert idx.name == "indexes"
        assert idx.tickers == _unique_tickers(ndx.tickers, spx.tickers, r2k.tickers)
        expected_pairs = _unique_pairs(ndx.pairs, spx.pairs, r2k.pairs)
        assert [frozenset(p) for p in idx.pairs] == [frozenset(p) for p in expected_pairs]
        assert idx.ticker_count >= 2000
        assert idx.pair_count >= 700
        assert idx.vol_driver
        assert "NVDA" in idx.tickers
        assert "AAPL" in idx.tickers

        desc = idx.description.lower()
        assert any(token in desc for token in ("nasdaq", "ndx", "nasdaq-100", "nasdaq 100"))
        assert any(token in desc for token in ("s&p", "sp500", "s&p 500", "sp 500"))
        assert any(token in desc for token in ("russell", "r2k", "russell 2000"))

    def test_tickers_are_order_preserving_unique(self):
        idx = load_preset("indexes")
        assert len(idx.tickers) == len(set(idx.tickers))

    def test_pairs_unique_by_unordered_membership(self):
        idx = load_preset("indexes")
        assert all(isinstance(pair, list) and len(pair) == 2 for pair in idx.pairs)
        keys = [frozenset(pair) for pair in idx.pairs]
        assert len(keys) == len(set(keys))

    def test_traversal_indexes_still_rejected(self):
        with pytest.raises(FileNotFoundError):
            load_preset("../indexes")
