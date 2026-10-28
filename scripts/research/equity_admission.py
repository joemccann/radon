"""Deterministic issuer admission before source selection and final publication.

Title symbols establish primary issuers; incidental body mentions never grant
membership. Mixed stock recommendations and unresolved scope are held. Macro,
FX, credit and broad strategy need positive source context, not a relevance LLM.
"""
from __future__ import annotations

import re

# Identity aliases, never an allowlist: membership is fetched for every check.
_COMPANY_NAMES = {
    'micron': 'MU', 'robinhood': 'HOOD', 'galaxy digital': 'GLXY',
    'energy fuels': 'UUUU', 'st engineering': 'STEG.SI', 'transunion': 'TRU',
    's&p global': 'SPGI', 'poste italiane': 'PST.MI', 'solaria': 'SLRS.MC',
    'coca-cola': 'KO', 'nxp semiconductor': 'NXPI',
}
_NON_ISSUERS = frozenset({'US','UK','EU','FX','GDP','CPI','EPS','AI','CEO','CFO','ETF',
    'TRIO','TIC','PMI','PCE','PPI','FOMC','ECB','BOJ','BOE','UST','TIPS',
    'USD','EUR','JPY','GBP','CHF','CAD','AUD','NZD','DXY','SPX','SPY','NDX','QQQ','VIX','RUT','IWM','G10','SX7E','RSP','XHB','XLE','XLF','XLK','SOXX','SMH','TLT','HYG','LQD'})
_SYMBOL = r'(?:[A-Za-z][A-Za-z0-9]{0,5}|\d{4,6})(?:\.[A-Za-z]{1,3})?'
_PAREN = re.compile(r'\((' + _SYMBOL + r')\)')
_DOLLAR = re.compile(r'\$([A-Z][A-Z0-9]{0,5}(?:\.[A-Z]{1,3})?)\b')
_EXCHANGE = re.compile(r'\b([A-Z][A-Z0-9.]{0,8})-(?:NASDAQ|NYSE|LSE|TSX)\b')
_COMPANY = re.compile(r'\b(?:inc\.?|corp(?:oration)?|plc|holdings|limited|ltd\.?|s\.?p\.?a\.?)\b', re.I)
_EQUITY = re.compile(r'\b(?:price target|target price|initiation|initiat\w* coverage|reiterat\w* (?:buy|sell)|'
    r'(?:buy|sell|neutral|outperform|overweight|underweight) ratings?|ratings?[: ]+(?:buy|sell|neutral)|'
    r'price.to.book|valuation|earnings per share)\b', re.I)
_FINANCIALS = re.compile(r'\b(?:revenues?|EBITDA|EPS|gross margin|net(?: interest)? income|earnings guidance|quarterly results|'
    r'(?:earnings|net(?: interest)? income|profits?|margins?|sales|bookings?|orders?|cash[ -]flows?|capex)\s+'
    r'(?:rose|fell|grew|declined|increased|decreased|beat|missed|reached|totaled|was|were|is|are|of|at|forecast|guidance|outlook|growth|up|down|above|below))\b', re.I)
_ISSUER_LIST = re.compile(r'\b(?:US\s?1|stock picks?|conviction list|equity recommendations?)\b', re.I)
_ISSUER_TITLE = re.compile(r'\b(?:[1-4]Q\d{2} Beat|earnings review|investor meetings|capital allocation|results|\w+ Inc)\b', re.I)
_STOCK_TERMS = (r'price targets?|target prices?|(?:PT|TP)\s*(?:of |to |: ?)?[$€£¥]?\d+|'
                r'(?:ratings?[: ]+|rated |: )(?:OW|UW|EW|OP|UP)\b')
_ISSUER_CLAIM = re.compile(r'\b(?:' + _STOCK_TERMS + r'|buybacks?|dividend yields?|share prices?|stock picks?|'
    r'preferred (?:stocks?|shares?)|top picks?|best picks?|long ideas?|'
    r'durable competitive advantage|attractive upside|'
    r'top investment ideas?|'
    r'recommend(?:s|ed)? (?:buying|selling) .{0,50}\b(?:shares?|stock)|'
    r'our preferred (?:stock|share|equity)|'
    r'(?:buy|sell|neutral|overweight|underweight) ratings?|ratings?[: ]+(?:buy|sell|neutral)|'
    r'(?:reiterat\w*|maintain\w*|retain\w*|downgrad\w*|upgrad\w*)\b.{0,35}\b(?:buy|sell|overweight|underweight)|'
    r'rates [A-Z][\w ,&.]{0,70} (?:Buy|Sell|Neutral|Overweight)|retains? hold on|stays? sell|buying [A-Z][\w -]+ near|'
    r'(?-i:[A-Z]{2,6}) shares|fundamental (?:scores?|rankings?)|margin.expansion thesis|'
    r'earnings per share|EPS|EBIT(?:DA)?|FCF|cash.flow shock|net(?: interest)? income|net profit|gross margin|profit margin|operating profit|op profit|pretax returns|revenue guidance|guided to|'
    r'target implies.{0,30}upside|multiple is at)\b', re.I)
_CREDIT_SCOPE = re.compile(r'\b(?:credit (?:research|calls|strategy|weekly|outlook|spreads|curves?|markets?|commentary|update|review|flows?)|'
    r'credit snapshot|(?:corporate|private|sovereign|high.yield|investment.grade) credit|credit\s*[:-]|'
    r'(?:corporate|sovereign|government|emerging markets) bonds?|'
    r'(?:corporate|sovereign|government|emerging markets) bond (?:yields?|spreads?)|'
    r'bond (?:research|strategy|review|valuation|market outlook|due|maturing|coupon|'
    r'senior|unsecured|subordinated|convertible|spread(?:s)? widened|yield to worst)|'
    r'\byields?\s+\d+(?:\.\d+)?%?\s+to worst|'
    r'convertible bonds?|equity conversion provisions?|coupon deferral|principal write.down|'
    r'fixed[ -]income research|fixed[ -]income strategy)\b', re.I)
_STOCK_CLAIM = re.compile(r'\b(?:share price|stock picks?|top picks?|best picks?|long ideas?|'
    r'preferred (?:stocks?|shares?)|our preferred (?:stock|share|equity)|'
    r'top investment ideas?|'
    r'recommend(?:s|ed)? (?:buying|selling) .{0,50}\b(?:shares?|stock)|'
    r'buybacks?|dividend yield|'
    r'(?:buy|sell|neutral|overweight|underweight) ratings?|' + _STOCK_TERMS + r'|buying [A-Z][\w -]+ near)\b', re.I)
