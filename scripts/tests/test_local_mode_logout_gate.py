"""R-044 / REL-311: local login waits for confirmed broker-owned logout.

The real shell runs from a temporary script tree; every host command is fake.
No env file, SSH connection, Docker operation or launchd action is performed.
"""
import os
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]


def _run(tmp_path, release_rc):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    (tmp_path / 'web').mkdir()
    (scripts / 'local.sh').write_text((SCRIPTS / 'local.sh').read_text())
    log = tmp_path / 'calls'
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    commands = {
        scripts / 'ib': ('ib', 'exit 0'),
        scripts / '_set_radon_mode.sh': ('mode', 'exit 0'),
        scripts / 'docker_ib_gateway.sh': ('gateway', 'exit 0'),
        bin_dir / 'ssh': ('ssh', f'exit {release_rc}'),
        bin_dir / 'docker': ('docker', 'echo healthy; exit 0'),
        bin_dir / 'launchctl': ('launchctl', 'exit 0'),
        bin_dir / 'lsof': ('lsof', 'exit 1'),
        bin_dir / 'npm': ('npm', 'exit 0'),
    }
    for path, (name, action) in commands.items():
        path.write_text(f'#!/bin/sh\nprintf "%s\\n" "{name} $*" >> "{log}"\n{action}\n')
        path.chmod(0o755)
    result = subprocess.run(['bash', str(scripts / 'local.sh')],
                            env={**os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}'},
                            capture_output=True, text=True, timeout=10)
    return result, log.read_text().splitlines()


@pytest.mark.parametrize('rc', [1, 74, 127, 255])
def test_unconfirmed_logout_stops_before_mode_or_local_login(tmp_path, rc):
    result, calls = _run(tmp_path, rc)
    assert result.returncode != 0
    assert calls == [
        'ssh -o BatchMode=yes -o ConnectTimeout=5 root@radon-broker '
        '/usr/local/bin/radon ib release --reason local-development',
    ]
    assert 'not confirmed' in result.stdout.lower()


def test_confirmed_logout_precedes_every_local_mutation(tmp_path):
    result, calls = _run(tmp_path, 0)
    assert result.returncode == 0, result.stderr
    assert calls[0] == (
        'ssh -o BatchMode=yes -o ConnectTimeout=5 root@radon-broker '
        '/usr/local/bin/radon ib release --reason local-development'
    )
    assert calls.index('ib mode local') > 0
    assert calls.index('gateway start') > calls.index('ib mode local')
    assert not any('compose down' in c for c in calls)


@pytest.mark.parametrize('fault', ['timeout', 'spawn-error'])
def test_broker_release_child_is_bounded_and_unknown_is_failure(monkeypatch, fault):
    code = (SCRIPTS / 'local.sh').read_text().split("<<'PYTHON'\n", 1)[1].split('\nPYTHON', 1)[0]
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if fault == 'timeout':
            raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
        raise OSError('injected unavailable SSH executable')

    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(SystemExit) as exited:
        exec(compile(code, 'local-logout-preflight', 'exec'), {})
    assert exited.value.code == 1
    assert len(calls) == 1
    assert calls[0][1]['timeout'] == 180
    assert calls[0][0][-2:] == [
        'root@radon-broker', '/usr/local/bin/radon ib release --reason local-development',
    ]
