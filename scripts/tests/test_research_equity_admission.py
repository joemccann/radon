"""Issuer research must prove current book membership before any copy models."""
from datetime import datetime, timedelta, timezone

import pytest
from research import book, marketdesk_filter, triage
from test_marketdesk_filter import decide, Select, WORDS


@pytest.mark.parametrize('title', [
    'ST Engineering (STEG.SI) New quarterly high of contract win; Buy',
    'Coca-Cola (KO) 3Q results',
    'TransUnion (TRU) 3Q First Take Revenue and margins outperform',
    'NXP Semiconductor (NXPI) earnings review',
    'Solaria (SLRS.MC) Results update',
    'S&P Global Inc. (SPGI) 3Q First Take',
    'Poste Italiane SpA (PST.MI) Reinstate at Neutral',
    'Galaxy Digital Inc(1)', 'Energy Fuels Inc(2)',
    'Inovance (300124.SZ) Earnings update',
    'Inovance (300124) Earnings update',
])
def test_off_book_subject_never_reaches_copy(title):
    selector = Select()
    result = decide(selector, title=title, pages={1: title + '\nRevenue increased 10%.'}, book={'HOOD', 'MU'})
    assert (result.outcome, result.reason_code) == ('dropped', 'EQUITY_RESEARCH_NOT_IN_BOOK')
    assert not selector.prompts


def test_incidental_in_book_body_reference_does_not_admit_primary_issuer():
    result = decide(title='TransUnion (TRU) Quarterly update',
                    pages={1: 'TransUnion (TRU) compares with Robinhood (HOOD). Rating: BUY Price Target $50.'}, book={'HOOD'})
    assert result.outcome == 'dropped'


@pytest.mark.parametrize('title', ['Robinhood (HOOD) Quarterly update',
    'Micron Technology, Inc investor meetings: product-demand assumptions and financing review'])
def test_in_book_subject_admitted_without_model_relevance(title):
    assert decide(title=title, pages={1: title + '\nRevenue increased 10%.'}, book={'HOOD', 'MU'}).outcome == 'publish'


@pytest.mark.parametrize('value', [None, 'HOOD', {'HOOD': True}])
def test_unknown_or_malformed_book_holds_company_copy(value):
    result = decide(title='Robinhood (HOOD) earnings', pages={1: 'Robinhood earnings'}, book=value)
    assert (result.outcome, result.reason_code) == ('held', 'EQUITY_RESEARCH_BOOK_UNAVAILABLE')


@pytest.mark.parametrize('title', ['BofA Earnings Tracker Week 2: Bad AI vibes clouding good fundamentals',
    'GS DeGrasse GS Financials & REITs Sector Specialist Factor Focus is back', 'EURUSD range test',
    'European equity strategy: positioning and market breadth'])
def test_broad_research_preserved_without_book_io(title):
    def forbidden():
        raise AssertionError('broad strategy must not need book')
    result = marketdesk_filter.decide(article_id='a', title=title, folders=['2026','October','Oct 26','Goldman Sachs'],
        folder_date='2026-10-26',pages={1:title+'\n'+WORDS},created=None,summary=[WORDS],root='/none',
        select=forbidden,book_tickers=forbidden,recent=[],index={})
    assert result.outcome == 'publish'


def test_company_with_unresolved_subject_is_held_not_admitted_by_body_ticker():
    result=decide(title='Acme Holdings plc quarterly review',
        pages={1:'Acme Holdings plc quarterly review\nPeer Robinhood (HOOD) has a Buy rating.'},book={'HOOD'})
    assert (result.outcome,result.reason_code)==('held','EQUITY_RESEARCH_SUBJECT_UNRESOLVED')


def test_sector_stock_picks_require_subject_resolution():
    result=decide(title='Japan Banks Post correction, quantifying risk factors and finding value',
        pages={1:'MUFG, SMFG and Mizuho retain Buy ratings with price-to-book upside.'},book={'HOOD'})
    assert result.outcome != 'publish'


def test_strict_book_never_falls_back_to_disk_or_partial_source(monkeypatch,tmp_path):
    import db.readers as readers
    monkeypatch.setattr(book,'DATA_DIR',tmp_path)
    (tmp_path/'portfolio.json').write_text('{"positions":[{"ticker":"HOOD"}]}')
    monkeypatch.setattr(readers,'read_watchlist_tickers',lambda: ['MU'])
    monkeypatch.setattr(readers,'read_latest_portfolio_snapshot',lambda: None)
    assert book.current_tickers() is None


@pytest.mark.parametrize('age,positions,expected', [
    (0,[{'ticker':'HOOD'},{'ticker':'MU   261218C00100000'}],frozenset({'HOOD','MU'})),
    (0,[],frozenset({'MU'})),(3600,[],None),(0,None,None),(0,[{}],None),
])
def test_strict_book_requires_fresh_complete_canonical_snapshot(monkeypatch,age,positions,expected):
    import db.readers as readers
    monkeypatch.setattr(readers,'read_watchlist_tickers',lambda: ['MU'])
    monkeypatch.setattr(readers,'read_latest_portfolio_snapshot',lambda:{'positions':positions,
        'last_sync':(datetime.now(timezone.utc)-timedelta(seconds=age)).isoformat()})
    assert book.current_tickers()==expected


@pytest.mark.parametrize('title', ['Aster Technologies quarterly results', 'Unknown issuer revenue update'])
def test_unnamed_issuer_financial_research_is_held(title):
    result=decide(title=title,pages={1:title+'\nRevenue and EBITDA exceeded estimates; EPS guidance was raised.'},book={'HOOD'})
    assert (result.outcome,result.reason_code)==('held','EQUITY_RESEARCH_SUBJECT_UNRESOLVED')