_MARKET_CONTEXT = re.compile(r'\b(?:macro|economics?|GDP|fiscal|sovereign|central bank|monetary|inflation|'
    r'consumer confidence|Fed|FOMC|ECB|BoJ|BoC|Treasur\w*|yields?|curves?|bond spreads?|corporate bonds?|'
    r'private credit|credit market|positioning|hedge funds?|volatility|options demand|gamma|CTA|flows?|'
    r'aggregate|industry|sector|market breadth|capital flows?|foreign investors|'
    r'pension|risk appetite|copper|Brent|WTI|gold|silver|natural gas|commodit\w*|'
    r'futures|open interest|dark pools?|S&P|Nasdaq|MSCI|KOSPI|CSI 300|SOX|'
    r'housing|home sales|Case.Shiller|exports?|imports?|trade balance|FDI|SSA|'
    r'climate|token costs?|data cent(?:er|re)|shipping rates|market intelligence|'
    r'semiconductor demand|DRAM demand|MLCC|multi.managers?|prime book|market shares|'
    r'country|countries|terminal rate|call spread|technically bearish|'
    r'RBNZ|RBA|BOE|SNB|rate expectations|midterms?|elections?|momentum|rotation|index|'
    r'US equities|European equities|global equities|enterprise AI|quantitative models|market growth|automation growth|jet fuel|'
    r'data.centre|property|market forecast)\b|'
    r'\b(?:USD|EUR|GBP|JPY|AUD|NZD|CAD|CHF)[/-](?:USD|EUR|GBP|JPY|AUD|NZD|CAD|CHF)\b', re.I)
_BROAD = re.compile(r'\b(?:equity strategy|earnings tracker|factor focus|sector specialist|macro|'
    r'economics|economic|FX|flows?|positioning|volatility|market breadth|market strategy|'
    r'central bank|monetary|inflation|GDP|consumer confidence)\b', re.I)
_PRIMARY_MARKET_SCOPE = re.compile(r'\b(?:cross[ -]asset|relative[ -]value|CTA|pension (?:equity )?selling|'
    r'institutional (?:tech )?(?:flows?|liquidation|buying|selling)|risk appetite|market positioning|'
    r'equity futures|Nasdaq futures|index futures|ETF (?:buying|selling|flows?|inflows?|outflows?)|'
    r'(?:FX|foreign exchange|currency|G10|haven demand|USD|EUR|GBP|JPY|AUD|NZD|CAD|CHF).{0,35}'
    r'(?:rate|target|pair|long|short|down|up|weak|strong|haven|forecast|scenario)|'
    r'\b(?:USD|EUR|GBP|JPY|AUD|NZD|CAD|CHF)[/-](?:USD|EUR|GBP|JPY|AUD|NZD|CAD|CHF)\b|'
    r'\b(?:USD|EUR|GBP|JPY|AUD|NZD|CAD|CHF)(?:USD|EUR|GBP|JPY|AUD|NZD|CAD|CHF)\b|'
    r'(?:pound|peso|yen|euro|franc|krone|currency).{0,55}(?:/USD|\bUSD\b|exchange rate|FX)|'
    r'GDP|inflation|unemployment|labou?r market|consumer confidence|retail price index|'
    r'fiscal deficit|debt.to.GDP|government bond issuance|sovereign bonds?|budget amendments|'
    r'trade surplus|trade balance|capital goods orders|crude inventories|Brent|WTI|natural gas|'
    r'property deal volume|home sales|housing market|coffee stocks|arabica|'
    r'(?:bond|credit) spreads? (?:widen|tighten|rise|fall)|\$?[A-Z]{2,6} spreads? (?:widen|tighten|rise|fall) \d+\s*bps|'
    r'\b(?:sector|industry|aggregate|global|regional) (?:revenues?|earnings|net(?: interest)? income|profits?|margins?|sales|demand|investment|capex)\b|'
    r'DRAM (?:industry )?demand|semiconductor (?:ETF )?inflows?|inference chips? .{0,30}rebound|'
    r'factor screen|quant screen|dual.discipline screen|AI Adopters beta|intersection stocks|prime book|long/short positioning|'
    r'hedge funds? (?:added|cut|reduced|increased) shorts?|'
    r'Top of Book Liquidity|residential floor area sold|'
    r'long .{1,40} versus short .{1,40} trade|relative[ -]value trade|'
    r'call buying|options demand|[A-Z]{2,6} saw \d+[km]? \d+ calls|'
    r'long/short funds?|net leverage|gross exposure|net exposure|'
    r'global equity sales|month.end (?:pension )?selling|market shares|market breadth|'
    r'interest.rate futures|rates? (?:surged?|rallied?|repriced?|rose|fell)|'
    r'price.index|consumer price index|inflation (?:rate|expectations?)|'
    r'(?:employment|unemployment|labou?r market|wage growth|hourly earnings)|'
    r'(?:capital goods|durable goods|new home|retail price) orders?|'
    r'(?:crude|oil|gas|arabica|coffee) (?:prices?|inventories|stocks?)|'
    r'(?:government|sovereign|corporate) bond (?:issuance|yields?|spreads?)|'
    r'convertible bonds?|equity conversion provisions?|coupon deferral|principal write.down|'
    r'AI enablers?[- ]to[- ]challenged|enablers? versus challenged|relative performance|'
    r'AI short.squeeze|short.squeeze risk|risk appetite indicator|asset allocation|'
    r'overweight equities|underweight credit|'
    r'(?:budget|government|fiscal) deficits?|fiscal revenues?|consumption tax|'
    r'government spending|government bond issuance|'
    r'regulatory (?:proposal|draft|change)|draft decree|policy amendments?|'
    r'(?:Chinese|corporate) (?:company )?(?:profit )?margins?|'
    r'chemical margins?|ethylene.naphtha margins?|'
    r'state.backed ETF buying|foreigners? (?:flip to|turn|become) net buyers|'
    r'(?:sector|industry) (?:flows?|positioning|rotation|investor demand)|'
    r'global tech (?:liquidity|flows?)|capital raising|fundraising|financing deals?|'
    r'(?:chip )?deals?|'
    r'TTF (?:prices?|contracts?)|LNG supply|winter supply risk|'
    r'crude (?:oil )?inventories|Brent (?:crude )?(?:prices?|rallied?|rose|surged?)|'
    r'oil drag|private demand|'
    r'FX PIX|USD/JPY|USD/CNY|EUR/GBP|USD/CHF|EUR/USD|AUD/USD|'
    r'EUR shorts?|USD/CAD|CHF longs?|haven demand|'
    r'consumer (?:price )?inflation|monthly (?:price )?index|'
    r'durable goods orders|new home sales|retail sales|'
    r'capex estimates?|GDP tracking|GDP tracker|GDP growth|'
    r'property deal volume|home sales|housing market|coffee stocks|arabica|'
    r'month.end pension|equity sales|net buyers|short additions|ETF inflows?)\b', re.I)
_INSTRUMENT_PRICING_SCOPE = re.compile(r'\b(?:implied volatility|nearby futures|broad index|'
    r'(?:OAT|Treasury|Bund|sovereign bond|government bond) yields?)\b', re.I)
