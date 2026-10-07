"""R-028 / REL-316: open broker sockets cannot hide feed or storage failures."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from scripts.api import server


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['stale', 'farm-down', 'database-down'])
async def test_lite_reports_dependency_failure_while_socket_is_open(monkeypatch, state):
    expected = {'database': 'down' if state == 'database-down' else 'up',
                'market_data': 'unknown' if state == 'database-down' else 'degraded'}
    from api import health_dependencies as observer
    now = datetime.now(timezone.utc)
    detail = {'last_tick_at': (now - timedelta(seconds=301)).isoformat(),
              'subscribed_symbols': 1, 'active_subscriptions': 1}
    def query(*args, **kwargs):
        if state == 'database-down':
            raise observer.db_http.DbHttpError('injected storage outage')
        return [('error' if state == 'farm-down' else 'ok', now.isoformat(), json.dumps(detail))]
    async def gateway(**kwargs):
        return {'auth_state': 'authenticated', 'port_listening': True, 'operator_hold': {'held': False}}
    monkeypatch.setattr(server, 'check_ib_gateway', gateway)
    monkeypatch.setattr(server, 'ib_pool', None)
    monkeypatch.setattr(observer.db_http, 'hrana_execute', query)
    monkeypatch.setattr(observer, 'is_market_open_et', lambda: True)
    monkeypatch.setattr(server, 'health_dependencies', observer.health_dependencies)
    result = await server.health_lite()
    assert result['auth_state'] == 'authenticated'
    assert result['port_listening'] is True
    assert result.get('dependencies') == expected


@pytest.mark.asyncio
async def test_trusted_health_reports_storage_failure_but_public_gets_only_liveness(monkeypatch):
    seen = []
    async def dependencies():
        seen.append('read')
        return {'database': 'down', 'market_data': 'unknown'}
    async def gateway(**kwargs): return {'auth_state': 'authenticated'}
    monkeypatch.setattr(server, 'health_dependencies', dependencies, raising=False)
    monkeypatch.setattr(server, 'check_ib_gateway', gateway)
    monkeypatch.setattr(server, 'ib_pool', None)
    def request(headers):
        return SimpleNamespace(client=SimpleNamespace(host='127.0.0.1'), headers=headers,
                               url=SimpleNamespace(path='/health'))
    assert await server.health(request({'x-forwarded-for': '203.0.113.1'})) == {'status': 'ok'}
    assert not seen
    result = await server.health(request({}))
    assert result.get('dependencies') == {'database': 'down', 'market_data': 'unknown'}
    assert seen == ['read']


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['old-tick', 'future-tick', 'malformed-row'])
async def test_actual_dependency_observer_rejects_false_feed_evidence(monkeypatch, fault):
    from api import health_dependencies as observer
    now = datetime.now(timezone.utc)
    detail = {'last_tick_at': now.isoformat(), 'subscribed_symbols': 1, 'active_subscriptions': 1}
    if fault == 'old-tick': detail['last_tick_at'] = (now - timedelta(seconds=301)).isoformat()
    if fault == 'future-tick': detail['last_tick_at'] = (now + timedelta(seconds=10)).isoformat()
    rows = [('ok', now.isoformat(), json.dumps(detail))]
    if fault == 'malformed-row': rows = [('ok',)]
    calls = []
    def query(sql, args, **kwargs):
        calls.append((sql, args, kwargs))
        return rows
    monkeypatch.setattr(observer.db_http, 'hrana_execute', query)
    monkeypatch.setattr(observer, 'is_market_open_et', lambda: True)
    result = await observer.health_dependencies()
    assert result == {'database': 'up', 'market_data': 'degraded' if fault == 'old-tick' else 'unknown'}
    assert len(calls) == 1
    assert calls[0][1:] == (('ib-realtime-relay',), {'timeout': 0.25})
    assert 'LIMIT 1' in calls[0][0]


@pytest.mark.asyncio
@pytest.mark.parametrize('state,age,detail,open_,expected', [
    ('error', 0, {}, True, 'degraded'),
    ('ok', 301, {}, True, 'degraded'),
    ('ok', 0, {'reason': 'farm_down'}, True, 'degraded'),
    ('ok', 0, {'subscribed_symbols': 0}, True, 'idle'),
    ('ok', 0, {}, True, 'unknown'),
    ('syncing', 0, {}, True, 'unknown'),
    ('ok', 3600, {}, False, 'idle'),
    ('ok', 86401, {}, False, 'degraded'),
])
async def test_dependency_rows_preserve_stale_fault_idle_and_offhours_semantics(
    monkeypatch, state, age, detail, open_, expected,
):
    from api import health_dependencies as observer
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()
    monkeypatch.setattr(observer.db_http, 'hrana_execute', lambda *a, **kw: [(state, stamp, json.dumps(detail))])
    monkeypatch.setattr(observer, 'is_market_open_et', lambda: open_)
    assert await observer.health_dependencies() == {'database': 'up', 'market_data': expected}


@pytest.mark.asyncio
async def test_fresh_real_row_recovers_and_exposes_only_coarse_flags(monkeypatch):
    from api import health_dependencies as observer
    now = datetime.now(timezone.utc).isoformat()
    detail = {'last_tick_at': now, 'subscribed_symbols': 1, 'active_subscriptions': 1,
              'account': 'U-PRIVATE', 'message': 'private provider diagnosis'}
    monkeypatch.setattr(observer.db_http, 'hrana_execute', lambda *a, **kw: [('ok', now, json.dumps(detail))])
    monkeypatch.setattr(observer, 'is_market_open_et', lambda: True)
    monkeypatch.setattr(server, 'health_dependencies', observer.health_dependencies)
    async def gateway(**kwargs): return {'auth_state': 'authenticated'}
    monkeypatch.setattr(server, 'check_ib_gateway', gateway)
    monkeypatch.setattr(server, 'ib_pool', None)
    result = await server.health_lite()
    assert result['dependencies'] == {'database': 'up', 'market_data': 'up'}
    assert 'PRIVATE' not in repr(result)
    assert 'provider diagnosis' not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['outage', 'stall'])
async def test_database_failure_is_bounded_and_not_reported_as_healthy(monkeypatch, fault):
    import asyncio
    import time
    from api import health_dependencies as observer
    calls = []
    def query(*a, **kwargs):
        calls.append(kwargs)
        if fault == 'stall':
            time.sleep(0.1)
            return []
        raise observer.db_http.DbHttpError('injected outage, private diagnostics')
    monkeypatch.setattr(observer.db_http, 'hrana_execute', query)
    monkeypatch.setattr(observer, 'DEPENDENCY_DEADLINE_S', 0.02)
    result = await asyncio.wait_for(observer.health_dependencies(), timeout=0.08)
    assert result == {'database': 'down', 'market_data': 'unknown'}
    assert calls == [{'timeout': 0.25}]


@pytest.mark.asyncio
async def test_passive_probes_run_concurrently_inside_existing_health_budget(monkeypatch):
    import asyncio
    both = asyncio.Event()
    starts = []
    async def started(name):
        starts.append(name)
        if len(starts) == 2: both.set()
        await both.wait()
    async def gateway(**kwargs):
        assert kwargs['pool'] is None
        await started('gateway')
        return {'auth_state': 'authenticated'}
    async def dependencies():
        await started('dependencies')
        return {'database': 'up', 'market_data': 'up'}
    monkeypatch.setattr(server, 'check_ib_gateway', gateway)
    monkeypatch.setattr(server, 'health_dependencies', dependencies)
    monkeypatch.setattr(server, 'ib_pool', None)
    result = await asyncio.wait_for(server.health_lite(), timeout=0.5)
    assert result['dependencies'] == {'database': 'up', 'market_data': 'up'}
    assert sorted(starts) == ['dependencies', 'gateway']


@pytest.mark.asyncio
@pytest.mark.parametrize('rows', [
    [],
    [None],
    [('ok', None, None)],
    [('ok', 'bad-date', None)],
    [('ok', '9999-12-31T00:00:00+00:00', None)],
    [('ok', '2026-10-06T00:00:00', None)],
])
async def test_absent_or_malformed_relay_evidence_remains_unknown(monkeypatch, rows):
    from api import health_dependencies as observer
    monkeypatch.setattr(observer.db_http, 'hrana_execute', lambda *a, **kw: rows)
    monkeypatch.setattr(observer, 'is_market_open_et', lambda: True)
    assert await observer.health_dependencies() == {'database': 'up', 'market_data': 'unknown'}


@pytest.mark.asyncio
@pytest.mark.parametrize('raw', ['not-json', '[]', '{"last_tick_at":"bad-date"}', '{"last_tick_at":"2026-10-06T00:00:00"}'])
async def test_unparseable_tick_evidence_is_not_fabricated_as_fresh(monkeypatch, raw):
    from api import health_dependencies as observer
    now = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(observer.db_http, 'hrana_execute', lambda *a, **kw: [('ok', now, raw)])
    monkeypatch.setattr(observer, 'is_market_open_et', lambda: True)
    assert await observer.health_dependencies() == {'database': 'up', 'market_data': 'unknown'}
