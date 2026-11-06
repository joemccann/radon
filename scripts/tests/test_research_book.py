"""Operator book for triage: watchlist + portfolio tickers from Turso only."""
import pytest

import db.readers as readers
from research import book


def _fail(*_args, **_kwargs):
    raise RuntimeError("Turso unavailable")


def test_turso_union_normalizes_occ_symbols_and_case(monkeypatch):
    monkeypatch.setattr(readers, "read_watchlist_tickers", lambda db=None: ["NVDA", " aspi "])
    monkeypatch.setattr(readers, "read_portfolio_positions", lambda db=None: [
        {"ticker": "SPY   260918P00740000"}, {"ticker": "tsla"}, {"symbol": "GLD"}, {"ticker": ""}, {}])
    assert book.tickers() == frozenset({"NVDA", "ASPI", "SPY", "TSLA", "GLD"})


def test_one_source_down_still_returns_the_other(monkeypatch):
    monkeypatch.setattr(readers, "read_watchlist_tickers", lambda db=None: ["NVDA"])
    monkeypatch.setattr(readers, "read_portfolio_positions", _fail)
    assert book.tickers() == frozenset({"NVDA"})


def test_no_readable_source_returns_none(monkeypatch):
    monkeypatch.setattr(readers, "read_watchlist_tickers", _fail)
    monkeypatch.setattr(readers, "read_portfolio_positions", _fail)
    assert book.tickers() is None


def test_empty_but_readable_book_is_a_real_answer(monkeypatch):
    monkeypatch.setattr(readers, "read_watchlist_tickers", lambda db=None: [])
    monkeypatch.setattr(readers, "read_portfolio_positions", lambda db=None: [])
    assert book.tickers() == frozenset()