def test_mixed_issuer_list_cannot_pass_on_one_named_watched_subject():
    result=decide(title='BofA US1 list adds (SPCX)',
        pages={1:'Adds FOXA and SPCX, removes RNR from the stock list.'},book={'SPCX'})
    assert result.outcome=='held'


def test_all_explicit_primary_issuers_must_be_in_book():
    title='Micron (MU) and TransUnion (TRU) earnings comparison'
    assert decide(title=title,pages={1:title},book={'MU'}).outcome=='dropped'
    assert decide(title=title,pages={1:title},book={'MU','TRU'}).outcome=='publish'


@pytest.mark.parametrize('title,source', [
 ('MS Samsung C&T 3Q26 Beat: A Firmer Construction Outlook','Samsung C&T raised order guidance; operating profit beat consensus.'),
 ('GS KOREA FINAL ALL EYES ON SK HYNIX','SK Hynix DRAM capacity sold out through 2029; Samsung buyback details.'),
 ('Arbor bank desk: Acme deposit trends and quarterly profit outlook','Acme operating profit outlook.'),
 ('Global Shipyard Logbook Resilient industry demand','Hengli won 35% of new ship orders, supporting a margin expansion thesis.'),
 ('Citi The Point for Latin America','PBR shares fell 23%; equity strategists prefer Petrobras.'),
 ('GS US Industrials and Materials let the games begin','JCI, PWR and NVT have the strongest fundamental scores of 9.'),
])
def test_unlabelled_company_research_does_not_silently_default_to_broad(title,source):
    assert decide(title=title,pages={1:source},book={'HOOD','MU'}).outcome!='publish'


@pytest.mark.parametrize('title,source', [
 ('Best of Wire G10 events guide and gameplan (repost)','USD/CAD rose as investors priced a BoC hike.'),
 ('Mexico Non Oil Trade Balance Surplus (Ramos)','Mexico non-oil exports increased and GDP growth accelerated.'),
 ('US Midterm Elections market impact','Federal revenue grew 3%; the fiscal deficit was 6% of GDP.'),
 ('Emerging markets bonds for all investors','Corporate bond spreads tightened; Cemex bond valuation remains attractive.'),
 ('Keep moving forward','France fiscal revenues fell; debt-to-GDP and sovereign bond spreads increased.'),
 ('Nomura McElligott CLEANSING','Nasdaq volatility fell; market positioning shifted before semiconductor earnings; sector revenue grew.'),
 ('Infrastructure private credit','Private credit valuations and aggregate loan revenue increased.'),
 ('Transformational Innovation Opportunities (TRIO)','DRAM industry demand growth, aggregate investment and supply.'),
])
def test_source_establishes_nonissuer_macro_credit_or_sector_scope(title,source):
    assert decide(title=title,pages={1:source},book={'HOOD','MU'}).outcome=='publish'


def test_macro_wrapper_cannot_hide_explicit_company_rating_section():
    result=decide(title='European Equity Daily',pages={1:'Gas prices rose. The bank retains Hold on Tullow and raises its price target.'},book={'MU'})
    assert result.outcome=='held'


@pytest.mark.parametrize('title,body', [
 ('Memory shortage through 2030','Hynix memory outlook. Seagate revenue beat; EPS guidance is $9.20. The FOMC meets today.'),
 ('INFICON margins','INFICON EBIT margin is 23.5%; inflation limits expansion.'),
 ('GS flags $RCL bookings and $KO valuation','RCL EPS target is $20; KO trades at 27x.'),
 ('$HLT falls as $RCL rises','Revenue and EPS guidance missed consensus. Flow-driven volatility.'),
 ('Audi factory investment','Audi pretax returns exceed 30%; annual revenues rise. US FX volatility fell.'),
 ('GBX earns $0.39 vs consensus','Greenbrier adjusted EPS was $0.39; Goldman stays Sell despite a better cycle.'),
 ('Oil E&P FCF yield estimates','DVN estimated yield is 14.1%, PR 14.1%, OXY 12%; equity valuations use $73 oil.'),
 ('Nexchip net income estimates cut','Net income forecasts fell 15%; its target implies 64.5% upside. Demand volatility remains.'),
 ('Brazil equity valuation','Fiscal concerns remain; the desk favors buying Vale near $12.'),
])
def test_issuer_thesis_cannot_use_late_macro_or_flow_footer(title,body):
    assert decide(title=title,pages={1:body},book={'HOOD','MU'}).outcome!='publish'


@pytest.mark.parametrize('title,body', [
 ('Goldman cuts $HOOD target','Robinhood earnings and EPS outlook.'),
 ('Micron supply agreements stretch','Memory supply agreements cover three years.'),
])
def test_generated_watched_subject_names_are_recognized(title,body):
    assert decide(title=title,pages={1:body},book={'HOOD','MU'}).outcome=='publish'


def test_watched_title_does_not_exempt_off_book_body_recommendation():
    result=decide(title='Robinhood (HOOD) results',pages={1:'Robinhood EPS rose.\n\nWalmart (WMT) Rating: Buy Price Target $100.'},book={'HOOD'})
    assert result.outcome!='publish'


def test_watched_title_with_incidental_peer_mention_stays_eligible():
    result=decide(title='Robinhood (HOOD) results',pages={1:'Robinhood EPS rose.\n\nPeers include Walmart (WMT).'},book={'HOOD'})
    assert result.outcome=='publish'


