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
    from datetime import datetime
    from trade_blotter.flex_query import FlexQueryFetcher

    def _exec(exec_id):
        # Not an individual-fill match: the journal row below has no contract.
        from decimal import Decimal
        from trade_blotter.models import Execution, SecurityType, Side
        return Execution(
            exec_id=exec_id,
            time=datetime(2026, 1, 2, 15, 0),
            symbol='ZZ',
            sec_type=SecurityType.OPTION,
            side=Side.BUY,
            quantity=Decimal('1'),
            price=Decimal('1'),
            commission=Decimal('0'),
            strike=Decimal('1'),
            right='C',
            expiry='20260101',
        )

    monkeypatch.setattr(FlexQueryFetcher, 'parse_xml_with_drops',
                        lambda *a: ([_exec('e1'), _exec('e2')], 0))
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


def test_legacy_key_skipped_meta_bucket_verifies_as_duplicate(monkeypatch):
    """A same-day META fill the live path already journaled is skipped by the
    writer on (ticker, date, structure). Its Flex exec id never lands, so an
    exec-id check stays coverage_unverified on every re-sync."""
    import json
    from datetime import datetime
    from decimal import Decimal

    from trade_blotter.flex_query import FlexQueryFetcher
    from trade_blotter.models import Execution, SecurityType, Side

    execution = Execution(
        exec_id='FLEX-META-NEW',
        time=datetime(2026, 9, 24, 10, 15),
        symbol='META',
        sec_type=SecurityType.OPTION,
        side=Side.BUY,
        quantity=Decimal('1'),
        price=Decimal('7.2557'),
        commission=Decimal('0'),
        strike=Decimal('575'),
        right='C',
        expiry='20260918',
    )
    # Label `_bucket_to_entry` emits for this contract. The live row carries
    # a different exec id, so only the legacy key can cover it.
    journal_row = {
        'id': 42,
        'date': '2026-09-24',
        'ticker': 'META',
        'structure': 'Long Call $575 2026-09-18',
        'action': 'BUY_OPTION',
        'ib_exec_id': 'LIVE-PATH-OTHER',
        'contracts': 1,
        'strike': 575.0,
        'right': 'C',
        'expiry': '20260918',
    }
    trades_xml = (Path(__file__).parent / 'fixtures/flex_trade_confirm_sample.xml').read_text()
    monkeypatch.setenv('IB_FLEX_ACCOUNT_ID', 'U0000000')
    monkeypatch.setattr(FlexQueryFetcher, 'parse_xml_with_drops', lambda *a: ([execution], 0))
    monkeypatch.setattr(
        hrana_http, 'hrana_query',
        lambda *a, **k: [('t-live', json.dumps(journal_row))],
    )
    monkeypatch.setattr(ingest, 'claim_flex_delivery', lambda *a, **k: False)
    monkeypatch.setattr(ingest, 'flex_delivery_status', lambda digest: 'applied')
    monkeypatch.setattr(ingest, '_apply_classified', lambda *a: pytest.fail('duplicate reapplied'))

    result = ingest.ingest_xml(trades_xml)

    assert result['outcome'] == 'duplicate'
    assert result['ok'] is True
    assert result['persistence_confirmed'] is True


# 2026-09-22 page d3b66eaf: the 08:30 retry failed applied Equity_Summary
# duplicates. Cash had landed. A suppressed TWR persist writes no series, so
# the statement NAV dates never reached nav_snapshots. Coverage then failed
# the oneshot. The retry must insert those missing dates and must not replay
# the cash writer. An existing date is left alone (DO NOTHING, not DO UPDATE).
ACTIVITY_NAV_XML = (
    '<FlexQueryResponse><FlexStatements>'
    '<FlexStatement accountId="U1" fromDate="20260921" toDate="20260921">'
    '<EquitySummaryByReportDateInBase accountId="U1" reportDate="20260918" total="1000"/>'
    '<EquitySummaryByReportDateInBase accountId="U1" reportDate="20260921" total="1100"/>'
    '<CashTransactions/>'
    '<Transfers/>'
    '</FlexStatement></FlexStatements></FlexQueryResponse>'
)


