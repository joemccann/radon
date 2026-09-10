"""Golden contracts and fault injection; never call the hosted network in CI."""
import json

import pytest
import requests

from mcp_hosted import evaluate as evaluation
from mcp_hosted import server
from mcp_hosted.auth import Principal


def test_all_golden_contracts_and_report_latency(monkeypatch):
    monkeypatch.setattr(requests, 'get', lambda *a, **kw: pytest.fail('network forbidden'))
    ticks = iter(i / 1000 for i in range(100))
    result = evaluation.evaluate(clock=lambda: next(ticks))
    assert result['mode'] == 'offline-contracts' and result['endpoint'] is None
    assert result['metrics']['total'] == 12
    assert result['metrics']['failed'] == 0
    assert result['metrics']['contract_accuracy'] == 1
    assert result['metrics']['p95_ms'] == 1
    assert 'fixture-caller-token' not in json.dumps(result)
    assert 'financial fact accuracy' in result['limitations'][0]


def test_live_mode_never_uses_operator_token_or_mutation_tools(monkeypatch):
    called = []
    def invoke(name, arguments):
        called.append((name, arguments))
        return {'hosted_mcp_url': server.HOSTED_MCP_URL, 'markdown': '# Radon public docs with source links', 'status': 401}
    monkeypatch.setattr(evaluation, 'live_call', invoke)
    result = evaluation.evaluate(live=True)
    assert {name for name, _ in called} == evaluation.LIVE_TOOLS
    assert result['metrics']['failed'] == 0
    assert result['mode'] == 'live-public'


def test_mismatch_and_exception_are_failures_without_secret_leak(monkeypatch):
    def explode(): raise RuntimeError('https://secret:token@provider.example')
    monkeypatch.setattr(evaluation, '_cases', lambda live: [('mismatch', lambda: {}, lambda r: False), ('error', explode, lambda r: True)])
    result = evaluation.evaluate()
    assert result['metrics']['failed'] == 2
    assert result['queries'][1]['error_class'] == 'RuntimeError'
    assert 'secret' not in json.dumps(result)


@pytest.mark.parametrize('error,status', [(requests.Timeout('secret'),504), (requests.ConnectionError('secret'),503), (ValueError('secret'),502)])
@pytest.mark.parametrize('kind', ['docs','health','demo','operator'])
def test_provider_outages_are_scrubbed_not_empty_data(error, status, kind):
    def broken(url, headers): raise error
    principal = Principal('operator', 'user', 'sensitive-token')
    invoke = {
        'docs': lambda: server._radon_docs_impl('llms.txt', http_get=broken),
        'health': lambda: server._radon_health_impl(http_get=broken),
        'demo': lambda: server._demo_read_impl(principal, '/api/regime', http_get=broken),
        'operator': lambda: server._operator_read_impl(principal, '/api/portfolio', http_get=broken),
    }[kind]
    result = invoke()
    assert result['status'] == status and 'data' not in result
    assert 'secret' not in json.dumps(result) and 'sensitive-token' not in json.dumps(result)


def test_public_probe_refuses_unregistered_write_before_network():
    with pytest.raises(ValueError): evaluation.live_call('place_order', {})


def test_stream_deadline_closes_response_and_never_redirects(monkeypatch):
    observed = {}
    class Response:
        status_code = 200
        def iter_content(self, chunk_size): yield b'{}'
        def close(self): observed['closed'] = True
    def get(*args, **kwargs):
        observed.update(kwargs)
        return Response()
    ticks = iter([0, server.HTTP_TIMEOUT_S + 1])
    monkeypatch.setattr(server.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(requests, 'get', get)
    with pytest.raises(requests.Timeout): server._http_get('https://example.test', {})
    assert observed['closed'] and observed['allow_redirects'] is False
