"""Shared comma-separated ticker-list parsing for scanner CLIs (utils.ticker_args)."""
from __future__ import annotations

from utils.ticker_args import parse_ticker_list


def test_parse_ticker_list_splits_trims_and_uppercases():
    assert parse_ticker_list("nvda, amd ,TSM") == ["NVDA", "AMD", "TSM"]


def test_parse_ticker_list_dedupes_preserving_order():
    assert parse_ticker_list("MU,mu,AAPL,MU") == ["MU", "AAPL"]


def test_parse_ticker_list_keeps_duplicates_when_dedupe_disabled():
    assert parse_ticker_list("NVDA,AMD,NVDA,TSM", dedupe=False) == [
        "NVDA",
        "AMD",
        "NVDA",
        "TSM",
    ]


def test_parse_ticker_list_drops_empty_tokens():
    assert parse_ticker_list(" , NVDA,, ") == ["NVDA"]


def test_parse_ticker_list_handles_none_and_empty():
    assert parse_ticker_list("") == []
    assert parse_ticker_list(None) == []
