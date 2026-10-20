"""Official monthly export composite: source units, calendar maths and durability."""
import copy
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

import fetch_asia_export_lead as indicator

FIXTURES = Path(__file__).parent / 'fixtures'
TW = (FIXTURES / 'asia_export_lead_taiwan_sample.csv').read_bytes()
KR = (FIXTURES / 'asia_export_lead_korea_sample.json').read_bytes()
MIGRATION = Path(__file__).parents[1] / 'db/migrations/0099_asia_export_lead.sql'


def payload():
    return indicator.build_output(indicator.parse_taiwan_csv(TW), indicator.parse_korea_json(KR))


def test_captured_official_fields_dates_and_units():
    tw, kr = indicator.parse_taiwan_csv(TW), indicator.parse_korea_json(KR)
    assert len(tw) == 309 and len(kr) == 321
    assert tw[0] == {'date': '2001-01', 'exports_usd_mn': 10064.663}
    assert kr[0] == {'date': '2000-01', 'exports_usd_mn': 12162.425}
    assert tw[-1] == {'date': '2026-09', 'exports_usd_mn': 87222.162}
    assert kr[-1] == {'date': '2026-09', 'exports_usd_mn': 121050.082}


def test_same_vintage_composite_and_exact_calendar_shift():
    result = payload()
    rows = result['series']
    current = result['current']
    assert current['date'] == '2026-09'
    assert current['lead_date'] == '2027-03'
    assert current['taiwan_yoy_pct'] == pytest.approx(60.86788525833797)
    assert current['korea_yoy_pct'] == pytest.approx(83.67605192743797)
    assert current['mean_yoy_pct'] == pytest.approx(72.27196859288797)
    assert current['ma3_yoy_pct'] == pytest.approx(58.43498029405504)
    assert next(r['date'] for r in rows if r['mean_yoy_pct'] is not None) == '2002-01'
    assert next(r['date'] for r in rows if r['ma3_yoy_pct'] is not None) == '2002-03'
    assert result['data_date'] == '2026-09-30'
    assert result['source_last_modified'] is None
    assert result['comparison']['available'] is False
    assert datetime.fromisoformat(result['scan_time']).utcoffset().total_seconds() == 0
    assert math.isfinite(current['ma3_change_pp'])


def synthetic():
    dates = [f'{year}-{month:02d}' for year in (2024, 2025) for month in range(1, 13)]
    return [{'date': date, 'exports_usd_mn': 100 if date.startswith('2024') else 120} for date in dates]


def test_equal_country_weighting_instead_of_dollar_weighting():
    tw = synthetic()
    kr = [{**r, 'exports_usd_mn': 1000 if r['date'].startswith('2024') else 1400} for r in tw]
    rows = indicator.compute_series(tw, kr)
    assert rows[-1]['mean_yoy_pct'] == pytest.approx(30)
    assert rows[-1]['ma3_yoy_pct'] == pytest.approx(30)
    assert rows[-1]['lead_date'] == '2026-06'


def test_missing_month_breaks_calendar_smoothing_and_yoy():
    tw = [r for r in synthetic() if r['date'] not in ('2025-02', '2024-05')]
    rows = {r['date']: r for r in indicator.compute_series(tw, synthetic())}
    assert rows['2025-02']['mean_yoy_pct'] is None
    assert rows['2025-03']['ma3_yoy_pct'] is None
    assert rows['2025-04']['ma3_yoy_pct'] is None
    assert rows['2025-05']['taiwan_yoy_pct'] is None
    assert rows['2025-05']['mean_yoy_pct'] is None
    assert rows['2025-08']['ma3_yoy_pct'] == pytest.approx(20)


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-1', 'not a number'])
def test_corrupt_monthly_numbers_fail_closed(value):
    raw = json.loads(KR)
    raw['items'][1]['expUsdAmt'] = value
    with pytest.raises(ValueError):
        indicator.parse_korea_json(json.dumps(raw).encode())


def test_korea_truncated_pagination_is_rejected():
    raw = json.loads(KR)
    raw['items'] = raw['items'][:3]
    with pytest.raises(ValueError):
        indicator.parse_korea_json(json.dumps(raw).encode())