_HARD_ISSUER_THESIS = re.compile(r'\b(?:margin[ -]expansion thesis|fundamental scores?|'
    r'fundamental rankings?|equity thesis|issuer investment thesis|'
    r'(?:FCF|free cash flow) yields?)\b', re.I)
_ENTITY_NAME = r'(?:[A-Z0-9]{2,}[ \t]+[A-Z][A-Za-z0-9&.-]*|[A-Z][A-Za-z0-9&.-]*(?:[ \t]+[A-Z][A-Za-z0-9&.-]*){0,2}|\d+[A-Z][A-Za-z0-9&.-]*)'
_TITLE_ISSUER_METRIC = re.compile(r'\b(?P<entity>' + _ENTITY_NAME + r')[ \t]+'
    r'(?:revenues?|earnings|EPS|EBITDA|net(?: interest)? income|profits?|margins?|sales|bookings?|orders?|guidance|outlook)\b')
_NAMED_ISSUER_METRIC = re.compile(r'\b(?P<entity>' + _ENTITY_NAME + r')[ \t]+'
    r'(?:(?:annual|quarterly|monthly|weekly|yearly|reported|posted|delivered|recorded|'
    r'saw|had|raised|cut|increased|lowered)[ \t]+)+'
    r'(?:revenues?|earnings|EPS|EBITDA|net(?: interest)? income|profits?|margins?|sales|bookings?|orders?|cash[ -]flow|capex)\b')
_NAMED_ISSUER_FACT = re.compile(r'\b(?P<entity>' + _ENTITY_NAME + r')\b(?:\'s)?[ \t]+'
    r'(?:(?:annual|quarterly|monthly|weekly|yearly|reported|posted|delivered|recorded|'
    r'saw|had|rose|fell|grew|increased|decreased|'
    r'beat|missed|raised|cut|lowered)[ \t]+)?'
    r'(?:revenues?|earnings|EPS|EBITDA|net(?: interest)? income|profits?|margins?|sales|bookings?|orders?|cash[ -]flow|capex)\b')
_LOWERCASE_ISSUER_FACT = re.compile(r'(?<!\w)(?P<entity>[a-z][a-z0-9&.-]*(?:[ \t]+[a-z][a-z0-9&.-]*){0,2})[ \t]+'
    r'(?:revenues?|earnings|EPS|EBITDA|net(?: interest)? income|profits?|margins?|sales|bookings?|orders?|cash[ -]flow|capex)\b')
_NAMED_ISSUER_ACTIVITY = re.compile(r'\b(?P<entity>' + _ENTITY_NAME + r')\b'
    r'(?:\'s)?[ \t]+(?:Q[1-4]\d?|FY\d{2,4}|results?|earnings|orderbook|market share)\b')
_COMPANY_FINANCIAL_OBJECT = r'(?:revenues?|earnings|EPS|EBITDA|net(?: interest)? income|profits?|margins?|sales|cash[ -]flow|capex)'
_NAMED_ISSUER_FORWARD = re.compile(r'\b(?P<entity>' + _ENTITY_NAME + r")\b(?:'s)?[ \t]+"
    r'(?i:(?:[^\n.!?]{0,45}\b' + _COMPANY_FINANCIAL_OBJECT +
    r'\b[^\n.!?]{0,30}\b(?:guidance|outlook|forecast|target)|'
    r'[^\n.!?]{0,35}\b(?:guidance|outlook|forecast|target)\b[^\n.!?]{0,35}\b' +
    _COMPANY_FINANCIAL_OBJECT + r'\b|'
    r'(?:expects?|projects?|guides?|forecasts?)\b[^\n.!?]{0,45}\b' +
    _COMPANY_FINANCIAL_OBJECT + r'\b|[^\n.!?]{0,45}\b(?:valuation|P/E|P/B)\b))')
_NAMED_ISSUER_IDEA = re.compile(r'\b(?P<entity>' + _ENTITY_NAME + r')\b.{0,80}'
    r'(?i:\b(?:(?:our|the)\s+)?(?:preferred stock|top pick|top investment idea|'
    r'attractive risk.reward|compelling risk.reward|durable competitive advantage|attractive upside)\b)')
_NAMED_ISSUER_VALUATION = re.compile(r'\b(?P<entity>' + _ENTITY_NAME + r')\b.{0,100}'
    r'(?i:\b(?:is|looks?|remains?|seems?)\s+(?:attractively valued|undervalued|overvalued|'
    r'cheap|expensive|mispriced)|\b(?:offers?|has|provides?)\s+(?:significant\s+)?'
    r'(?:rerating|re.rating)\s+upside|\b(?:shares?|stock|equity)\s+(?:are|is|look|trade)\s+'
    r'cheap(?:\s+(?:relative to|versus|vs\.?)\s+peers?)?|\b(?:downside|risk)\s+as\s+'
    r'(?:its|the)\s+valuation\s+premium\s+compresses?)')
# These are equity investment claims even without earnings, ratings or a PT.
# Both standalone scope and every secondary-subject passage use this predicate.
_EQUITY_THESIS = re.compile(r'\b(?:valuation|price.to.book|undervalued|overvalued|mispriced|expensive|attractively valued|'
    r'rerating upside|re.rating upside|valuation premium compresses?|'
    r'cheap(?: relative to peers)?|at a discount to peers|'
    r'trades? at \d+(?:\.\d+)?x earnings|high.quality compounder|durable economics|'
    r'(?:has|with) (?:a )?moat|attractive returns|attractive risk.reward|'
    r'compelling risk.reward|durable competitive advantage|attractive upside)\b', re.I)
_FINANCIAL_FORWARD = re.compile(r'\b(?:(?:expects?|projects?|guides?|forecasts?)\b'
    r'[^\n.!?]{0,45}\b' + _COMPANY_FINANCIAL_OBJECT + r'\b|' +
    _COMPANY_FINANCIAL_OBJECT + r'\b[^\n.!?]{0,30}\b(?:guidance|outlook|forecast|target)\b|'
    r'(?:guidance|outlook|forecast|target)\b[^\n.!?]{0,30}\b' + _COMPANY_FINANCIAL_OBJECT + r'\b)', re.I)
# Resolve the security that receives a recommendation, including lowercase
# objects. A watched heading cannot stand in for an unnamed buy/sell object.
_RECOMMENDATION_OBJECTS = tuple(re.compile(pattern, re.I) for pattern in (
    r'\b(?:buy|sell|buying|selling)\s+(?P<issuer>\$?[\w&.-]+(?:[ \t]+[\w&.-]+){0,5}?)\s+(?:shares?|stock)\b',
    r'\bpreferred (?:stock|share|equity)\s+is\s+(?P<issuer>\$?[\w&-]+(?:[ \t]+[\w&-]+){0,5})(?=[.;,\n]|$)',
    r'\brates?\s+(?P<issuer>\$?[\w&.-]+(?:[ \t]+[\w&.-]+){0,5}?)\s+(?:Buy|Sell|Neutral|Overweight|Underweight|Outperform|Underperform|Hold)\b',
))
# Action recognition is deliberately broader than supported object parsing.
# Unparsed syntax is unresolved research, never evidence that no issuer exists.
_RECOMMENDATION_ACTION = re.compile(
    r'\b(?:recommend(?:s|ed)?\s+)?(?:buy|sell|buying|selling)\b'
    r'(?=[^\n.!?;]{0,100}\b(?:shares?|stock)\b)|'
    r'\b(?:our\s+)?preferred (?:stock|share|equity)\s+is\b|'
    r'\brates?\b(?=[^\n.!?;]{0,100}\b(?:Buy|Sell|Neutral|Overweight|Underweight|Outperform|Underperform|Hold)\b)', re.I)
