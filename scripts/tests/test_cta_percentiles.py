"""MenthorQ ships the CTA percentile columns on two different scales and the
vision extractor is told to report integers, so on a fractional card it rounds
0.43 to 0 and 0.98 to 1. A max-LONG row then reads as "0th pctile" and every
narrative built on it inverts. These tests pin the repair.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.cta_percentiles import (  # noqa: E402
    normalize_pctile,
    percentile_from_z,
    reconcile_tables,
)

# Verbatim from the Turso `menthorq_cta` payload for 2026-08-25: the main table
# carries 0/0/0 for SPX and NQ while the index table carries the same two rows
# — identical positions, identical z — on the fractional scale.
AUG_25 = {
    "main": [
        {"underlying": "E-Mini S&P 500 Index", "position_today": 3.66, "position_yesterday": 3.85, "position_1m_ago": 1.69, "percentile_1m": 0, "percentile_3m": 0, "percentile_1y": 0, "z_score_3m": 1.48},
        {"underlying": "CME Nasdaq 100 Index", "position_today": 2.52, "position_yesterday": 2.69, "position_1m_ago": 1.59, "percentile_1m": 0, "percentile_3m": 0, "percentile_1y": 0, "z_score_3m": 0.26},
        {"underlying": "10-Year T-Note", "position_today": -1.59, "position_yesterday": -1.38, "position_1m_ago": -2.93, "percentile_1m": 71, "percentile_3m": 57, "percentile_1y": 19, "z_score_3m": 0.41},
    ],
    "index": [
        {"underlying": "E-Mini S&P 500 Index", "position_today": 3.66, "position_yesterday": 3.85, "position_1m_ago": 1.69, "percentile_1m": 0.43, "percentile_3m": 0.81, "percentile_1y": 0.88, "z_score_3m": 1.48},
        {"underlying": "CME Nasdaq 100 Index", "position_today": 2.52, "position_yesterday": 2.69, "position_1m_ago": 1.59, "percentile_1m": 0.38, "percentile_3m": 0.52, "percentile_1y": 0.68, "z_score_3m": 0.26},
    ],
}


def test_percentile_from_z_maps_a_z_score_onto_its_percentile():
    assert round(percentile_from_z(0.0)) == 50
    assert round(percentile_from_z(1.48)) == 93
    assert round(percentile_from_z(-1.89)) == 3
    assert percentile_from_z(None) is None


def test_repairs_a_rounded_percentile_from_the_same_row_in_another_table():
    out = reconcile_tables(AUG_25)
    spx = out["main"][0]
    assert spx["percentile_3m"] == 81
    assert spx["percentile_1m"] == 43
    assert spx["percentile_1y"] == 88
    assert out["main"][1]["percentile_3m"] == 52


def test_leaves_rows_their_z_score_already_agrees_with():
    out = reconcile_tables(AUG_25)
    assert out["main"][2]["percentile_3m"] == 57


def test_nulls_a_percentile_its_z_score_flatly_contradicts():
    # 2026-08-18 currency: the whole table came back rounded to 0, and no other
    # table carries those contracts.
    out = reconcile_tables({
        "currency": [
            {"underlying": "British Pound", "position_today": 1.3, "position_yesterday": 1.2, "position_1m_ago": -0.4, "percentile_1m": 0, "percentile_3m": 0, "percentile_1y": 0, "z_score_3m": 1.52},
            {"underlying": "Brazilian Real", "position_today": -0.1, "position_yesterday": 0.1, "position_1m_ago": 0.3, "percentile_1m": 10, "percentile_3m": 3, "percentile_1y": 2, "z_score_3m": -1.37},
        ],
    })
    assert out["currency"][0]["percentile_3m"] is None
    assert out["currency"][0]["percentile_1m"] is None
    assert out["currency"][1]["percentile_3m"] == 3


def test_keeps_a_genuine_0th_percentile_its_z_score_corroborates():
    out = reconcile_tables({
        "main": [
            {"underlying": "Dollar Index", "position_today": -2.1, "position_yesterday": -2.0, "position_1m_ago": 1.1, "percentile_1m": 1, "percentile_3m": 0, "percentile_1y": 2, "z_score_3m": -2.4},
        ],
    })
    assert out["main"][0]["percentile_3m"] == 0


def test_reads_a_fractional_one_beside_fractional_siblings_as_the_100th():
    out = reconcile_tables({
        "commodity": [
            {"underlying": "Coffee", "position_today": 1.4, "position_yesterday": 1.06, "position_1m_ago": 0.53, "percentile_1m": 1.0, "percentile_3m": 0.98, "percentile_1y": 0.89, "z_score_3m": 1.07},
        ],
    })
    assert out["commodity"][0]["percentile_1m"] == 100
    assert out["commodity"][0]["percentile_3m"] == 98


def test_reconcile_tolerates_missing_and_empty_tables():
    assert reconcile_tables(None) is None
    assert reconcile_tables({"main": [], "index": None}) == {"main": [], "index": []}


# `(None, 50)` and `("x", 50)` were the OLD contract, changed deliberately by
# R-291: returning the median for a value the reconciler had just nulled — for
# contradicting its own z-score, or for having no z-score to check it — put a
# confident "the 50th percentile" in front of every reader downstream. The rest
# of the contract (scale disambiguation by TYPE) is unchanged and still pinned.
@pytest.mark.parametrize("raw,expected", [(0.9, 90), (90, 90), (0, 0), (1, 1), (1.0, 100)])
def test_normalize_pctile_keeps_its_scale_contract(raw, expected):
    assert normalize_pctile(raw) == expected


@pytest.mark.parametrize("raw", [None, "x", ""])
def test_normalize_pctile_reports_an_unknown_percentile_as_none(raw):
    assert normalize_pctile(raw) is None
# The vision extractor returns a null z for any row it cannot read. Both copies
# of the row then have an unverifiable gap, and a strict `<` tie-break lets the
# first table win — which republishes the rounded 0 over the good 81.
NULL_Z_SPX = {
    "main": [
        {"underlying": "E-Mini S&P 500 Index", "position_today": 3.66, "position_yesterday": 3.85, "position_1m_ago": 1.69, "percentile_1m": 0, "percentile_3m": 0, "percentile_1y": 0, "z_score_3m": None},
    ],
    "index": [
        {"underlying": "E-Mini S&P 500 Index", "position_today": 3.66, "position_yesterday": 3.85, "position_1m_ago": 1.69, "percentile_1m": 0.43, "percentile_3m": 0.81, "percentile_1y": 0.88, "z_score_3m": None},
    ],
}


def test_prefers_the_unrounded_row_when_no_z_score_can_arbitrate():
    out = reconcile_tables(NULL_Z_SPX)
    for table in ("main", "index"):
        assert out[table][0]["percentile_3m"] == 81
        assert out[table][0]["percentile_1m"] == 43
        assert out[table][0]["percentile_1y"] == 88


def test_repair_does_not_depend_on_table_order():
    forward = reconcile_tables(NULL_Z_SPX)
    reversed_tables = {k: NULL_Z_SPX[k] for k in reversed(list(NULL_Z_SPX))}
    assert reconcile_tables(reversed_tables)["main"] == forward["main"]


def test_invents_nothing_when_every_copy_of_a_null_z_row_is_rounded():
    out = reconcile_tables({
        "main": [
            {"underlying": "Gold", "position_today": -1.9, "percentile_1m": 0, "percentile_3m": 0, "percentile_1y": 1, "z_score_3m": None},
        ],
        "commodity": [
            {"underlying": "Gold", "position_today": -1.9, "percentile_1m": 0, "percentile_3m": 0, "percentile_1y": 1, "z_score_3m": None},
        ],
    })
    assert out["main"][0]["percentile_3m"] == 0
    assert out["main"][0]["percentile_1y"] == 1
