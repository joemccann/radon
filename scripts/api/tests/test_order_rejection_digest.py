"""R-025 / REL-310: rejection survives an absent browser in the durable digest."""
import json
from types import SimpleNamespace

import pytest

from api import order_audit
from watchdog import notify
from api.tests.test_order_audit_trail import trusted_client, _STOCK_ORDER, _fake_recovery


@pytest.fixture
def digest(monkeypatch, tmp_path):
    path = tmp_path / 'digest.json'
    monkeypatch.setattr(notify, 'DIGEST_STATE_PATH', path)
    monkeypatch.setattr(order_audit, 'hrana_execute', lambda *a, **kw: [])

    def forbidden(*a, **kw):
        raise AssertionError('rejection enqueue touched a live notification channel')

    monkeypatch.setattr(notify, '_pushover_creds', forbidden)
    monkeypatch.setattr(notify, '_post_pushover', forbidden)
    return path


@pytest.mark.parametrize('ok', [True, False])
@pytest.mark.parametrize('db_failure', [False, True])
def test_rejection_reaches_digest_without_browser_or_audit_storage(
    trusted_client, digest, monkeypatch, ok, db_failure,
):
    client, server = trusted_client
    if db_failure:
        def fail(*args, **kwargs):
            raise OSError('injected database outage')
        monkeypatch.setattr(order_audit, 'hrana_execute', fail)
    detail = {'status': 'error', 'message': 'Order rejected: margin',
              'ib_error_code': 201, 'orderRef': 'radon-rejected', 'orderId': 42}
    monkeypatch.setattr(server, '_run_ib_script_with_recovery', _fake_recovery(
        SimpleNamespace(ok=ok, error=None if ok else 'Script failed', data=detail)))
    response = client.post('/orders/place', json=_STOCK_ORDER)
    assert response.status_code == 502
    assert response.json()['detail'] == detail
    # Read from disk after the initiating request has ended, with no browser consumer.
    pending = json.loads(digest.read_text())['pending']
    assert len(pending) == 1
    event = pending[0]
    assert event['severity'] == 'P2'
    assert event['kind'] == 'order-rejected'
    assert all(value in event['message'] for value in ['AAPL', 'radon-rejected', '201'])


@pytest.mark.asyncio
async def test_success_never_queues_a_rejection(digest):
    assert await order_audit.record_order_event('submitted', order_ref='radon-ok')
    assert not digest.exists()


@pytest.mark.asyncio
async def test_digest_failure_keeps_original_audit_outcome(digest, monkeypatch, caplog):
    def fail(*a, **kw):
        raise OSError('injected disk failure')
    monkeypatch.setattr(notify, '_save_digest_state', fail)
    assert await order_audit.record_order_event('rejected', symbol='AAPL', order_ref='radon-rejected')
    assert 'digest enqueue failed' in caplog.text


@pytest.mark.asyncio
async def test_digest_has_bounded_identity_and_omits_raw_broker_detail(digest):
    detail = {'ib_error_code': 'not numeric', 'message': 'untrusted-' * 100000}
    assert await order_audit.record_order_event('rejected', symbol='S' * 100000,
                                                order_ref='R' * 100000, detail=detail)
    message = json.loads(digest.read_text())['pending'][0]['message']
    assert len(message) < 300
    assert 'IB unknown' in message
    assert 'untrusted' not in message


@pytest.mark.parametrize('ok,error,detail', [
    (False, 'Script timed out', {'status': 'error', 'message': 'partial output'}),
    (True, None, {'status': 'error', 'indeterminate': True, 'orderRef': 'radon-unknown'}),
])
def test_indeterminate_attempt_never_becomes_a_rejection_digest(
    trusted_client, digest, monkeypatch, ok, error, detail,
):
    client, server = trusted_client
    monkeypatch.setattr(server, '_run_ib_script_with_recovery', _fake_recovery(
        SimpleNamespace(ok=ok, error=error, data=detail)))
    response = client.post('/orders/place', json=_STOCK_ORDER)
    assert response.status_code == (504 if not ok else 502)
    if not ok:
        assert response.json()['detail']['code'] == 'ORDER_INDETERMINATE'
    else:
        assert response.json()['detail']['indeterminate'] is True
    assert not digest.exists()
