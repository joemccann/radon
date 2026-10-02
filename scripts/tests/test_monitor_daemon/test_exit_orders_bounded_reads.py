"""REL-108 / NF-2: exit-order history scans cannot pin the native GIL."""
import json
import sqlite3
from unittest.mock import Mock

import pytest
from knowledge import http_db
from monitor_daemon.handlers import exit_orders as mod


class History:
    def __init__(self, fail_page=None):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE journal(trade_id TEXT PRIMARY KEY, payload TEXT, filled_at TEXT, written_at TEXT)')
        self.pages = 0
        self.writes = 0
        self.fail_page = fail_page
        for i in range(405):
            self.add(f'z-trade-{i}', i)

    def add(self, identity, number):
        payload = {'id': number, 'ticker': 'TEST', 'structure': 'Long Call', 'expiry': '20261016',
                   'contracts': 1, 'strike': 100, 'direction': 'long',
                   'exit_orders': {'target': {'status': 'PENDING', 'price': 2}}}
        self.db.execute('INSERT INTO journal VALUES (?, ?, ?, ?)',
                        (identity, json.dumps(payload), f'2026-10-01T{number:06d}', f'2026-10-01T{number:06d}'))

    def execute(self, sql, args=()):
        if sql.lstrip().upper().startswith('UPDATE'):
            self.writes += 1
        elif 'WHERE trade_id = ?' not in sql:
            assert 'LIMIT' in sql.upper(), 'unbounded journal history request'
            self.pages += 1
            if self.pages == self.fail_page:
                raise TimeoutError('injected later-page outage')
        rows = self.db.execute(sql, args).fetchall()
        if self.pages == 1:
            self.db.execute('INSERT OR IGNORE INTO journal VALUES (?, ?, ?, ?)',
                            ('0-concurrent', json.dumps({'id': 999, 'ticker': 'LATE', 'contracts': 1,
                             'exit_orders': {'target': {'status': 'PENDING', 'price': 3}}}),
                             '2026-10-02', '2026-10-02'))
        return Mock(fetchall=Mock(return_value=rows))

    def commit(self):
        self.db.commit()


def test_pending_scan_includes_concurrent_insert_and_preserves_priority():
    db = History()
    orders = mod.ExitOrdersHandler(db=db)._load_pending_orders()
    assert len(orders) == 406
    assert orders[0]['ticker'] == 'LATE'
    assert db.pages == 3


def test_failed_pending_page_exposes_no_partial_exit_work():
    db = History(fail_page=2)
    with pytest.raises(RuntimeError, match='later-page outage'):
        mod.ExitOrdersHandler(db=db)._load_pending_orders()
    assert db.pages == 2


@pytest.mark.parametrize('fail_page,expected', [(None, True), (2, False)])
def test_legacy_update_search_is_bounded_and_failure_writes_nothing(fail_page, expected):
    db = History(fail_page=fail_page)
    assert mod.ExitOrdersHandler(db=db)._update_journal_trade(404, 'target', 123) is expected
    assert db.writes == int(expected)
    assert db.pages == (3 if expected else 2)


def test_exit_reads_use_bounded_http_without_native_connection(monkeypatch):
    monkeypatch.setattr(http_db, 'read_env', lambda: ('libsql://fixture.turso.io', 'fixture-token'))
    monkeypatch.setattr(http_db, '_refuse_pytest_pollution', lambda: None)
    transport = Mock()
    transport.open.side_effect = TimeoutError('injected HTTP timeout')
    monkeypatch.setattr(http_db.urllib.request, 'build_opener', lambda *args: transport)
    assert mod.get_db is http_db.Connection
    with pytest.raises(RuntimeError, match='injected HTTP timeout'):
        mod.ExitOrdersHandler()._load_pending_orders()
    assert transport.open.call_count == 1
    assert transport.open.call_args.kwargs['timeout'] == http_db.REQUEST_TIMEOUT


def test_http_autocommit_update_is_verified_without_an_inactive_commit(monkeypatch):
    history = History()
    monkeypatch.setattr(http_db, 'read_env', lambda: ('libsql://fixture.turso.io', 'fixture-token'))
    monkeypatch.setattr(http_db, '_refuse_pytest_pollution', lambda: None)
    verbs = []
    def request(_self, requests, **kwargs):
        stmt = requests[0]['stmt']
        verbs.append(stmt['sql'].strip().split()[0])
        args = [arg.get('value') for arg in stmt['args']]
        cursor = history.db.execute(stmt['sql'], args)
        result = {'cols': [{'name': col[0]} for col in cursor.description or []],
                  'rows': [[{'type': 'null'} if value is None else
                            {'type': 'integer' if isinstance(value, int) else 'text', 'value': str(value)}
                            for value in row] for row in cursor.fetchall()]}
        return [{'type': 'ok', 'response': {'type': 'execute', 'result': result}},
                {'type': 'ok', 'response': {'type': 'close'}}]
    monkeypatch.setattr(http_db.Connection, '_request', request)
    assert mod.ExitOrdersHandler()._update_journal_trade(404, 'target', 123, 'z-trade-404') is True
    assert verbs == ['SELECT', 'UPDATE', 'SELECT']


@pytest.mark.parametrize('cursor', [0, 'invalid'])
def test_nonadvancing_cursor_refuses_instead_of_spinning(cursor):
    db = Mock()
    db.execute.return_value.fetchall.return_value = [('trade', '{}', cursor, '')] * 200
    with pytest.raises(RuntimeError, match='cursor did not advance'):
        mod.ExitOrdersHandler(db=db)._load_pending_orders()
    assert db.execute.call_count == 1


def test_scan_deadline_discards_a_successful_late_page(monkeypatch):
    clock = iter([0, 0, 30])
    monkeypatch.setattr(mod.time, 'monotonic', lambda: next(clock))
    with pytest.raises(RuntimeError, match='scan deadline exceeded'):
        mod.ExitOrdersHandler(db=History())._load_pending_orders()