def test_conflicting_duplicate_month_is_rejected():
    raw = json.loads(KR)
    row = copy.deepcopy(raw['items'][1])
    row['expUsdAmt'] = '999'
    raw['items'].append(row)
    raw['count'] += 1
    with pytest.raises(ValueError):
        indicator.parse_korea_json(json.dumps(raw).encode())


def test_zero_denominator_does_not_create_infinite_growth():
    tw = synthetic()
    tw[0]['exports_usd_mn'] = 0
    rows = {r['date']: r for r in indicator.compute_series(tw, synthetic())}
    assert rows['2025-01']['taiwan_yoy_pct'] is None
    assert rows['2025-01']['mean_yoy_pct'] is None


def test_persistence_skips_unchanged_rows_but_always_refreshes_snapshot_and_health(monkeypatch, tmp_path):
    from db import writer
    calls = []
    monkeypatch.setattr(writer, 'ensure_no_replica_for_writers', lambda: calls.append('direct'))
    monkeypatch.setattr(writer, 'upsert_asia_export_lead_rows', lambda rows, recorded_at=None: calls.append('rows'))
    monkeypatch.setattr(writer, 'upsert_scan_snapshot', lambda service, stamp, body: calls.append(('snapshot', service)))
    monkeypatch.setattr(writer, 'record_service_health', lambda service, state, **kw: calls.append(('health', service, state)))
    value = payload()
    indicator.persist_result(value, path=tmp_path/'cache.json')
    assert calls == ['direct', 'rows', ('snapshot', 'asia-export-lead'), ('health', 'asia-export-lead', 'ok')]
    calls.clear()
    indicator.persist_result(value, previous=copy.deepcopy(value), path=tmp_path/'cache.json')
    assert calls == ['direct', ('snapshot', 'asia-export-lead'), ('health', 'asia-export-lead', 'ok')]
    assert json.loads((tmp_path/'cache.json').read_text())['current'] == value['current']


def test_empty_result_cannot_replace_last_good_cache(monkeypatch, tmp_path):
    path = tmp_path/'cache.json'
    path.write_text('last-good')
    monkeypatch.setattr(indicator, '_write_db_cache', lambda *a, **kw: pytest.fail('empty DB write'))
    with pytest.raises(ValueError):
        indicator.persist_result({'series': [], 'current': None}, path=path)
    assert path.read_text() == 'last-good'


def test_taiwan_304_reuses_levels_and_refreshes_sample_clock(monkeypatch, tmp_path):
    old = payload()
    old['sources']['taiwan']['last_modified'] = 'Tue, 01 Sep 2026 00:00:00 GMT'
    class Client:
        def fetch_taiwan(self, if_modified_since=None):
            assert if_modified_since == old['sources']['taiwan']['last_modified']
            return None, if_modified_since
        def fetch_korea(self, end_month):
            assert end_month == '202609'
            return KR
    writes = []
    monkeypatch.setattr(indicator, '_write_db_cache', lambda value, stamp, rows_changed: writes.append(rows_changed))
    now = datetime(2026, 10, 19, 9, 40, tzinfo=timezone.utc)
    value = indicator.refresh(Client(), previous=old, now=now, path=tmp_path/'cache.json')
    assert value['series'] == old['series']
    assert value['scan_time'] == now.isoformat()
    assert writes == [False]


def test_304_without_cached_levels_is_an_error(monkeypatch, tmp_path):
    class Client:
        def fetch_taiwan(self, if_modified_since=None): return None, None
        def fetch_korea(self, end_month): return KR
    monkeypatch.setattr(indicator, '_write_db_cache', lambda *a, **kw: pytest.fail('invalid cache write'))
    with pytest.raises(ValueError):
        indicator.refresh(Client(), path=tmp_path/'cache.json')


def test_migration_and_writer_execute_real_schema_with_idempotent_upsert(monkeypatch):
    from db import writer
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)')
    conn.executescript(MIGRATION.read_text())
    monkeypatch.setattr(writer, 'get_db', lambda: conn)
    row = payload()['series'][-1]
    writer.upsert_asia_export_lead_rows([row], recorded_at='2026-10-19T00:00:00Z')
    row['ma3_yoy_pct'] = 42.5
    writer.upsert_asia_export_lead_rows([row], recorded_at='2026-10-19T01:00:00Z')
    assert conn.execute('SELECT COUNT(*) FROM asia_export_lead_history').fetchone()[0] == 1
    assert conn.execute('SELECT ma3_yoy_pct FROM asia_export_lead_history').fetchone()[0] == 42.5
    assert conn.execute('SELECT version FROM schema_migrations').fetchone()[0] == 99


