"""Deterministic document identity: publisher, series, report date, document type.

Header samples are taken from real page-1 extractions in the private corpus
(2026-09-06..17); none of these decisions involve a model.
"""
import pytest

from research import identify

FOLDER = "2026-09-17"


def meta(name, publisher_folder="goldman sachs", client_modified="2026-09-17T08:15:00Z"):
    path = f"/joe mccann/current/2026/september/sep 17/{publisher_folder}/{name}"
    return {"name": name, "path_lower": path, "client_modified": client_modified}


# --- publisher -------------------------------------------------------------

def test_publisher_comes_from_the_dropbox_folder():
    ident = identify.identify({1: "## Economics Research ## 16 September 2026 | 5:20PM EDT"}, meta("tic data.pdf"), FOLDER)
    assert ident.publisher == "Goldman Sachs"
    assert ident.publisher_source == "folder"


@pytest.mark.parametrize("folder,text,expected", [
    ("nuclear", "**Global Research** ## ab Powered by 4 September 2026 UBS Evidence Lab", "UBS"),
    ("nuclear", "### Utilities # The Wolfe Power Trip August 31, 2026 ## A peek at the peaks", "Wolfe Research"),
    ("other", "MARKETING COMMUNICATION # Deep Value Beyond the Screen September 2026", "unknown"),
])
def test_topic_folders_fall_back_to_page_one_brand(folder, text, expected):
    ident = identify.identify({1: text}, meta("x.pdf", folder), FOLDER)
    assert ident.publisher == expected
    assert ident.publisher_source == ("page" if expected != "unknown" else "none")


# --- report date ladder ----------------------------------------------------

@pytest.mark.parametrize("text,expected,quote", [
    ("## Economics Research ## 16 September 2026 | 5:20PM EDT # TIC Data", "2026-09-16", "16 September 2026"),
    ("## TECHNICAL STRATEGY # Far From a Warsh-Out September 16, 2026 ### Breakdowns", "2026-09-16", "September 16, 2026"),
    ("# Flat Tape Hides Violent Indigestion BY TYLER DURDEN WEDNESDAY, SEP 16, 2026 - 05:45 PM", "2026-09-16", "SEP 16, 2026"),
    ("# Nomura McElligott-STAY WEIRD 17 Sep 2026 Charlie McElligott Thu 17 Sep 2026, 1:28pm ET", "2026-09-17", "17 Sep 2026"),
    ("|16 SEPTEMBER 2026||WHY YOU SHOULD READ THIS REPORT|", "2026-09-16", "16 SEPTEMBER 2026"),
    ("**17 September 2026, 20:30 UTC** Chief Investment Office GWM", "2026-09-17", "17 September 2026"),
    ("# GLOBAL QUANTITATIVE RESEARCH 07 September 2026 |Global Equity Market Arithmetic", "2026-09-07", "07 September 2026"),
    ("Equity Research 15 January 2026 ### Sustainable Investing", "2026-01-15", "15 January 2026"),
])
def test_text_date_is_found_with_its_literal_quote(text, expected, quote):
    ident = identify.identify({1: text}, meta("x.pdf"), FOLDER)
    assert ident.date == expected
    assert ident.date_source == "text"
    assert ident.date_page == 1
    assert quote in ident.date_quote


def test_download_stamp_is_skipped_for_the_report_date():
    text = ("Downloaded from Capital IQ by Someone at BlackRock, Inc. on Monday Sep 07 2026 09:44:42 PM, Sessionid:abc "
            "### Utilities # The Wolfe Power Trip August 31, 2026 ## A peek at the peaks")
    ident = identify.identify({1: text}, meta("x.pdf", "nuclear"), FOLDER)
    assert ident.date == "2026-08-31"


def test_coverage_ranges_and_calendars_do_not_supply_a_report_date():
    text = "## Week In Focus: 7-11 September 2026 ## Highlights include: US inflation - **SUN:** OPEC+7 meeting"
    ident = identify.identify({1: text}, meta("week_in_focus.pdf", "week ahead"), FOLDER, pdf_created="2026-09-06T18:02:00Z")
    assert ident.date == "2026-09-06"
    assert ident.date_source == "pdf"


def test_month_only_date_falls_through_the_ladder():
    text = "# The Reindustrialization of America Is Underway ### September 2026 **Rob Bittencourt**"
    ident = identify.identify({1: text}, meta("x.pdf", "apollo", client_modified="2026-09-16T12:00:00Z"), FOLDER)
    assert ident.date == "2026-09-16"
    assert ident.date_source == "dropbox"


def test_last_page_is_searched_when_page_one_has_no_date():
    pages = {1: "# DB CoTD: The first year In this week's CoTDs", 2: "Deutsche Bank Research 14 September 2026 Disclosures"}
    ident = identify.identify(pages, meta("x.pdf", "deutsche bank"), FOLDER)
    assert (ident.date, ident.date_page) == ("2026-09-14", 2)


