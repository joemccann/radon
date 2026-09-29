"""REL-108 / R-319: a new fill cannot sort behind a consumed page."""
import json
import sqlite3

import pytest

from clients import journal_basis


class ConcurrentJournal:
    def __init__(self, *, fail=False):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("CREATE TABLE journal (trade_id TEXT PRIMARY KEY, payload TEXT, filled_at TEXT, written_at TEXT)")
        self.calls = []
        self.fail = fail
        self.insert("ep-old", "2026-09-28T10:00:00Z")
        self.insert("zz-tail", "2026-09-28T12:00:00Z")

    def insert(self, trade_id, timestamp):
        self.connection.execute("INSERT INTO journal VALUES (?, ?, ?, ?)", (
            trade_id, json.dumps({"ticker": "AAPL", "ib_exec_id": trade_id}), timestamp, timestamp,
        ))

    def execute(self, sql, params):
        self.calls.append((sql, params))
        if len(self.calls) == 2:
            if self.fail:
                raise sqlite3.OperationalError("injected page failure")
            # The first page has already passed the digit-prefixed namespace.
            self.insert("2026-09-29T09:31:00#1", "2026-09-28T11:00:00Z")
        return self.connection.execute(sql, params)


def test_insert_after_first_page_is_returned_in_effective_time_order(monkeypatch):
    monkeypatch.setattr(journal_basis, "_JOURNAL_PAGE_SIZE", 1)
    db = ConcurrentJournal()
    try:
        rows = journal_basis._fetch_journal_rows_for_tickers(db, ["AAPL"])
        assert [json.loads(row[0])["ib_exec_id"] for row in rows] == [
            "ep-old", "2026-09-29T09:31:00#1", "zz-tail",
        ]
        assert all(params[-1] == 1 for _, params in db.calls)
        assert len(db.calls) == 4  # Three complete pages and the final empty page.
    finally:
        db.connection.close()


def test_page_failure_never_returns_a_partial_basis(monkeypatch):
    monkeypatch.setattr(journal_basis, "_JOURNAL_PAGE_SIZE", 1)
    db = ConcurrentJournal(fail=True)
    try:
        with pytest.raises(sqlite3.OperationalError, match="injected page failure"):
            journal_basis._fetch_journal_rows_for_tickers(db, ["AAPL"])
    finally:
        db.connection.close()