@pytest.mark.parametrize('title,body', [
 ('$HOOD outlook', 'Walmart has a price target of $150.'),
 ('Robinhood outlook', 'Robinhood platform flows improved. Enphase revenue guidance rose and its EPS target is $2.'),
 ('Arbor trims Robinhood and Acme targets, keeps Buy ratings',
  "Arbor Research reduced price targets for Robinhood, Acme Energy and Boreal Water while maintaining Buy ratings in its January 12, 2030 weekly recap. The targets changed while the stock recommendations remained positive.\n\n"
  "Cinder Data received a higher target and retained Neutral; Arbor cited housing-related demand risk. Delta Labs received a target increase and stayed Buy.\n\n"
  "In another region, Elm Shipping moved from Neutral to Buy. Fern Gaming received a higher target while its Sell rating was maintained."),
 ('Company research update', 'Robinhood Markets Inc. (HOOD) Rating: BUY\n\nTransUnion (TRU) has a price target of $150.'),
])
def test_partial_primary_resolution_cannot_skip_other_issuer_claims(title,body):
    assert decide(title=title,pages={1:body},book={'HOOD','MU'}).outcome!='publish'


def test_source_desk_wrapper_cannot_admit_company_cash_flow_thesis():
    result=decide(title='Arbor industrials morning briefing: Acme Corp reports $3bn cash flow',
        pages={1:'Acme Corp generated $3bn FCF. Industrial market flows were quiet.'},book={'HOOD','MU'})
    assert result.outcome!='publish'


@pytest.mark.parametrize('watch', [None, {'MU': True}, ['MU', None], [' ']])
def test_strict_book_rejects_incomplete_watchlist(monkeypatch,watch):
    import db.readers as readers
    monkeypatch.setattr(readers,'read_watchlist_tickers',lambda:watch)
    monkeypatch.setattr(readers,'read_latest_portfolio_snapshot',lambda:{'positions':[],
        'last_sync':datetime.now(timezone.utc).isoformat()})
    assert book.current_tickers() is None


@pytest.mark.parametrize('stamp', [None, 'invalid', '2026-10-28T12:00:00'])
def test_strict_book_rejects_unverifiable_snapshot_time(monkeypatch,stamp):
    import db.readers as readers
    monkeypatch.setattr(readers,'read_watchlist_tickers',lambda:['MU'])
    monkeypatch.setattr(readers,'read_latest_portfolio_snapshot',lambda:{'positions':[], 'last_sync':stamp})
    assert book.current_tickers() is None


def test_strict_book_connection_failure_is_unavailable(monkeypatch):
    import db.readers as readers
    def failure():
        raise ConnectionError('canonical store unavailable')
    monkeypatch.setattr(readers,'read_watchlist_tickers',failure)
    assert book.current_tickers() is None


def test_book_callback_failure_holds_and_unknown_scope_never_assumes_macro():
    from research import equity_admission
    def failure():
        raise ConnectionError('canonical store unavailable')
    assert equity_admission.decide('Robinhood outlook','Earnings update',book_tickers=failure)[:2]==(
        'held','EQUITY_RESEARCH_BOOK_UNAVAILABLE')
    assert equity_admission.decide('Daily bulletin','New information',book_tickers=failure)[:2]==(
        'held','EQUITY_RESEARCH_SUBJECT_UNRESOLVED')


def test_lowercase_secondary_name_is_not_an_implicit_heading_reference():
    assert decide(title='$HOOD outlook',pages={1:'walmart has a price target of $150.'},book={'HOOD'}).outcome=='held'


@pytest.mark.parametrize('title,body', [
    ('Acme data center expansion', 'Acme revenue rose 10%.'),
    ('$HOOD outlook', 'Walmart: OW, PT $150.'),
    ('$HOOD outlook', 'walmart has a target price of $150.'),
])
def test_unresolved_fundamentals_and_short_rating_forms_never_inherit_scope(title,body):
    assert decide(title=title,pages={1:body},book={'HOOD','MU'}).outcome!='publish'


def test_synthetic_watched_revision_with_multiple_financial_metrics_remains_eligible():
    body=("Robinhood revenue was $2.4bn and adjusted EPS was $0.70. "
          "Robinhood net interest income was 5% lower, while expenses fell 3%.\n\n"
          "Goldman cut the $HOOD price target to $95. The earnings estimate changed in 2032. "
          "Risks include transaction fees, retail activity and order flow regulation.")
    assert decide(title='Robinhood (HOOD) financial revision',pages={1:body},book={'HOOD','MU'}).outcome=='publish'


def test_company_net_debt_does_not_establish_credit_research_scope():
    result=decide(title='Acme data center expansion',
        pages={1:'Acme revenue rose 10% and its net debt fell 5%.'},book={'HOOD','MU'})
    assert result.outcome!='publish'


def test_explicit_credit_research_can_include_issuer_fundamentals():
    result=decide(title='JPM Credit Calls Boeing',
        pages={1:'Boeing revenue rose 10%; EBITDA recovered and bond spreads tightened.'},book={'HOOD','MU'})
    assert result.outcome=='publish'


@pytest.mark.parametrize('body,expected', [
    ('Acme revenue rose 10%; EBITDA recovered and bond spreads tightened.', 'publish'),
    ('Acme revenue rose 10%; Acme (ACME) has a stock price target of $150.', 'blocked'),
])
def test_explicit_credit_scope_with_symbol_does_not_exempt_stock_recommendations(body,expected):
    result=decide(title='Acme (ACME) credit research',pages={1:body},book={'HOOD','MU'})
    assert (result.outcome=='publish')==(expected=='publish')


@pytest.mark.parametrize('title', ['Credit Suisse Amazon earnings review', 'Credit Agricole Amazon earnings review'])
def test_publisher_credit_name_does_not_establish_credit_research(title):
    assert decide(title=title,pages={1:'Amazon revenue rose 10%; EPS increased 20%.'},book={'HOOD','MU'}).outcome!='publish'


