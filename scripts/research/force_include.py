"""Always-include desks: never code-drop or empty-hold these series."""
from __future__ import annotations

from dataclasses import dataclass
import re

BOFA_PUBLISHERS = frozenset({'bofa global research', 'bank of america'})
DB_PUBLISHERS = frozenset({'deutsche bank research', 'deutsche bank'})
CITADEL_PUBLISHERS = frozenset({'citadel', 'citadel securities'})


@dataclass(frozen=True)
class Desk:
    name: str
    always_publish: bool = False
    priority: bool = False


BOFA = Desk('bofa_flow_show')
DB = Desk('db_positioning')
RUBNER = Desk('citadel_rubner', always_publish=True, priority=True)

_GMI = re.compile(r'\bgmi\b|global markets? intelligence')
_CS_RUBNER = re.compile(r'\bcs rubner\b')


def _fold(value):
    return ' '.join((value or '').casefold().replace('_', ' ').replace('-', ' ').split())


def _publishers(identity):
    return {_fold(getattr(identity, 'publisher', None)), _fold(getattr(identity, 'publisher_folder', None))}


def _haystack(identity, filename=None):
    parts = [_fold(getattr(identity, 'series', None))]
    if filename:
        parts.append(_fold(filename))
    return ' '.join(p for p in parts if p)


def _citadel_or_unknown(pubs):
    named = {p for p in pubs if p}
    return bool(named & CITADEL_PUBLISHERS), not (named - {'unknown'})


def _is_rubner(pubs, hay, page):
    citadel, unknown = _citadel_or_unknown(pubs)
    if not citadel and not unknown:
        return False
    blob = f'{hay} {page}'.strip()
    if 'scott rubner' in page:
        return True
    if not citadel:
        return False
    return 'rubner' in hay or bool(_CS_RUBNER.search(blob)) or bool(_GMI.search(hay))


def match(identity, filename=None, page_text=None):
    """Named desk for a force-include identity, or None."""
    pubs, text = _publishers(identity), _haystack(identity, filename)
    if pubs & BOFA_PUBLISHERS and 'flow show' in text:
        return BOFA
    if pubs & DB_PUBLISHERS and ('positioning data' in text or 'db positioning' in text):
        return DB
    if _is_rubner(pubs, text, _fold(page_text)):
        return RUBNER
    return None


def matches(identity, filename=None, page_text=None):
    """True for BofA Flow Show, Deutsche Bank positioning data, or Citadel Rubner GMI."""
    return match(identity, filename=filename, page_text=page_text) is not None


def always_publish(identity, filename=None, page_text=None):
    desk = match(identity, filename=filename, page_text=page_text)
    return bool(desk and desk.always_publish)

