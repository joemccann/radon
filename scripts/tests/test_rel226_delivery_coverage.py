"""An applied claim alone cannot prove that its financial rows still exist."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import flex_delivery_ingest as ingest
from cash_flow_sync import parse_cash_transactions
from db import hrana_http

XML = (Path(__file__).parent / 'fixtures/cash_transactions_flex_ytd_detail_sample.xml').read_text()


@pytest.mark.parametrize('missing', [True, False])
def test_duplicate_verifies_cash_rows_without_reapplying(monkeypatch, missing):
    expected = parse_cash_transactions(XML)
    rows = [(r['id'], r['date'], r['amount'], r['currency']) for r in expected]
    if missing:
        rows.pop()
    monkeypatch.setattr(ingest, 'claim_flex_delivery', lambda *a, **k: False)
    monkeypatch.setattr(ingest, 'flex_delivery_status', lambda d: 'applied')
    from perf_twr_builder import parse_nav_entries
    monkeypatch.setattr(hrana_http, 'hrana_query', lambda sql, *a, **k:
                        [(d,) for d in parse_nav_entries(XML)] if 'nav_snapshots' in sql else rows)
    monkeypatch.setattr(ingest, '_apply_classified', lambda *a: pytest.fail('duplicate reapplied'))
    health = []
    monkeypatch.setattr(ingest, '_heartbeat_cash_flow_sync', lambda state, *a: health.append(state))
    result = ingest.ingest_xml(XML)
    assert result['ok'] is (not missing)
    assert result.get('persistence_confirmed') is (not missing)
    assert health == ['error' if missing else 'ok']


def test_missing_claim_is_not_a_confirmed_duplicate(monkeypatch):
    monkeypatch.setattr(ingest, 'claim_flex_delivery', lambda *a, **k: False)
    monkeypatch.setattr(ingest, 'flex_delivery_status', lambda d: None)
    monkeypatch.setattr(ingest, '_heartbeat_cash_flow_sync', lambda *a: None)
    assert ingest.ingest_xml(XML)['ok'] is False


def test_later_cash_corrections_do_not_invalidate_existing_row_coverage(monkeypatch):
    expected = parse_cash_transactions(XML)
    rows = [(r['id'], r['date'], r['amount'] + 1, r['currency']) for r in expected]
    from perf_twr_builder import parse_nav_entries
    monkeypatch.setattr(hrana_http, 'hrana_query', lambda sql, *a, **k:
                        [(d,) for d in parse_nav_entries(XML)] if 'nav_snapshots' in sql else rows)
    assert ingest.delivery_rows_present(ingest.ACTIVITY, XML) is True


@pytest.mark.parametrize('ids,expected', [('e1+e2', True), ('e1', False)])
def test_trade_duplicate_requires_all_executions(monkeypatch, ids, expected):
    import json
    from types import SimpleNamespace
    from trade_blotter.flex_query import FlexQueryFetcher
    monkeypatch.setattr(FlexQueryFetcher, 'parse_xml_with_drops',
                        lambda *a: ([SimpleNamespace(exec_id='e1'), SimpleNamespace(exec_id='e2')], 0))
    pages = iter([[('t1', json.dumps({'ib_exec_id': ids}))], []])
    monkeypatch.setattr(hrana_http, 'hrana_query', lambda *a, **k: next(pages))
    assert ingest.delivery_rows_present(ingest.TRADES, '<unused/>') is expected


def test_read_failure_never_replays_or_reports_duplicate_success(monkeypatch):
    monkeypatch.setattr(ingest, 'claim_flex_delivery', lambda *a, **k: False)
    monkeypatch.setattr(ingest, 'flex_delivery_status', lambda d: 'applied')
    monkeypatch.setattr(ingest, '_heartbeat_cash_flow_sync', lambda *a: None)
    def unavailable(*a, **k):
        raise hrana_http.HranaHttpError('offline')
    monkeypatch.setattr(hrana_http, 'hrana_query', unavailable)
    monkeypatch.setattr(ingest, '_apply_classified', lambda *a: pytest.fail('duplicate reapplied'))
    assert ingest.ingest_xml(XML)['persistence_confirmed'] is False


def test_nav_only_duplicate_requires_persisted_nav(monkeypatch):
    xml = '<FlexQueryResponse><FlexStatement><EquitySummaryByReportDateInBase reportDate="20260917" total="1000"/></FlexStatement></FlexQueryResponse>'
    monkeypatch.setattr(hrana_http, 'hrana_query', lambda *a, **k: [])
    assert ingest.delivery_rows_present(ingest.ACTIVITY, xml) is False
