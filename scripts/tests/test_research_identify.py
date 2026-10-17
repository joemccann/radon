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


# Page excerpts from the ten production "unknown" posts of 2026-10-09/10 (all filed under other/).
BODY = "Rates rose 25bp on the week while credit spreads widened 10bp. " * 8


@pytest.mark.parametrize("name,pages,expected", [
    ("10.06.2026-From-Ellen-Hazen-Congress-May-Change.pdf",
     {1: "# The Investor’s Landscape ## Congress May Change. ### By Ellen Hazen, CFA, Chief Market Strategist\n" + BODY
         + "\nF.L.PUTNAM INVESTMENT MANAGEMENT COMPANY **|** FLPUTNAM.COM **| September 2026**",
      2: BODY + "\nF.L.PUTNAM INVESTMENT MANAGEMENT COMPANY **|** FLPUTNAM.COM **| September 2026**",
      3: "Past performance may not be indicative of future results.\nF.L.PUTNAM INVESTMENT MANAGEMENT COMPANY **|** FLPUTNAM.COM **| September 2026**"},
     "F.L.Putnam"),
    ("Financial_Forecasts_October_2026.pdf",
     {1: "## RESEARCH\n\nresearch.natixis.com\n\n# Financial Forecasts\n\n### October 2026", 2: BODY + " Contact: a.b@natixis.com",
      3: "This document has been prepared by Natixis. Links from Natixis web sites are meant for convenience only. www.natixis.com"},
     "Natixis"),
    ("AOM-Not-All-Tightening-Cycles-Are-Created-Equal.pdf",
     {1: "# Alger On The Money\n# Not All Tightening Cycles Are Created Equal\n" + BODY,
      2: "Copyright 2026 S&P Dow Jones Indices LLC, a subsidiary of S&P Global Inc. All rights reserved. "
         "FactSet is an independent source, which Alger believes to be a reliable source. "
         "**Fred Alger Management, LLC** 100 Pearl Street, New York, NY 10004 / 212.806.8800/ <u>www.alger.com</u> AOM 493"},
     "Fred Alger Management"),
    ("economic-commentary-091026.pdf",
     {1: "### OCTOBER 9, 2026\n# WEEKLY ECONOMIC COMMENTARY\nGlobal Economic Research <u>northerntrust.com</u> "
         "**Carl R. Tannenbaum** Chief Economist <u>ct92@ntrs.com</u>\n" + BODY + "\n*Northern Trust*",
      2: BODY + "\n*Northern Trust*",
      3: "© 2026 Northern Trust Corporation. Head Office: 50 South La Salle Street, Chicago, Illinois 60603 U.S.A.\n*Northern Trust*"},
     "Northern Trust"),
    ("credit-quarterly-new-terms.pdf",
     {1: "# 3Q 2026 Credit Quarterly ## New Terms\n" + BODY,
      2: "Oaktree believes that such information is accurate. © 2026 Oaktree Capital Management, L.P"},
     "Oaktree Capital Management"),
    ("made-in-europe.pdf",
     {1: "### Global Investment Research | Equities # Made in Europe **AUTHORS** **Alex Nae** <u>Alex.Nae@lseg.com</u> "
         "**David McNay, CFA** <u>David.McNay@lseg.com</u>",
      2: "© [2026] London Stock Exchange Group plc and its applicable group undertakings (“LSEG”). Use of LSEG data requires a licence."},
     "LSEG"),
    ("2610-future-quality-insights.pdf",
     {1: "# Future Quality Insights: what comes after the AI boom?\n" + BODY,
      2: "**Amova Asset Management team in Europe** Email: EMEAenquiries@amova-am.com\n"
         "**Important information:** This document is prepared by Amova Asset Management Co., Ltd. and/or its affiliates.",
      3: "**United Kingdom:** This document is communicated by Amova Asset Management UK Limited, which is authorised by the FCA. "
         "**Ireland:** This document is communicated by Amova Asset Management Ireland Limited."},
     "Amova Asset Management"),
    ("Market Musings - Time is of the Essence.pdf",
     {1: "**INVESTMENT MANAGEMENT** ## Market Musings 10/26\n" + BODY,
      2: "are entirely those of VLK Investment Management. **Capital at risk** vanlanschotkempen.com/ investment-management "
         "©2026 Van Lanschot Kempen Investment Management Follow us on LinkedIn any given time, without prior notice."},
     "Van Lanschot Kempen"),
    ("TDAGeneric_NAM_10122026.pdf",
     {1: "NORTH AMERICA For Monday, October 12, 2026 ## Market Recap at 4 PM ET\n" + BODY,
      2: "*For questions or comments about this report, email us at: **TheDay.Ahead@thomsonreuters.com.*** "
         "*© 2026 London Stock Exchange Group plc. All rights reserved.* *LSEG* *10 Paternoster Square, London*"},
     "LSEG"),
    ("weekly-municipal-monitor-2026-10-08.pdf",
     {1: "MARKETS October 08, 2026 # Weekly Municipal Monitor By Sam Weitzman\n" + BODY,
      2: "*Source: Bloomberg, Franklin Templeton. As of 30 Sep 26.* ## Western Asset Key Themes for Muni Investors",
      3: "© Western Asset Management Company, LLC 2025. Western Asset Management Company Ltd is authorised by the FCA."},
     "Western Asset Management"),
])
def test_topic_folder_publisher_is_found_in_footers_disclaimers_and_domains(name, pages, expected):
    ident = identify.identify(pages, meta(name, "other"), FOLDER)
    assert ident.publisher == expected
    # A grounded legal copyright line (LEGAL_PUBLISHERS) outranks document discovery.
    assert ident.publisher_source == ("page" if ident.publisher_quote else "document")


