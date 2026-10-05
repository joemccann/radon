"""R-727 / REL-308: unknown firewall inventory must never authorize creation.

All hcloud calls are fakes; no cloud context, credentials or network is used.
"""
from types import SimpleNamespace
import subprocess

import pytest

from test_hetzner_firewalls import _load_tool


@pytest.mark.parametrize('fault', ['unauthorized', 'unavailable', 'stalled', 'invalid-json', 'invalid-shape', 'invalid-row', 'spawn-error'])
def test_inventory_failure_never_reaches_a_mutating_command(monkeypatch, fault, capsys):
    tool = _load_tool()
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[2] in ('describe', 'list'):
            if fault == 'stalled':
                raise subprocess.TimeoutExpired(argv, kwargs.get('timeout', 0))
            if fault == 'spawn-error':
                raise FileNotFoundError('hcloud')
            payload = {'invalid-json': 'not json', 'invalid-shape': '{}', 'invalid-row': '[{}]'}
            return SimpleNamespace(returncode=0 if fault in payload else 1,
                                   stdout=payload.get(fault, ''), stderr='probe unavailable')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(tool.subprocess, 'run', run)
    assert tool.main(['--firewall', 'fw-radon-app', '--server', 'ib-gateway', '--apply']) == 1
    assert [c[0][2] for c in calls] in (['describe'], ['list'])
    assert calls[0][1]['timeout'] > 0
    assert 'hcloud_firewalls:' in capsys.readouterr().err


@pytest.mark.parametrize('verb', ['replace-rules', 'apply-to-resource'])
def test_timed_out_mutation_is_indeterminate_and_never_retried(monkeypatch, verb, capsys):
    tool = _load_tool()
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[2] in ('describe', 'list'):
            return SimpleNamespace(returncode=0, stdout='[{"name":"fw-radon-app"}]')
        if argv[2] == verb:
            raise subprocess.TimeoutExpired(argv, kwargs.get('timeout', 0))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(tool.subprocess, 'run', run)
    assert tool.main(['--firewall', 'fw-radon-app', '--server', 'ib-gateway', '--apply']) == 1
    assert [c[0][2] for c in calls].count(verb) == 1
    assert calls[-1][0][2] == verb
    assert all(c[1]['timeout'] > 0 for c in calls)
    assert 'indeterminate' in capsys.readouterr().err.lower()


@pytest.mark.parametrize('fault', ['timeout', 'spawn-error', 'exit-error'])
def test_create_failure_does_not_advance_to_rules_or_attachment(monkeypatch, fault, capsys):
    tool = _load_tool()
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs['timeout'] > 0
        if argv[2] == 'list':
            return SimpleNamespace(returncode=0, stdout='[]')
        if fault == 'timeout':
            raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
        if fault == 'spawn-error':
            raise FileNotFoundError('hcloud')
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(tool.subprocess, 'run', run)
    assert tool.main(['--firewall', 'fw-radon-app', '--server', 'ib-gateway', '--apply']) == 1
    assert [c[2] for c in calls] == ['list', 'create']
    assert 'hcloud_firewalls:' in capsys.readouterr().err
