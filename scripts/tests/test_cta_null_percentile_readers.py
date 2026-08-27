"""`reconcile_tables` nulls a percentile its own z-score contradicts, and
`fetch_menthorq_cta` writes that null straight into `data/menthorq_cache`.
The key is PRESENT with a null value, so every `.get("percentile_3m", 50)`
default silently fails to fire. These tests pin the two readers that were
never updated: the CRI HTML report, which died on `None < 25` and wrote no
file at all, and the regime share card, which printed "Noneth percentile".
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cri_scan  # noqa: E402
import generate_regime_share  # noqa: E402

# A row the reconciler nulled: the percentiles came back rounded to 0 and the
# z-score flatly contradicted them, so none of the three is publishable.
NULLED_SPX = {
    "underlying": "E-Mini S&P 500 Index",
    "position_today": 3.66,
    "position_yesterday": 3.85,
    "position_1m_ago": 1.69,
    "percentile_1m": None,
    "percentile_3m": None,
    "percentile_1y": None,
    "z_score_3m": 1.48,
}

NULLED_MENTHORQ = {
    "date": "2026-08-25",
    "spx": NULLED_SPX,
    "tables": {"main": [NULLED_SPX], "index": [], "commodity": [], "currency": []},
}


def _cri_result() -> dict:
    return {
        "date": "2026-08-25",
        "vix": 18.0,
        "vvix": 100.0,
        "spy": 440.0,
        "vix_5d_roc": 0.0,
        "vvix_vix_ratio": 5.56,
        "spx_100d_ma": 425.76,
        "spx_distance_pct": 3.35,
        "cor1m": 30.0,
        "cor1m_previous_close": 30.0,
        "cor1m_5d_change": 0.0,
        "realized_vol": 12.0,
        "cri": {
            "score": 8.6,
            "level": "LOW",
            "components": {"vix": 1.8, "vvix": 4.9, "correlation": 1.9, "momentum": 0.0},
        },
        "cta": {
            "realized_vol": 12.0,
            "exposure_pct": 200.0,
            "forced_reduction_pct": 0.0,
            "est_selling_bn": 0.0,
        },
        "crash_trigger": {
            "triggered": False,
            "conditions": {
                "spx_below_100d_ma": False,
                "realized_vol_gt_25": False,
                "cor1m_gt_60": False,
            },
            "values": {"realized_vol": 12.0, "cor1m": 30.0},
        },
        "history": [
            {
                "date": "2026-08-25",
                "vix": 18.0,
                "vvix": 100.0,
                "spy": 440.0,
                "cor1m": 30.0,
                "realized_vol": 12.0,
                "spx_vs_ma_pct": 3.35,
                "vix_5d_roc": 0.0,
            }
        ],
        "menthorq_cta": NULLED_MENTHORQ,
    }


def _regime_data() -> dict:
    return {
        "date": "2026-08-25",
        "cri": {"score": 55.0, "level": "ELEVATED", "components": {}},
        "cta": {"exposure_pct": 120.0, "forced_reduction_pct": 0.0, "est_selling_bn": 0.0},
        "crash_trigger": {"triggered": False, "conditions": {}},
        "spy": 440.0,
        "spx_distance_pct": 1.2,
        "vix": 18.0,
        "vvix": 100.0,
        "cor1m": 30.0,
        "realized_vol": 12.0,
        "menthorq_cta": NULLED_MENTHORQ,
    }


class TestCriReportReadsANulledPercentile:
    def test_html_report_still_renders(self):
        html = cri_scan.generate_html_report(_cri_result(), market_open=False, elapsed=1.0)
        assert "MenthorQ CTA Positioning" in html

    def test_report_shows_no_reading_rather_than_a_fabricated_one(self):
        html = cri_scan.generate_html_report(_cri_result(), market_open=False, elapsed=1.0)
        assert "None" not in html
        # The 3M Percentile metric renders an explicit blank, not a midpoint.
        card = html.split("3M Percentile")[1].split("</div></div>")[0]
        assert "---" in card


class TestRegimeShareReadsANulledPercentile:
    def test_card_never_prints_noneth(self):
        card = generate_regime_share.card4_cta_squeeze(_regime_data(), "2026-08-25")
        assert "Noneth" not in card
        assert "None percentile" not in card

    def test_card_claims_no_percentile_it_does_not_have(self):
        card = generate_regime_share.card4_cta_squeeze(_regime_data(), "2026-08-25")
        assert "percentile of their 3M range" not in card

    def test_tweet_still_builds(self):
        tweet = generate_regime_share.build_tweet(_regime_data(), "2026-08-25")
        assert "Noneth" not in tweet
        assert "pctile" not in tweet

    def test_a_readable_percentile_is_still_reported(self):
        data = _regime_data()
        row = {**NULLED_SPX, "percentile_1m": 43, "percentile_3m": 81, "percentile_1y": 88}
        data["menthorq_cta"] = {**NULLED_MENTHORQ, "spx": row, "tables": {"main": [row]}}
        label = generate_regime_share.pctile_label(81)
        assert f"{label} pctile" in generate_regime_share.build_tweet(data, "2026-08-25")
        assert f"{label} percentile" in generate_regime_share.card4_cta_squeeze(data, "2026-08-25")