_RECOMMENDATION_CLAIM = re.compile('|'.join(pattern.pattern.replace('(?P<issuer>', '(?:')
    for pattern in _RECOMMENDATION_OBJECTS), re.I)
_RATING_TABLE = re.compile(r'\b(?:regional equity ratings?|equity rating changes|'
    r'equity[^\n.!?]{0,50}(?:new and old|old and new) ratings?)\b', re.I)
_RATING_CALL = re.compile(r'\b(?:rated\s+(?:Buy|Sell|Neutral|Overweight|Underweight|Outperform|Underperform|Hold)|'
    r'(?:moves?|moved|changes?|changed|upgraded|downgraded)\s+to\s+'
    r'(?:Buy|Sell|Neutral|Overweight|Underweight|Outperform|Underperform|Hold))\b', re.I)
_UNPARSED_ISSUER_ACTION = re.compile(r'\b(?:initiation|initiat\w* coverage|'
    r'(?:reiterat\w*|maintain\w*|retain\w*|downgrad\w*|upgrad\w*)\b[^\n.!?]{0,35}\b(?:buy|sell|neutral|overweight|underweight|outperform|underperform|hold)|retains? hold on|stays? sell|buying [A-Z][\w -]+ near)\b', re.I)
# These narrow complete grammars bind an explicit object after primary/alias
# normalization. Any unsupported grammar still fails closed.
_RESOLVED_CLAIM_OBJECTS = _RECOMMENDATION_OBJECTS + tuple(re.compile(pattern, re.I) for pattern in (
    r'\b(?:maintain\w*|retain\w*|reiterat\w*)\s+(?:Buy|Sell|Neutral|Overweight|Underweight|Outperform|Underperform|Hold)\s+(?:on|for)\s+(?P<issuer>[\w&-]+(?:[ \t]+[\w&-]+){0,5})(?=[.;,\n]|$)',
    r'\b(?:upgrad\w*|downgrad\w*)\s+(?P<issuer>[\w&-]+(?:[ \t]+[\w&-]+){0,5}?)\s+to\s+(?:Buy|Sell|Neutral|Overweight|Underweight|Outperform|Underperform|Hold)\b',
    r'\b(?:price target|target price)\s+(?:for|on)\s+(?P<issuer>[\w&-]+(?:[ \t]+[\w&-]+){0,5}?)\s+is\s+[$€£]?\d+(?:\.\d+)?\b',
))
_CLAIM_ACTION = re.compile(_RECOMMENDATION_ACTION.pattern + '|' + _UNPARSED_ISSUER_ACTION.pattern, re.I)
_RESEARCH_CLAIM = re.compile('|'.join(pattern.pattern for pattern in
    (_ISSUER_CLAIM, _STOCK_CLAIM, _EQUITY, _ISSUER_LIST, _UNPARSED_ISSUER_ACTION, _FINANCIALS, _EQUITY_THESIS, _HARD_ISSUER_THESIS, _FINANCIAL_FORWARD, _RECOMMENDATION_CLAIM, _RECOMMENDATION_ACTION, _RATING_TABLE, _RATING_CALL)), re.I)
_INDEX_VALUATION = re.compile(r'\b(?:Shiller|S&P\s*500|Nasdaq(?:\s*100)?|Russell\s+\d{4}|TOPIX|KOSPI|'
    r'MSCI\s+(?:World|Europe|Korea|Emerging Markets)|Euro Stoxx 50|Stoxx 600)\b.{0,100}'
    r'(?i:\b(?:undervalued|overvalued|cheap|expensive|attractively valued|valuation|multiple|P/E|P/B))|'
    r'(?i:\b(?:valuation|multiple)\b.{0,80}\b(?:S&P\s*500|Nasdaq(?:\s*100)?|Russell\s+\d{4}|TOPIX|KOSPI|'
    r'MSCI\s+(?:World|Europe|Korea|Emerging Markets)|Euro Stoxx 50|Stoxx 600)\b)', re.I)
_NONCOMPANY_ENTITIES = frozenset({
    'capex', 'sales', 'orders', 'earnings', 'forward', 'rates', 'earnings growth', 'hourly earnings growth',
    'estimated sales', 'private sector', 'private sector hourly', 'core capital goods',
    'annual', 'quarterly', 'monthly', 'weekly', 'yearly', 'expected', 'estimated',
    'better-than-expected', 'tactical', 'hyperscaler', 'contract', 'top',
    'better-than-expected earnings', 'tactical earnings', 'hyperscaler capex',
    'contract sales', 'top earnings',
    'global', 'regional', 'private', 'public', 'sector', 'industry', 'aggregate',
    'corporate', 'fiscal', 'government', 'federal', 'state', 'index', 'index and fx',
    'economic', 'property', 'valuation', 'month', 'year', 'us', 'us macro', 's&p', 'ctas',
    'shiller', 'inflation', 'q1', 'q2', 'q3', 'q4', 'fed chair', 'fed',
    'durable goods', 'capital goods', 'consumer confidence', 'retail sales',
    'home sales', 'new home sales', 'foreign born', 'foreign born swedes',
    'october consumer confidence', 'consumer confidence index', 'median forecast',
    'september new single family', 'estimated september new single family',
    'annual inflation', 'european chemical', 'european ethylene-naphtha',
    'us chemical', 'global chemical',
    'global growth', 'global corporate', 'regional corporate', 'industry corporate',
    'sector corporate', 'aggregate corporate', 'federal government', 'state government',
    'government bond', 'fiscal revenues', 'national accounts', 'chinese corporate',
    'china corporate', 'european corporate', 'american corporate', 'japanese corporate',
    'swedish corporate', 'french corporate', 'british corporate', 'korean corporate',
    'brazilian corporate', 'canadian corporate', 'mexican corporate', 'australian corporate',
    'german corporate', 'china gdp', 'europe gdp', 'japan gdp', 'sweden gdp',
    'france gdp', 'britain gdp', 'korea gdp', 'brazil gdp', 'canada gdp',
    'mexico gdp', 'australia gdp', 'germany gdp', 'conference board', 'census bureau',
    'that', 'this', 'it', 'the', 'its', 'annual', 'fed funds', 'dxy', 'usd', 'eur', 'gbp', 'jpy',
    'tokyo cpi', 'tokyo', 'cpi', 'cbe', 'europe', 'european', 'france', 'french', 'japan',
    'japanese', 'china', 'chinese', 'brazil', 'brazilian', 'egypt', 'egyptian',
    'uk', 'britain', 'british', 'sweden', 'swedish', 'korea', 'korean',
})
_PUBLISHER_HEADS = frozenset({
    'goldman', 'jpmorgan', 'jpm', 'ubs', 'citi', 'barclays', 'morgan', 'bofa',
    'nomura', 'mizuho', 'natixis', 'deutsche', 'pictet', 'westpac', 'anz', 'safra',
    'mufg', 'credit', 'agricole', 'sachs', 'morgan stanley', 'goldman sachs',
})


