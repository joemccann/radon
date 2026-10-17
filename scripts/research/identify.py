"""Deterministic document identity: publisher, series, report date, document type.

No model is consulted. Every fact carries its source so the selector receives
it as given data; a missing text date falls down a ladder (PDF creation date,
Dropbox client_modified) and never holds a document.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, asdict
from datetime import date, datetime

# Dropbox folders that group by topic rather than by publisher.
TOPIC_FOLDERS = {'nuclear', 'other', 'week ahead', 'misc', 'unknown', 'unidentified', 'unverified'}

PUBLISHER_DISPLAY = {
    'goldman sachs': 'Goldman Sachs', 'jp morgan': 'J.P. Morgan', 'bank of america': 'BofA Global Research',
    'deutsche bank': 'Deutsche Bank Research', 'ubs': 'UBS', 'citi': 'Citi Research', 'societe generale': 'Societe Generale',
    'ts lombard': 'TS Lombard', 'zero hedge': 'Tyler Durden', 'nomura (mcelligott)': 'Nomura', 'nomura': 'Nomura',
    'wolfe': 'Wolfe Research', 'barclays': 'Barclays', 'btig': 'BTIG', 'canaccord': 'Canaccord Genuity', 'mizuho': 'Mizuho',
    'mufg': 'MUFG', 'rabobank': 'RaboResearch', 'pnc': 'PNC Economics', 'rbc': 'RBC Capital Markets', 'safra': 'Bank J Safra Sarasin',
    'standard chartered': 'Standard Chartered', 'apollo': 'Apollo', 'jefferies': 'Jefferies', 'bnp paribas': 'BNP Paribas',
    'morgan stanley': 'Morgan Stanley', 'kb securities': 'KB Securities', 'seb': 'SEB',
    'lseg': 'LSEG', 'london stock exchange group': 'LSEG', 'london stock exchange group plc': 'LSEG',
}

# Brand tokens looked for on page 1 when the folder is a topic, ordered longest first.
PAGE_BRANDS = [
    ('Wolfe Research', r'\bWolfe (?:Research|Power Trip)\b|The Wolfe B'), ('UBS', r'\bUBS\b'), ('Goldman Sachs', r'\bGoldman Sachs\b|@gs\.com'),
    ('J.P. Morgan', r'J\.?P\.? ?Morgan|J P M O R G A N'), ('Barclays', r'\bBarclays\b'), ('Maxim Group', r'\bMaxim\b'),
    ('Deutsche Bank Research', r'\bDeutsche Bank\b'), ('BofA Global Research', r'\bBofA\b|Bank of America'),
    ('Morgan Stanley', r'\bMorgan Stanley\b'), ('Citi Research', r'\bCiti(?:group)? Research\b'), ('Nomura', r'\bNomura\b'),
    ('Jefferies', r'\bJefferies\b'), ('Mizuho', r'\bMizuho\b'), ('MUFG', r'\bMUFG\b'), ('RBC Capital Markets', r'\bRBC\b'),
    ('Canaccord Genuity', r'\bCanaccord\b'), ('BTIG', r'\bBTIG\b|@btig\.com'), ('TS Lombard', r'\bTS Lombard\b'),
    ('Societe Generale', r'Societe Generale|@sgcib\.com'), ('RaboResearch', r'\bRabo(?:Research|bank)\b'),
    ('PNC Economics', r'\bPNC\b'), ('Standard Chartered', r'Standard Chartered'), ('Apollo', r'\bApollo\b'),
    ('Bank J Safra Sarasin', r'Safra Sarasin'), ('BNP Paribas', r'BNP Paribas'), ('Citadel Securities', r'\bCitadel\b'),
]

# Ownership statements outrank cited banks and data-provider names in report prose.
# The brand must immediately follow the copyright marker/year, not occur later
# in a data credit. End pages are searched first; the quote stays source-exact.
LEGAL_PUBLISHERS = [
    ("LSEG", r"(?:London\s*Stock\s*Exchange\s*Group(?:\s+plc)?|LSEG)\b"),
    *PAGE_BRANDS,
]
_COPYRIGHT_PREFIX = r"(?:©|\bcopyright\b|\(c\))\s*(?:copyright\s*)?(?:(?:19|20)\d{2}(?:\s*[-–/]\s*(?:(?:19|20)?\d{2}))?\s*)?[\s*_]*"
_COPYRIGHT_TAIL = r"(?:[ \t]+(?:Inc\.?|LLC|LLP|Limited|plc))?(?:[ \t]+(?:19|20)\d{2})?[ \t]*\.?(?:[ \t]*\**[ \t]*All rights reserved\.?)?"


MONTHS = 'january|february|march|april|may|june|july|august|september|october|november|december'
MONTHS_ABBR = 'jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec'
_MONTH = rf'(?:{MONTHS}|{MONTHS_ABBR})\.?'
# "16 September 2026", "07 Sep 2026", "16th September 2026"
_DMY = rf'(?P<d1>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<m1>{_MONTH})\s+(?P<y1>20\d\d)'
# "September 16, 2026", "SEP 16, 2026", "Sep 07 2026"
_MDY = rf'(?P<m2>{_MONTH})\s+(?P<d2>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y2>20\d\d)'
_ISO = r'(?P<y3>20\d\d)-(?P<mo3>\d\d)-(?P<d3>\d\d)'
DATE_RE = re.compile(rf'(?<![\w.])(?:{_DMY}|{_MDY}|{_ISO})(?![\w])', re.I)

# Text immediately before a date that marks it as something other than the report date.
_WEEKDAY = r'(?:mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?'
_EXCLUDE_BEFORE = re.compile(
    r'(?:downloaded|printed|accessed|retrieved|copyright|©|period ending|coverage|as of'
    rf'|\bon\s+{_WEEKDAY}\s*,?\s*'                                            # "Downloaded ... on Monday Sep 07 2026"
    r'|\d(?:st|nd|rd|th)?\s*[-–—]\s*(?:\d{1,2}(?:st|nd|rd|th)?\s*)?|\b(?:to|through|until|from)\s*)$', re.I)
# A trailing range partner ("- 17 September 2026"), not a clock time ("- 05:45 PM").
_EXCLUDE_AFTER = re.compile(rf'^\s*(?:[-–—]\s*\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTH}|20\d\d)|(?:to|through|until)\b)', re.I)
_MONTH_INDEX = {m: i % 12 + 1 for i, m in enumerate(MONTHS.split('|') + MONTHS_ABBR.split('|'))}
_MONTH_INDEX['sept'] = 9


@dataclass
class Identity:
    publisher: str
    publisher_source: str        # folder | page | document | none
    publisher_folder: str
    series: str
    date: str | None
    date_source: str             # text | pdf | dropbox | none
    date_page: int | None
    date_quote: str | None
    doc_type: str                # calendar | single_stock | conference | fx_pair_note | digest | research
    doc_type_reason: str
    tickers: tuple = ()          # single-name candidates; gates single_stock against the operator book (research.triage)
    publisher_page: int | None = None
    publisher_quote: str | None = None

    def as_dict(self):
        return asdict(self)


def publisher_folder(path_lower):
    parts = path_lower.split('/')
    return parts[6] if len(parts) > 7 else ''


def _brand_on_page(text):
    head = text[:3000]
    for display, pattern in PAGE_BRANDS:
        if re.search(pattern, head):
            return display
    return None


def _publisher_ownership(page_text):
    from research.nemotron_parse import grounding_text
    for number in sorted(page_text, reverse=True):
        text = grounding_text(page_text[number])
        for display, owner in LEGAL_PUBLISHERS:
            pattern = _COPYRIGHT_PREFIX + r"(?:" + owner + r")" + _COPYRIGHT_TAIL
            match = re.search(pattern, text[-3000:], re.I)
            if match:
                return display, number, match.group(0)
    return None


# --- publisher discovery for topic folders ----------------------------------
# A topic-folder PDF from a firm outside PAGE_BRANDS names its publisher in
# the copyright line, an "issued by" sentence, a running footer or its web and
# email domains, usually on the last pages. Each kind of evidence scores; a
# bare legal-entity mention never wins alone (a single-stock note says
# "Apple Inc." more often than it names its broker).
_SUFFIX = (r'(?:Co\.,?\s*Ltd\.?|L\.\s?L\.\s?C\.?|LLC|L\.\s?P\.?|LP|Inc\.?|Incorporated|Ltd\.?|Limited|plc|PLC|Corporation|Corp\.?'
           r'|AG|S\.A\.|SA|N\.V\.|NV|GmbH|SE|LLP)')
_CAP = r"(?:[A-Z][\w&'’.\-]*|&)"
# Words that end a name when two-column extraction runs the next sentence into it.
_STOP = r"(?:Follow|Visit|All|Please|For|Head|Registered|This|The|Member|Authori[sz]ed|Regulated|Incorporated|Is|Are|Has|And|Or|It|Its|We|Our|In|On|At|To|As|If|With|Their)\b"
_NAME = rf"{_CAP}(?:[ \t]+(?:(?!{_STOP}){_CAP}|of|and|de|du|la|des)){{0,6}}"
_ENTITY_RE = re.compile(rf"(?<![\w&])({_NAME}),?[ \t]+{_SUFFIX}(?![\w])")
# Firm-shaped names with no legal suffix: "Commonwealth Bank of Australia", "Fiera Capital".
_ORG_RE = re.compile(rf"(?<![\w&])({_CAP}(?:[ \t]+(?:(?!{_STOP}){_CAP}|of|and|de|du|la|des)){{0,4}}[ \t]+"
                     r"(?:Bank(?:[ \t]+of[ \t]+[A-Z]\w+)?|Capital|Securities|Asset Management|Investment Management|Investments?"
                     r"|Partners|Advisors|Advisers|Financial|Economics))(?![\w])")
_COPYRIGHT_RE = re.compile(rf"(?:(?:©|\((?i:c)\)|\b(?i:copyright)\b)[\s:]*)+(?:\[?(?:19|20)\d\d\]?[\s,]*)?(?:by[ \t]+)?(?:the[ \t]+)?({_NAME}(?:,?[ \t]+{_SUFFIX})?)")
_ISSUER_RE = re.compile(rf"\b(?:prepared|issued|published|communicated|produced|written|approved)[ \t]+by[ \t]+(?:the[ \t]+)?({_NAME}(?:,?[ \t]+{_SUFFIX})?)")
_DOMAIN_RE = re.compile(r'(?:@|\bwww\.|https?://|\b)((?:[a-z0-9-]+\.)*([a-z0-9][a-z0-9-]{2,})\.(?:com|net|org|co\.uk|com\.au|eu|ch|de|fr|nl|jp|lu|ie|it|es|se|no|dk|fi|at|be|nz|ca|sg|hk))\b', re.I)
# Data vendors, regulators and citation sources: their names and domains appear in every
# disclaimer and chart footer without ever being the publisher.
_THIRD_PARTY = re.compile(
    r"^(?:s&p|standard & poor|dow jones|bloomberg|msci|factset|refinitiv|ice\b|intercontinental exchange|morningstar|haver"
    r"|macrobond|datastream|ftse|russell|federal reserve|fred$|reuters|the wall street journal"
    r"|moody|fitch|ishares|google|microsoft|apple|amazon|linkedin|twitter|youtube|facebook|instagram|adobe|capital iq"
    r"|bureau of|u\.?s\.? department|financial conduct|monetary authority|securities and|commodity futures)", re.I)
# Sentence and label fragments that are never a firm.
_NOISE = re.compile(r"^(?:the content|all rights|all\b|our\b|this|these|the information|information|important|disclaimer|sources?\b"
                    r"|data|notes?|figure|chart|exhibit|table|page)", re.I)
# Trailing words a domain may leave off: "Scotiabank Europe" at scotiabank.com is Scotiabank.
_GENERIC_TAIL = {'bank', 'banking', 'investment', 'investments', 'investors', 'management', 'asset', 'group', 'financial', 'services',
                 'solutions', 'exchange', 'securities', 'research', 'insurance', 'wealth', 'overseas', 'economics', 'europe',
                 'australia', 'americas', 'asia', 'uk', 'im'}
_CITATION_DOMAINS = {
    'reuters', 'bloomberg', 'wsj', 'ft', 'nytimes', 'cnbc', 'marketwatch', 'barrons', 'economist', 'sec', 'federalreserve',
    'linkedin', 'twitter', 'x', 'youtube', 'facebook', 'instagram', 'google', 'gmail', 'outlook', 'yahoo', 'apple', 'amazon',
    'microsoft', 'adobe', 'msci', 'spglobal', 'spdji', 'factset', 'refinitiv', 'theice', 'ishares', 'wikipedia',
    'imf', 'worldbank', 'oecd', 'bis', 'ecb', 'europa', 'treasury', 'bls', 'bea', 'census', 'stlouisfed', 'newyorkfed',
    'finra', 'coinmarketcap', 'fca', 'mas', 'sfc', 'esma', 'bit', 'doi', 'github', 'medium', 'substack', 'eia',
}
_DISPLAY_TAIL = re.compile(r'(?:[\s,]+(?:Group Holdings|Holdings|Pty|ABN|BD|AB|ASA|Company|Co\.?|Corporation|Corp\.?|Incorporated|Inc\.?|LLC|L\.\s?L\.\s?C\.?'
                           r'|L\.\s?P\.?|LP|LLP|Ltd\.?|Limited|plc|PLC|AG|S\.A\.|SA|N\.V\.|NV|GmbH|SE))+\s*$')
_ALIASES = {'londonstockexchangegroup': 'LSEG'}
# Email and web domains whose label does not spell the firm.
_DOMAIN_BRANDS = {'sgcib': 'Societe Generale', 'gs': 'Goldman Sachs', 'jpmorgan': 'J.P. Morgan', 'jpmchase': 'J.P. Morgan',
                  'bofa': 'BofA Global Research', 'baml': 'BofA Global Research', 'db': 'Deutsche Bank Research',
                  'ms': 'Morgan Stanley', 'ntrs': 'Northern Trust', 'lloydsbank': 'Lloyds Bank', 'incomeresearch': 'IR+M'}
_LEAD_NOISE = re.compile(r'^(?:(?:\d{4}|The|By)\s+|.*?\b(?:Division|Department|Unit|Desk) of (?:the )?)')


def _key(name):
    return re.sub(r'[^a-z0-9]', '', name.casefold())


def _display(name):
    name = re.sub(r'[*_`|]+', ' ', name)
    name = _LEAD_NOISE.sub('', re.sub(r'\s+', ' ', name).strip(' ,.;:-'))
    letters = [c for c in name if c.isalpha()]
    if letters and all(c.isupper() for c in letters) and len(letters) > 5:
        name = ' '.join(word if len(word) <= 3 and '.' not in word else word.title() for word in name.split())
    # Two-column extraction runs the next sentence on: "Ares Management. Interests ..."
    name = re.split(r'(?:(?<=[A-Za-z]{4})|(?<=\b[A-Z]{3}))\.\s', name, maxsplit=1)[0]
    name = _DISPLAY_TAIL.sub('', name).strip(' ,.;:-&')
    return _ALIASES.get(_key(name), name)


def _plain(text):
    return re.sub(r'</?u>|[*_`#>]+', ' ', text)


def _running_lines(pages):
    """Lines repeated on several pages: running headers and footers."""
    seen = {}
    for text in pages:
        for line in {re.sub(r'\s+', ' ', _plain(raw)).strip(' |') for raw in text.splitlines()}:
            if 3 <= len(line) <= 160 and re.search(r'[A-Za-z]{3}', line):
                seen[line] = seen.get(line, 0) + 1
    floor = max(2, -(-len(pages) * 3 // 10))
    return [line for line, count in seen.items() if count >= floor]


# A full link to a document is a footnote citation ("23 Monolithic Power Systems, 2026 Corporate
# Responsibility Report. https://www.monolithicpower.com/media/....pdf"), not the issuer's address.
_CITED_PATH = re.compile(r'\.pdf\b|/media/|/news/|/articles?/|/(?:19|20)\d\d/', re.I)
_OWN_PATH = re.compile(r'privacy|legal|terms|disclaimer|disclosure|contact|important-information|cookie', re.I)


def _link_weight(match, text):
    """1 for a bare domain or email, 0.5 for a link to some page, 0 for a cited document; own legal pages count in full."""
    linked = match.group(0).lower().startswith('http') or text[max(0, match.start() - 3):match.start()] == '://'
    path = re.match(r'/[^\s)\]|>]*', text[match.end():match.end() + 200])
    if not linked or not path or len(path.group(0)) < 2:
        return 1
    if _OWN_PATH.search(path.group(0)):
        return 1
    return 0 if _CITED_PATH.search(path.group(0)) else 0.5


def _discover_publisher(page_text):
    """Best publisher name from copyright, issuer, footer and domain evidence, or None."""
    numbers = sorted(page_text)
    if not numbers:
        return None
    # Image-derived (OCR) text counts here: a publisher name is not a figure the copy has to ground.
    from research.nemotron_parse import _FENCE_CLOSE, _FENCE_OPEN
    pages = {n: _plain((page_text[n] or '').replace(_FENCE_OPEN, ' ').replace(_FENCE_CLOSE, ' ')) for n in numbers}
    focus_numbers = list(dict.fromkeys([*numbers[:2], *numbers[-3:]]))
    focus = [pages[n] for n in focus_numbers]
    scores, names, vendors, backed = {}, {}, set(), set()

    def add(raw, weight):
        name = _display(raw)
        key = _key(name)
        if len(key) < 3 or _NOISE.match(name) or not re.search(r'[A-Za-z]{2}', name):
            return None
        if _THIRD_PARTY.match(name):
            vendors.add(key)  # a data vendor counts only when the document's own domain is theirs too
        scores[key] = scores.get(key, 0) + weight
        names.setdefault(key, name)
        return key

    for text in focus:
        for match in _COPYRIGHT_RE.finditer(text):
            add(match.group(1), 6)
        for match in _ISSUER_RE.finditer(text):
            add(match.group(1), 5)
    entity_hits = {}
    for text in focus:
        for match in (*_ENTITY_RE.finditer(text), *_ORG_RE.finditer(text)):
            key = add(match.group(1), 0)
            if key:
                entity_hits[key] = entity_hits.get(key, 0) + 1
    for key, hits in entity_hits.items():
        scores[key] += min(hits, 3)
    for text in focus:
        brand = _brand_on_page(text[-4000:])
        if brand:
            add(brand, 2)
    for line in _running_lines(list(pages.values())):
        for segment in re.split(r'\s*\|\s*|\s{3,}', line):
            if not re.search(r'\d{2}', segment) and not _DOMAIN_RE.search(segment) and 1 <= len(segment.split()) <= 7:
                add(segment, 2)

    domains = {}
    for number, text in pages.items():
        for match in _DOMAIN_RE.finditer(text):
            label = match.group(2).lower()
            if label not in _CITATION_DOMAINS:
                weight = _link_weight(match, text)
                if weight:
                    domains[label] = domains.get(label, 0) + weight * (2 if number in focus_numbers else 1)
    def initials(key):
        return ''.join(w[0] for w in re.findall(r"[A-Za-z][\w'’.&+-]*", names[key]) if w.lower() not in ('of', 'and', 'the')).lower()

    for label, count in domains.items():
        stems = {label.replace('-', '')} | {part for part in label.split('-') if len(part) >= 4}
        matched = [key for key in scores if initials(key) == label] or [key for key in scores if any(len(stem) >= 4 and (stem in key or key.startswith(stem[:max(5, len(key))])) for stem in stems)] or [key for key in scores if len(_key(names[key].split()[0])) >= 4 and label.replace("-", "").startswith(_key(names[key].split()[0]))]
        # The issuer's own domain recurs in every footer; a cited company's appears once or twice.
        strength = min(2 + count, 8)
        for key in matched:
            scores[key] += strength
            backed.add(key)
            words = names[key].split()
            prefix = next((' '.join(words[:n]) for n in range(1, len(words))
                           if _key(' '.join(words[:n])) == label.replace('-', '') and {w.lower() for w in words[n:]} <= _GENERIC_TAIL), None)
            if prefix:
                names[key] = prefix  # "Brown Advisory Investment" spelled by brownadvisory.com is "Brown Advisory"
        if label in _DOMAIN_BRANDS:
            add(_DOMAIN_BRANDS[label], 4 + min(count, 3))
        elif not matched and count >= 2:
            stem = label.split('-')[0]
            # Spelled as the document writes it: brownadvisory -> "Brown Advisory".
            spaced = r'\s?'.join(map(re.escape, stem))
            spelled = next((m.group(0) for m in re.finditer(rf'\b{spaced}\b', ' '.join(pages.values()), re.I) if not m.group(0).islower()), None)
            spelled = spelled or (stem.upper() if len(stem) <= 4 else stem.title())
            # Only a domain repeated across pages (a running footer, author emails) carries a name alone.
            add(spelled, min(1 + count, 8))

    for key in vendors - backed:
        scores.pop(key, None)
    if not scores:
        return None
    # A base name gathers half the weight of its own variants ("X Asset Management" over "X Asset Management UK").
    ranked = {key: score + 0.5 * sum(other for k, other in scores.items() if k != key and k.startswith(key)) for key, score in scores.items()}
    best = max(ranked, key=lambda key: (ranked[key], -len(key)))
    return names[best] if ranked[best] >= 5 else None


def _month(token):
    return _MONTH_INDEX.get(token.lower().rstrip('.'))


def _dates_in(text):
    """Yield (iso, quote) for every complete, non-excluded date in reading order."""
    for match in DATE_RE.finditer(text):
        before, after = text[max(0, match.start() - 40):match.start()], text[match.end():match.end() + 12]
        if _EXCLUDE_BEFORE.search(before) or _EXCLUDE_AFTER.match(after):
            continue
        g = match.groupdict()
        try:
            if g['y1']:
                value = date(int(g['y1']), _month(g['m1']), int(g['d1']))
            elif g['y2']:
                value = date(int(g['y2']), _month(g['m2']), int(g['d2']))
            else:
                value = date(int(g['y3']), int(g['mo3']), int(g['d3']))
        except (TypeError, ValueError):
            continue
        yield value.isoformat(), match.group(0)


def parse_pdf_date(raw):
    """PDF `D:YYYYMMDDHHmmSS...` or ISO 8601 → ISO date, else None."""
    if not isinstance(raw, str):
        return None
    match = re.match(r"D:(\d{4})(\d{2})(\d{2})", raw) or re.match(r'(\d{4})-(\d{2})-(\d{2})', raw)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
    except ValueError:
        return None


def series(name):
    base = re.sub(r'\.pdf$', '', name.lower()).replace('_', ' ')
    base = re.sub(r'\(\d+\)$', '', base).strip()
    base = re.sub(rf'\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b,?', '', base)
    base = re.sub(r'\b20\d\d-?\d\d-?\d\d\b', '', base)                                                # 2026-09-17, 20260917
    base = re.sub(rf'\b\d{{1,2}}(?:st|nd|rd|th)?[\s-]*(?:{_MONTH})[\s-]*(?:20\d\d)?\b', '', base)     # 17 september 2026
    base = re.sub(rf'\b(?:{_MONTH})[\s-]*\d{{1,2}}(?:st|nd|rd|th)?,?[\s-]*(?:20\d\d)?\b', '', base)   # sept 17, september 17 2026
    base = re.sub(r'\b20\d\d\b', '', base)
    base = re.sub(r'(?:[\s-]+en)?(?:\s+\d{5,})?\s*$', '', base)                                       # _en_1666698 suffixes
    base = re.sub(r'\s*[-,]\s*$', '', base)
    return re.sub(r'\s{2,}', ' ', base).strip(' -,')


_TICKER = re.compile(r'\(([A-Z]{1,5}(?:\.[A-Z]{1,2})?)\)|\b([A-Z]{2,5})-(?:NASDAQ|NYSE|LSE|TSX)\b')
# Filenames are often lowercased by Dropbox ("deep fission inc (fisn)"); a stray
# parenthesized word is harmless because candidates only PASS the book gate.
_NAME_TICKER = re.compile(r'\(([A-Za-z]{1,5}(?:\.[A-Za-z]{1,2})?)\)')
_RATING = re.compile(r'(?:Rating|Price Target|Target Price|\bPT\b).{0,40}\b(?:BUY|SELL|HOLD|NEUTRAL|OUTPERFORM|UNDERPERFORM|OVERWEIGHT|UNDERWEIGHT)\b'
                     r'|\b(?:BUY|SELL|HOLD|NEUTRAL|OUTPERFORM|UNDERPERFORM|OVERWEIGHT|UNDERWEIGHT)\b.{0,40}(?:Rating|Price Target|Target Price|\bPT\b)'
                     r'|Initiation of Coverage|initiat(?:e|ing|ion)\b.{0,30}\b(?:buy|sell|outperform|overweight)', re.I)


def tickers(name, page_one):
    """Uppercase ticker candidates from the filename and the page-one head."""
    found = [match.group(1).upper() for match in _NAME_TICKER.finditer(name)]
    found += [(match.group(1) or match.group(2)).upper() for match in _TICKER.finditer(page_one[:1500])]
    return tuple(dict.fromkeys(found))


def doc_type(name, page_one):
    lname, head = name.lower().replace('_', ' '), page_one[:1500]
    # Earnings-estimate calendars stay in scope (operator label 2026-09-19); economic calendars do not.
    if re.search(r'calendar|week in focus|week ahead|economic diary', lname) and 'earnings' not in lname:
        return 'calendar', 'filename names a calendar'
    if re.search(r'^[a-z]{6} en \d+\.pdf$', lname) or re.search(r'# (?:[A-Z]{3}/?[A-Z]{3}|[A-Z]{6}):', head):
        return 'fx_pair_note', 'FX pair note'
    if re.search(r'conference|communacopia|fireside|key takeaways|q-bank|takeaways', lname):
        return 'conference', 'filename names a conference recap'
    if re.search(r'initiation', lname) or (_TICKER.search(name) and _RATING.search(head)) or _RATING.search(head[:600]):
        return 'single_stock', 'ticker plus rating language'
    if re.search(r'morning meeting|research at a glance|daily download|ratings and target price changes|early morning research recap', lname):
        return 'digest', 'filename names a research digest'
    return 'research', ''


def identify(page_text, metadata, folder_date, pdf_created=None):
    """page_text: {page_number: markdown}. Returns an Identity with sourced facts."""
    folder = publisher_folder(metadata.get('path_lower', ''))
    name = metadata.get('name') or metadata.get('path_lower', '').split('/')[-1]
    from research.nemotron_parse import grounding_text
    page_one = grounding_text(page_text.get(1, '')) if page_text else ''
    attribution_page, attribution_quote = None, None
    if folder and folder not in TOPIC_FOLDERS:
        publisher, source = PUBLISHER_DISPLAY.get(folder, folder.title()), 'folder'
    else:
        ownership = _publisher_ownership(page_text)
        if ownership:
            publisher, attribution_page, attribution_quote = ownership
            source = 'page'
        else:
            brand = _brand_on_page(page_one)
            found = None if brand else _discover_publisher(page_text or {})
            publisher, source = (brand, 'page') if brand else (found, 'document') if found else ('unknown', 'none')
            if brand:
                attribution_page = 1
                pattern = next(pattern for display, pattern in PAGE_BRANDS if display == brand)
                attribution_quote = re.search(pattern, page_one[:3000]).group(0)

    limit = date.fromisoformat(folder_date) if folder_date else None
    found = None
    numbers = sorted(page_text) if page_text else []
    for number in ([numbers[0]] if numbers else []) + ([numbers[-1]] if len(numbers) > 1 else []):
        for iso, quote in _dates_in(grounding_text(page_text[number])):
            if limit and date.fromisoformat(iso) > limit:
                continue
            found = (iso, 'text', number, quote)
            break
        if found:
            break
    if not found:
        created = parse_pdf_date(pdf_created)
        if created and (not limit or date.fromisoformat(created) <= limit):
            found = (created, 'pdf', None, None)
    if not found:
        modified = parse_pdf_date(metadata.get('client_modified') or metadata.get('server_modified') or '')
        if modified:
            found = (min(modified, limit.isoformat()) if limit else modified, 'dropbox', None, None)
    if not found:
        found = (folder_date or None, 'folder' if folder_date else 'none', None, None)

    kind, reason = doc_type(name, page_one)
    names = tickers(name, page_one) if kind == 'single_stock' else ()
    return Identity(publisher, source, folder, series(name), found[0], found[1], found[2], found[3], kind, reason, names,
                    publisher_page=attribution_page, publisher_quote=attribution_quote)
