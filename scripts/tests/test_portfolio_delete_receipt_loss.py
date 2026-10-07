"""R-032 / REL-315: retry only the same deletion after a lost commit receipt.

Real disposable SQLite executes production SQL; injected receipt loss follows
its commit. All Hrana entrypoints are replaced, so no cloud I/O is possible.
"""
import sqlite3

import pytest

from db import hrana_http, writer


@pytest.mark.parametrize('lost_receipts', [1, 2])
def test_lost_batch_receipt_replays_same_page_and_keeps_exact_accounting(monkeypatch, lost_receipts):
    with sqlite3.connect(':memory:', isolation_level=None) as db:
        db.execute('CREATE TABLE portfolio_snapshots (taken_at TEXT PRIMARY KEY, payload TEXT)')
        old = [f'2026-09-{i:02}T00:00:00Z' for i in range(1, 8)]
        retained = '2026-10-01T00:00:00Z'
        db.executemany('INSERT INTO portfolio_snapshots VALUES (?, ?)', [(k, '{}') for k in [*old, retained]])
        batch_effects = []
        attempts = []
        def query(sql, args=(), **kwargs):
            assert kwargs['timeout'] == writer._DELETE_HTTP_TIMEOUT_S
            return db.execute(sql, args).fetchall()
        def execute(sql, args=(), **kwargs):
            assert kwargs['timeout'] == writer._DELETE_HTTP_TIMEOUT_S
            attempts.append((sql, tuple(args)))
            db.execute(sql, args)
            if ' IN (' in sql:
                batch_effects.append(db.execute('SELECT taken_at FROM portfolio_snapshots ORDER BY taken_at').fetchall())
                if len(batch_effects) <= lost_receipts:
                    raise hrana_http.HranaHttpError('TimeoutError: injected lost commit receipt')
            return []
        monkeypatch.setattr(hrana_http, 'hrana_query', query)
        monkeypatch.setattr(hrana_http, 'hrana_execute', execute)
        monkeypatch.setattr('time.sleep', lambda *a: None)
        monkeypatch.setattr(writer, 'get_db', lambda: pytest.fail('native DB opened during bounded deletion'))
        count = writer.delete_portfolio_snapshots_before('2026-10-01T00:00:00Z', batch_size=3)
        assert count == len(old), f'{count} reported for {len(old)} deleted snapshots'
        assert batch_effects[0] == batch_effects[1], 'retry deleted the following page after the first commit'
        assert attempts[0] == attempts[1], 'receipt loss changed the retry identity'
        assert db.execute('SELECT taken_at FROM portfolio_snapshots').fetchall() == [(retained,)]
        assert writer.delete_portfolio_snapshots_before('2026-10-01T00:00:00Z', batch_size=3) == 0


@pytest.mark.parametrize('transport', ['daemon', 'api-execute', 'api-transaction'])
def test_ambiguous_transport_surfaces_once_without_implicit_replay(monkeypatch, transport):
    """Other Hrana entrypoints never turn a lost receipt into a second write."""
    import urllib.request
    from api import db_http
    calls = []
    def lost_receipt(request, **kwargs):
        calls.append((request, kwargs))
        raise TimeoutError('injected commit receipt loss')
    monkeypatch.setattr(urllib.request, 'urlopen', lost_receipt)
    monkeypatch.setattr(hrana_http, 'read_env', lambda: ('libsql://fake.invalid', 'test-only'))
    monkeypatch.setattr(db_http, 'read_env', lambda: ('libsql://fake.invalid', 'test-only'))
    monkeypatch.setenv('RADON_DB_TEST_WRITE_OK', '1')
    sql = 'INSERT INTO audit_events (id) VALUES (?)'
    if transport == 'daemon':
        operation = lambda: hrana_http.hrana_execute(sql, ('fixed-id',))
        error = hrana_http.HranaHttpError
    elif transport == 'api-execute':
        operation = lambda: db_http.hrana_execute(sql, ('fixed-id',))
        error = db_http.DbHttpError
    else:
        operation = lambda: db_http.hrana_transaction([(sql, ('fixed-id',))])
        error = db_http.DbHttpError
    with pytest.raises(error):
        operation()
    assert len(calls) == 1
    assert calls[0][1]['timeout'] == 4
