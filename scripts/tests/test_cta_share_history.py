"""The share copy must be UNIQUE per post, not merely data-driven.

Commit 6c410697 made the CTA X copy derive from the payload instead of
asserting a frozen story. It did not make it distinct: two different sessions
inside the same "max long" regime produced 3 of 4 lines byte-identical,
because every branch is a fixed string with numbers interpolated.

These tests pin the differentiating facts, all of which change day to day even
when the regime does not:

  * WHICH instruments crossed into / out of an extreme since the prior read
  * WHAT moved on SPX versus that read (position, z, percentile)
  * HOW LONG the current regime has run

Every one of them is measured against a payload that actually exists (the
dated Turso rows / disk cache). With no prior payload the sentences are
omitted entirely: a delta is never invented.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import generate_cta_share as gcs  # noqa: E402
from utils import cta_history  # noqa: E402


def row(underlying: str, today: float, ago: float, z: float, pctile) -> dict:
    return {
        "underlying": underlying,
        "position_today": today,
        "position_1m_ago": ago,
        "z_score_3m": z,
        "percentile_3m": pctile,
        "percentile_1y": pctile,
    }


def payload(date: str, main: list, **tables) -> dict:
    return {
        "date": date,
        "tables": {
            "main": main,
            "index": tables.get("index", []),
            "commodity": tables.get("commodity", []),
            "currency": tables.get("currency", []),
        },
    }


def spx(today: float, ago: float, z: float, pctile) -> dict:
    return row("E-Mini S&P 500 Index", today, ago, z, pctile)


# Two sessions inside the SAME max-long regime. Same branch, same shape.
DAY_1 = payload(
    "2026-08-06",
    [spx(3.64, 1.70, 3.40, 97), row("CME Nasdaq 100 Index", 2.59, 2.16, 0.75, 78)],
    index=[row("DAX Index", 2.90, 1.10, 2.10, 96), row("ICE MSCI World Index", 0.40, 0.20, 0.30, 60)],
    commodity=[row("Palladium", -0.90, -2.55, 1.15, 86), row("Brent", -1.12, 0.51, -2.66, 3)],
    currency=[row("Euro", 0.28, -2.20, 1.20, 74)],
)
DAY_2 = payload(
    "2026-08-07",
    [spx(3.69, 1.75, 3.68, 100), row("CME Nasdaq 100 Index", 2.59, 2.16, 0.75, 78)],
    index=[row("DAX Index", 1.80, 1.10, 0.90, 71), row("ICE MSCI World Index", 0.40, 0.20, 0.30, 60)],
    commodity=[row("Palladium", -0.90, -2.55, 1.15, 86), row("Brent", -1.12, 0.51, -2.66, 3)],
    currency=[row("Euro", 0.55, -2.20, 1.84, 100)],
)
# Older max-long sessions, so DAY_2's regime run is measurably longer than DAY_1's.
DAY_0 = payload("2026-08-05", [spx(3.55, 1.60, 3.10, 95)])
DAY_MINUS_1 = payload("2026-08-04", [spx(0.20, 0.10, 0.15, 52)])  # regime break

DAY_1_PRIORS = [DAY_0, DAY_MINUS_1]
DAY_2_PRIORS = [DAY_1, DAY_0, DAY_MINUS_1]


def digitless_lines(text: str) -> set[str]:
    """Lines with every digit removed: what is left is the PROSE.

    Two posts whose only difference is interpolated numbers collapse to the
    same set here, which is exactly the failure mode under test.
    """
    return {re.sub(r"\d", "", line).strip() for line in text.splitlines() if line.strip()}


class TestPostUniqueness:
    def test_same_regime_days_differ_beyond_number_substitution(self):
        one = gcs.build_tweet(DAY_1, "2026-08-06", priors=DAY_1_PRIORS)
        two = gcs.build_tweet(DAY_2, "2026-08-07", priors=DAY_2_PRIORS)

        assert one != two
        # The real bar: strip the numbers and the prose must STILL differ.
        assert digitless_lines(one) != digitless_lines(two)

    def test_each_post_names_the_instruments_that_moved(self):
        two = gcs.build_tweet(DAY_2, "2026-08-07", priors=DAY_2_PRIORS)

        # Euro crossed INTO an extreme (74th -> 100th) between the two reads.
        assert "Euro" in two
        # DAX dropped OUT of one (96th -> 71st).
        assert "DAX" in two

    def test_a_crossing_named_on_one_day_is_absent_on_the_other(self):
        one = gcs.build_tweet(DAY_1, "2026-08-06", priors=DAY_1_PRIORS)
        two = gcs.build_tweet(DAY_2, "2026-08-07", priors=DAY_2_PRIORS)

        assert "Euro" not in one
        assert "Euro" in two

    def test_regime_persistence_is_counted_not_asserted(self):
        two = gcs.build_tweet(DAY_2, "2026-08-07", priors=DAY_2_PRIORS)
        # 2026-08-07, 08-06, 08-05 are max long; 08-04 breaks the run.
        assert "Session 3 of unbroken max long positioning" in two

    def test_spx_delta_is_measured_against_the_prior_read(self):
        two = gcs.build_tweet(DAY_2, "2026-08-07", priors=DAY_2_PRIORS)

        assert "2026-08-06" in two
        assert "+3.64" in two and "+3.69" in two
        assert "3.40" in two and "3.68" in two


class TestNoFabrication:
    def test_no_history_means_no_change_sentences(self):
        tweet = gcs.build_tweet(DAY_2, "2026-08-07")

        assert "Since the" not in tweet
        assert "Crossed INTO" not in tweet
        assert "Dropped OUT" not in tweet
        assert "Session" not in tweet

    def test_empty_history_matches_the_no_history_post(self):
        assert gcs.build_tweet(DAY_2, "2026-08-07", priors=[]) == gcs.build_tweet(
            DAY_2, "2026-08-07"
        )

    def test_unbounded_run_is_labelled_as_a_floor_not_a_count(self):
        """History that never breaks the regime cannot support "session N of"."""
        tweet = gcs.build_tweet(DAY_2, "2026-08-07", priors=[DAY_1, DAY_0])

        assert "Session 3 of unbroken" not in tweet
        assert "at least 3 straight sessions" in tweet

    def test_copy_stays_publishable(self):
        for data, priors in ((DAY_1, DAY_1_PRIORS), (DAY_2, DAY_2_PRIORS)):
            tweet = gcs.build_tweet(data, data["date"], priors=priors)
            assert "—" not in tweet  # mandatory rule 6: no em dashes
            assert len(tweet) <= 4000


class TestChangeContext:
    def _context(self, current, priors):
        return cta_history.derive_change_context(
            current,
            priors,
            assess=gcs.assess_positioning,
            find_spx=gcs.spx_row,
        )

    def test_crossings_are_split_by_direction(self):
        ctx = self._context(DAY_2, DAY_2_PRIORS)

        assert ctx["entered_extreme"] == ["Euro"]
        assert ctx["exited_extreme"] == ["DAX"]

    def test_regime_label_is_derived_from_the_assessment(self):
        assert cta_history.regime_label(
            DAY_2, assess=gcs.assess_positioning, find_spx=gcs.spx_row
        ) == "max long"
        assert cta_history.regime_label(
            DAY_MINUS_1, assess=gcs.assess_positioning, find_spx=gcs.spx_row
        ) == "in-range"

    def test_delta_is_none_without_a_prior_payload(self):
        ctx = self._context(DAY_2, [])

        assert ctx["prior_date"] is None
        assert ctx["spx_delta"] is None
        assert ctx["entered_extreme"] == []


class TestHistoryLoader:
    @pytest.fixture
    def cache_dir(self, tmp_path):
        cache = tmp_path / "menthorq_cache"
        cache.mkdir()
        for data in (DAY_MINUS_1, DAY_0, DAY_1, DAY_2):
            (cache / f"cta_{data['date']}.json").write_text(json.dumps(data))
        return cache

    def test_reads_prior_sessions_newest_first(self, cache_dir):
        priors = cta_history.load_prior_payloads("2026-08-07", cache_dir=cache_dir)

        assert [p["date"] for p in priors] == ["2026-08-06", "2026-08-05", "2026-08-04"]

    def test_excludes_the_current_session_and_anything_after_it(self, cache_dir):
        priors = cta_history.load_prior_payloads("2026-08-05", cache_dir=cache_dir)

        assert [p["date"] for p in priors] == ["2026-08-04"]

    def test_skips_structurally_empty_payloads(self, cache_dir):
        (cache_dir / "cta_2026-08-06.json").write_text(
            json.dumps({"date": "2026-08-06", "tables": {"main": []}})
        )
        priors = cta_history.load_prior_payloads("2026-08-07", cache_dir=cache_dir)

        assert [p["date"] for p in priors] == ["2026-08-05", "2026-08-04"]

    def test_respects_the_lookback_bound(self, cache_dir):
        priors = cta_history.load_prior_payloads("2026-08-07", cache_dir=cache_dir, limit=2)

        assert len(priors) == 2

    def test_missing_cache_dir_is_not_an_error(self, tmp_path):
        assert cta_history.load_prior_payloads("2026-08-07", cache_dir=tmp_path / "nope") == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
