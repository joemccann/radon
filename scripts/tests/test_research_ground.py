"""Numeric grounding: every number in the copy must appear on a cited page, matched by code, no model quotes."""
import pytest

from research import ground

PAGE = ("J P M O R G A N Global Markets Strategy 16 September 2026 Flows & Liquidity. The $260bn increase in net bond "
        "issuance by tech companies for 2026 looks manageable and would put perhaps 10-15bp of upward pressure on Global "
        "Agg yields. Headline INPC ran at 0.20% mom in Aug (6bp below consensus of 0.26%); core at 0.16%. Annual headline "
        "rose 14bp to 3.26%. Revenues 50.2 vs 50.0 in 2Q26. Q1 2023 was the trough. The 10Y yield sits at 5.00%, 2s10s at "
        "-25bp, FY27 EPS of USD 1.7tn and USD 2.9tn across the 2024-50 period; see Chart 2 and Exhibit 1. Brent at $100/bbl. "
        "Equinor re-gearing +10pp ND/CE within a 15-30% range; another 200bps. Valued at USD 9.1bn in 2025, CAGR 4.05%. "
        "Reducing its holding to c.3.4% from c.5% over FY27–28E. SEP median (Percent): Federal funds rate 4.1 (3.8) 3.9 (3.4). "
        "IoT (Software) revenue 3.4 4.4 -22% yoy. 2026E EPS $2.90. Hiking cycle length (months): Aug-06 9 Dec-07 16 27%. "
        "The FOMC raised the target range to 3.75-4.00%. Buyers accumulated 379,000 VIX calls. Only 48% of names sit above the 200 DMA.")
PAGES = {3: PAGE}


def tokens(text):
    return [t.token for t in ground.numeric_tokens(text)]


@pytest.mark.parametrize("text,expected", [
    ("Revenues 50.2 in 2Q26 and Q1 2023", ["50.2", "2Q26", "Q1 2023"]),
    ("USD 1.7-2.9tn across 2024-50", ["USD 1.7-2.9tn", "2024-50"]),
    ("10-15bp of pressure, 200bps, -25bp", ["10-15bp", "200bps", "-25bp"]),
    ("$260bn and $100/bbl", ["$260bn", "$100/bbl"]),
    ("10Y at 5.00% and 2s10s, FY27 and 1H26", ["10Y", "5.00%", "2s10s", "FY27", "1H26"]),
    ("see Chart 2 and Exhibit 1 on 16 September 2026", ["Chart 2", "Exhibit 1", "16 September 2026"]),
    ("printed at 9:04AM EDT, roughly ten times", []),
    ("+10pp ND/CE within a 15-30% range", ["+10pp", "15-30%"]),
    ("c.3.4% over FY27–28E", ["3.4%", "FY27-28E"]),
    ("revenue 3.4 4.4 -22% yoy", ["3.4", "4.4", "-22%"]),      # a spaced minus is a sign, not a range
    ("hiked to 3.75%-4.00% today", ["3.75%-4.00%"]),          # unit repeated on both ends of a range
])
def test_tokenizer_keeps_finance_notation_whole(text, expected):
    assert tokens(text) == expected


@pytest.mark.parametrize("copy_text", [
    "Tech issuance adds 10-15bp to yields; the $260bn increase for 2026 is manageable.",
    "Headline 0.20% mom vs 0.26% consensus, core 0.16%; annual headline up 14bp to 3.26%.",
    "Revenues 50.2 vs 50.0 in 2Q26, the first beat since Q1 2023.",
    "10Y at 5.00% with 2s10s at -25bp; FY27 EPS of USD 1.7-2.9tn across 2024-50.",
    "Re-gearing of +10pp ND/CE keeps Equinor in a 15-30% range; Orsted would consume another 200bps.",
    "Chart 2 shows Brent at $100/bbl; the 16 September 2026 note values the market at USD 9.1bn in 2025.",
    "Headline inflation ran at 0.2% in August.",        # 0.20% on the page: formatting, not rounding
    "Issuance of $260 billion; pressure of 10 to 15 basis points.",   # unit aliases
    "The 16 Sep 2026 note, dated 2026-09-16, for calendar 2026.",     # date spellings and a bare year inside a date
    "The 10-year sits at 5.00% after a 2024-2050 outlook.",           # tenor spelled out, four-digit year range
    "2s10s fell 25bp.",                                               # words carry the direction; the page shows -25bp
    "Holding cut to c.3.4% over FY27-28E.",                           # abbreviation before a decimal, fiscal-year range
    "Fed funds at 3.8% per the SEP table.",                           # table states 'Percent' once; cells are bare
    "Revenues of 50.2 in Q2 2026, a trough in 1Q23; on hold through mid-2026.",   # period spellings, hyphenated year
    "IoT revenue fell -22% in Q2; FY26 EPS of $2.90 after 16 months.",            # signed table cell, bare quarter, FY vs 2026E
    "Fed funds now 3.75%-4.00%; 379k VIX calls; breadth below the 200-day average.",  # range units, k vs 379,000, 200-day vs 200 DMA
])
def test_grounded_copy_passes(copy_text):
    result = ground.ground([copy_text], PAGES, [3])
    assert result["passed"], [t for t in result["tokens"] if t["page"] is None]


@pytest.mark.parametrize("copy_text,missing", [
    ("Tech issuance adds 10-20bp to yields.", "10-20bp"),                       # 20bp is not on the page
    ("Annual headline rose 0.14% to 3.26%.", "0.14%"),                          # 14bp restated in other units
    ("The $265bn increase for 2026.", "$265bn"),                                 # wrong value
    ("Issuance of €260bn.", "€260bn"),                                           # wrong currency
    ("Revenues grew 0.4% in 2Q26.", "0.4%"),                                     # computed, not stated
    ("Roughly a 3-year high in the 10Y.", "3-year"),                             # derived relative age
    ("Brent at $100/bbl per the 17 September 2026 note.", "17 September 2026"),  # wrong date
    ("Core printed -0.16%.", "-0.16%"),                                          # sign added by the copy
])
def test_ungrounded_numbers_fail_and_name_the_token(copy_text, missing):
    result = ground.ground([copy_text], PAGES, [3])
    assert not result["passed"]
    assert missing in [t["token"] for t in result["tokens"] if t["page"] is None]


def test_only_cited_pages_count():
    assert ground.ground(["Brent at $100/bbl."], {3: PAGE, 4: "nothing"}, [4])["passed"] is False
    assert ground.ground(["Brent at $100/bbl."], {3: PAGE, 4: "nothing"}, [3, 4])["passed"] is True


def test_known_facts_are_grounded_without_a_cited_page():
    # The report date comes from identify with its own page; copy may restate it even when that page is not cited.
    result = ground.ground(["Per the 16 September 2026 note, Brent at $100/bbl."], {3: "Brent at $100/bbl.", 1: "16 September 2026 header"}, [3],
                           known={"date": "2026-09-16", "date_page": 1})
    assert result["passed"] and [t["page"] for t in result["tokens"] if t["kind"] == "date"] == [1]
    assert not ground.ground(["Per the 17 September 2026 note."], {3: PAGE}, [3], known={"date": "2026-09-16", "date_page": 1})["passed"]


def test_copy_without_numbers_passes_and_markdown_emphasis_is_ignored():
    assert ground.ground(["Positioning looks stretched."], PAGES, [3])["passed"]
    assert ground.ground(["Core at 0.16%."], {1: "core at **0.16%** today"}, [1])["passed"]
