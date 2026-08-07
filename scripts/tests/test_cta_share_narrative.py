#!/usr/bin/env python3
"""CTA share narrative must be DERIVED from the payload, never asserted.

2026-08-07 bug: the X share published a frozen short-squeeze story from a
prior session. Live data had CTAs at their **maximum LONG** (SPX +3.69,
z +3.68, 100th percentile, up from +1.75 a month earlier), but the card
rendered:

  * "CTAs flipped from +1.75 long -> +3.69 short"  (literal word "short")
  * "MAX SHORT (NOW)" with the squeeze-meter marker hardcoded at left:3%
  * "$90.6B forced selling pipeline" while `cta_model` was null (fabricated)
  * "SQUEEZE RISK: HIGH" as a hardcoded literal

and the tweet fell through to "No extreme positioning. CTAs are adjusting
normally" because every branch keyed on NEGATIVE z only (z <= -1.5).

The two outputs contradicted each other and both misstated the direction.
These tests pin the direction-aware contract for both.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import generate_cta_share as gcs  # noqa: E402


def row(underlying: str, today: float, ago: float, z: float, pctile: float) -> dict:
    return {
        "underlying": underlying,
        "position_today": today,
        "position_1m_ago": ago,
        "z_score_3m": z,
        "percentile_3m": pctile,
    }


def payload(main_rows: list, **tables) -> dict:
    return {
        "date": "2026-08-07",
        "tables": {
            "main": main_rows,
            "index": tables.get("index", []),
            "commodity": tables.get("commodity", []),
            "currency": tables.get("currency", []),
        },
    }


# The live 2026-08-07 rows that exposed the bug.
MAX_LONG = payload([
    row("E-Mini S&P 500 Index", 3.69, 1.75, 3.68, 100),
    row("CME Nasdaq 100 Index", 2.59, 2.16, 0.75, 78),
])
MAX_SHORT = payload([
    row("E-Mini S&P 500 Index", -3.10, 1.75, -2.90, 1),
    row("CME Nasdaq 100 Index", -2.40, 0.90, -2.10, 3),
])
NEUTRAL = payload([
    row("E-Mini S&P 500 Index", 0.30, 0.10, 0.20, 52),
    row("CME Nasdaq 100 Index", -0.10, 0.20, -0.15, 47),
])


class TestAssessPositioning:
    """A single derivation both the card and the tweet consume."""

    def test_positive_extreme_is_long_not_short(self):
        a = gcs.assess_positioning(MAX_LONG["tables"]["main"][0])
        assert a["side"] == "long"
        assert a["is_extreme"] is True

    def test_negative_extreme_is_short(self):
        a = gcs.assess_positioning(MAX_SHORT["tables"]["main"][0])
        assert a["side"] == "short"
        assert a["is_extreme"] is True

    def test_middling_positioning_is_not_extreme(self):
        a = gcs.assess_positioning(NEUTRAL["tables"]["main"][0])
        assert a["is_extreme"] is False

    def test_max_long_risk_is_forced_selling_not_a_short_squeeze(self):
        a = gcs.assess_positioning(MAX_LONG["tables"]["main"][0])
        assert a["risk_direction"] == "downside"

    def test_max_short_risk_is_upside_covering(self):
        a = gcs.assess_positioning(MAX_SHORT["tables"]["main"][0])
        assert a["risk_direction"] == "upside"

    def test_meter_marker_tracks_percentile(self):
        # 100th pctile pins the marker to the LONG (right) end; 1st to the left.
        assert gcs.assess_positioning(MAX_LONG["tables"]["main"][0])["meter_pct"] > 90
        assert gcs.assess_positioning(MAX_SHORT["tables"]["main"][0])["meter_pct"] < 10

    def test_percentile_accepts_both_0_1_and_0_100_encodings(self):
        as_int = gcs.assess_positioning(row("X", 3.69, 1.75, 3.68, 100))
        as_frac = gcs.assess_positioning(row("X", 3.69, 1.75, 3.68, 1.0))
        assert as_int["pctile"] == 100
        assert as_frac["pctile"] == 100

    def test_direction_of_travel_is_derived_not_assumed(self):
        a = gcs.assess_positioning(MAX_LONG["tables"]["main"][0])
        # +1.75 -> +3.69 is an EXTENSION of a long, not a flip.
        assert a["flipped"] is False
        b = gcs.assess_positioning(MAX_SHORT["tables"]["main"][0])
        assert b["flipped"] is True


class TestCardNarrative:
    """card1 must not assert a short story over long data."""

    def test_max_long_card_never_calls_the_position_short(self):
        html = gcs.card1_squeeze(MAX_LONG, "2026-08-07")
        assert "+3.69 short" not in html
        assert "MAX SHORT (NOW)" not in html

    def test_max_long_card_marks_the_long_end_of_the_meter(self):
        html = gcs.card1_squeeze(MAX_LONG, "2026-08-07")
        assert "MAX LONG (NOW)" in html
        # The old bug hardcoded the marker at the short end.
        assert "left:3%" not in html.replace(" ", "")

    def test_max_short_card_still_reads_as_a_squeeze(self):
        html = gcs.card1_squeeze(MAX_SHORT, "2026-08-07")
        assert "MAX SHORT (NOW)" in html

    def test_no_fabricated_selling_figure_when_the_model_is_absent(self):
        # cta_model is null in the live payload; $90.6B was a hardcoded literal.
        html = gcs.card1_squeeze(MAX_LONG, "2026-08-07")
        assert "90.6" not in html
        assert "$90B" not in html

    def test_publishes_the_figure_when_the_model_actually_supplies_it(self):
        with_model = dict(MAX_LONG, cta_model={"est_selling_bn": 42.5})
        html = gcs.card1_squeeze(with_model, "2026-08-07")
        assert "42.5" in html

    def test_headline_reflects_the_measured_state(self):
        long_html = gcs.card1_squeeze(MAX_LONG, "2026-08-07")
        short_html = gcs.card1_squeeze(MAX_SHORT, "2026-08-07")
        assert long_html != short_html
        # "The Coil Is Set" is a short-squeeze headline; it must not head a max long.
        assert "The Coil Is Set" not in long_html

    def test_risk_tile_is_derived_not_hardcoded_high(self):
        neutral_html = gcs.card1_squeeze(NEUTRAL, "2026-08-07")
        assert ">HIGH<" not in neutral_html


class TestTweetNarrative:
    """build_tweet must recognise a positive extreme."""

    def test_max_long_is_not_described_as_no_extreme_positioning(self):
        tweet = gcs.build_tweet(MAX_LONG, "2026-08-07")
        assert "No extreme positioning" not in tweet
        assert "adjusting normally" not in tweet

    def test_max_long_tweet_names_the_long_side_and_the_percentile(self):
        tweet = gcs.build_tweet(MAX_LONG, "2026-08-07")
        assert "long" in tweet.lower()
        assert "100th" in tweet

    def test_max_short_tweet_still_reads_short(self):
        tweet = gcs.build_tweet(MAX_SHORT, "2026-08-07")
        assert "short" in tweet.lower()

    def test_neutral_payload_keeps_the_measured_calm_language(self):
        tweet = gcs.build_tweet(NEUTRAL, "2026-08-07")
        assert "No extreme positioning" in tweet

    def test_tweet_and_card_agree_on_direction(self):
        tweet = gcs.build_tweet(MAX_LONG, "2026-08-07").lower()
        card = gcs.card1_squeeze(MAX_LONG, "2026-08-07")
        # The card says MAX LONG; the tweet must not claim a short.
        assert "MAX LONG (NOW)" in card
        assert "cta equity positioning is at max short" not in tweet

    def test_no_em_dashes_in_published_copy(self):
        # Mandatory rule 6: no em dashes in user-facing copy.
        for data in (MAX_LONG, MAX_SHORT, NEUTRAL):
            assert "—" not in gcs.build_tweet(data, "2026-08-07")

    def test_tweet_fits_in_a_post(self):
        for data in (MAX_LONG, MAX_SHORT, NEUTRAL):
            assert len(gcs.build_tweet(data, "2026-08-07")) <= 4000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
