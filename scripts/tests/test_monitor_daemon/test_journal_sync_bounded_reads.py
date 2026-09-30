"""REL-108 / NF-2: journal history is complete or unavailable, never truncated."""
import json
import sqlite3
from unittest.mock import Mock

import pytest

from monitor_daemon.handlers import journal_sync as mod
from knowledge import http_db


class History:
    def __init__(self, fail_page=None):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE journal(trade_id TEXT PRIMARY KEY, payload TEXT, filled_at TEXT, written_at TEXT)')
        self.calls = []
        self.fail_page = fail_page
        for i in range(405):
            self.add(f'fill-monitor:con-{i + 1}:order-1:2026-09-30:filled-1', i)

    def add(self, identity, ordinal):
        self.db.execute('INSERT INTO journal VALUES (?, ?, ?, ?)', (identity, json.dumps({'ib_exec_id': identity, 'ticker': 'TEST', 'id': ordinal}), f'2026-09-30T{ordinal:06d}', f'2026-09-30T{ordinal:06d}'))

    def execute(self, sql, args=()):
        self.calls.append((sql, args))
        assert 'LIMIT' in sql.upper(), 'unbounded history request'
        if len(self.calls) == self.fail_page:
            raise TimeoutError('injected later-page timeout')
        rows = self.db.execute(sql, args).fetchall()
        if len(self.calls) == 1:
            # A lexical cursor would lose this insert after the first page.
            self.add('0-new-execution', 999)
        cursor = Mock()
        cursor.fetchall.return_value = rows
        return cursor


@pytest.mark.parametrize('reader', ['recovery', 'coverage', 'mirrors'])
def test_history_pages_are_complete_and_insertion_safe(reader):
    db = History()
    handler = mod.JournalSyncHandler()
    if reader == 'recovery':
        rows = handler._load_existing_from_journal(db)['trades']
        assert [r['ib_exec_id'] for r in rows] == [r[0] for r in db.db.execute('SELECT trade_id FROM journal ORDER BY COALESCE(filled_at,written_at),written_at,trade_id')]
    elif reader == 'coverage':
        assert handler._journal_exec_ids_from_db(db) == {r[0] for r in db.db.execute('SELECT trade_id FROM journal')}
    else:
        assert len(handler._fill_monitor_mirror_rows(db)) == 405
    assert len(db.calls) >= 3


@pytest.mark.parametrize('reader', ['recovery', 'coverage', 'mirrors'])
def test_failed_later_page_exposes_no_partial_history(reader):
    db = History(fail_page=2)
    handler = mod.JournalSyncHandler()
    if reader == 'recovery':
        with pytest.raises(RuntimeError, match='later-page timeout'):
            handler._load_existing_from_journal(db)
    elif reader == 'coverage':
        assert handler._journal_exec_ids_from_db(db) == set()
    else:
        assert handler._fill_monitor_mirror_rows(db) == []
    assert len(db.calls) == 2


def test_journal_reader_uses_bounded_http_not_native_client(monkeypatch):
    # No real environment, credentials, DNS, sockets or IB calls.
    monkeypatch.setattr(http_db, 'read_env', lambda: ('libsql://journal-test.turso.io', 'fake-test-token'))
    monkeypatch.setattr(http_db, '_refuse_pytest_pollution', lambda: None)
    transport = Mock()
    transport.open.side_effect = TimeoutError('injected HTTP timeout')
    monkeypatch.setattr(http_db.urllib.request, 'build_opener', lambda *args: transport)
    assert mod.get_db is http_db.Connection
    db = mod.JournalSyncHandler._open_db()
    with pytest.raises(RuntimeError, match='injected HTTP timeout'):
        mod.JournalSyncHandler()._load_existing_from_journal(db)
    assert transport.open.call_count == 1
    assert transport.open.call_args.kwargs['timeout'] == http_db.REQUEST_TIMEOUT


def test_expired_scan_discards_even_a_successful_page(monkeypatch):
    clock = iter([0.0, 0.0, 30.0])
    monkeypatch.setattr(mod.time, 'monotonic', lambda: next(clock))
    db = History()
    with pytest.raises(RuntimeError, match='scan deadline exceeded'):
        mod.JournalSyncHandler()._load_existing_from_journal(db)
    assert len(db.calls) == 1


@pytest.mark.parametrize('cursor', [0, 'invalid'])
def test_nonadvancing_cursor_refuses_instead_of_looping(cursor):
    db = Mock()
    db.execute.return_value.fetchall.return_value = [('exec', cursor)] * 200
    assert mod.JournalSyncHandler._journal_exec_ids_from_db(db) == set()
    assert db.execute.call_count == 1