@pytest.mark.parametrize('raw', [b'<html>blocked</html>', b'', b'{}', b'[]', b'{"items":[]}'])
def test_malformed_korea_payload_is_never_an_empty_success(raw):
    with pytest.raises((ValueError, TypeError)):
        indicator.parse_korea_json(raw)


@pytest.mark.parametrize('raw', [b'', b'HTML', '幣別,按美元計算/ 出口總值\n115年 13月,100'.encode(),
                               '幣別,按美元計算/ 出口總值\n115年 1月'.encode(),
                               '幣別,按美元計算/ 出口總值\n115年 1月,NaN'.encode()])
def test_malformed_taiwan_month_or_number_fails_closed(raw):
    with pytest.raises(ValueError):
        indicator.parse_taiwan_csv(raw)


def test_duplicate_identical_month_and_annual_rows_do_not_change_monthly_values():
    raw = '幣別,按美元計算/ 出口總值\n115年,999\n115年 1月,100\n115年 1月,100\n'
    assert indicator.parse_taiwan_csv(raw.encode()) == [{'date': '2026-01', 'exports_usd_mn': 100}]
    assert len(indicator.compute_series(synthetic(), synthetic())) == 24


def test_partial_month_and_count_type_are_rejected():
    raw = json.loads(KR)
    raw['items'][1]['priodTitle'] = '2026.09.01-10'
    with pytest.raises(ValueError):
        indicator.parse_korea_json(json.dumps(raw).encode())
    with pytest.raises(ValueError):
        indicator.parse_korea_json(b'{"count":1,"items":[null]}')
    with pytest.raises(ValueError):
        indicator.parse_korea_json(b'{"count":1,"items":[{"priodTitle":"2026.01"}]}')
    raw['count'] = True
    with pytest.raises(ValueError):
        indicator.parse_korea_json(json.dumps(raw).encode())


def test_payload_states_nulls_and_timezones():
    rows = synthetic()
    result = indicator.build_output(rows, rows, scan_time='2026-10-19T10:00:00+08:00')
    assert result['scan_time'] == '2026-10-19T02:00:00+00:00'
    assert result['current']['growth'] == 'expanding'
    assert result['current']['momentum'] == 'unchanged'
    assert 'licence_url' in result['sources']['taiwan']
    short = indicator.build_output(rows[:2], rows[:2])
    assert short['current']['growth'] == short['current']['momentum'] == 'unavailable'
    flat = [{**r, 'exports_usd_mn': 100} for r in rows]
    assert indicator.build_output(flat, flat)['current']['growth'] == 'flat'
    falling = [{**r, 'exports_usd_mn': 80 if r['date'].startswith('2025') else 100} for r in rows]
    assert indicator.build_output(falling, falling)['current']['growth'] == 'contracting'
    with pytest.raises(ValueError):
        indicator.build_output(rows, rows, scan_time='2026-10-19T10:00:00')
    with pytest.raises(ValueError):
        indicator.build_output(rows[:1], rows[-1:])
    with pytest.raises(ValueError):
        indicator.compute_series([{'date': '2026-01', 'exports_usd_mn': True}], rows)


def test_country_only_tail_is_preserved_without_advancing_latest_common_month():
    tw = synthetic()[:-2]
    result = indicator.build_output(tw, synthetic())
    assert result['current']['date'] == '2025-10'
    assert result['series'][-1]['date'] == '2025-12'
    assert result['series'][-1]['taiwan_exports_usd_mn'] is None
    assert result['sources']['korea']['latest_month'] == '2025-12'
    assert indicator._cached_levels(result, 'korea') == synthetic()