@pytest.mark.parametrize('metric', ['earnings','profit','profits','margins','sales','bookings','orders','cash flow','capex'])
def test_company_financial_predicate_taxonomy_overrides_generic_topic_words(metric):
    result=decide(title='Acme data center expansion',
        pages={1:f'Acme {metric} rose 10% on data center demand.'},book={'HOOD','MU'})
    assert result.outcome!='publish'


@pytest.mark.parametrize('metric', ['sector earnings','industry profit','aggregate margins','retail sales','factory orders'])
def test_aggregate_financial_measures_remain_macro(metric):
    result=decide(title='US macro outlook',pages={1:f'{metric} rose 10%; GDP growth accelerated.'},book={'HOOD','MU'})
    assert result.outcome=='publish'


@pytest.mark.parametrize('title,body', [
    ('Acme earnings benefit from bonds rally','Acme revenue rose 10%; EPS rose 20%.'),
    ('Goldman Acme bond yields and earnings','Acme net income rose 20%.'),
])
def test_incidental_bond_market_reference_is_not_credit_research(title,body):
    assert decide(title=title,pages={1:body},book={'HOOD','MU'}).outcome!='publish'


@pytest.mark.parametrize('metric', ['sector gross margin','industry net income','aggregate operating profit'])
def test_aggregate_income_and_margin_measures_are_not_stock_theses(metric):
    assert decide(title='US macro outlook',pages={1:f'{metric} rose 10%; GDP growth accelerated.'},book={'HOOD','MU'}).outcome=='publish'


@pytest.mark.parametrize('title,body', [
    ('GS Stankard Equity Positioning and Key Levels',
     'CTAs are forecast to sell $23.5bn of global equities; month-end positioning is the subject.'),
    ('Goldman USDJPY Growth Hedge',
     'The USD/JPY hedge costs about 10% and could pay about 10x in the stated growth-shock scenario.'),
    ('Middle East FX', "Egypt's pound reached 56/USD as the currency weakened."),
    ('Emerging Markets Morning Pulse', '$VESTL bond spreads widened 704 bps.'),
    ('Global Direct Property Chartset', 'Global property deal volume is forecast to grow 15% in 2026.'),
    ('Quant Factor Screen', 'The report blends fundamental and quant ratings to find intersection stocks.'),
])
def test_primary_non_equity_scope_can_include_company_or_market_measurements(title, body):
    assert decide(title=title, pages={1:body}, book={'HOOD','MU'}).outcome == 'publish'


@pytest.mark.parametrize('title,body', [
    ('US macro and semiconductor flows', 'Acme raised FY2027 revenue guidance and expects margins to expand.'),
    ('Cross-asset market update', 'Acme (ACME) keeps its Buy rating with a $150 price target.'),
    ('Acme data center expansion', 'Acme revenue rose 10% on data center demand.'),
    ('US macro outlook', 'Acme Inc. quarterly earnings showed revenue rose 10% and EBITDA rose 12%.'),
    ('Macro update', 'Acme revenue rose 10% and margins reached 20%.'),
    ('Macro update', 'Contoso earnings increased 10%; sales reached $2bn. GDP growth is 3%.'),
    ('Arbor consumer-company digest', 'Acme Drinks Q2 revenue grew 7.1%; Boreal Brewing beat volume estimates and retained its FY2032 guidance.'),
    ('Global shipbuilding update', 'Hengli won 35% of global new ship orders and expects orderbook cover to fall after its capacity additions.'),
    ('Crude up 2% as industrials earnings split', '$GPI missed on EPS at $9.36 vs $9.50; $AOS is exiting its China business. Brent is up 2%.'),
])
def test_market_context_does_not_exempt_company_guidance_or_stock_thesis(title, body):
    assert decide(title=title, pages={1:body}, book={'HOOD','MU'}).outcome != 'publish'


@pytest.mark.parametrize('issuer', [
    'Acme', 'China Mobile', 'Capital One', 'The Trade Desk', 'National Grid',
    'Goldman', 'JPMorgan', 'OpenAI', '3M', 'LVMH', 'BHP Group', 'Vantage Data Centers',
    'acme', 'china mobile', 'capital one', 'national grid',
])
@pytest.mark.parametrize('claim', [
    'revenue rose 10%', 'earnings increased 10%', 'EPS rose 20%',
    'sales reached $2bn', 'annual earnings reached $2bn',
])
def test_macro_title_does_not_admit_unresolved_issuer_financial_claims(issuer, claim):
    result = decide(title='Macro update', pages={1:f'{issuer} {claim}.'}, book={'HOOD','MU'})
    assert result.outcome == 'held'


@pytest.mark.parametrize('issuer', ['china mobile', 'annual china mobile', 'japanese national grid'])
def test_lowercase_entity_matching_never_exempts_a_company_by_its_first_word(issuer):
    result = decide(title='Macro update', pages={1:f'{issuer} revenue rose 10%.'}, book={'HOOD','MU'})
    assert result.outcome == 'held'


@pytest.mark.parametrize('phrase', [
    'annual retail sales', 'private sector hourly earnings growth',
    'better-than-expected earnings', 'hyperscaler capex',
])
def test_known_aggregate_phrase_exemptions_match_complete_measurement(phrase):
    result = decide(title='US macro outlook',
                    pages={1:f'{phrase} rose 10%; GDP growth accelerated.'}, book={'HOOD','MU'})
    assert result.outcome == 'publish'


def test_generic_etf_footer_does_not_reclassify_company_earnings_as_market_context():
    body='Acme revenue rose 10% and margins reached 20%. ETF flows were positive.'
    assert decide(title='Macro update', pages={1:body}, book={'HOOD','MU'}).outcome == 'held'