def test_text_date_after_the_folder_day_is_rejected_and_ladder_continues():
    text = "## Economics Research ## 19 September 2026 | 5:20PM EDT"
    ident = identify.identify({1: text}, meta("x.pdf"), "2026-09-17", pdf_created="2026-09-17T20:00:00Z")
    assert ident.date == "2026-09-17"
    assert ident.date_source == "pdf"


def test_pdf_creation_date_parses_pdf_syntax():
    assert identify.parse_pdf_date("D:20260908124300+01'00'") == "2026-09-08"
    assert identify.parse_pdf_date("2026-09-06T18:02:00Z") == "2026-09-06"
    assert identify.parse_pdf_date("garbage") is None


# --- series and document type ---------------------------------------------

@pytest.mark.parametrize("name,series", [
    ("the point for europe thursday, 17 september 2026.pdf", "the point for europe"),
    ("usdchf_en_1666698.pdf", "usdchf"),
    ("db cotd - the first year - sept 17.pdf", "db cotd - the first year"),
    ("gs mcelligott - stay weird 17 sep 2026.pdf", "gs mcelligott - stay weird"),
    ("2026-09-17-euro perfect storm-en.pdf", "euro perfect storm"),
    ("wolfe research -a brief history of the end of the world(1).pdf", "wolfe research -a brief history of the end of the world"),
    ("pnc_economics_research_nahb_16_september_2026.pdf", "pnc economics research nahb"),
    ("intradayfxview_20260917_en.pdf", "intradayfxview"),
    ("the flow show friday, 19 september 2026.pdf", "the flow show"),
    ("bofa - the flow show - 19 sep 2026.pdf", "bofa - the flow show"),
    ("db positioning data - 19 september 2026.pdf", "db positioning data"),
    ("deutsche_bank_positioning_data_20260919.pdf", "deutsche bank positioning data"),
])
def test_series_strips_dates_and_ids(name, series):
    assert identify.series(name) == series


@pytest.mark.parametrize("name,text,doc_type", [
    ("weekly_economic_calendar_-_6th-11th_september_2026.pdf", "# Weekly Economic Calendar", "calendar"),
    ("week_in_focus__7-11_september_2026_.pdf", "## Week In Focus: 7-11 September 2026", "calendar"),
    ("weekly_us_earnings_estimates__7-11th_september_2026.pdf", "# Weekly US Earnings Estimates", "research"),
    ("aspi - a sum of its parts.pdf", "**Estimates Revised** **US Equity Research** 15 September 2026 RatingPrice Target **BUY US$11.00** Price **ASPI-NASDAQ US$3.16**", "single_stock"),
    ("maxim - deep fission inc (fisn) - initiation buy - 31 august 2026.pdf", "Initiation of Coverage Rating: Buy", "single_stock"),
    ("spotify technology s.a. (spot)_ communacopia + technology 2026 — key takeaways.pdf", "Key takeaways from the fireside", "conference"),
    ("usdchf_en_1666698.pdf", "**17 September 2026, 13:22 UTC** # USDCHF: Carry still favors the dollar", "fx_pair_note"),
    ("jpm europe equity research _ today's morning meeting.pdf", "Morning meeting summary", "digest"),
    ("jpm_flows___liquidity.pdf", "J P M O R G A N **Global Markets Strategy** 16 September 2026", "research"),
])
def test_document_type_rules(name, text, doc_type):
    assert identify.identify({1: text}, meta(name), FOLDER).doc_type == doc_type


# --- single-stock ticker candidates -----------------------------------------

def test_exchange_suffixed_ticker_on_page_one_is_a_candidate():
    text = "**Estimates Revised** **US Equity Research** 15 September 2026 RatingPrice Target **BUY US$11.00** Price **ASPI-NASDAQ US$3.16**"
    ident = identify.identify({1: text}, meta("aspi - a sum of its parts.pdf"), FOLDER)
    assert ident.doc_type == "single_stock" and "ASPI" in ident.tickers


def test_parenthesized_ticker_in_the_filename_is_a_candidate():
    ident = identify.identify({1: "Initiation of Coverage Rating: Buy"},
                              meta("maxim - deep fission inc (fisn) - initiation buy - 31 august 2026.pdf"), FOLDER)
    assert ident.doc_type == "single_stock" and "FISN" in ident.tickers


def test_parenthesized_ticker_on_page_one_is_a_candidate():
    text = "# Spotify Technology S.A. (SPOT) Rating: BUY Price Target US$700"
    ident = identify.identify({1: text}, meta("spotify update.pdf"), FOLDER)
    assert ident.doc_type == "single_stock" and "SPOT" in ident.tickers


def test_non_single_stock_documents_carry_no_ticker_candidates():
    ident = identify.identify({1: "## Economics Research ## 16 September 2026 (US) outlook"}, meta("tic data.pdf"), FOLDER)
    assert ident.doc_type == "research" and ident.tickers == ()