def test_db_failure_keeps_disk_cache_and_never_records_ok(monkeypatch, tmp_path):
    from db import writer
    path = tmp_path / 'cache.json'
    path.write_text('last-good')
    monkeypatch.setattr(writer, 'ensure_no_replica_for_writers', lambda: None)
    def fail(*a, **kw): raise RuntimeError('database unavailable')
    monkeypatch.setattr(writer, 'upsert_asia_export_lead_rows', fail)
    monkeypatch.setattr(writer, 'record_service_health', lambda *a, **kw: pytest.fail('false ok heartbeat'))
    with pytest.raises(RuntimeError, match='database unavailable'):
        indicator.persist_result(payload(), path=path)
    assert path.read_text() == 'last-good'


def test_unchanged_normalized_history_ignores_metadata_and_preserves_country_only_rows(monkeypatch, tmp_path):
    old = payload()
    old['sources']['taiwan']['last_modified'] = 'old stamp'
    class Client:
        def fetch_taiwan(self, if_modified_since=None): return TW, 'new stamp'
        def fetch_korea(self, end_month): return KR
    writes = []
    monkeypatch.setattr(indicator, '_write_db_cache', lambda value, stamp, rows_changed: writes.append(rows_changed))
    result = indicator.refresh(Client(), previous=old, now=datetime(2026, 10, 19, tzinfo=timezone.utc), path=tmp_path/'cache.json')
    assert writes == [False]
    assert result['series'][0]['date'] == '2000-01'
    assert result['series'][0]['taiwan_exports_usd_mn'] is None
    assert result['sources']['taiwan']['last_modified'] == 'new stamp'


def test_incomplete_month_is_rejected_and_year_boundary_uses_taipei_calendar(monkeypatch, tmp_path):
    months = []
    class Client:
        def fetch_taiwan(self, if_modified_since=None): return TW, None
        def fetch_korea(self, end_month): months.append(end_month); return KR
    monkeypatch.setattr(indicator, '_write_db_cache', lambda *a, **kw: None)
    indicator.refresh(Client(), now=datetime(2026, 12, 31, 18, tzinfo=timezone.utc), path=tmp_path/'cache.json')
    assert months == ['202612']
    with pytest.raises(ValueError, match='incomplete'):
        indicator.refresh(Client(), now=datetime(2026, 9, 1, tzinfo=timezone.utc), path=tmp_path/'cache.json')
    with pytest.raises(ValueError, match='timezone aware'):
        indicator.refresh(Client(), now=datetime(2026, 10, 1), path=tmp_path/'cache.json')


def test_previous_snapshot_prefers_turso_then_valid_disk(monkeypatch, tmp_path):
    from db import hrana_http
    from utils.atomic_io import atomic_save
    value = payload()
    path = tmp_path/'cache.json'
    path.write_text('bad JSON')
    monkeypatch.setattr(hrana_http, 'hrana_query', lambda *a: [(json.dumps(value),)])
    assert indicator._read_previous(path)['current'] == value['current']
    def fail(*a): raise RuntimeError('offline')
    monkeypatch.setattr(hrana_http, 'hrana_query', fail)
    atomic_save(str(path), value)
    assert indicator._read_previous(path)['current'] == value['current']
    path.write_text('bad JSON')
    assert indicator._read_previous(path) is None
    monkeypatch.setattr(hrana_http, 'hrana_query', lambda *a: [])
    assert indicator._read_previous(path) is None


def test_bad_payload_cannot_be_persisted(monkeypatch, tmp_path):
    monkeypatch.setattr(indicator, '_write_db_cache', lambda *a, **kw: pytest.fail('invalid DB write'))
    for mutate in (lambda p: p.update(count=0), lambda p: p['current'].update(ma3_yoy_pct=999),
                   lambda p: p.update(missing=True)):
        value = payload()
        mutate(value)
        with pytest.raises(ValueError):
            indicator.persist_result(value, path=tmp_path/'cache.json')


def test_writer_batches_real_history_and_preserves_country_prefix(monkeypatch):
    from db import writer
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)')
    conn.executescript(MIGRATION.read_text())
    monkeypatch.setattr(writer, 'get_db', lambda: conn)
    monkeypatch.setattr(writer, '_PRICE_HISTORY_INSERT_CHUNK_ROWS', 37)
    writer.upsert_asia_export_lead_rows([])
    writer.upsert_asia_export_lead_rows(payload()['series'])
    assert conn.execute('SELECT COUNT(*) FROM asia_export_lead_history').fetchone()[0] == 321
    assert conn.execute("SELECT taiwan_exports_usd_mn, korea_exports_usd_mn FROM asia_export_lead_history WHERE date='2000-01'").fetchone() == (None, 12162.425)