def test_substantive_separate_flow_paragraph_cannot_resolve_company_earnings():
    body=('Acme revenue rose 32% and margins reached 80%.\n\n'
          'Institutional tech liquidations are abating as supply pressure eases.\n\n'
          'Cross-asset correlations shifted with tech relative performance.')
    assert decide(title='Cross-asset sentiment', pages={1:body}, book={'HOOD','MU'}).outcome != 'publish'


@pytest.mark.parametrize('title,body', [
    ('Quarterly consumer confidence reaches 104 against a forecast of 96',
     'Consumer confidence reached 104. New home sales declined 1.3%; regional survey estimates crossed zero.'),
    ('Nomura lifts its Q2 GDP estimate to 2.1%',
     'Nomura sees GDP at 2.1% after capital goods orders increased 0.4%. Capex paths diverged between industries.'),
    ('ANZ sees NZ unemployment at 5.6%',
     'ANZ expects unemployment at 5.6%. Private sector hourly earnings growth is forecast at 3.6%.'),
])
def test_named_source_and_economic_series_are_not_company_earnings(title, body):
    assert decide(title=title, pages={1:body}, book={'HOOD','MU'}).outcome == 'publish'


@pytest.mark.parametrize('title,body', [
    ('$VESTL spreads widen 704 bps', '$VESTL is in the credit snapshot; its 5-year bond spread widened 704 bps.'),
    ('Cemex 2036 bond yields 6.37% to worst', 'Cemex 5.75% senior unsecured bond due 2036 yields 6.37% to worst.'),
    ('10 bonds carry equity conversion risk', 'The bonds have equity conversion provisions, coupon deferral and principal write-down risk.'),
    ('JPM Credit Calls Boeing', 'Boeing is Overweight in the October 28 credit calls; free cash flow supports bond coverage.'),
])
def test_debt_instrument_and_credit_scope_are_not_equity_recommendations(title, body):
    assert decide(title=title, pages={1:body}, book={'HOOD','MU'}).outcome == 'publish'


def test_asset_class_overweight_and_index_dividend_yield_are_not_stock_calls():
    assert decide(title='Risk appetite update', pages={1:'OW Equities, UW Credit, and OW Bonds. S&P 500 dividend yield is 1.4%.'}, book={'HOOD','MU'}).outcome == 'publish'


@pytest.mark.parametrize('title,body', [
    ('Macro update', 'Acme is our preferred stock in semiconductors.'),
    ('Macro update', 'We recommend buying Acme shares for its superior pricing power.'),
    ('Semiconductor sector strategy', 'Acme benefits from a stronger product mix and remains our top pick.'),
    ('Macro update', 'Acme offers an attractive risk-reward and is our top investment idea.'),
    ('$HOOD outlook', 'Acme is our preferred stock in semiconductors.'),
    ('$HOOD outlook', 'capital one EPS rose 20%.'),
])
def test_stock_recommendations_and_unknown_issuer_claims_do_not_inherit_macro_or_ticker_scope(title, body):
    assert decide(title=title, pages={1:body}, book={'HOOD','MU'}).outcome == 'held'


def test_publisher_attribution_is_not_misread_as_an_issuer_risk_reward_thesis():
    body = 'Morgan Stanley sees attractive risk-reward in a broader European equity rebound.'
    assert decide(title='Cross-asset European strategy', pages={1:body}, book={'HOOD','MU'}).outcome == 'publish'


@pytest.mark.parametrize('body', [
    'Acme is attractively valued.',
    'Acme is undervalued and offers rerating upside.',
    'Acme shares are cheap relative to peers.',
    'Acme has downside as its valuation premium compresses.',
    'Acme is attractively valued while HOOD remains our watchlist focus.',
])
def test_qualitative_issuer_valuation_thesis_is_held_even_in_macro_wrapper(body):
    assert decide(title='Macro update', pages={1:body}, book={'HOOD','MU'}).outcome == 'held'


@pytest.mark.parametrize('body', [
    'S&P 500 is attractively valued on earnings and EUR/USD is undervalued versus fair value.',
    'Valuation multiples for the Nasdaq 100 remain below their long-run range.',
])
def test_index_and_fx_valuation_roles_remain_nonissuer_scope(body):
    assert decide(title='Index and FX valuation', pages={1:body}, book={'HOOD','MU'}).outcome == 'publish'


@pytest.mark.parametrize('title,body', [
    ('Energy futures imply eurozone inflation', "The ECB's forecast puts inflation at 2.9%. The bank sees that forecast as conditional on energy futures."),
    ('Intraday FX View', 'Safra expects a BoJ hold after Tokyo CPI. USD/JPY would reach 155 in a 25bp hike scenario.'),
    ('Rates preview', 'No rate hike. Hawkish guidance remains possible from the central bank.'),
    ('Global Direct Property Chartset', 'Global property deal volume is projected at USD1tr. Its forecast is for 15% growth.'),
    ('Fiscal policy update', "The government's target is a 5% deficit. France has a negative sovereign outlook."),
    ('US GDP tracking', 'US GDP is tracking at 3.7%, above the prior forecast.'),
    ('Index valuation', 'TOPIX forward P/E is 14.9x, above its historical average.'),
    ('European chemical margins', "European ethylene-naphtha margins rose 6%. Europe's margins rose 24% year over year."),
    ('Japan Macro', 'The government is reviewing tax and non-tax revenues to fund relief.'),
])
def test_forward_predicate_requires_company_financial_object_not_macro_forecast(title, body):
    assert decide(title=title, pages={1:body}, book={'HOOD','MU'}).outcome == 'publish'


