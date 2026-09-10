"""Golden-query contract/latency report for the existing read-only hosted MCP.

Default is offline fixture replay through production implementations. --live
measures only public identity/docs and anonymous denial, with no credentials.
A passing report proves these contracts, never financial-data correctness.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from mcp_hosted import server
from mcp_hosted.auth import ANONYMOUS, Principal
from utils.atomic_io import atomic_save

LIVE_TOOLS = frozenset({'radon_identity', 'radon_docs', 'operator_portfolio', 'demo_regime'})


def live_call(name: str, arguments: dict) -> dict:
    """Pinned HTTPS target, no redirects or auth/env credentials, bounded bytes."""
    import requests
    if name not in LIVE_TOOLS:
        raise ValueError('Tool is not in the public evaluation allowlist')
    with requests.Session() as session:
        session.trust_env = False  # no ambient netrc authentication or proxies
        with session.post(server.HOSTED_MCP_URL, json={'jsonrpc': '2.0', 'id': 1,
                'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}},
                headers={'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json'},
                timeout=(5, 15), stream=True, allow_redirects=False) as response:
            response.raise_for_status()
            parts, size, started = [], 0, time.monotonic()
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > server.MAX_RESPONSE_BYTES or time.monotonic() - started > 20:
                    raise ValueError('Evaluation response exceeded budget')
                parts.append(chunk)
            rpc = json.loads(b''.join(parts))
    result = rpc.get('result', {})
    if result.get('isError') or 'error' in rpc:
        raise ValueError('MCP returned a protocol/tool error')
    payload = json.loads(result['content'][0]['text'])
    if not isinstance(payload, dict):
        raise ValueError('MCP result is not an object')
    return payload


def _cases(live: bool):
    def fixture(status=200, body='{"observed":true}'):
        return lambda url, headers: server.HttpResult(status, body)
    if live:
        return [
            ('identity', lambda: live_call('radon_identity', {}), lambda r: r.get('hosted_mcp_url') == server.HOSTED_MCP_URL),
            ('docs', lambda: live_call('radon_docs', {'slug': 'llms.txt'}), lambda r: isinstance(r.get('markdown'), str) and len(r['markdown']) > 20),
            ('anonymous-operator-denied', lambda: live_call('operator_portfolio', {}), lambda r: r.get('status') == 401 and 'data' not in r),
            ('anonymous-demo-denied', lambda: live_call('demo_regime', {}), lambda r: r.get('status') == 401 and 'data' not in r),
        ]
    def no_network(url, headers):
        raise AssertionError('Denied query reached upstream')
    def outage(url, headers):
        import requests
        raise requests.Timeout('sensitive provider URL/token must not reach report')
    cases = [
        ('identity', server._radon_identity_impl, lambda r: r['auth']['writes'].startswith('none')),
        ('docs-citation', lambda: server._radon_docs_impl('llms.txt', http_get=fixture(body='# Radon evidence')), lambda r: r == {'url': server.SITE_BASE + '/llms.txt', 'markdown': '# Radon evidence'}),
        ('docs-traversal-denied', lambda: server._radon_docs_impl('../api/portfolio', http_get=no_network), lambda r: 'error' in r and 'markdown' not in r),
        ('upstream-malformed', lambda: server._radon_health_impl(http_get=fixture(body='<html>error</html>')), lambda r: r.get('status') == 502 and 'data' not in r),
        ('upstream-timeout', lambda: server._radon_health_impl(http_get=outage), lambda r: r == {'error': 'upstream read timed out', 'status': 504, 'retryable': True}),
        ('upstream-rate-limit', lambda: server._radon_health_impl(http_get=fixture(status=429)), lambda r: r.get('status') == 429 and 'data' not in r),
    ]
    for role, status in [('anonymous', 401), ('demo', 403), ('unknown', 401)]:
        cases.append((f'{role}-operator-denied', lambda role=role: server._operator_read_impl(Principal(role), '/api/portfolio', http_get=no_network), lambda r, status=status: r.get('status') == status and 'data' not in r))
    cases.append(('anonymous-demo-denied', lambda: server._demo_read_impl(ANONYMOUS, '/api/regime', http_get=no_network), lambda r: r.get('status') == 401))
    for role in ['demo', 'operator']:
        principal = Principal(role, 'fixture-user', 'fixture-caller-token')
        def read(principal=principal):
            def getter(url, headers):
                assert headers['Authorization'] == 'Bearer fixture-caller-token'
                assert headers['X-Forwarded-For'] == '0.0.0.0'
                return server.HttpResult(200, '{"value":-12.5,"source":"fixture"}')
            return server._demo_read_impl(principal, '/api/regime', http_get=getter)
        cases.append((f'{role}-caller-scoped-read', read, lambda r: r == {'data': {'value': -12.5, 'source': 'fixture'}}))
    return cases


def evaluate(*, live: bool = False, clock=time.perf_counter) -> dict:
    records = []
    for name, invoke, judge in _cases(live):
        started = clock()
        error_class = None
        try:
            passed = bool(judge(invoke()))
        except Exception as exc:
            passed, error_class = False, type(exc).__name__
        elapsed = max(0, (clock() - started) * 1000)
        records.append({'query': name, 'passed': passed, 'latency_ms': round(elapsed, 3), 'error_class': error_class})
    durations = sorted(row['latency_ms'] for row in records)
    passed = sum(row['passed'] for row in records)
    return {'schema_version': 1, 'mode': 'live-public' if live else 'offline-contracts',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'endpoint': server.HOSTED_MCP_URL if live else None,
        'metrics': {'total': len(records), 'passed': passed, 'failed': len(records) - passed,
            'contract_accuracy': passed / len(records), 'p50_ms': durations[math.ceil(len(durations) * .5) - 1],
            'p95_ms': durations[math.ceil(len(durations) * .95) - 1]},
        'limitations': ['Contract accuracy is expected-response agreement, not financial fact accuracy.',
            'Offline latency measures fixture execution only; it is not an operational SLA.' if not live else 'Public probes do not measure licensed data or operator-book accuracy.'],
        'queries': records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = evaluate(live=args.live)
    if args.output:
        atomic_save(str(args.output), result)
    print(json.dumps(result))
    return int(result['metrics']['failed'] > 0)


if __name__ == '__main__':
    raise SystemExit(main())
