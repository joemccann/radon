"""Recall-first triage before any model call.

Measured on the private corpus 2026-09-19: published documents include
single-stock, conference and digest types, so only types with zero published
and zero operator in-scope examples are dropped. Every drop carries a reason
code. A series denylist is explicit configuration, never inferred.
"""
DROP_TYPES = {'fx_pair_note': 'DOC_TYPE_FX_PAIR_NOTE', 'calendar': 'DOC_TYPE_CALENDAR'}


def decide(identity, denylist=frozenset()):
    """Return ('drop', REASON_CODE) or ('review', None)."""
    if identity.series in denylist:
        return 'drop', 'SERIES_DENYLIST'
    code = DROP_TYPES.get(identity.doc_type)
    return ('drop', code) if code else ('review', None)