@pytest.mark.parametrize('body', [
    'Acme expects revenue growth next year. ETF flows were positive.',
    'Acme forecasts higher earnings next year. GDP growth is 3%.',
    'Acme EPS guidance is above consensus. Institutional tech liquidations are abating.',
    'Acme revenue outlook improved. Cross-asset correlations shifted.',
])
def test_company_forward_financial_thesis_blocks_even_with_market_transmission(body):
    assert decide(title='Macro update', pages={1:body}, book={'HOOD','MU'}).outcome != 'publish'


@pytest.mark.parametrize('title,body', [
    ('Remy Martin growth beats, FX adds upgrade potential',
     'Remy Martin Q2 growth came in at12.4%. Carlsberg has an FX tailwind to net revenue and op profit. Heineken beat Q3 volumes; FY26 guidance is unchanged.'),
    ('Oil E&P FCF yield estimates',
     'Goldman estimates free cash flow yield at12.1%. DVN yield is14.1%; OXY is12%. Its equity valuations use $73 WTI.'),
])
def test_company_operating_and_equity_cashflow_theses_cannot_hide_in_fx_or_oil(title, body):
    assert decide(title=title, pages={1:body}, book={'HOOD','MU'}).outcome != 'publish'


@pytest.mark.parametrize('title,body', [
    ('Global Markets Daily Macro', "UBS's comparison displays a Shiller P/E of24. Inflation stands at1.8%."),
    ('Japan quant screen', 'The Japan screen identifies the top50 stocks using AI Adopters beta. The portfolio is95% IT/Services. Forward P/E is25x.'),
    ('Industry demand tracker', 'DRAM demand is forecast to grow40%. MSCI Korea trades at1.2x P/B. Earnings sustainability matters for that valuation.'),
    ('Desk color', 'Top of Book Liquidity is up25%. Hedge fund short sales are32% of the tape.'),
    ('Property weekly', 'Primary residential floor area sold fell4% across75 cities. MTD October sales remain below last year.'),
])
def test_report_benchmark_and_aggregate_market_roles_are_not_issuer_names(title,body):
    assert decide(title=title,pages={1:body},book={'HOOD','MU'}).outcome=='publish'


def test_buyback_plural_still_requires_review_in_mixed_company_flow_copy():
    assert decide(title='Macro flows',pages={1:'Acme share buybacks remain under consideration. ETF flows were positive.'},book={'HOOD','MU'}).outcome!='publish'


@pytest.mark.parametrize('title,body', [
    ('Macro update', 'Acme has a durable competitive advantage and attractive upside.'),
    ('Macro update', 'Acme revenue rose10%. ETF buying was positive.'),
    ('Macro update', 'acme revenue rose10%. ETF buying was positive.'),
    ('$HOOD outlook', 'china mobile revenue rose10%.'),
    ('$HOOD outlook', 'capital one EPS rose20%.'),
    ('$HOOD outlook', 'national grid has a target price of $150.'),
    ('$HOOD outlook', 'acme adjusted revenue rose10%.'),
    ('Company research update', 'Robinhood Markets Inc. (HOOD) Rating: BUY\n\ncapital one EPS rose20%.'),
])
def test_practical_adversarial_scope_never_inherits_generic_market_or_known_issuer(title,body):
    assert decide(title=title,pages={1:body},book={'HOOD','MU'}).outcome!='publish'


def test_corporate_news_fact_is_not_itself_an_equity_investment_thesis():
    assert decide(title='Macro update',pages={1:'Acme opened a new factory yesterday.'},book={'HOOD','MU'}).outcome=='publish'


@pytest.mark.parametrize('title',['Macro update','$HOOD outlook'])
def test_unknown_bank_own_income_is_not_publisher_attribution(title):
    assert decide(title=title,pages={1:'jpmorgan net interest income rose 10%.'},book={'HOOD','MU'}).outcome!='publish'


@pytest.mark.parametrize('title,body', [
    ('Macro update','annual hyperscaler capex grew 10%; sector demand increased.'),
    ('Macro update','Chinese Industry earnings rose 10%; GDP growth accelerated.'),
    ('Macro update',"Goldman's sector earnings outlook assumes 3% growth."),
    ('Asset allocation','Allocation: OW Equities, UW Credit. Risk appetite is improving.'),
    ('Fiscal update','The fiscal revenue outlook reflects changes to government tax policy.'),
])
def test_complete_aggregate_or_reporting_roles_do_not_infer_company_identity(title,body):
    assert decide(title=title,pages={1:body},book={'HOOD','MU'}).outcome=='publish'


def test_exchange_qualified_second_issuer_cannot_inherit_watched_title():
    result=decide(title='$HOOD outlook',pages={1:'ACME-NASDAQ Rating: Buy. Price target is $150.'},book={'HOOD','MU'})
    assert result.outcome!='publish'
    assert 'ACME' in result.review['equity_admission']['subjects']


def test_index_dividend_measurement_does_not_exempt_separate_company_dividend_claim():
    assert decide(title='Macro update',pages={1:'S&P 500 dividend yield is 1.4%. Acme dividend yield is 3%.'},book={'HOOD','MU'}).outcome!='publish'


@pytest.mark.parametrize('title',['Macro update','$HOOD outlook'])
@pytest.mark.parametrize('issuer',['Acme','acme'])
@pytest.mark.parametrize('thesis',[
    'is undervalued and offers rerating upside',
    'shares are cheap relative to peers',
    'has downside as its valuation premium compresses',
    'shares trade at a discount to peers',
    'trades at 8x earnings versus peers at 12x',
    'is a high-quality compounder with durable economics',
    'has a moat and attractive returns',
    'offers an attractive risk-reward',
])
def test_equity_thesis_family_shares_standalone_and_secondary_subject_gate(title,issuer,thesis):
    assert decide(title=title,pages={1:f'{issuer} {thesis}.'},book={'HOOD','MU'}).outcome!='publish'