class FakeResponse:
    def __init__(self, status=200, body=b'payload', headers=None):
        self.status_code, self.body, self.headers = status, body, headers or {}
    def __enter__(self): return self
    def __exit__(self, *a): return None
    def iter_content(self, chunk_size): yield self.body


def test_official_client_transport_conditional_header_and_bounds(monkeypatch):
    from clients import asia_export_client as client_module
    requests = []
    with client_module.AsiaExportClient(max_retries=0) as client:
        def request(method, url, **kw):
            requests.append((method, url, kw))
            return FakeResponse(headers={'Last-Modified': 'stamp'})
        monkeypatch.setattr(client.session, 'request', request)
        assert client.session.headers['User-Agent'] == 'radon/2.0'
        assert client.fetch_taiwan('old') == (b'payload', 'stamp')
        assert requests[0][2]['headers'] == {'If-Modified-Since': 'old'}
        assert client.fetch_korea('202609') == b'payload'
        assert requests[1][0] == 'POST' and requests[1][2]['data']['priodTo'] == '202609'
        assert requests[1][2]['data']['showPagingLine'] == '1000'
        assert requests[1][2]['stream'] is True
        with pytest.raises(ValueError): client.fetch_korea('202613')
        monkeypatch.setattr(client.session, 'request', lambda *a, **kw: FakeResponse(status=304))
        assert client.fetch_taiwan('stamp') == (None, 'stamp')
        with pytest.raises(client_module.AsiaExportClientError): client.fetch_korea('202609')
        for response in [FakeResponse(403), FakeResponse(body=b''), FakeResponse(body=b'x'*(client_module.MAX_RESPONSE_BYTES+1))]:
            monkeypatch.setattr(client.session, 'request', lambda *a, **kw: response)
            with pytest.raises(client_module.AsiaExportClientError): client.fetch_taiwan()


def test_official_client_retries_transient_status_and_connection_failure(monkeypatch):
    from clients import asia_export_client as client_module
    sleeps = []
    monkeypatch.setattr(client_module.time, 'sleep', sleeps.append)
    with client_module.AsiaExportClient(max_retries=1) as client:
        responses = iter([FakeResponse(503), FakeResponse()])
        monkeypatch.setattr(client.session, 'request', lambda *a, **kw: next(responses))
        assert client.fetch_taiwan()[0] == b'payload' and sleeps == [1]
        calls = []
        def fail(*a, **kw):
            calls.append(1)
            raise client_module.requests.Timeout('offline')
        monkeypatch.setattr(client.session, 'request', fail)
        with pytest.raises(client_module.AsiaExportClientError, match='exhausted'): client.fetch_korea('202609')
        assert len(calls) == 2
        monkeypatch.setattr(client.session, 'request', lambda *a, **kw: FakeResponse(503))
        with pytest.raises(client_module.AsiaExportClientError, match='HTTP 503'): client.fetch_taiwan()


def test_cli_stdout_is_json_and_failures_exit_nonzero(monkeypatch, capsys):
    value = payload()
    monkeypatch.setattr(indicator, '_read_previous', lambda: None)
    class Client:
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(indicator, 'AsiaExportClient', Client)
    monkeypatch.setattr(indicator, 'refresh', lambda *a, **kw: value)
    monkeypatch.setattr('sys.argv', ['fetch_asia_export_lead.py', '--json'])
    assert indicator.main() == 0
    assert json.loads(capsys.readouterr().out)['current'] == value['current']
    monkeypatch.setattr('sys.argv', ['fetch_asia_export_lead.py'])
    assert indicator.main() == 0
    output = capsys.readouterr()
    assert output.out == '' and 'Data Freshness:' in output.err
    def fail(*a, **kw): raise RuntimeError('source failed')
    monkeypatch.setattr(indicator, 'refresh', fail)
    assert indicator.main() == 1
    output = capsys.readouterr()
    assert output.out == '' and 'source failed' in output.err
