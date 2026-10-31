"""The retired bootstrap cannot copy grants, contact vendors, or rewrite settings."""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import pytest
ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / 'scripts/agent_cli_bootstrap.sh'


def _run(tmp_path, *args):
    home = tmp_path / 'home'; home.mkdir(exist_ok=True)
    return subprocess.run(['/bin/bash', str(BOOTSTRAP), *args], env={'PATH': '/usr/bin:/bin', 'HOME': str(home), 'NVIDIA_API_KEY': 'fixture-dollar-$value', 'CEREBRAS_API_KEY': 'fixture-key'}, capture_output=True, text=True, timeout=5)


@pytest.mark.parametrize('args', [(), ('--check',)])
def test_bootstrap_does_not_write_or_disclose_credentials(tmp_path, args):
    result = _run(tmp_path, *args)
    assert not (tmp_path / 'home/.radon/agent-cli/env').exists()
    assert not (tmp_path / 'home/.fx/settings.json').exists()
    assert 'fixture-' not in result.stdout + result.stderr
    assert result.returncode in {0, 75}


def test_rerun_preserves_existing_operator_settings_and_secrets(tmp_path):
    fx = tmp_path / 'home/.fx'; fx.mkdir(parents=True)
    settings = fx / 'settings.json'; settings.write_text('{"theme":"operator"}')
    secret = tmp_path / 'home/.radon/agent-cli/env'; secret.parent.mkdir(parents=True); secret.write_text('NVIDIA_API_KEY=existing')
    _run(tmp_path); _run(tmp_path, '--check')
    assert settings.read_text() == '{"theme":"operator"}'
    assert secret.read_text() == 'NVIDIA_API_KEY=existing'


def test_invalid_argument_refused_before_service_probe(tmp_path):
    result = _run(tmp_path, '--provision-keys')
    assert result.returncode == 64


def test_bootstrap_only_invokes_trusted_gateway_status_helper():
    body = BOOTSTRAP.read_text()
    assert '/opt/radon-model-clients/current/lib/model_gateway/cli.py' in body
    assert '"$HELPER" status' in body
    for forbidden in ('curl ', 'auth.json', 'NVIDIA_API_KEY', 'CEREBRAS_API_KEY', 'write_grok_home', 'write_fx_settings'):
        assert forbidden not in body
