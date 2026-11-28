"""Rows inside a provider payload.

Unusual Whales answers with either a list of row objects or
{"data": [row, ...]}. Other shapes and non-object entries are dropped.
"""
from __future__ import annotations

from typing import Any


def rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        rows = payload.get("data")
    else:
        rows = payload
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]