def test_applied_activity_duplicate_inserts_missing_nav_without_reapply(monkeypatch):
    stored = {'2026-09-21'}
    inserted = []

    def query(sql, args=(), **_k):
        if 'nav_snapshots' in sql:
            asked = list(args)[1:]
            return [(day,) for day in asked if day in stored]
        raise AssertionError(sql)

    def execute(sql, args=(), **_k):
        assert 'ON CONFLICT(account_id, report_date) DO NOTHING' in sql
        assert 'DO UPDATE' not in sql
        width = 7
        for offset in range(0, len(args), width):
            account_id, report_date, total = args[offset:offset + 3]
            assert account_id == 'ALL'
            assert report_date != '2026-09-21'
            stored.add(report_date)
            inserted.append((report_date, total))

    monkeypatch.setattr(hrana_http, 'hrana_query', query)
    monkeypatch.setattr(hrana_http, 'hrana_execute', execute)
    monkeypatch.setattr(ingest, 'claim_flex_delivery', lambda *a, **k: False)
    monkeypatch.setattr(ingest, 'flex_delivery_status', lambda _d: 'applied')
    monkeypatch.setattr(ingest, '_apply_classified', lambda *a: pytest.fail('duplicate reapplied'))
    monkeypatch.setattr(ingest, '_heartbeat_cash_flow_sync', lambda *a, **k: None)
    result = ingest.ingest_xml(ACTIVITY_NAV_XML)
    assert result['ok'] is True
    assert result['outcome'] == 'duplicate'
    assert result['persistence_confirmed'] is True
    assert inserted == [('2026-09-18', 1000.0)]


def test_activity_duplicate_does_not_insert_nav_when_cash_is_missing(monkeypatch):
    xml = (
        '<FlexQueryResponse><FlexStatements>'
        '<FlexStatement accountId="U1" fromDate="20260918" toDate="20260918">'
        '<EquitySummaryByReportDateInBase accountId="U1" reportDate="20260918" total="1000"/>'
        '<CashTransactions>'
        '<CashTransaction transactionID="T-missing" type="Deposits/Withdrawals" '
        'reportDate="20260918" amount="-5" currency="USD"/>'
        '</CashTransactions>'
        '<Transfers/>'
        '</FlexStatement></FlexStatements></FlexQueryResponse>'
    )
    monkeypatch.setattr(hrana_http, 'hrana_query', lambda *a, **k: [])
    monkeypatch.setattr(hrana_http, 'hrana_execute', lambda *a, **k: pytest.fail('nav inserted'))
    monkeypatch.setattr(ingest, 'claim_flex_delivery', lambda *a, **k: False)
    monkeypatch.setattr(ingest, 'flex_delivery_status', lambda _d: 'applied')
    monkeypatch.setattr(ingest, '_apply_classified', lambda *a: pytest.fail('duplicate reapplied'))
    monkeypatch.setattr(ingest, '_heartbeat_cash_flow_sync', lambda *a, **k: None)
    result = ingest.ingest_xml(xml)
    assert result['ok'] is False
    assert result['outcome'] == 'coverage_unverified'


# 2026-09-22 page e1297eea: an applied Trade_History duplicate failed the
# oneshot because NF-4 books nothing when individual IB fills already match.
# The Flex tradeIDs are absent on purpose. That is coverage, not a gap.
TRADE_XML = (
    '<FlexQueryResponse><FlexStatement fromDate="20260410" toDate="20260410">'
    '<Trades></Trades></FlexStatement></FlexQueryResponse>'
)


def _option_exec(exec_id, qty, price, when):
    from decimal import Decimal
    from trade_blotter.models import Execution, SecurityType, Side
    return Execution(
        exec_id=exec_id,
        time=when,
        symbol='SPY',
        sec_type=SecurityType.OPTION,
        side=Side.BUY,
        quantity=Decimal(str(qty)),
        price=Decimal(str(price)),
        commission=Decimal('0'),
        strike=Decimal('500'),
        right='P',
        expiry='20260515',
    )


