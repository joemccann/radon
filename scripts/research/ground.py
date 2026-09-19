"""Numeric grounding gate: every number in authored copy must appear on a cited page.

Finance notation is tokenized whole (2Q26, Q1 2023, FY27, 10Y, 2s10s, $260bn,
10-15bp, 16 September 2026, Chart 2) and looked up on the cited pages by
code. Values must be equal as decimals (formatting such as 0.2% vs 0.20% is
not rounding), units must match when the copy states one, currencies must
not contradict, and signs must agree. The model supplies no quotes.
"""
from __future__ import annotations
import re
import unicodedata
from dataclasses import dataclass, asdict
from decimal import Decimal, InvalidOperation

MONTH = r'(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?'
_NUM = r'(?:(?<!\d)(?<!\d\.)(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)|(?<![\w.])\.\d+)'
_UNITS = (r'%|percent|pct|bps?|basis\s+points?|ppts?|pp|tn|trn|trillion|bn|billion|mn|mm|million|k|x|/bbl|/oz|/t\b|'
          r'-?years?|-?yrs?|-?months?|-?days?|-?weeks?')
_CURRENCY = r'(?:USD|EUR|GBP|JPY|CHF|CNY|CNH|HKD|AUD|CAD|NZD|SEK|NOK|DKK|Nkr|SKr|DKr)\s?|[$€£¥]'

TOKEN_RE = re.compile('|'.join([
    rf'(?P<period>\b(?:[1-4]Q(?:20)?\d\d|Q[1-4](?:\s?(?:20)?\d\d)?|[12]H(?:20)?\d\d|H[12](?:\s?20\d\d)?|(?:FY|CY)\s?(?:20)?\d\d(?:-(?:20)?\d\d)?[A-Z]?)\b)',
    rf'(?P<date>\b\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTH}\s+20\d\d\b|\b{MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+20\d\d\b|\b20\d\d-\d\d-\d\d\b)',
    r'(?P<figure>\b(?:Chart|Exhibit|Figure|Fig\.?|Table|Panel)\s?\d+[A-Za-z]?\b)',
    r'(?P<tenor>\b\d{1,2}[sS]\d{1,2}[sS]\b|\b\d{1,2}[yY]\d{1,2}[yY]\b|\b\d{1,3}[YyM]\b)',
    r'(?P<yearrange>\b(?:19|20)\d\d[-–](?:(?:19|20)\d\d|\d\d)[A-Z]?\b)',
    rf'(?P<number>(?P<cur>{_CURRENCY})?(?:(?<![A-Za-z])(?P<sign>[+\-−]))?(?P<v1>{_NUM})(?:(?:-|\s-\s|\s?to\s?)(?P<v2>{_NUM}))?(?P<unit>\s?(?:{_UNITS})(?![A-Za-z]))?)',
]), re.I)
# Page-side number scan: plain numbers everywhere, including inside dates and periods, so a bare
# year or day in the copy can still be grounded.
NUMBER_RE = re.compile(rf'(?P<cur>{_CURRENCY})?(?:(?<![A-Za-z])(?P<sign>[+\-−]))?(?P<v1>{_NUM})(?:(?:-|\s-\s|\s?to\s?)(?P<v2>{_NUM}))?(?P<unit>\s?(?:{_UNITS})(?![A-Za-z]))?', re.I)
_PERIOD = re.compile(r'^(?:(?P<q1>[1-4])Q|Q(?P<q2>[1-4])|(?P<h1>[12])H|H(?P<h2>[12])|(?P<fy>FY|CY))\s?-?\'?(?P<y>(?:20)?\d\d)?(?:-(?:20)?(?P<y2>\d\d))?(?P<e>[A-Z])?$', re.I)


def _period_key(token):
    """Canonical (kind, n, year, year2): Q4 2027, 4Q27, Q4-27 and 4Q2027 are the same period."""
    match = _PERIOD.match(token.strip())
    if not match:
        return None
    g = match.groupdict()
    kind = 'Q' if g['q1'] or g['q2'] else 'H' if g['h1'] or g['h2'] else g['fy'].upper()
    n = g['q1'] or g['q2'] or g['h1'] or g['h2'] or ''
    year = (g['y'] or '')[-2:]
    return kind, n, year, (g['y2'] or '')[-2:]
