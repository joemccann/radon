"""R-728 / REL-309: interrupted ufw replacement preserves prior ingress.

Shell and ufw are exercised against a temporary /etc tree, never host state.
"""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('entrypoint', ['operator', 'setup'])
@pytest.mark.parametrize('failure', ['reset', 'rule', 'enable', 'term', 'rollback-failure', 'success', 'unknown-state'])
@pytest.mark.parametrize('enabled', ['yes', 'no'])
def test_failed_apply_restores_previous_configuration(tmp_path, entrypoint, failure, enabled):
    etc = tmp_path / 'etc'
    (etc / 'ufw').mkdir(parents=True)
    (etc / 'default').mkdir()
    (etc / 'ufw/user.rules').write_text('prior ingress\n')
    (etc / 'default/ufw').write_text(f'ENABLED={enabled if failure != "unknown-state" else "unknown"}\n')
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    log = tmp_path / 'calls'
    failed = tmp_path / 'failed'
    ufw = fake_bin / 'ufw'
    ufw.write_text(f'''#!/bin/bash
printf '%s\\n' "$*" >> '{log}'
if [[ "$*" == '--force reset' ]]; then
  echo 'replacement ingress' > '{etc}/ufw/user.rules'
  echo 'ENABLED=no' > '{etc}/default/ufw'
fi
fail=0
case '{failure}':"$*" in
 reset:--force\ reset|rule:allow\ 80/tcp*|enable:--force\ enable|term:allow\ 80/tcp*|rollback-failure:allow\ 80/tcp*) fail=1;;
 rollback-failure:--force\ enable|rollback-failure:--force\ disable) exit 43;;
esac
if (( fail )) && [[ ! -e '{failed}' ]]; then
  touch '{failed}'
  if [[ '{failure}' == term ]]; then kill -TERM "$PPID"; exit 0; fi
  exit 42
fi
exit 0
''')
    ufw.chmod(0o755)
    source = ROOT / 'scripts' / ('host-firewall.sh' if entrypoint == 'operator' else 'setup-vps.sh')
    staged = tmp_path / source.name
    staged.write_text(source.read_text().replace('/etc/', str(etc) + '/'))
    env = {**os.environ, 'PATH': f'{fake_bin}:{os.environ["PATH"]}',
           'RADON_FW_TEST_MODE': '1', 'RADON_SETUP_SOURCE_ONLY': '1',
           'RADON_FW_OPERATOR_SOURCES': '100.98.36.17', 'RADON_FW_OPS_SOURCES': ''}
    command = ['bash', str(staged), '--role', 'app', '--apply'] if entrypoint == 'operator' else [
        'bash', '-c', 'source "$1"; open_firewall', 'test', str(staged)]
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
    calls = log.read_text().splitlines() if log.exists() else []
    backups = list(etc.glob('radon-ufw-rollback.*'))
    if failure == 'success':
        assert result.returncode == 0, result.stderr
        assert (etc / 'ufw/user.rules').read_text() == 'replacement ingress\n'
        assert not backups
    elif failure == 'unknown-state':
        assert result.returncode != 0
        assert not calls
        assert (etc / 'ufw/user.rules').read_text() == 'prior ingress\n'
        assert not backups
    else:
        assert result.returncode != 0
        assert (etc / 'ufw/user.rules').read_text() == 'prior ingress\n'
        assert (etc / 'default/ufw').read_text() == f'ENABLED={enabled}\n'
        assert calls[-1] == ('--force enable' if enabled == 'yes' else '--force disable')
        if failure == 'rollback-failure':
            assert len(backups) == 1
            assert (backups[0] / 'ufw/user.rules').read_text() == 'prior ingress\n'
            assert str(backups[0]) in result.stderr
            assert 'rollback failed' in result.stderr
        else:
            assert 'restored' in result.stderr.lower()
            assert not backups
