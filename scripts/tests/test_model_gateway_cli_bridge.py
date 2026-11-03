"""Coding CLIs retain local tools while every model request crosses the private gateway."""
import json
import urllib.request
import urllib.error
import pytest


def _post(handle, path, body, *, token=None, host=None):
    request = urllib.request.Request(handle.url + path, data=json.dumps(body).encode(), headers={
        'Content-Type': 'application/json', 'Authorization': 'Bearer ' + (token or handle.token),
        **({'Host': host} if host else {}),
    })
    return urllib.request.urlopen(request, timeout=2)


def test_native_messages_retains_advanced_tools_and_stream():
    from model_gateway.cli_bridge import bridge
    seen = []
    stream = 'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    def gateway(operation, payload, **kwargs):
        seen.append((operation, payload))
        return {'status': 200, 'body': stream, 'content_type': 'text/event-stream'}
    body = {'model': 'claude-opus-5', 'tools': [{'name': 'Read', 'input_schema': {'type': 'object'}}], 'stream': True, 'context_management': {'edits': []}}
    with bridge(gateway=gateway) as handle:
        with _post(handle, '/v1/messages?beta=true', body) as response:
            assert response.read().decode() == stream
    assert seen == [('native', {'protocol': 'messages', 'request': body, 'stream': True})]


def test_bridge_refuses_unauthenticated_or_cross_host_requests_before_gateway():
    from model_gateway.cli_bridge import bridge
    seen = []
    with bridge(gateway=lambda *a, **k: seen.append(a)) as handle:
        for kwargs in ({'token': 'wrong'}, {'host': 'attacker.example'}):
            with pytest.raises(urllib.error.HTTPError):
                _post(handle, '/v1/responses', {}, **kwargs)
    assert not seen


def test_bridge_accepts_only_fixed_native_paths():
    from model_gateway.cli_bridge import bridge
    with bridge(gateway=lambda *a, **k: pytest.fail('unknown route reached service')) as handle:
        with pytest.raises(urllib.error.HTTPError) as error:
            _post(handle, '/proxy?url=https://attacker.example', {})
        assert error.value.code == 404


def test_bridge_models_are_service_owned_without_credentials():
    from model_gateway.cli_bridge import bridge
    def gateway(operation, payload, **kwargs):
        assert operation == 'models'
        return {'models': [{'id': 'antigravity-default', 'provider': 'antigravity', 'label': 'AGY', 'refreshedAt': '2026-10-30'}], 'defaultId': 'antigravity-default', 'source': 'gateway'}
    with bridge(gateway=gateway) as handle:
        request = urllib.request.Request(handle.url + '/v1/models', headers={'Authorization': 'Bearer ' + handle.token})
        with urllib.request.urlopen(request, timeout=2) as response:
            result = json.load(response)
    assert result['data'][0]['id'] == 'antigravity-default'


def test_bridge_never_exposes_private_failure_diagnostics():
    from model_gateway.cli_bridge import bridge
    def gateway(*a, **k):
        raise RuntimeError('secret-provider-diagnostic')
    with bridge(gateway=gateway) as handle:
        with pytest.raises(urllib.error.HTTPError) as error:
            _post(handle, '/v1/responses', {})
        assert error.value.code == 503
        assert b'secret-provider-diagnostic' not in error.value.read()
