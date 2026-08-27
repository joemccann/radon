#!/usr/bin/env python3
"""R-291 / R-292 / R-293 / R-298 (REL-100): a nulled percentile stays null.

REL-099 nulls a `percentile_3m` its own z-score contradicts, and nulls one no
z-score can verify. That is only worth doing if the null survives to the
reader. Every consumer turned it straight back into a number:

  * `normalize_pctile(None)` returned 50, so a deliberately-unknown position
    was published as "the 50th percentile" — a confident neutral reading of a
    value the pipeline had just refused to stand behind.
  * `r.get("percentile_3m", 50)` supplied the same 50 when the key was absent.
  * `cri_scan` compared `None < 25` and raised TypeError.
  * `generate_regime_share` defaulted a missing percentile to 0 — max short.

Fixed as ONE class: a fix on one side leaves the defect live on the others.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from utils.cta_percentiles import normalize_pctile  # noqa: E402


class TestNormalizePctile:
    @pytest.mark.parametrize("raw", [None, "x", "", [], {}, True, False])
    def test_a_non_numeric_percentile_is_unknown_not_the_median(self, raw):
        assert normalize_pctile(raw) is None, (
            "an unverifiable percentile must not be published as the 50th"
        )

    @pytest.mark.parametrize("raw,expected", [(0.9, 90), (90, 90), (0, 0), (1, 1), (1.0, 100)])
    def test_real_values_are_unchanged(self, raw, expected):
        assert normalize_pctile(raw) == expected


class TestCtaShareConsumers:
    def _row(self, pctile):
        return {
            "underlying": "E-Mini S&P 500 Index",
            "position_today": -2.4,
            "position_1m_ago": 1.1,
            "percentile_3m": pctile,
            "percentile_1y": pctile,
            "z_score_3m": -2.1,
        }

    def test_assess_positioning_does_not_invent_a_median(self):
        from generate_cta_share import assess_positioning

        a = assess_positioning(self._row(None))
        assert a["pctile"] is None, "a nulled percentile came back as a number"

    def test_a_nulled_percentile_still_reads_as_extreme_on_its_z_score(self):
        from generate_cta_share import assess_positioning

        # z = -2.1 is extreme on its own; losing the percentile must not
        # downgrade the severity to NORMAL.
        a = assess_positioning(self._row(None))
        assert a["is_extreme"] is True
        assert a["severity"] in {"HIGH", "ELEVATED"}

    def test_the_tweet_body_never_says_the_50th_percentile(self):
        from generate_cta_share import build_tweet

        data = {
            "date": "2026-08-27",
            "tables": {
                "main": [self._row(None)],
                "index": [self._row(None)],
                "commodity": [],
            },
        }
        body = build_tweet(data, "2026-08-27")
        assert "50th percentile" not in body, body

    def test_an_unknown_percentile_renders_as_a_dash(self):
        from generate_cta_share import pctile_label

        assert pctile_label(None) == "---"

    def test_a_nulled_row_is_not_counted_as_an_extreme_index_short(self):
        from generate_cta_share import build_llm_facts

        payload = build_llm_facts(
            {
                "date": "2026-08-27",
                "tables": {
                    "main": [self._row(None)],
                    "index": [self._row(None)],
                    "commodity": [self._row(None)],
                },
            },
            "2026-08-27",
        )
        crowded = payload.get("crowded_commodity_longs", [])
        assert all(c["percentile_3m"] is not None for c in crowded), (
            "a row with no percentile was published as a crowded long"
        )


class TestCriScanDoesNotCrash:
    """`pctl_3m < 25` on a None is a TypeError that takes the whole scan down."""

    def test_a_nulled_percentile_gets_a_neutral_tone_instead_of_raising(self):
        import cri_scan

        assert cri_scan._pctile_tone_class(None) == "text-muted"

    @pytest.mark.parametrize("p,expected", [
        (10, "text-negative"), (50, "text-warning"), (90, "text-positive"),
    ])
    def test_real_percentiles_keep_their_tone(self, p, expected):
        import cri_scan

        assert cri_scan._pctile_tone_class(p) == expected

    def test_the_report_path_normalizes_before_comparing(self):
        """The call site must go through `normalize_pctile`, not a `, 50` default."""
        src = (SCRIPTS_DIR / "cri_scan.py").read_text()
        body = "\n".join(
            line for line in src.split("\n") if not line.strip().startswith("#")
        )
        assert 'spx.get("percentile_3m", 50)' not in body
        assert "_pctile_tone_class(pctl_3m)" in body