@pytest.mark.parametrize("pages", [
    # Company names in the body and a data vendor's copyright are not a publisher.
    {1: "# Apple Inc. raises guidance\nApple Inc. reported revenue up 8%. Microsoft Corporation lagged.\n" + BODY,
     2: "Copyright 2026 S&P Dow Jones Indices LLC. Source: Bloomberg Finance L.P. Data: FactSet Research Systems Inc."},
    # A single citation link is not a publisher either.
    {1: BODY + " See https://www.reuters.com/markets/story-2026-10-01/", 2: BODY},
])
def test_topic_folder_without_publisher_evidence_stays_unknown(pages):
    ident = identify.identify(pages, meta("note.pdf", "other"), FOLDER)
    assert ident.publisher == "unknown"
    assert ident.publisher_source == "none"


def test_footnote_links_to_a_cited_company_do_not_make_it_the_publisher():
    # Brown Advisory "Energy Expansion" (2026-10-09): the endnotes link Monolithic Power Systems' own report.
    holding = "Monolithic Power Systems Inc. (MPWR), held in the Brown Advisory Large-Cap Sustainable Growth Strategy. "
    footer = "\nwww.brownadvisory.com\n"
    notes = ("23 Monolithic Power Systems, 2026 Corporate Responsibility Report. "
             "[https://www.monolithicpower.com/media/mps_cms_document/c/o/corporate-responsibility-report-2026.pdf] "
             "24 Monolithic Power Systems Inc., *2026 Corporate Responsibility Report*. "
             "[https://www.monolithicpower.com/media/mps_cms_document/2/0/2026-report.pdf]")
    pages = {1: BODY + holding + footer, 2: BODY + footer, 3: BODY + holding + footer,
             4: "Source: Brown Advisory analysis." + footer, 5: notes + footer}
    ident = identify.identify(pages, meta("Energy_Expansion_and_the_Economics_of_Constraint.pdf", "other"), FOLDER)
    assert ident.publisher == "Brown Advisory"


def test_a_vendor_counts_when_the_document_is_on_its_own_domain():
    # Morningstar Manager Research (HSA Landscape 2026) is excluded as a data vendor everywhere else.
    pages = {1: "MANAGER RESEARCH | SEPTEMBER 2026 # 2026 Health Savings Account Landscape\n" + BODY,
             2: BODY + " Contact greg.carlson@morningstar.com",
             3: "©2026 Morningstar. All Rights Reserved. To license the research, visit global.morningstar.com."}
    assert identify.identify(pages, meta("HSA_Landscape_2026_FINAL.pdf", "other"), FOLDER).publisher == "Morningstar"
    cited = {1: BODY, 2: "© 2026 Morningstar. All Rights Reserved. © 2026 Northern Trust Corporation. northerntrust.com"}
    assert identify.identify(cited, meta("x.pdf", "other"), FOLDER).publisher == "Northern Trust"


@pytest.mark.parametrize("raw,expected", [
    ("WTW. Proprietary and Confidential", "WTW"),
    ("Westpac Banking Corporation ABN", "Westpac Banking"),
    ("Neuberger Berman BD, LLC", "Neuberger Berman"),
])
def test_display_name_drops_run_on_sentences_and_registration_tails(raw, expected):
    assert identify._display(raw) == expected


def test_image_derived_text_names_the_publisher_of_a_scanned_pdf():
    page = ("<!-- image-derived -->\nEconomics **Daily Alert** 7 October 2026 **Commonwealth Bank of Australia**\n"
            "adam.donaldson@cba.com.au | carol.kong@cba.com.au\n<!-- /image-derived -->\n")
    ident = identify.identify({1: page, 2: page}, meta("Economics Daily Alert.pdf", "other"), FOLDER)
    assert ident.publisher == "Commonwealth Bank of Australia"


def test_folder_publisher_still_wins_over_document_evidence():
    ident = identify.identify({1: "© 2026 Northern Trust Corporation"}, meta("x.pdf", "jefferies"), FOLDER)
    assert (ident.publisher, ident.publisher_source) == ("Jefferies", "folder")


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


