"""Runner fixture failures retain bounded, credential-free setup diagnostics."""
import importlib.util
from pathlib import Path
import subprocess

import pytest

CLOUD = Path(__file__).resolve().parents[1]


def module():
    spec = importlib.util.spec_from_file_location(
        'check_model_transport', CLOUD / 'scripts/check_model_transport.py'
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_setup_failure_identifies_executable_exit_and_bounded_stderr(monkeypatch):
    api = module()
    error = subprocess.CalledProcessError(
        255, ['/usr/sbin/sshd', 'private-argument'],
        output=b'private stdout',
        stderr=b"Directive 'PermitUserEnvironment' is not allowed within a Match block\n" + b'x' * 9000,
    )
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(api.subprocess, 'run', fail)
    with pytest.raises(RuntimeError) as caught:
        api.run(['/usr/sbin/sshd', 'private-argument'])
    message = str(caught.value)
    assert 'sshd' in message and '255' in message
    assert "Directive 'PermitUserEnvironment'" in message
    assert 'private-argument' not in message and 'private stdout' not in message
    assert len(message) < 2200


def test_timeout_identifies_fixture_executable_without_arguments(monkeypatch):
    api = module()
    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired(['/usr/bin/ssh', 'private-argument'], 10)
    monkeypatch.setattr(api.subprocess, 'run', fail)
    with pytest.raises(RuntimeError, match='ssh.*timed out') as caught:
        api.run(['/usr/bin/ssh', 'private-argument'])
    assert 'private-argument' not in str(caught.value)


def test_expected_denial_is_returned_without_becoming_setup_failure(monkeypatch):
    api = module()
    denied = subprocess.CompletedProcess(['/usr/bin/sftp'], 1, b'', b'Permission denied')
    def complete(*args, **kwargs):
        assert kwargs['check'] is False
        return denied
    monkeypatch.setattr(api.subprocess, 'run', complete)
    assert api.run(['/usr/bin/sftp'], check=False) is denied