@pytest.mark.parametrize('issuer',['China National Grid','China National Petroleum','Chinese Corporate Bank'])
def test_country_and_macro_role_prefix_cannot_exempt_full_company_name(issuer):
    assert decide(title='Macro update',pages={1:f'{issuer} revenue rose10%.'},book={'HOOD','MU'}).outcome!='publish'


@pytest.mark.parametrize('thesis',['is undervalued and offers rerating upside','is a high-quality compounder with durable economics'])
def test_watched_issuer_own_qualitative_thesis_still_allowed(thesis):
    assert decide(title='Robinhood outlook',pages={1:f'Robinhood {thesis}.'},book={'HOOD','MU'}).outcome=='publish'


@pytest.mark.parametrize('title',['Macro update','$HOOD outlook'])
@pytest.mark.parametrize('issuer',['Acme','acme'])
@pytest.mark.parametrize('thesis',[
    'is expensive versus peers',
    'is mispriced',
    'forecasts higher earnings next year',
    'has a free cash flow yield of10%',
])
def test_all_valuation_forward_and_cashflow_claims_enter_the_shared_subject_scan(title,issuer,thesis):
    body=f'{issuer} {thesis}. Institutional tech liquidations are abating. Cross-asset correlations shifted.'
    assert decide(title=title,pages={1:body},book={'HOOD','MU'}).outcome!='publish'


@pytest.mark.parametrize('title,body',[
    ('Volatility update','AI implied volatility is cheap at the15th percentile.'),
    ('Rates outlook','Goldman calls OAT yields significantly overvalued versus fundamentals.'),
    ('Commodity futures','Nearby futures are more expensive than later contracts because inventories are scarce.'),
    ('Index valuation','The broad index shows equities are relatively cheap versus bonds.'),
    ('USDJPY puts a cheap hedge','USDJPY puts cost10% and pay10x in the specified macro growth shock.'),
])
def test_instrument_pricing_is_not_a_company_equity_thesis(title,body):
    assert decide(title=title,pages={1:body},book={'HOOD','MU'}).outcome=='publish'


@pytest.mark.parametrize('title',['Macro update','$HOOD outlook'])
@pytest.mark.parametrize('issuer',['Acme','acme'])
@pytest.mark.parametrize('recommendation',[
    'We recommend buying {issuer} shares.',
    'We recommend selling {issuer} stock.',
    'Our preferred stock is {issuer}.',
    'Buy {issuer} shares on weakness.',
    'We rate {issuer} Buy.',
])
def test_recommendation_object_is_an_issuer_subject_not_the_watched_heading(title,issuer,recommendation):
    assert decide(title=title,pages={1:recommendation.format(issuer=issuer)},book={'HOOD','MU'}).outcome!='publish'


@pytest.mark.parametrize('recommendation',[
    'We recommend buying Robinhood shares.',
    'Our preferred stock is Robinhood.',
    'We rate HOOD Buy.',
])
def test_resolved_watched_recommendation_object_remains_eligible(recommendation):
    assert decide(title='$HOOD outlook',pages={1:recommendation},book={'HOOD','MU'}).outcome=='publish'

@pytest.mark.parametrize('caption,symbol,expected',[
    ('Robinhood (HOOD): Buy, price target $150.','HOOD','publish'),
    ('Meta (META): Buy, price target $850.','META','dropped'),
])
def test_rendered_caption_heading_resolves_its_own_stock_subject(caption,symbol,expected):
    from research.equity_admission import decide as admission
    result=admission('Dispersion shifted','New sector evidence.\n\n'+caption,book_tickers=lambda:{'HOOD'})
    assert result[0]==expected
    assert symbol in result[2]

@pytest.mark.parametrize('title,body',[
    ('Acme equity research','New issuer evidence.'),
    ('Macro update','acme revenue rose 10%.'),
    ('Macro update','Acme has a durable competitive advantage.'),
])
def test_later_watched_caption_cannot_supply_an_unresolved_primary_subject(title,body):
    from research.equity_admission import decide as admission
    result=admission(title,body+'\n\nRobinhood (HOOD): Buy, price target $150.',book_tickers=lambda:{'HOOD'})
    assert result[0]!='publish'

@pytest.mark.parametrize('recommendation',['Buy acme shares on weakness.','We rate acme Buy.'])
def test_credit_role_cannot_bypass_the_same_recommendation_object_family(recommendation):
    from research.equity_admission import decide as admission
    assert admission('Credit research',recommendation,book_tickers=lambda:{'HOOD'})[0]!='publish'

@pytest.mark.parametrize('title',['Macro update','$HOOD outlook','Credit research'])
@pytest.mark.parametrize('caption',[
    'Regional equity ratings: Acme moves to Buy, Boreal and Cinder move to Neutral, and Delta Materials is rated Buy.',
    'Equity rating changes across North, South and Central regions, with new and old ratings.',
])
def test_synthetic_rendered_equity_rating_tables_cannot_escape_any_scope_branch(title,caption):
    from research.equity_admission import decide as admission
    assert admission(title,'Hedge fund exposure fell.\n\n'+caption,book_tickers=lambda:{'HOOD'})[0]!='publish'

@pytest.mark.parametrize('title',['Macro update','$HOOD outlook','Credit research'])
@pytest.mark.parametrize('call',['acme is rated Buy.','acme moves to Neutral.'])
def test_unnamed_rating_transition_uses_complete_subject_scan(title,call):
    from research.equity_admission import decide as admission
    assert admission(title,call,book_tickers=lambda:{'HOOD'})[0]!='publish'

