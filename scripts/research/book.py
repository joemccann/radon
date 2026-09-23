"""Operator book for triage: watchlist plus portfolio underlying tickers.

Turso first (watchlist table, latest portfolio snapshot), data/*.json fallback.
`tickers()` returns None only when NEITHER source of a series is readable, so
triage fails open to review; an empty-but-readable book is a real answer.
"""
from __future__ import annotations
import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / 'data'

# Flex rehydrate stores OCC symbols ("SPY   260918P00740000"); live rows store the root.
_OCC_TICKER = re.compile(r'^([A-Z.]{1,6})\s+\d{6}[CP]\d{8}$')


def _underlying(raw):
    text = str(raw or '').strip().upper()
    match = _OCC_TICKER.match(text)
    return match.group(1) if match else text


def _clean(values):
    return {symbol for symbol in (str(v).strip().upper() for v in values if v) if symbol}


def _positions_tickers(positions):
    if not isinstance(positions, list):
        positions = []
    return _clean(_underlying(p.get('ticker') or p.get('symbol'))
                  for p in positions if isinstance(p, dict) and (p.get('ticker') or p.get('symbol')))


def _watchlist():
    try:
        from db.readers import read_watchlist_tickers
        return _clean(read_watchlist_tickers())
    except Exception:
        pass
    try:
        stored = json.loads((DATA_DIR / 'watchlist.json').read_text())
    except (OSError, ValueError):
        return None
    items = stored.get('tickers') if isinstance(stored, dict) else None
    return _clean(item.get('ticker') if isinstance(item, dict) else item for item in items or [])


def _portfolio():
    try:
        from db.readers import read_portfolio_positions
        return _positions_tickers(read_portfolio_positions())
    except Exception:
        pass
    try:
        payload = json.loads((DATA_DIR / 'portfolio.json').read_text())
    except (OSError, ValueError):
        return None
    return _positions_tickers(payload.get('positions') if isinstance(payload, dict) else None)


def tickers():
    """frozenset of book tickers, or None when no source is readable."""
    watch, port = _watchlist(), _portfolio()
    if watch is None and port is None:
        return None
    return frozenset((watch or set()) | (port or set()))
