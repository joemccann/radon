"""Refuse empty UW-quota scans that would clobber the last good snapshot."""
from __future__ import annotations

from utils.scan_coverage import (
    coverage_block,
    is_coverage_failed,
    payload_has_candidates,
    should_persist_scan,
)


def test_payload_has_candidates_reads_count_or_results() -> None:
    assert payload_has_candidates({"candidates_found": 59, "results": []}) is True
    assert payload_has_candidates({"candidates_found": 0, "results": [{"ticker": "AAPL"}]}) is True
    assert payload_has_candidates({"candidates_found": 0, "results": []}) is False
    assert payload_has_candidates(None) is False


def test_coverage_failed_when_universe_was_not_evaluated() -> None:
    empty = {
        "candidates_found": 0,
        "results": [],
        "tickers_scanned": 102,
        "coverage": coverage_block(tickers=102, ok=0, no_setup=0, rate_limited=102, errors=0),
    }
    assert is_coverage_failed(empty) is True
    assert should_persist_scan(empty, {"candidates_found": 59, "results": [{"t": 1}]}) is False


def test_high_coverage_empty_may_persist_only_when_there_is_no_last_good() -> None:
    empty = {
        "candidates_found": 0,
        "results": [],
        "tickers_scanned": 102,
        "coverage": coverage_block(tickers=102, ok=0, no_setup=102, rate_limited=0, errors=0),
    }
    assert is_coverage_failed(empty) is False
    assert should_persist_scan(empty, {"candidates_found": 59, "results": [{"t": 1}]}) is False
    assert should_persist_scan(empty, None) is True


def test_legacy_empty_ndx_snapshot_without_coverage_is_failed() -> None:
    # Production 2026-08-14 15:45–17:15 rows have no coverage block.
    empty = {"candidates_found": 0, "results": [], "tickers_scanned": 102}
    assert is_coverage_failed(empty) is True
    assert should_persist_scan(empty, {"candidates_found": 59, "results": [{"t": 1}]}) is False
