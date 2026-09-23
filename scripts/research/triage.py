"""Recall-first triage before any model call.

Measured on the private corpus 2026-09-19: published documents include
single-stock, conference and digest types, so only types with zero published
and zero operator in-scope examples are dropped. Every drop carries a reason
code. A series denylist is explicit configuration, never inferred.
"""
# Calendars are not dropped by code: the operator upvoted a week-ahead note (Fed speaker dates, 2026-09-20).
# A calendar drop, like any other type, now needs an approved doc_type_drop rule (research.learn).
DROP_TYPES = {'fx_pair_note': 'DOC_TYPE_FX_PAIR_NOTE'}


def decide(identity, denylist=frozenset(), rules=None, book=None):
    """Return ('drop', REASON_CODE) or ('review', None). `rules` are operator-approved proposals (research.learn).

    `book` is the operator's watchlist + portfolio ticker set (research.book):
    single-name equity research drops unless a ticker candidate is in it. None
    means no book source was readable; that fails open so an outage cannot
    drop watched names.
    """
    rules = rules or {}
    if identity.series in denylist or identity.series in rules.get('series_deny', ()):
        return 'drop', 'SERIES_DENYLIST'
    if identity.doc_type in rules.get('doc_type_drop', ()):
        return 'drop', 'RULE_DOC_TYPE'
    if identity.publisher in rules.get('publisher_deny', ()):
        return 'drop', 'RULE_PUBLISHER'
    if identity.doc_type == 'single_stock' and book is not None and not book.intersection(identity.tickers):
        return 'drop', 'SINGLE_STOCK_NOT_IN_BOOK'
    code = DROP_TYPES.get(identity.doc_type)
    return ('drop', code) if code else ('review', None)