def _individual_fill(exec_id, qty, price, day='2026-04-10'):
    return {
        'date': day,
        'ticker': 'SPY',
        'action': 'BUY_OPTION',
        'fill_price': price,
        'contracts': qty,
        'strike': 500.0,
        'right': 'P',
        'expiry': '20260515',
        'ib_exec_id': exec_id,
    }


def _stub_trade_journal(monkeypatch, executions, rows):
    import json
    from trade_blotter.flex_query import FlexQueryFetcher
    monkeypatch.setattr(
        FlexQueryFetcher, 'parse_xml_with_drops', lambda *a: (executions, 0),
    )
    pages = iter([[('t1', json.dumps(row)) for row in rows], []])
    monkeypatch.setattr(hrana_http, 'hrana_query', lambda *a, **k: next(pages))


def test_applied_trade_duplicate_covered_by_individual_fills_is_confirmed(monkeypatch):
    from datetime import datetime
    when = datetime(2026, 4, 10, 10, 0, 0)
    later = datetime(2026, 4, 10, 14, 0, 0)
    executions = [
        _option_exec('9100000001', 3, 1.00, when),
        _option_exec('9100000002', 2, 1.10, later),
    ]
    rows = [
        _individual_fill('0001aaaa.6a000001.01.01', 3, 1.00),
        _individual_fill('0001aaaa.6a000002.01.01', 2, 1.10),
    ]
    _stub_trade_journal(monkeypatch, executions, rows)
    assert ingest.delivery_rows_present(ingest.TRADES, TRADE_XML) is True

    monkeypatch.setattr(ingest, 'claim_flex_delivery', lambda *a, **k: False)
    monkeypatch.setattr(ingest, 'flex_delivery_status', lambda d: 'applied')
    monkeypatch.setattr(ingest, '_apply_classified', lambda *a: pytest.fail('duplicate reapplied'))
    _stub_trade_journal(monkeypatch, executions, rows)
    result = ingest.ingest_xml(TRADE_XML)
    assert result['ok'] is True
    assert result['outcome'] == 'duplicate'
    assert result['persistence_confirmed'] is True


def test_trade_duplicate_disagreement_stays_unverified(monkeypatch):
    from datetime import datetime
    when = datetime(2026, 4, 10, 10, 0, 0)
    later = datetime(2026, 4, 10, 14, 0, 0)
    executions = [
        _option_exec('9100000001', 3, 1.00, when),
        _option_exec('9100000002', 3, 1.10, later),
    ]
    rows = [
        _individual_fill('0001aaaa.6a000001.01.01', 3, 1.00),
        _individual_fill('0001aaaa.6a000002.01.01', 2, 1.10),
    ]
    _stub_trade_journal(monkeypatch, executions, rows)
    assert ingest.delivery_rows_present(ingest.TRADES, TRADE_XML) is False

    monkeypatch.setattr(ingest, 'claim_flex_delivery', lambda *a, **k: False)
    monkeypatch.setattr(ingest, 'flex_delivery_status', lambda d: 'applied')
    monkeypatch.setattr(ingest, '_apply_classified', lambda *a: pytest.fail('duplicate reapplied'))
    _stub_trade_journal(monkeypatch, executions, rows)
    result = ingest.ingest_xml(TRADE_XML)
    assert result['ok'] is False
    assert result['outcome'] == 'coverage_unverified'
    assert result['persistence_confirmed'] is False


def test_trade_duplicate_uncovered_day_stays_unverified(monkeypatch):
    from datetime import datetime
    when = datetime(2026, 4, 10, 10, 0, 0)
    other = datetime(2026, 4, 13, 11, 0, 0)
    executions = [
        _option_exec('9100000001', 3, 1.00, when),
        _option_exec('9100000003', 4, 1.20, other),
    ]
    rows = [_individual_fill('0001aaaa.6a000001.01.01', 3, 1.00)]
    _stub_trade_journal(monkeypatch, executions, rows)
    assert ingest.delivery_rows_present(ingest.TRADES, TRADE_XML) is False
