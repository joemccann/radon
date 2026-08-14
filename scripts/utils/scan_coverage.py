"""Coverage + persist gate for UW universe scans.

A completed NDX pass that found nothing because UW 429'd every name still
writes ``candidates_found: 0``. Readers and writers must not treat that as
a real empty tape — it clobbers the last good snapshot.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

MIN_COMPLETED_RATIO = 0.5


def payload_has_candidates(payload: Optional[Mapping[str, Any]]) -> bool:
    if not isinstance(payload, Mapping):
        return False
    try:
        found = int(payload.get("candidates_found") or 0)
    except (TypeError, ValueError):
        found = 0
    if found > 0:
        return True
    results = payload.get("results")
    return isinstance(results, list) and len(results) > 0


def coverage_block(
    *,
    tickers: int,
    ok: int,
    no_setup: int,
    rate_limited: int,
    errors: int,
) -> dict[str, int]:
    return {
        "tickers": tickers,
        "ok": ok,
        "no_setup": no_setup,
        "rate_limited": rate_limited,
        "errors": errors,
        "completed": ok + no_setup,
    }


def is_coverage_failed(payload: Optional[Mapping[str, Any]]) -> bool:
    if payload_has_candidates(payload):
        return False
    if not isinstance(payload, Mapping):
        return True
    coverage = payload.get("coverage")
    try:
        tickers = int(payload.get("tickers_scanned") or 0)
        completed = 0
        if isinstance(coverage, Mapping):
            tickers = int(coverage.get("tickers") or tickers)
            completed = int(coverage.get("completed") or 0)
    except (TypeError, ValueError):
        return True
    if tickers <= 0:
        return True
    return completed / tickers < MIN_COMPLETED_RATIO


def should_persist_scan(
    payload: Optional[Mapping[str, Any]],
    prior: Optional[Mapping[str, Any]],
) -> bool:
    if payload_has_candidates(payload):
        return True
    if payload_has_candidates(prior):
        return False
    return not is_coverage_failed(payload)
