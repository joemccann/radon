"""R-296 / REL-312: typed UW faults survive a successful last-resort cycle."""
import importlib

import pytest

from clients.uw_client import UWAuthError, UWRateLimitError


@pytest.mark.parametrize('module_name', ['fetch_credit_spread', 'fetch_iei_hyg'])
@pytest.mark.parametrize('error_type,status,expected', [
    (UWAuthError, 401, 'uw_auth'), (UWRateLimitError, 429, 'uw_rate_limited'),
])
def test_structural_uw_failure_is_not_healthy_after_yahoo(monkeypatch, module_name, error_type, status, expected):
    mod = importlib.import_module(module_name)
    calls = []
    fault = [error_type]
    class Client:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def get_stock_ohlc(self, ticker, **kwargs):
            calls.append(ticker)
            if fault[0] is not None:
                raise fault[0]('private provider response must not be persisted', status_code=status)
            return {'data': []}
    monkeypatch.setattr('clients.uw_client.UWClient', Client)
    monkeypatch.setattr(mod, 'fetch_ib_closes', lambda tickers: {})
    monkeypatch.setattr(mod, 'fetch_rh_closes', lambda tickers: {})
    monkeypatch.setattr(mod, 'fetch_yahoo_closes', lambda tickers: {
        t: {'2026-10-02': 100.0, '2026-10-05': 101.0} for t in tickers
    })
    monkeypatch.setattr(mod, '_robinhood_degradation', lambda sources: None)
    monkeypatch.setattr(mod, '_read_cached_series' if module_name == 'fetch_credit_spread' else 'load_cached_series', lambda: [])
    heartbeats = []
    for name in ('ensure_no_replica_for_writers', 'upsert_credit_spread_rows', 'upsert_iei_hyg_rows', 'upsert_scan_snapshot'):
        monkeypatch.setattr(mod.writer, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(mod.writer, 'record_service_health', lambda *args, **kwargs: heartbeats.append((args, kwargs)))
    monkeypatch.setattr(mod, '_write_json_cache', lambda payload: None)
    payload = mod.run()
    assert payload['source'] == 'yahoo'
    assert len(calls) == 1, 'structural provider failure must stop this rung'
    assert len(heartbeats) == 1
    args, kwargs = heartbeats[0]
    assert args == (mod.SERVICE, 'error')
    assert kwargs['error']['class'] == expected
    assert kwargs['error']['source'] == 'uw'
    assert 'private provider' not in str(kwargs)

    # A later clean empty UW response clears the fault rather than retaining
    # process-global health state. Fallback still succeeds.
    fault[0] = None
    heartbeats.clear()
    mod.run()
    assert heartbeats[0][0] == (mod.SERVICE, 'ok')
    assert heartbeats[0][1]['error'] is None


@pytest.mark.parametrize('module_name', ['fetch_credit_spread', 'fetch_iei_hyg'])
@pytest.mark.parametrize('error_type', [UWAuthError, UWRateLimitError])
def test_constructor_structural_failure_keeps_typed_cause(monkeypatch, module_name, error_type):
    mod = importlib.import_module(module_name)
    def unavailable():
        raise error_type('injected constructor fault')
    monkeypatch.setattr('clients.uw_client.UWClient', unavailable)
    with pytest.raises(error_type):
        mod.fetch_uw_closes(['HYG'])


@pytest.mark.parametrize('module_name', ['fetch_credit_spread', 'fetch_iei_hyg'])
def test_ib_hit_never_constructs_uw_or_records_a_fault(module_name):
    mod = importlib.import_module(module_name)
    errors = []
    def forbidden(tickers):
        raise AssertionError('lower source called after IB hit')
    mod.fetch_closes(['HYG'], fetch_ib=lambda ts: {'HYG': {'2026-10-05': 100.0}},
                     fetch_rh=forbidden, fetch_uw=forbidden, fetch_yahoo=forbidden,
                     source_errors=errors)
    assert errors == []