def test_tyler_durden_october_byline_precedes_the_next_day_folder_date():
    text = "BY TYLER DURDEN TUESDAY, OCT 13, 2026 - 08:55 PM"
    ident = identify.identify({1: text}, meta("x.pdf"), "2026-10-14")

    assert ident.date == "2026-10-13"
    assert ident.date_source == "text"
    assert ident.date_page == 1
    assert "OCT 13, 2026" in ident.date_quote


@pytest.mark.parametrize("month_token,month_number", [
    ("january", 1), ("february", 2), ("march", 3), ("april", 4),
    ("may", 5), ("june", 6), ("july", 7), ("august", 8),
    ("september", 9), ("october", 10), ("november", 11), ("december", 12),
    ("jan", 1), ("feb", 2), ("mar", 3), ("apr", 4), ("may", 5),
    ("jun", 6), ("jul", 7), ("aug", 8), ("sep", 9), ("sept", 9),
    ("oct", 10), ("nov", 11), ("dec", 12),
])
def test_all_month_names_and_abbreviations_parse_before_next_day_cutoff(month_token, month_number):
    text = f"{month_token.upper()} 13, 2026"
    folder_date = f"2026-{month_number:02d}-14"
    ident = identify.identify({1: text}, meta("x.pdf"), folder_date)

    assert ident.date == f"2026-{month_number:02d}-13"
    assert ident.date_source == "text"
    assert ident.date_page == 1
    assert ident.date_quote == text


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


@pytest.mark.parametrize('folder', ['other', 'week ahead', 'unknown', ''])
def test_last_page_legal_publisher_beats_cited_banks_in_body(folder):
    quote = '© 2026 London Stock Exchange Group plc. All rights reserved.'
    pages = {1: '# The Day Ahead\nGoldman Sachs said yields would rise. Reuters photo credit.',
             6: 'T-Mobile US slid 13.27%. LSEG provides market data.',
             11: '# The Day Ahead\n' + quote + '\nLSEG\n10 Paternoster Square, London'}
    ident = identify.identify(pages, meta('the day ahead.pdf', folder), '2026-10-09')
    assert ident.publisher == 'LSEG'
    assert ident.publisher_source == 'page'
    assert ident.publisher_page == 11
    assert ident.publisher_quote == quote
    assert ident.publisher_quote in pages[ident.publisher_page]


@pytest.mark.parametrize('quote', [
    '©2026London Stock Exchange Group plc. All rights reserved.',
    'Copyright 2026 LSEG. All rights reserved.',
    '© London Stock Exchange Group plc 2026. All rights reserved.',
])
def test_lseg_legal_footer_variants_are_grounded(quote):
    ident = identify.identify({1: 'A markets wrap.', 9: quote}, meta('wrap.pdf', 'other'), FOLDER)
    assert ident.publisher == 'LSEG'
    assert ident.publisher_page == 9
    assert ident.publisher_quote in quote


def test_named_folder_priority_is_unchanged_and_new_fields_default():
    ident = identify.identify({1: 'Goldman Sachs Research', 11: '© 2026 London Stock Exchange Group plc. All rights reserved.'},
                              meta('gs research.pdf'), FOLDER)
    assert ident.publisher == 'Goldman Sachs' and ident.publisher_source == 'folder'
    assert ident.publisher_page is None and ident.publisher_quote is None


@pytest.mark.parametrize('text', [
    'LSEG market data shows that Reuters quoted Goldman Sachs.',
    'Photo: REUTERS/Adam Gray. LSEG shares rose.',
])
def test_body_mentions_photo_credits_and_data_credits_do_not_attribute_lseg(text):
    ident = identify.identify({1: 'A markets wrap.', 11: text}, meta('lseg reuters wrap.pdf', 'other'), FOLDER)
    assert ident.publisher == 'unknown' and ident.publisher_source == 'none'
    assert ident.publisher_page is None and ident.publisher_quote is None


def test_data_credit_names_the_copyright_holder_not_lseg():
    ident = identify.identify({1: 'A markets wrap.', 11: '© 2026 Independent Publisher. Data supplied by LSEG.'},
                              meta('lseg reuters wrap.pdf', 'other'), FOLDER)
    assert ident.publisher == 'Independent Publisher' and ident.publisher_source == 'document'
    assert ident.publisher_page is None and ident.publisher_quote is None


@pytest.mark.parametrize('folder', ['lseg', 'london stock exchange group', 'london stock exchange group plc'])
def test_lseg_folder_aliases_use_canonical_display(folder):
    ident = identify.identify({1: 'The Day Ahead'}, meta('wrap.pdf', folder), FOLDER)
    assert ident.publisher == 'LSEG' and ident.publisher_source == 'folder'