def _symbols(text):
    lower_filename = text == text.lower()
    found = [m.group(1) for m in _DOLLAR.finditer(text) if m.group(1) not in _NON_ISSUERS]
    found += [m.group(1).upper() for m in _PAREN.finditer(text)
        if (m.group(1).isupper() or m.group(1).isdigit() or lower_filename)
        and m.group(1).upper() not in _NON_ISSUERS
        and not re.fullmatch(r'(?:19|20)\d{2}', m.group(1))
        and m.group(1).lower() not in {'repost', 'update', 'up', 'down'}]
    return tuple(dict.fromkeys(found))


def _aliases(text):
    return tuple(symbol for name, symbol in _COMPANY_NAMES.items()
                 if re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text, re.I))


def _claim_text(text):
    # Explicit aggregate/fiscal revenue measurements are not issuer fundamentals.
    financial_text = re.sub(r'\b(?:aggregate|industry|sector|corporate|federal|state|government|fiscal|tax|national)\s+'
                            r'(?:revenues?|earnings|(?:gross|net|operating) (?:income|profit|margin)s?|net(?: interest)? income|profits?|margins?|sales|bookings?|orders?|cash[ -]flows?|capex)\b', 'market measurement', text, flags=re.I)
    financial_text = re.sub(r'\b(?:retail sales|home sales|factory orders|durable.goods orders|'
                            r'core capital goods orders|private sector hourly earnings growth|'
                            r'average hourly earnings growth|hourly earnings growth|estimated sales|'
                            r'new single.family home sales|single.family home sales|new home sales|'
                            r'durable goods orders|richmond fed manufacturing|'
                            r'(?:durable goods orders|new home sales) (?:beat|missed) expectations|'
                            r'consumer confidence(?: index)?|unemployment|labou?r market participation|'
                            r'(?:overweight|underweight|OW|UW) (?:equities|credit|bonds|cash|rates|FX|commodities)|'
                            r'(?:S&P\s*500|Nasdaq(?:\s*100)?|Russell\s+\d{4}) dividend yield|'
                            r'(?:Chinese|China|European|Europe|Japanese|Japan|US|American|'
                            r'French|France|German|Germany) corporate (?:profit )?margins?|'
                            r'(?:ethylene.naphtha|chemical) margins?|'
                            r'(?:better-than-expected|tactical) earnings|hyperscaler capex|'
                            r'S&P\s*500 blended earnings growth|global equity sales|'
                            r'(?:US|European|global) equity sales)\b',
                            'economic measurement', financial_text, flags=re.I)
    return financial_text


def _issuer_claim(text):
    return bool(_RESEARCH_CLAIM.search(_claim_text(text)))


_LOWERCASE_ENTITY_MODIFIERS = frozenset({
    'the', 'a', 'an', 'in', 'on', 'at', 'and', 'or', 'with', 'amid', 'after', 'before',
    'during', 'while', 'but', 'although', 'as', 'net', 'gross', 'adjusted', 'reported',
    'expected', 'estimated', 'annual', 'quarterly', 'monthly', 'weekly', 'yearly', 'core',
    'private', 'public', 'global', 'regional', 'sector', 'industry', 'aggregate', 'corporate',
    'fiscal', 'government', 'federal', 'state', 'worse', 'weaker', 'stronger', 'lower',
    'higher', 'modest', 'better', 'growth', 'revenue', 'earnings', 'sales', 'profit',
    'of', 'tax', 'funds', 'price', 'industrial', 'chemical', 'europe', 'european',
    'china', 'chinese', 'japan', 'japanese', 's', 'wars', 'pulling', 'potential',
    'proposed', 'non-tax', 'ethylene-naphtha', 'manufacturing',
    'better-than-expected', 'tactical', 'contract', 'top',
})


def _known_noncompany_lowercase_entity(entity):
    """Ignore only a complete known measurement phrase, never its first word."""
    words = entity.split()
    while words and words[0] in _LOWERCASE_ENTITY_MODIFIERS:
        words.pop(0)
        remainder = ' '.join(words)
        if remainder in _NONCOMPANY_ENTITIES:
            return True
    return entity in _NONCOMPANY_ENTITIES or set(entity.split()).issubset(_LOWERCASE_ENTITY_MODIFIERS)


def _has_lowercase_company_metric(text):
    # A lowercase company subject must start a sentence/clause. Arbitrary words
    # before a metric ("tax and non-tax revenues", "its earnings") are not names.
    text = re.sub(r'\bhedge fund short sales\b', 'positioning measurement', text, flags=re.I)
    for match in _LOWERCASE_ISSUER_FACT.finditer(text):
        prefix = text[:match.start()]
        if prefix and not re.search(r'(?:^|[\n.!?;:]|\band|\bwhile)\s*$', prefix):
            continue
        entity = re.sub(r'^(?:the|a|an)\s+', '', match.group('entity').lower())
        if _known_noncompany_lowercase_entity(entity):
            continue
        return True
    return False