_DATE_PARSE = re.compile(rf'(?:(?P<d1>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<m1>{MONTH})\s+(?P<y1>20\d\d))|(?:(?P<m2>{MONTH})\s+(?P<d2>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y2>20\d\d))|(?:(?P<y3>20\d\d)-(?P<mo3>\d\d)-(?P<d3>\d\d))', re.I)
_MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
_TIME = re.compile(r'(?<=\d)[:.]\d\d\s?(?:am|pm)\b|\b\d{1,2}:\d\d', re.I)
_ALIASES = {'bp': 'bp', 'bps': 'bp', 'basispoint': 'bp', 'basispoints': 'bp', 'percent': '%', 'pct': '%', 'ppt': 'pp',
            'ppts': 'pp', 'trn': 'tn', 'trillion': 'tn', 'billion': 'bn', 'mm': 'mn', 'million': 'mn', 'years': 'year',
            'yr': 'year', 'yrs': 'year', 'months': 'month', 'days': 'day', 'weeks': 'week'}
_CURRENCY_NORM = {'$': 'USD', '€': 'EUR', '£': 'GBP', '¥': 'JPY', 'nkr': 'NOK', 'skr': 'SEK', 'dkr': 'DKK'}


@dataclass
class Token:
    token: str
    kind: str                      # period | date | figure | tenor | yearrange | number
    values: tuple = ()             # Decimal values for number tokens (one, or two for a range)
    sign: str = ''
    unit: str = ''
    currency: str = ''
    page: int | None = None        # cited page where it was found, set by ground()

    def as_dict(self):
        record = asdict(self)
        record['values'] = [str(v) for v in self.values]
        return record


def normalize(text):
    text = unicodedata.normalize('NFKC', text or '')
    text = re.sub(r'</?u>|\*\*|__|(?<!\w)\*|\*(?!\w)|(?<![\w|])_|_(?![\w|])', '', text)
    text = text.replace('|', ' ').replace('×', 'x')
    for dash in ('−', '–', '—', '‐', '‑', '‒', '―'):
        text = text.replace(dash, '-')
    return re.sub(r'\s+', ' ', text).strip()


_UNIT_WORDS = {'%': r'%|percent|per cent|pct', 'bp': r'\bbps?\b|basis points?', 'bn': r'\bbns?\b|billions?', 'mn': r'\bmns?\b|\bmm\b|\bm\b|millions?',
               'tn': r'\btn\b|trillions?', 'pp': r'\bpp\b|\bppts?\b|percentage points?', 'x': r'\bx\b|times', 'k': r'\bk\b|thousands?',
               'month': r'\bmonths?\b|\bmos?\b', 'year': r'\byears?\b|\byrs?\b'}


def _unit_in_context(page_text, position, unit, reach=800):
    """A table or axis often states its unit once (a 'Percent' header); accept a bare value near that word."""
    words = _UNIT_WORDS.get(unit)
    if not words:
        return False
    window = page_text[max(0, position - reach):position + reach]
    return re.search(words, window, re.I) is not None


def _unit(raw):
    key = re.sub(r'[\s-]+', '', (raw or '').lower())
    return _ALIASES.get(key, key)


def _date_key(text):
    """ISO date for any complete date spelling, so '11 Sep 2026' grounds on '11 September 2026'."""
    match = _DATE_PARSE.search(text)
    if not match:
        return None
    g = match.groupdict()
    try:
        if g['y1']:
            return f"{int(g['y1']):04d}-{_MONTHS.index(g['m1'][:3].lower()) + 1:02d}-{int(g['d1']):02d}"
        if g['y2']:
            return f"{int(g['y2']):04d}-{_MONTHS.index(g['m2'][:3].lower()) + 1:02d}-{int(g['d2']):02d}"
        return f"{int(g['y3']):04d}-{int(g['mo3']):02d}-{int(g['d3']):02d}"
    except ValueError:
        return None


def _tenor_pattern(token):
    match = re.fullmatch(r'(\d{1,3})([YyM])', token)
    if not match:
        return r'\s*'.join(re.escape(part) for part in token.lower().split())
    value, kind = match.group(1), match.group(2).lower()
    words = 'y|yr|yrs|year|years' if kind == 'y' else 'm|mo|month|months'
    return rf'{value}\s?-?\s?(?:{words})'


def _currency(raw):
    key = (raw or '').strip()
    return _CURRENCY_NORM.get(key.lower(), key.upper())


