"""Always-include desks: never code-drop or empty-hold these series.

BofA Michael Hartnett (The Flow Show, Fund Manager Survey, and any note
authored by Hartnett) always publishes. Deutsche Bank positioning stays
must-include without that exemption.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
import re

BOFA_PUBLISHERS = frozenset({
    'bofa global research', 'bank of america',
    'bofa', 'baml', 'merrill', 'bofa securities',
})
DB_PUBLISHERS = frozenset({'deutsche bank research', 'deutsche bank'})
CITADEL_PUBLISHERS = frozenset({'citadel', 'citadel securities'})
UNRESOLVED_SUBJECT_HOLD = 'EQUITY_RESEARCH_SUBJECT_UNRESOLVED'


@dataclass(frozen=True)
class Desk:
    name: str
    always_publish: bool = False
    priority: bool = False


BOFA = Desk('bofa_flow_show', always_publish=True)
BOFA_FMS = Desk('bofa_fund_manager_survey', always_publish=True)
HARTNETT = Desk('hartnett', always_publish=True)
DB = Desk('db_positioning')
RUBNER = Desk('citadel_rubner', always_publish=True, priority=True)

_GMI = re.compile(r'\bgmi\b|global markets? intelligence')
_CS_RUBNER = re.compile(r'\bcs rubner\b')
_HARTNETT = re.compile(r'\bhartnett\b')
_FMS = re.compile(r'\b(?:fund manager surveys?|fms)\b')
_BYLINE_CHARS = 400


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


def _is_bofa(pubs):
    return bool(pubs & BOFA_PUBLISHERS)


def _hartnett_author(author, text, page, bofa):
    """Author field or title when present; a BofA byline counts, a later citation does not."""
    named = _fold(author)
    if named and _HARTNETT.search(named):
        return True
    if _HARTNETT.search(text):
        return True
    return bool(bofa and _HARTNETT.search((page or '')[:_BYLINE_CHARS]))


def match(identity, filename=None, page_text=None, author=None):
    """Named desk for a force-include identity, or None."""
    pubs, text = _publishers(identity), _haystack(identity, filename)
    page = _fold(page_text)
    bofa = _is_bofa(pubs)
    if bofa and 'flow show' in text:
        return BOFA
    if bofa and _FMS.search(text):
        return BOFA_FMS
    if _hartnett_author(author, text, page, bofa):
        return HARTNETT
    if pubs & DB_PUBLISHERS and ('positioning data' in text or 'db positioning' in text):
        return DB
    if _is_rubner(pubs, text, page):
        return RUBNER
    return None


def matches(identity, filename=None, page_text=None, author=None):
    """True for Hartnett/BofA Flow Show/FMS, Deutsche Bank positioning data, or Citadel Rubner GMI."""
    return match(identity, filename=filename, page_text=page_text, author=author) is not None


def always_publish(identity, filename=None, page_text=None, author=None):
    desk = match(identity, filename=filename, page_text=page_text, author=author)
    return bool(desk and desk.always_publish)


def skips_unresolved_subject_hold(post, outcome, reason):
    """Always-publish desks are not parked when equity admission cannot name a subject."""
    if outcome != 'held' or reason != UNRESOLVED_SUBJECT_HOLD or not isinstance(post, dict):
        return False
    source = post.get('source') if isinstance(post.get('source'), dict) else {}
    folders = source.get('folders') if isinstance(source.get('folders'), list) else []
    folder = folders[3] if len(folders) > 3 and isinstance(folders[3], str) else ''
    identity = SimpleNamespace(publisher=source.get('publisher') or '', series='', publisher_folder=folder)
    return always_publish(identity, filename=source.get('fileName') or post.get('title') or '',
                          page_text=post.get('content') or post.get('title') or '')

