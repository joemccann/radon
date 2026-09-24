"""Always-include desks: never code-drop or empty-hold these series."""
from __future__ import annotations

BOFA_PUBLISHERS = frozenset({'bofa global research', 'bank of america'})
DB_PUBLISHERS = frozenset({'deutsche bank research', 'deutsche bank'})


def _fold(value):
    return ' '.join((value or '').casefold().replace('_', ' ').replace('-', ' ').split())


def _publishers(identity):
    return {_fold(getattr(identity, 'publisher', None)), _fold(getattr(identity, 'publisher_folder', None))}


def _haystack(identity, filename=None):
    parts = [_fold(getattr(identity, 'series', None))]
    if filename:
        parts.append(_fold(filename))
    return ' '.join(p for p in parts if p)


def matches(identity, filename=None):
    """True for BofA Flow Show or Deutsche Bank positioning data (title variants OK)."""
    pubs, text = _publishers(identity), _haystack(identity, filename)
    if pubs & BOFA_PUBLISHERS and 'flow show' in text:
        return True
    if pubs & DB_PUBLISHERS and ('positioning data' in text or 'db positioning' in text):
        return True
    return False
