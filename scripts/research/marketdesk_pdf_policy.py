"""Conservative PDF-only relevance. Article publication never consults this policy.

Use the already-published article and current Turso book, not another provider
request or model call. Unknown/ambiguous relevance does not spend PDF allowance.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from types import SimpleNamespace

from api.db_http import hrana_execute
from research import book, force_include, identify, triage

POLICY_VERSION = 1
SECURITY = re.compile(r"\$([A-Z][A-Z0-9.\-]{0,9})\b")
MARKET_SYMBOLS = frozenset({"SPX", "SPY", "NDX", "QQQ", "IWM", "RUT", "VIX", "VIX1D", "VVIX", "DXY", "USD", "EUR", "JPY", "GBP", "BTC", "ETH", "TLT", "HYG", "LQD"})
TOPIC = re.compile(r"\b(?:flows?|positioning|volatility|options?|gamma|liquidity|breadth|(?:aggregate|index|US) earnings|GDP|inflation|yields?|rates?|credit|semiconductors?|chip exports?|AI investment|market structure)\b", re.I)
MEASURE = re.compile(r"(?:[-+]?\d+(?:\.\d+)?\s*(?:%|bps?\b|bn\b|million\b|billion\b|trillion\b)|\$\s*\d)", re.I)
FX = re.compile(r"\b(?:USD|EUR|JPY|GBP|CHF|CAD|AUD|NZD|BRL|MXN|CNY|CNH|SEK|NOK|ZAR)[/\-](?:USD|EUR|JPY|GBP|CHF|CAD|AUD|NZD|BRL|MXN|CNY|CNH|SEK|NOK|ZAR)\b")


class SelectionUnavailable(RuntimeError):
    """No usable current book; never fail open to an account-metered download."""


@dataclass(frozen=True)
class Context:
    tickers: frozenset[str]
    rules: dict

    @property
    def key(self):
        payload = {"version": POLICY_VERSION, "tickers": sorted(self.tickers),
                   "rules": {k: sorted(v) for k, v in self.rules.items()}}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def load_context():
    watch = hrana_execute("SELECT ticker FROM watchlist ORDER BY ticker LIMIT 5001")
    snapshots = hrana_execute("SELECT json_extract(payload,'$.positions') FROM portfolio_snapshots ORDER BY taken_at DESC LIMIT 1")
    try:
        positions = json.loads(snapshots[0][0])
    except (IndexError, TypeError, ValueError):
        raise SelectionUnavailable("book_unavailable") from None
    if not isinstance(positions, list):
        raise SelectionUnavailable("book_unavailable")
    if len(watch) > 5000:
        raise SelectionUnavailable("book_unavailable")
    rules = {}
    approved = hrana_execute("SELECT kind,key FROM research_rule_proposals WHERE status='approved' ORDER BY id LIMIT 5001")
    if len(approved) > 5000:
        raise SelectionUnavailable("book_unavailable")
    for kind, key in approved:
        if kind in {"series_deny", "publisher_deny", "doc_type_drop"}:
            rules.setdefault(kind, set()).add(key)
    return Context(frozenset(book._clean(row[0] for row in watch) | book._positions_tickers(positions)), rules)


def decide(*, title, content, publisher, context):
    title, content = str(title or ""), str(content or "")
    kind, _ = identify.doc_type(title + ".pdf", content)
    symbols = set(SECURITY.findall(title + "\n" + content)) - MARKET_SYMBOLS
    if kind == "single_stock":
        symbols.update(set(identify.tickers(title, title)) - MARKET_SYMBOLS)
    identity = SimpleNamespace(series=identify.series(title + ".pdf"), doc_type=kind, publisher=publisher or "unknown",
                               publisher_folder=publisher or "", tickers=tuple(symbols))
    decision, reason = triage.decide(identity, rules=context.rules, book=context.tickers)
    if decision == "drop":
        return False, reason
    if FX.search(title) or kind == "fx_pair_note":
        return False, "DOC_TYPE_FX_PAIR_NOTE"
    if kind == "calendar":
        return False, "PDF_ROUTINE_CALENDAR"
    if force_include.matches(identity, filename=title, page_text=content):
        return True, "PDF_PREFERRED_DESK"
    if symbols:
        if symbols & context.tickers:
            return True, "PDF_BOOK_OR_WATCHLIST"
        return False, "SINGLE_STOCK_NOT_IN_BOOK"
    if TOPIC.search(title + "\n" + content) and MEASURE.search(content):
        return True, "PDF_MEASURED_MARKET_RESEARCH"
    return False, "PDF_NO_MEASURED_RELEVANCE"