def numeric_tokens(text):
    """Every numeric token in normalized text, finance notation kept whole; clock times are skipped."""
    text = normalize(text)
    blocked = [m.span() for m in _TIME.finditer(text)]
    out = []
    for match in TOKEN_RE.finditer(text):
        if any(start <= match.start() < end or start < match.end() <= end for start, end in blocked):
            continue
        kind = match.lastgroup if match.lastgroup in ('period', 'date', 'figure', 'tenor', 'yearrange') else 'number'
        raw = match.group(0).strip()
        if kind != 'number':
            out.append(Token(raw, kind))
            continue
        try:
            values = tuple(Decimal(match.group(v).replace(',', '')) for v in ('v1', 'v2') if match.group(v))
        except InvalidOperation:
            continue
        out.append(Token(raw, 'number', values, match.group('sign') or '', _unit(match.group('unit')), _currency(match.group('cur'))))
    return out


def _page_numbers(page_text):
    out = []
    for match in NUMBER_RE.finditer(page_text):
        try:
            values = tuple(Decimal(match.group(v).replace(',', '')) for v in ('v1', 'v2') if match.group(v))
        except InvalidOperation:
            continue
        out.append((Token(match.group(0), 'number', values, match.group('sign') or '', _unit(match.group('unit')), _currency(match.group('cur'))), match.start()))
    return out


def _number_found(token, page_tokens, page_text):
    for value in token.values:
        hit = False
        for other, position in page_tokens:
            if value not in other.values:
                continue
            if token.unit and other.unit != token.unit:
                if other.unit or not _unit_in_context(page_text, position, token.unit):
                    continue
            if token.currency and other.currency and other.currency != token.currency:
                continue
            # An explicit sign in the copy must be on the page; unsigned copy may describe a signed value in words.
            if token.sign == '-' and other.sign != '-':
                continue
            hit = True
            break
        if not hit:
            return False
    return True


def ground(copy_fields, page_text, cited_pages):
    """copy_fields: strings (title, body, captions). Returns {'passed': bool, 'tokens': [...]}."""
    pages = {p: normalize(page_text.get(p, '')) for p in cited_pages}
    page_numbers = {p: _page_numbers(text) for p, text in pages.items()}
    page_lower = {p: text.lower() for p, text in pages.items()}
    page_dates = {p: {_date_key(m.group(0)) for m in _DATE_PARSE.finditer(text)} for p, text in pages.items()}
    page_periods = {p: {_period_key(t.token) for t in numeric_tokens(text) if t.kind == 'period'} for p, text in pages.items()}
    seen, results = set(), []
    for field in copy_fields:
        for token in numeric_tokens(field or ''):
            key = token.token.lower()
            if key in seen:
                continue
            seen.add(key)
            for p in cited_pages:
                if token.kind == 'number':
                    found = _number_found(token, page_numbers[p], pages[p])
                    if not found and token.unit in ('year', 'month') and len(token.values) == 1 and not token.currency:
                        # "10-year" in the copy grounds on a "10Y" tenor on the page.
                        found = re.search(rf'(?<![\w]){_tenor_pattern(f"{token.values[0]}{token.unit[0].upper()}")}(?![\w])', page_lower[p]) is not None
                elif token.kind == 'date':
                    found = _date_key(token.token) in page_dates[p]
                elif token.kind == 'period':
                    key_ = _period_key(token.token)
                    found = key_ in page_periods[p]
                    if not found and key_ and not key_[2]:
                        found = any(k and k[:2] == key_[:2] for k in page_periods[p])       # bare "Q2" against "Q2 2026"
                    if not found and key_ and key_[0] in ('FY', 'CY') and key_[2] and not key_[3]:
                        found = _number_found(Token(token.token, 'number', (Decimal('20' + key_[2]),)), page_numbers[p], pages[p])  # FY26 against 2026E
                else:
                    pattern = _tenor_pattern(token.token) if token.kind == 'tenor' else r'\s*'.join(re.escape(part) for part in token.token.lower().split())
                    if token.kind == 'yearrange':
                        # "2024-2050" in the copy grounds on "2024-50" on the page and vice versa.
                        first, second = re.match(r'((?:19|20)\d\d)-((?:19|20)?\d\d)', token.token).groups()
                        pattern = rf'{first}\s?-\s?(?:19|20)?{second[-2:]}'
                    found = re.search(rf'(?<![\w]){pattern}(?![\w])', page_lower[p]) is not None
                if found:
                    token.page = p
                    break
            results.append(token.as_dict())
    return {'passed': all(t['page'] is not None for t in results), 'tokens': results}