@pytest.mark.parametrize('call',['Robinhood is rated Buy.','Robinhood moves to Neutral.'])
def test_rating_transition_for_resolved_watched_issuer_remains_allowed(call):
    from research.equity_admission import decide as admission
    assert admission('$HOOD outlook',call,book_tickers=lambda:{'HOOD'})[0]=='publish'

@pytest.mark.parametrize('title',['Macro update','$HOOD outlook','Credit research'])
@pytest.mark.parametrize('body',[
    "We recommend buying acme's shares.",
    'We recommend buying shares in acme.',
    '$HOOD; we recommend buying shares in acme.',
    'We recommend buying Robinhood and acme shares.',
    'We recommend buying Robinhood shares and acme stock.',
    'Our preferred stock is Robinhood and acme.',
    'We rate Robinhood and acme Buy.',
])
def test_every_recommendation_action_requires_its_complete_object_to_resolve(title,body):
    from research.equity_admission import decide as admission
    assert admission(title,body,book_tickers=lambda:{'HOOD'})[0]!='publish'

@pytest.mark.parametrize('body',[
    'We recommend buying Robinhood shares and selling HOOD stock.',
    'We rate Robinhood Buy. Our preferred stock is HOOD.',
])
def test_each_fully_resolved_recommendation_object_can_publish(body):
    from research.equity_admission import decide as admission
    assert admission('$HOOD outlook',body,book_tickers=lambda:{'HOOD'})[0]=='publish'

@pytest.mark.parametrize('body',[
    'We rate HOOD Buy and acme Sell.',
    'We rate HOOD Buy, acme Neutral.',
])
def test_first_resolved_rating_cannot_license_a_coordinated_unresolved_rating(body):
    from research.equity_admission import decide as admission
    assert admission('$HOOD outlook',body,book_tickers=lambda:{'HOOD'})[0]!='publish'

@pytest.mark.parametrize('title',['Macro update','$HOOD outlook','Credit research'])
@pytest.mark.parametrize('body',[
    'We initiate coverage of acme.',
    'We reiterate Buy on acme.',
    'Equity recommendations: acme and boreal.',
])
def test_existing_coverage_and_equity_recommendation_families_share_fail_closed_resolution(title,body):
    from research.equity_admission import decide as admission
    assert admission(title,body,book_tickers=lambda:{'HOOD'})[0]!='publish'

@pytest.mark.parametrize('title',['Macro update','$HOOD outlook','Credit research'])
@pytest.mark.parametrize('body',['acme valuation implies 20% upside.','acme price-to-book multiple is 0.8x.'])
def test_existing_equity_valuation_terms_enter_all_scope_checks(title,body):
    from research.equity_admission import decide as admission
    assert admission(title,body,book_tickers=lambda:{'HOOD'})[0]!='publish'

@pytest.mark.parametrize('body',[
    'Our preferred stock is HOOD, acme.',
    'We recommend buying HOOD shares and acme.',
    'We recommend buying HOOD shares, acme too.',
])
def test_unconsumed_coordinated_target_text_never_inherits_the_first_object(body):
    from research.equity_admission import decide as admission
    assert admission('$HOOD outlook',body,book_tickers=lambda:{'HOOD'})[0]!='publish'

@pytest.mark.parametrize('title',['Macro update','$HOOD outlook','Credit research'])
@pytest.mark.parametrize('body',[
    'We upgrade acme to Buy.',
    'We maintain Buy on acme.',
    'Our price target for acme is $50.',
    'EPS forecasts for acme rise.',
    'Rating: Buy for acme.',
])
def test_every_existing_claim_family_holds_an_unsupported_explicit_object(title,body):
    from research.equity_admission import decide as admission
    assert admission(title,body,book_tickers=lambda:{'HOOD'})[0]!='publish'

@pytest.mark.parametrize('title,body',[
    ('Macro update','Acme revenue rose 10%. ETF inflows totaled $2bn.'),
    ('Macro update','Acme revenue rose 10%. Nasdaq volatility declined 20%.'),
    ('Macro update','Acme revenue rose 10%. Institutional tech liquidation fell $2bn. Cross-asset correlations shifted.'),
    ('Credit update','Acme revenue rose 10%. Bond spreads widened 20 bps.'),
])
def test_unrelated_measured_market_detail_never_resolves_company_fundamentals(title,body):
    from research.equity_admission import decide as admission
    assert admission(title,body,book_tickers=lambda:{'HOOD'})[0]!='publish'

@pytest.mark.parametrize('body',[
    'Our price target for HOOD is $50.',
    'We maintain Buy on HOOD.',
    'We upgrade HOOD to Buy.',
    'We reiterate Buy on Robinhood.',
])
def test_explicit_wholly_resolved_known_object_is_not_unparsed(body):
    from research.equity_admission import decide as admission
    assert admission('$HOOD outlook',body,book_tickers=lambda:{'HOOD'})[0]=='publish'

@pytest.mark.parametrize('body',[
    'Our price target for Revenue Inc is $50.',
    'Our price target for revenue holdings is $50.',
    'Our price target for HOOD and acme is $50.',
    'We maintain Buy on HOOD and acme.',
    'We upgrade HOOD and acme to Buy.',
])
def test_resolved_object_requires_whole_identity_not_financial_prefix_or_first_member(body):
    from research.equity_admission import decide as admission
    assert admission('$HOOD outlook',body,book_tickers=lambda:{'HOOD'})[0]!='publish'

def test_parsed_price_object_also_consumes_coordinated_tail_without_action_verb():
    from research.equity_admission import decide as admission
    assert admission('$HOOD outlook','Our price target for HOOD is $50, acme too.',book_tickers=lambda:{'HOOD'})[0]!='publish'