def _named_company_match(pattern, text):
    for match in pattern.finditer(text):
        entity = re.sub(r'^(?:the|a|an)\s+', '', match.groupdict().get('entity', ''), flags=re.I).lower()
        # A sentence ending followed by "Forward P/E" is not a multiword name.
        if re.search(r'(?<!inc)(?<!ltd)(?<!corp)\.\s+', entity):
            continue
        if entity in _NONCOMPANY_ENTITIES:
            continue
        if re.fullmatch(r'(?:mtd|ytd|qtd) (?:january|february|march|april|may|june|'
                        r'july|august|september|october|november|december)', entity):
            continue
        if re.fullmatch(r'(?:s&p(?: 500)?|nasdaq(?: 100)?|topix|kospi|'
                        r'msci (?:world|europe|korea|emerging markets)|shiller)', entity):
            continue
        if re.fullmatch(r'(?:china|chinese|european|american|japanese|swedish|french|british|korean|'
                        r'brazilian|canadian|mexican|australian|german)\s+(?:corporate|industry|sector|'
                        r'aggregate|national accounts|government|fiscal)', entity):
            continue
        if entity in _PUBLISHER_HEADS:
            publisher = re.match(r'(?:Goldman Sachs|Morgan Stanley|Goldman|JPMorgan|JPM|UBS|'
                                 r'Citi|Barclays|BofA|Nomura|Mizuho|Natixis|Deutsche|Pictet|'
                                 r'Westpac|ANZ|Safra|MUFG|Rabobank|SEB|TS Lombard)\b',
                                 match.group(0), re.I)
            after_entity = match.group(0)[publisher.end() if publisher else len(match.group('entity')):]
            if re.match(r"\s+(?:says?|said|sees?|forecasts?|targets?|estimates?|projects?|"
                        r"reports?|notes?|flags?|puts?|calculates?|expects?|finds?|"
                        r"lifts?|raises?|cuts?|maintains?|reiterates?|upgrades?|downgrades?|"
                        r"recommends?)\b",
                        after_entity, re.I):
                continue
            if re.match(r"\s*'s\s+(?:dollar|currency|FX|rate|GDP|inflation|macro|market|sector|oil|"
                        r'commodity|equity|bond|credit|risk|growth|economic|normalization|'
                        r'longer[ -]term)\b', after_entity, re.I):
                continue
            if re.match(r"\s*'(?:s)?\s+(?:\d{4}\s+)?(?:comparison|report|note|analysis|"
                        r'(?:TOPIX|KOSPI|S&P 500|Nasdaq)\s+chart)\b', after_entity, re.I):
                continue
        if entity:
            return True
    return False


def _primary_non_equity_scope(title, text):
    """Require a primary-market signal, rather than an incidental macro word."""
    body = str(text or '')[:2500]
    return bool(_BROAD.search(str(title or '')) or _INDEX_VALUATION.search(str(title or '') + '\n' + body) or
                _PRIMARY_MARKET_SCOPE.search(str(title or '') + '\n' + body) or
                _INSTRUMENT_PRICING_SCOPE.search(str(title or '') + '\n' + body))


def _unparsed_claim_object(text, *, resolved_objects=()):
    """A recognized research claim with an unsupported explicit object holds.

    This consumes the shared normalized claim family, not a second list of
    targets/EPS/ratings. Supported prefix-only claims cannot hide "for Acme"
    after the predicate. Numeric/time-only objects introduce no named issuer.
    """
    normalized = _claim_text(text)
    # Mask only spans whose entire object was checked equal to "issuer".
    # This removes no unknown list member, prefix match or unparsed suffix.
    for target in sorted(resolved_objects, key=lambda item: item.start(), reverse=True):
        normalized = normalized[:target.start()] + ' ' * (target.end() - target.start()) + normalized[target.end():]
    for passage in re.split(r'\n+|(?<=[.!?])\s+', normalized):
        for claim in _RESEARCH_CLAIM.finditer(passage):
            if _EQUITY_THESIS.fullmatch(claim.group(0)) and not _has_company_equity_thesis(passage):
                continue  # Explicit benchmark/currency/bond valuation is not an equity object.
            suffix = re.split(r'[;.!?]', passage[claim.end():], maxsplit=1)[0]
            for slot in re.finditer(r'\b(?:for|on|of|in|about|regarding)\s+([A-Za-z][^,;.!?]*)', suffix, re.I):
                # A financial measurement used as an object is not a new name.
                if re.fullmatch(r'(?:the |its |their )?' + _COMPANY_FINANCIAL_OBJECT, slot.group(1).strip(), re.I):
                    continue
                return True
    return False


def _has_equity_stock_claim(text):
    if _unparsed_claim_object(text):
        return True
    if _UNPARSED_ISSUER_ACTION.search(text) or _ISSUER_LIST.search(text) or _RECOMMENDATION_ACTION.search(text) or _RECOMMENDATION_CLAIM.search(text) or _RATING_TABLE.search(text) or _RATING_CALL.search(text):
        return True
    for match in _STOCK_CLAIM.finditer(text):
        tail = text[match.end():]
        if re.match(r'\s+(?:equities|bonds|credit|cash|rates|FX|commodities)\b', tail, re.I):
            continue
        if re.search(r'\bdividend yield\b', match.group(0), re.I):
            start = max(text.rfind(mark, 0, match.start()) for mark in '.!?\n') + 1
            prefix = text[start:match.start()]
            if re.search(r'\b(?:S&P\s*500|Nasdaq(?:\s*100)?|MSCI\s+\w+|Russell\s+\d{4})\b.{0,50}$', prefix, re.I):
                continue
        return True
    return False


def _has_company_equity_thesis(text):
    for claim in _EQUITY_THESIS.finditer(text):
        # Resolve the subject of this clause, not a benchmark elsewhere in the
        # report. "S&P 500 is cheap and Acme is cheap" still contains an issuer.
        prefix = re.split(r'[\n.!?;]|\band\b', text[:claim.start()])[-1].strip()
        subject = re.sub(r'\s+(?:(?:is|are|looks?|remains?|seems?|shares?|stock|equity)\s*)+$',
                         '', prefix, flags=re.I).strip()
        if claim.group(0).lower() == 'valuation':
            if not prefix and _INDEX_VALUATION.search(text[claim.start():]):
                continue  # "Valuation multiples for the Nasdaq 100" names its object after the noun.
            if re.search(r'\b(?:that|this)\s*$', prefix, re.I) and _INDEX_VALUATION.search(text[:claim.start()]):
                continue  # A benchmark valuation's anaphoric reference adds no company subject.
        if re.fullmatch(r'(?:(?:the )?(?:broad )?(?:index|market|sector|industry|aggregate)|'
                        r'FX|USD|EUR|GBP|JPY|AUD|NZD|CAD|CHF)', subject, re.I):
            continue
        if re.fullmatch(r'(?:S&P\s*500|Nasdaq(?:\s*100)?|Russell\s+\d{4}|TOPIX|KOSPI|'
                        r'MSCI\s+(?:World|Europe|Korea|Emerging Markets)|'
                        r'(?:USD|EUR|GBP|JPY|AUD|NZD|CAD|CHF)[/-](?:USD|EUR|GBP|JPY|AUD|NZD|CAD|CHF))',
                        subject, re.I):
            continue
        if re.search(r'\b(?:bonds?|AT1s?|convertibles?)$', subject, re.I):
            continue
        if re.search(r'\b(?:implied volatility|(?:OAT|Treasury|Bund|sovereign bond|government bond) yields?)'
                     r'(?:\s+(?:is|are|looks?|significantly|relatively|more))*$', prefix, re.I):
            continue
        if re.search(r'\b(?:USD|EUR|GBP|JPY|AUD|NZD|CAD|CHF)[/-]?'
                     r'(?:USD|EUR|GBP|JPY|AUD|NZD|CAD|CHF) (?:puts?|calls?)(?:\s+a)?$', prefix, re.I):
            continue
        if re.search(r'\bnearby futures (?:are )?more$', prefix, re.I) and re.match(
                r'\s+than later contracts\b', text[claim.end():], re.I):
            continue
        if (re.search(r'\b(?:broad index|yield.gap)\b', prefix, re.I) and
                re.search(r'\bequities(?: are)?(?: relatively)?$', prefix, re.I)):
            continue
        if re.match(r'\s+equities relative to bonds\b', text[claim.end():], re.I):
            continue
        # A named analyst can express a broad-market rebound view. Requiring
        # its explicit market object prevents a bank name from hiding Acme.
        if re.fullmatch(r'(?:Morgan Stanley|Goldman Sachs|Goldman|UBS|Citi|JPMorgan|BofA)\s+'
                        r'(?:sees?|finds?|flags?)', prefix, re.I) and re.match(
                r'\s+(?:in|for)\s+(?:a |the |this |broader )*(?:'
                r'(?:European|global|US) (?:equity|market) rebound|'
                r'rebound within (?:this|the) rotational bear market)', text[claim.end():], re.I):
            continue
        return True
    return False


