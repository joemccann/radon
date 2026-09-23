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
TOPIC_FOLDERS = {'nuclear', 'other', 'week ahead', 'misc'}

PUBLISHER_DISPLAY = {
    'goldman sachs': 'Goldman Sachs', 'jp morgan': 'J.P. Morgan', 'bank of america': 'BofA Global Research',
    'deutsche bank': 'Deutsche Bank Research', 'ubs': 'UBS', 'citi': 'Citi Research', 'societe generale': 'Societe Generale',
    'ts lombard': 'TS Lombard', 'zero hedge': 'Tyler Durden', 'nomura (mcelligott)': 'Nomura', 'nomura': 'Nomura',
    'wolfe': 'Wolfe Research', 'barclays': 'Barclays', 'btig': 'BTIG', 'canaccord': 'Canaccord Genuity', 'mizuho': 'Mizuho',
    'mufg': 'MUFG', 'rabobank': 'RaboResearch', 'pnc': 'PNC Economics', 'rbc': 'RBC Capital Markets', 'safra': 'Bank J Safra Sarasin',
    'standard chartered': 'Standard Chartered', 'apollo': 'Apollo', 'jefferies': 'Jefferies', 'bnp paribas': 'BNP Paribas',
    'morgan stanley': 'Morgan Stanley', 'kb securities': 'KB Securities', 'seb': 'SEB',
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
    ('Bank J Safra Sarasin', r'Safra Sarasin'), ('BNP Paribas', r'BNP Paribas'),
]

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
    publisher_source: str        # folder | page | none
    publisher_folder: str
    series: str
    date: str | None
    date_source: str             # text | pdf | dropbox | none
    date_page: int | None
    date_quote: str | None
    doc_type: str                # calendar | single_stock | conference | fx_pair_note | digest | research
    doc_type_reason: str
    tickers: tuple = ()          # single-name candidates; gates single_stock against the operator book (research.triage)

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
    page_one = page_text.get(1, '') if page_text else ''
    if folder and folder not in TOPIC_FOLDERS:
        publisher, source = PUBLISHER_DISPLAY.get(folder, folder.title()), 'folder'
    else:
        brand = _brand_on_page(page_one)
        publisher, source = (brand, 'page') if brand else ('unknown', 'none')

    limit = date.fromisoformat(folder_date) if folder_date else None
    found = None
    numbers = sorted(page_text) if page_text else []
    for number in ([numbers[0]] if numbers else []) + ([numbers[-1]] if len(numbers) > 1 else []):
        for iso, quote in _dates_in(page_text[number]):
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
    return Identity(publisher, source, folder, series(name), found[0], found[1], found[2], found[3], kind, reason, names)
