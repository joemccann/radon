"""REL-068 tranche C — R-189, R-190.

Two ways the delta's indicator math loses or fabricates provenance: a NYSE
generated index whose bid/ask is read as a quote in one function and as two
unrelated counts in another, and a per-leg source map collapsed to a set-join
before it reaches the payload.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------
# R-189 — bid/ask on AD-NYSE / VOL-NYSE is not a quote
# --------------------------------------------------------------------------
class _Ticker(dict):
    pass


class TestGeneratedIndexBidAsk:
    def test_two_different_counts_are_not_a_midpoint(self):
        import fetch_trin as mod

        # AD-NYSE: bid=advancers, ask=decliners — `_bid_ask` says so itself.
        # Their average is not a value of anything.
        assert mod.extract_index_value({"bid": 1800.0, "ask": 900.0}) is None

    def test_one_value_published_in_both_fields_is_still_read(self):
        import fetch_trin as mod

        # The AD-NYSE precedent the docstring cites: a generated index that
        # carries its single value in bid AND ask.
        assert mod.extract_index_value({"bid": 1.05, "ask": 1.05}) == 1.05

    def test_last_and_close_still_win(self):
        import fetch_trin as mod

        assert mod.extract_index_value({"last": 0.9, "bid": 5.0, "ask": 6.0}) == 0.9
        assert mod.extract_index_value({"close": 1.1, "bid": 5.0, "ask": 6.0}) == 1.1

    def test_the_counts_themselves_are_still_extracted(self):
        import fetch_trin as mod

        adv, dec = mod._bid_ask({"bid": 1800.0, "ask": 900.0})
        assert (adv, dec) == (1800.0, 900.0)

    def test_a_counts_only_ticker_is_still_accepted_as_priced(self):
        """The readiness check for AD/VOL must not depend on the midpoint —
        those tickers legitimately answer with two different counts."""
        import fetch_trin as mod

        assert mod.index_has_data({"bid": 1800.0, "ask": 900.0}) is True
        assert mod.index_has_data({"bid": None, "ask": None}) is False
        assert mod.index_has_data(None) is False

    def test_a_sample_built_from_counts_keeps_them_distinct(self):
        import fetch_trin as mod
        from datetime import datetime, timezone

        sample = mod._build_sample(
            1.05,
            {"bid": 1800.0, "ask": 900.0},
            {"bid": 5.0e8, "ask": 2.0e8},
            datetime(2026, 8, 21, 18, 0, tzinfo=timezone.utc),
        )
        assert sample["adv"] == 1800
        assert sample["dec"] == 900
        assert sample["up_vol"] == 5.0e8
        assert sample["down_vol"] == 2.0e8


# --------------------------------------------------------------------------
# R-190 — per-leg provenance survives to the payload
# --------------------------------------------------------------------------
class TestPerLegProvenance:
    def test_credit_spread_publishes_the_per_ticker_sources(self):
        import fetch_credit_spread as mod

        payload = mod.build_output(
            [{"date": "2026-08-21", "hyg_close": 80.0, "spx_close": 5000.0}],
            source="ib+yahoo",
            source_by_ticker={"HYG": "ib", "SPX": "yahoo"},
        )
        assert payload["source_by_ticker"] == {"HYG": "ib", "SPX": "yahoo"}
        assert payload["source"] == "ib+yahoo"

    def test_iei_hyg_publishes_the_per_ticker_sources(self):
        import fetch_iei_hyg as mod

        payload = mod.build_output(
            [{
                "date": "2026-08-21", "iei_close": 100.0, "hyg_close": 80.0,
                "dxy_close": None, "ratio": 1.25,
            }],
            source="ib+yahoo",
            source_by_ticker={"IEI": "ib", "HYG": "yahoo"},
        )
        assert payload["source_by_ticker"] == {"IEI": "ib", "HYG": "yahoo"}

    def test_an_all_ib_run_is_distinguishable_from_a_mixed_one(self):
        import fetch_credit_spread as mod

        rows = [{"date": "2026-08-21", "hyg_close": 80.0, "spx_close": 5000.0}]
        pure = mod.build_output(rows, source="ib", source_by_ticker={"HYG": "ib", "SPX": "ib"})
        mixed = mod.build_output(
            rows, source="ib+yahoo", source_by_ticker={"HYG": "ib", "SPX": "yahoo"}
        )
        assert pure["source_by_ticker"] != mixed["source_by_ticker"]

    def test_the_iei_cascade_returns_the_map_alongside_the_collapsed_string(self):
        import fetch_iei_hyg as mod

        closes, source, by_ticker = mod.fetch_closes(
            ["IEI", "HYG"],
            fetch_ib=lambda t: {"IEI": {"2026-08-21": 100.0}},
            fetch_uw=lambda t: {},
            fetch_yahoo=lambda t: {"HYG": {"2026-08-21": 80.0}},
        )
        assert source == "ib+yahoo"
        assert by_ticker == {"IEI": "ib", "HYG": "yahoo"}

    def test_the_map_is_absent_not_fabricated_when_nothing_was_fetched(self):
        import fetch_credit_spread as mod

        payload = mod.build_output([], source="none", source_by_ticker={})
        assert payload["source_by_ticker"] == {}
        assert payload["source"] == "none"

    def test_the_default_stays_backwards_compatible(self):
        import fetch_credit_spread as mod

        payload = mod.build_output([], source="ib")
        assert payload["source"] == "ib"
        assert payload.get("source_by_ticker") == {}