def _hard_issuer_thesis(title, text):
    """Keep stock calls and company forward/valuation claims blocking in macro wrappers."""
    combined = str(title or '') + '\n' + str(text or '')
    # Index/asset-class allocation views and index dividend yields are not
    # issuer recommendations. The same terms remain hard blocks when tied to
    # a named security or company.
    if _has_equity_stock_claim(combined):
        return True
    if _has_company_equity_thesis(combined):
        return True
    if _HARD_ISSUER_THESIS.search(combined):
        return True
    if (_ISSUER_TITLE.search(str(title or '')) and not
            (re.search(r'\bresults\b', str(title or ''), re.I)
             and re.search(r'\b(?:futures|open interest|index positioning)\b', str(title or ''), re.I)
             and not _TITLE_ISSUER_METRIC.search(str(title or '')))):
        return True
    # A macro label or separate market-flow paragraph cannot resolve an
    # unknown company earnings subject. Such copy stays held for review.
    sentences = re.split(r'(?<=[.!?])\s+|\n+', combined)
    fact_sentences = [sentence for sentence in sentences if _named_company_match(_NAMED_ISSUER_FACT, sentence)]
    futures_results_catalyst = bool(re.search(
        r'\b(?:futures|open interest|index positioning)\b.{0,100}\bafter\b.{0,60}\bresults?\b',
        str(title or ''), re.I))
    activity = bool((_named_company_match(_NAMED_ISSUER_ACTIVITY, combined) and
                     not futures_results_catalyst)
                    or _named_company_match(_TITLE_ISSUER_METRIC, str(title or '')))
    company_metrics = bool(_COMPANY.search(combined) or _aliases(combined) or
                           _named_company_match(_NAMED_ISSUER_METRIC, combined) or fact_sentences or
                           _has_lowercase_company_metric(combined))
    for forward in _FINANCIAL_FORWARD.finditer(combined):
        # Company financial guidance stays issuer research even in a flow
        # report. Aggregate and governmental subjects retain their exact roles.
        if fact_sentences:
            return True
        prefix = re.split(r'[\n.!?;:]|\band\b', combined[:forward.start()])[-1].strip()
        if re.fullmatch(r'[a-z][a-z-]*(?:\s+[a-z][a-z-]*){0,2}', prefix):
            subject = re.sub(r'^(?:the|a|an)\s+', '', prefix)
            if not _known_noncompany_lowercase_entity(subject):
                return True
    # If a financial/stock claim remains after known macro aggregates have been
    # normalized, an unresolved subject cannot inherit the article's broad title.
    if (company_metrics or activity) and _issuer_claim(combined):
        return True
    # A ticker-bound financial passage is an issuer fact even when a broad
    # article also discusses commodities or rates. Keep mixed stock coverage held.
    for sentence in sentences:
        if _symbols(sentence) and _issuer_claim(sentence):
            return True
    # Catch a named issuer's forward guidance even when the summary omits its ticker.
    return _named_company_match(_NAMED_ISSUER_FORWARD, combined)


def _complete_subjects(primary, text):
    """Resolve every stock-claim passage, holding unnamed additional issuers.

    A resolved heading is only a starting subject. Unknown proper names in a
    recommendation or earnings claim cannot silently inherit its membership.
    Financial labels/pronouns are not new entities; publisher attribution is
    ignored only in reporting position, never as a general issuer exemption.
    """
    # A regional/new-vs-old equity recommendation table does not enumerate its
    # issuer set in prose. Never let a watched heading grant that set membership.
    if _RATING_TABLE.search(text) or _ISSUER_LIST.search(text):
        return ()
    primary = set(primary)
    generic = set("the a an its their our we it this these those in on for as at by to and or "
        "buy sell neutral outperform overweight underweight hold rating ratings price target targets "
        "revenue revenues earnings eps ebit ebitda net income profit gross margin operating "
        "guidance outlook estimate estimates expected adjusted quarterly annual first second third "
        "fourth quarter q1 q2 q3 q4 fy fiscal markets inc corporation corp plc ltd limited take "
        "technology technologies group company shares stock consensus new current prior "
        "us uk eu ai ceo cfo usd eur gbp jpy equity research revised ratingprice platform kept lower "
        "january february march april may june july august september october november december "
        "annual quarterly adjusted reported expected estimated preliminary monthly weekly yearly core "
        "amid weaker modest transaction yoy qoq mom ytd mtd saar bps bp ppt".split())
    for passage in re.split(r'\n+|(?<=[.!?])\s+', text):
        if not _issuer_claim(passage):
            continue
        primary.update(_symbols(passage))
        primary.update(_aliases(passage))
        residue = _EXCHANGE.sub("issuer", passage)
        for name in _COMPANY_NAMES:
            residue = re.sub(r'(?<!\w)' + re.escape(name) + r'(?!\w)', 'issuer', residue, flags=re.I)
        # A printed name immediately bound to a symbol identifies that issuer.
        residue = re.sub(r"\b[A-Z][\w&.-]*(?: +[A-Z][\w&.-]*){0,5} *\(" + _SYMBOL + r"\)", 'issuer', residue)
        for symbol in primary:
            residue = re.sub(r'(?<!\w)\$?' + re.escape(symbol) + r'(?!\w)', 'issuer', residue)
        residue = re.sub(r'\b(?:Goldman Sachs|Goldman|JPMorgan|Morgan Stanley|BofA|Citi|UBS|Barclays)'
                         r'(?=\s+(?:flag\w*|says?|said|cut\w*|rais\w*|maintain\w*|retain\w*|reiterat\w*|upgrad\w*|downgrad\w*|estimat\w*|forecast\w*|rates?))',
                         'analyst', residue)
        # All recommendation objects must independently resolve to an explicit
        # symbol/alias already replaced above. Unknown objects fail closed.
        residue = _claim_text(residue)
        actions = list(_CLAIM_ACTION.finditer(residue))
        objects = [match for pattern in _RESOLVED_CLAIM_OBJECTS for match in pattern.finditer(residue)]
        for recommendation in objects:
            if recommendation.group('issuer').strip().lower() != 'issuer':
                return ()
        if _unparsed_claim_object(residue, resolved_objects=objects):
            return ()
        for action in actions:
            if not any(target.start() < action.end() and target.end() > action.start() for target in objects):
                return ()
        if actions or objects:
            scope_start = min(target.start() for target in objects)
            # Any trailing coordinated/list object that the parser did not
            # consume remains unresolved, even if it omits "shares" or a rating.
            remainder = residue[max(target.end() for target in objects):]
            if re.search(r'[,;]|\b(?:and|or)\b', remainder, re.I):
                return ()
            # A supported first target cannot license an additional coordinated
            # stock object ("issuer shares and unknown stock"). Every stock noun
            # or rating label after an action must belong to a resolved object.
            for noun in re.finditer(r'\b(?:shares?|stock|equity|Buy|Sell|Neutral|Overweight|Underweight|Outperform|Underperform|Hold)\b', residue[scope_start:], re.I):
                position = scope_start + noun.start()
                if not any(target.start() <= position < target.end() for target in objects):
                    return ()
        residue = re.sub(r"\bP/[EBS]\b", "ratio", residue)
        if any(word.lower() not in generic for word in re.findall(r'\b[A-Z][A-Za-z&-]*\b', residue)):
            return ()
        # Use the same claim grammar for lowercase names, rather than accepting
        # only one spelling of price-target or earnings predicates.
        for claim in _RESEARCH_CLAIM.finditer(residue):
            # "Pressure on net interest income" describes the resolved issuer;
            # a financial noun serving as an object does not introduce a name.
            if re.search(r'\b(?:on|for|from|with|of)\s+$', residue[:claim.start()]):
                continue
            clause = re.split(r'[.;:,]|\band\b|\bwhile\b', residue[:claim.start()])[-1]
            subject = re.fullmatch(r"\s*([a-z][a-z-]*(?:\s+[a-z][a-z-]*){0,5})\s+", clause)
            subject_labels = generic | {'issuer', 'analyst', 'has', 'have', 'had', 'is', 'are',
                'was', 'were', 'delivered', 'recorded', 'posted', 'saw', 'keeps', 'kept', 'maintains',
                'offers', 'offer', 'provides', 'provide', 'looks', 'remains', 'seems', 'trades', 'trade',
                'raises', 'cut', 'cuts', 'increases', 'lowers'}
            if subject and not set(subject.group(1).split()).issubset(subject_labels):
                return ()
    return tuple(sorted(primary))


