"""HR-6 eval metrics on a hand-computed 5-row fixture."""
from __future__ import annotations

from newsfeed.slm.contract import classify_slm_tags, normalise_tags
from newsfeed.slm.eval import bootstrap_ci, evaluate_fixed

TAXONOMY = ["GAMMA", "SPX", "VOL", "VIX", "PUTS", "OPTIONS"]
RARE = ["PUTS"]

GOLD = [
    {"id": "1", "gold": ["GAMMA", "SPX", "VOL"]},
    {"id": "2", "gold": ["GAMMA", "SPX", "VIX"]},
    {"id": "3", "gold": ["PUTS", "SPX", "VOL"]},
    {"id": "4", "gold": ["GAMMA", "OPTIONS", "VOL"]},
    {"id": "5", "gold": ["VIX", "SPX", "VOL"]},
]


class TestNormaliseParity:
    def test_js_fixtures(self):
        assert normalise_tags(["btc", "Vix", "USD", "puts", "Options"]) == [
            "BTC",
            "VIX",
            "USD",
            "PUTS",
            "OPTIONS",
        ]
        assert normalise_tags(
            ["Put Call Ratio", "single stock vol", "FUND_FLOWS", "Tail Hedge"]
        ) == ["PUT-CALL-RATIO", "SINGLE-STOCK-VOL", "FUND-FLOWS", "TAIL-HEDGE"]
        assert normalise_tags(["m&a", "S&P", "M&A"]) == ["M&A", "S&P"]
        assert normalise_tags(['"puts"', "#options", "calls.", "(positioning)"]) == [
            "PUTS",
            "OPTIONS",
            "CALLS",
            "POSITIONING",
        ]
        assert normalise_tags(["BTC", "btc", "BTC.", "Bitcoin"]) == ["BTC", "BITCOIN"]
        assert normalise_tags(["puts", "  ", "", None, "options"]) == ["PUTS", "OPTIONS"]


class TestClassify:
    def test_valid_three(self):
        code, tags = classify_slm_tags({"tags": ["GAMMA", "SPX", "VOL"]}, TAXONOMY)
        assert code == "ok"
        assert tags == ["GAMMA", "SPX", "VOL"]

    def test_count_two(self):
        code, _ = classify_slm_tags({"tags": ["GAMMA", "SPX"]}, TAXONOMY)
        assert code == "abstain:count"

    def test_count_four(self):
        code, _ = classify_slm_tags({"tags": ["GAMMA", "SPX", "VOL", "VIX"]}, TAXONOMY)
        assert code == "abstain:count"

    def test_empty(self):
        code, _ = classify_slm_tags({"tags": []}, TAXONOMY)
        assert code == "abstain:empty"

    def test_prose(self):
        code, _ = classify_slm_tags("not json", TAXONOMY)
        assert code == "unparseable"

    def test_out_of_taxonomy(self):
        code, _ = classify_slm_tags({"tags": ["GAMMA", "SPX", "NEWCOIN"]}, TAXONOMY)
        assert code == "abstain:out_of_taxonomy"

    def test_lowercase_dupes_become_count(self):
        code, _ = classify_slm_tags({"tags": ["gamma", "GAMMA", "spx"]}, TAXONOMY)
        assert code == "abstain:count"


class TestMetrics:
    def test_hand_computed_five_rows(self):
        pred = [
            {"id": "1", "pred": {"tags": ["GAMMA", "SPX", "VOL"]}},
            {"id": "2", "pred": {"tags": ["GAMMA", "SPX", "PUTS"]}},
            {"id": "3", "pred": "prose"},
            {"id": "4", "pred": {"tags": ["GAMMA", "OPTIONS", "NEW"]}},
            {"id": "5", "pred": {"tags": ["VIX", "SPX"]}},
        ]
        result = evaluate_fixed(GOLD, pred, taxonomy=TAXONOMY, rare=RARE)
        assert result["n"] == 5
        assert result["invalid_breakdown"]["unparseable"] == 1
        assert result["invalid_breakdown"]["out_of_taxonomy"] == 1
        assert result["invalid_breakdown"]["count"] == 1
        assert result["invalid_rate"] == 3 / 5
        assert result["exact3"] == 1 / 5
        # invalid rows contribute zero predictions (recall losses)
        assert result["micro_f1"] > 0
        assert 0 <= result["macro_f1"] <= 1
        assert 0 <= result["rare_label_recall"] <= 1
        assert 0 <= result["jaccard"] <= 1

    def test_bootstrap_ci_shape(self):
        ci = bootstrap_ci([0.1, 0.2, -0.05, 0.0, 0.15])
        assert set(ci) >= {"mean", "lo", "hi", "n"}
        assert ci["n"] == 5
        assert ci["lo"] <= ci["mean"] <= ci["hi"]
