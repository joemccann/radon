"""Comma-separated ticker-list parsing shared by scanner CLIs."""
from __future__ import annotations

from typing import List, Optional


def parse_ticker_list(raw: Optional[str], dedupe: bool = True) -> List[str]:
    """Split a comma-separated ticker string into uppercase symbols.

    Trims whitespace, drops empty tokens, and (by default) dedupes while
    preserving first-seen order. Pass ``dedupe=False`` for pair-oriented
    scanners where repeated symbols across pairs are legitimate.
    """
    tokens = [token.strip().upper() for token in (raw or "").split(",")]
    symbols = [token for token in tokens if token]
    if not dedupe:
        return symbols
    seen = set()
    unique: List[str] = []
    for symbol in symbols:
        if symbol not in seen:
            seen.add(symbol)
            unique.append(symbol)
    return unique