def subjects(title: str, page_one: str, *, doc_type: str = '') -> tuple[bool, tuple[str, ...]]:
    """Issuer research and primary subjects; never infer membership from peers."""
    title = re.sub(r'\.pdf$', '', str(title or ''), flags=re.I).replace('_', ' ')
    text = str(page_one or '')
    if _ISSUER_LIST.search(title):
        return True, ()  # title does not establish a stock list's complete subjects
    first_line = next((line.strip(' #*') for line in text.splitlines() if line.strip()), '')
    credit = bool(_CREDIT_SCOPE.search(title + '\n' + first_line) or re.match(
        r'^(?:(?:corporate|private|sovereign|investment.grade|high.yield)\s+)?'
        r'(?:credit (?:research|calls|strategy|markets?)|bond (?:research|strategy|market)|fixed[ -]income)\b',
        first_line, re.I))
    if credit and not _has_equity_stock_claim(title + '\n' + text) and not _has_company_equity_thesis(title + '\n' + text):
        if not _hard_issuer_thesis(title, text):
            return False, ()
        # A named credit report may discuss its own issuer fundamentals. A
        # generic "credit update" cannot license an unrelated company's facts.
        named = [match.group('entity') for pattern in (_NAMED_ISSUER_FACT, _NAMED_ISSUER_METRIC, _NAMED_ISSUER_ACTIVITY)
                 for match in pattern.finditer(text) if _named_company_match(pattern, match.group(0))]
        if named and not _has_lowercase_company_metric(text) and all(
                re.search(r'(?<!\w)' + re.escape(entity) + r'(?!\w)', title, re.I) for entity in named):
            return False, ()
    symbols = _symbols(title)
    aliases = _aliases(title)
    primary = set(symbols + aliases)
    header_symbols = _symbols(first_line)
    if header_symbols and (_COMPANY.search(first_line) or _EQUITY.search(first_line) or _STOCK_CLAIM.search(first_line)):
        if not re.search(r'\b(?:peer|compares? with|versus|vs\.?|relative to)\b', first_line, re.I):
            primary.update(header_symbols)
    # Later rendered caption headings may carry stock calls. Resolve their own
    # subjects only under established market scope, then inspect every claim;
    # an incidental watched caption cannot resolve an unknown issuer report.
    if _primary_non_equity_scope(title, text) or re.search(r'\bdispersion\b', title, re.I):
        for line in text.splitlines():
            if re.match(r'^\s*[^:\n]{1,100}:', line) and _issuer_claim(line):
                primary.update(_symbols(line))
                primary.update(_aliases(line))
    exchange = _EXCHANGE.search(text[:600])
    if exchange and _EQUITY.search(text[:600]):
        primary.add(exchange.group(1))
    if primary:
        return True, _complete_subjects(primary, title + "\n\n" + text)
    # A macro footer does not turn company earnings/valuation into macro copy.
    # Credit analysis can discuss issuer earnings without recommending equity.
    # Explicit report scope can include backward issuer facts as context, but
    # it cannot override a stock call, valuation, or company-forward thesis.
    hard_issuer_thesis = _hard_issuer_thesis(title, text)
    if _primary_non_equity_scope(title, text) and not hard_issuer_thesis:
        return False, ()
    if hard_issuer_thesis:
        return True, ()
    opening = title + '\n' + text.split('\n\n', 1)[0][:400]
    if (_ISSUER_TITLE.search(title) or (_issuer_claim(title + "\n" + text) and not credit)
            or (credit and _STOCK_CLAIM.search(text))):
        return True, ()
    if credit or _BROAD.search(title) or _MARKET_CONTEXT.search(opening):
        return False, ()
    # No positive source evidence establishes non-issuer scope. Park for review.
    return True, ()


def decide(title, page_one, *, doc_type='', book_tickers):
    """Return publish/held/dropped plus reason and auditable primary subjects."""
    equity, names = subjects(title, page_one, doc_type=doc_type)
    if not equity:
        return 'publish', None, names
    if not names:
        return 'held', 'EQUITY_RESEARCH_SUBJECT_UNRESOLVED', names
    try:
        current = book_tickers()
    except Exception:
        current = None
    if not isinstance(current, (set, frozenset)) or any(not isinstance(x, str) or not x.strip() for x in current):
        return 'held', 'EQUITY_RESEARCH_BOOK_UNAVAILABLE', names
    current = {x.strip().upper() for x in current}
    if not set(names).issubset(current):
        return 'dropped', 'EQUITY_RESEARCH_NOT_IN_BOOK', names
    return 'publish', None, names
