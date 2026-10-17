"""Immutable operator runtime updates preserve authority and never run candidates."""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import operator_runtime_refresh as refresh

SHA = 'a' * 40
NEW_SHA = 'b' * 40


class GitHub:
    def __init__(self, *, ready=True):
        self.sha, self.ready, self.calls = SHA, ready, []
        self.sources = {relative: b'# approved immutable source\n' for files in refresh.FILES.values() for relative in files}
        self.missing, self.corrupt, self.invalid = None, None, None
        self.move_during_fetch = False

    def main_sha(self):
        return self.sha

    def api(self, path):
        self.calls.append(path)
        if '/workflows/ci.yml/runs?' in path:
            return {'workflow_runs': [{'head_sha': self.sha, 'id': 7, 'status': 'completed',
                                      'conclusion': 'success' if self.ready else 'failure'}]}
        if '/actions/runs/7/jobs?' in path:
            return {'total_count': 1, 'jobs': [{'name': 'Deploy to VPS', 'status': 'completed', 'conclusion': 'success'}]}
        prefix = f'repos/{refresh.REPO}/contents/scripts/'
        assert path.startswith(prefix)
        relative, separator, sha = path[len(prefix):].partition('?ref=')
        assert separator and sha == SHA
        if relative == self.missing:
            raise RuntimeError('source unavailable')
        content = self.sources[relative]
        row = {'type': 'file', 'encoding': 'base64', 'path': 'scripts/' + relative,
               'content': base64.b64encode(content).decode(),
               'sha': hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest()}
        if relative == self.corrupt:
            row['sha'] = '0' * 40
        if relative == self.invalid:
            row['type'] = 'symlink'
        if self.move_during_fetch:
            self.sha = NEW_SHA
        return row


@pytest.fixture
def installation(tmp_path, monkeypatch):
    monkeypatch.setattr(refresh, '_operator_uid', lambda: os.getuid())
    home = tmp_path / 'operator'
    bases = {}
    for name, files in refresh.FILES.items():
        base = home / '.local/share' / name
        old = base / 'releases/old'
        old.mkdir(parents=True, mode=0o700)
        for relative in files:
            target = old / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('# previous source\n')
        (base / 'current').symlink_to(old)
        if name == 'radon-operator-releases':
            for filename in ('policy.json', 'hosts.json', 'notifications.env'):
                (base / filename).write_bytes(b'operator authority must remain unchanged\n')
                (base / filename).chmod(0o600)
        else:
            (old / 'toolchain-policy.json').write_bytes(b'{"standing_policy":"preserve exactly"}\n')
            (old / 'toolchain-policy.json').chmod(0o600)
        bases[name] = base
    return home, bases


def pointers(bases):
    return {name: (base / 'current').readlink() for name, base in bases.items()}


def test_green_exact_sha_updates_only_allowlisted_code_and_preserves_policy(installation, tmp_path):
    home, bases = installation
    github = GitHub()
    marker = tmp_path / 'candidate-executed'
    github.sources['unattended_release.py'] = f'open({str(marker)!r}, "w").write("must not execute")\n'.encode()
    before = pointers(bases)
    authority = {p: p.read_bytes() for p in bases['radon-operator-releases'].iterdir() if p.is_file()}
    old_policy = (before['radon-toolchain-updater'] / 'toolchain-policy.json').read_bytes()
    result = refresh.refresh(github, home=home)
    assert result['status'] == 'updated' and result['sha'] == SHA
    assert set(result['updated']) == set(bases)
    assert not marker.exists()
    assert set(refresh.FILES['radon-operator-releases']) == {
        'unattended_release.py', 'private_security_release.py',
        'operator_runtime_refresh.py', 'codex_capability_probe.py', 'ci/check_pr_body.py',
    }
    for name, base in bases.items():
        current = (base / 'current').resolve()
        assert current != before[name] and current.is_relative_to(base / 'releases')
        assert current.stat().st_mode & 0o777 == 0o700
        for relative in refresh.FILES[name]:
            assert (current / relative).read_bytes() == github.sources[relative]
            assert (current / relative).stat().st_mode & 0o777 == 0o500
    assert {p: p.read_bytes() for p in authority} == authority
    assert (bases['radon-toolchain-updater'] / 'current/toolchain-policy.json').read_bytes() == old_policy
    assert all('?ref=' + SHA in call for call in github.calls if '/contents/' in call)
    assert refresh.refresh(github, home=home)['status'] == 'current'


def test_failed_main_ci_never_downloads_or_activates(installation):
    home, bases = installation
    before = pointers(bases)
    github = GitHub(ready=False)
    assert refresh.refresh(github, home=home)['status'] == 'ci-pending'
    assert pointers(bases) == before
    assert not any('/contents/' in call for call in github.calls)


def test_missing_deploy_gate_never_activates(installation, monkeypatch):
    home, bases = installation
    before = pointers(bases)
    github, original = GitHub(), GitHub.api
    monkeypatch.setattr(github, 'api', lambda path: {'jobs': [], 'total_count': 0} if '/jobs?' in path else original(github, path))
    assert refresh.refresh(github, home=home)['status'] == 'ci-pending'
    assert pointers(bases) == before


def test_main_change_during_download_preserves_both_pointers(installation):
    home, bases = installation
    before = pointers(bases)
    github = GitHub()
    github.move_during_fetch = True
    assert refresh.refresh(github, home=home)['status'] == 'main-changed'
    assert pointers(bases) == before
    assert all(len(list((base / 'releases').iterdir())) == 1 for base in bases.values())


@pytest.mark.parametrize('failure', ['missing', 'corrupt', 'invalid', 'syntax'])
def test_unusable_source_never_activates_either_runtime(installation, failure):
    home, bases = installation
    before = pointers(bases)
    github = GitHub()
    target = 'utils/atomic_io.py'
    if failure == 'syntax':
        github.sources[target] = b'def broken(:\n'
    else:
        setattr(github, failure, target)
    if failure == 'missing':
        assert refresh.refresh(github, home=home)['status'] == 'source-unavailable'
    else:
        with pytest.raises((ValueError, SyntaxError)):
            refresh.refresh(github, home=home)
    assert pointers(bases) == before


@pytest.mark.parametrize('unsafe', ['base-link', 'outside-current', 'writable', 'policy-link', 'code-link'])
def test_untrusted_installed_paths_never_activate(installation, tmp_path, unsafe):
    home, bases = installation
    base = bases['radon-toolchain-updater']
    old = (base / 'current').resolve()
    if unsafe == 'base-link':
        moved = base.with_name('moved')
        base.rename(moved)
        base.symlink_to(moved)
    elif unsafe == 'outside-current':
        (base / 'current').unlink()
        (base / 'current').symlink_to(tmp_path)
    elif unsafe == 'writable':
        old.chmod(0o777)
    else:
        path = old / ('toolchain-policy.json' if unsafe == 'policy-link' else 'nightly_toolchain_update.py')
        path.unlink()
        path.symlink_to(tmp_path / 'outside')
    before = pointers(bases)
    with pytest.raises((PermissionError, FileNotFoundError)):
        refresh.refresh(GitHub(), home=home)
    assert pointers(bases) == before


def test_foreign_owned_installation_is_rejected(installation, monkeypatch):
    home, bases = installation
    target = bases['radon-toolchain-updater']
    original = Path.lstat
    def foreign(path):
        value = original(path)
        return SimpleNamespace(st_mode=value.st_mode, st_uid=os.getuid() + 10000) if path == target else value
    monkeypatch.setattr(Path, 'lstat', foreign)
    with pytest.raises(PermissionError):
        refresh.refresh(GitHub(), home=home)


@pytest.mark.parametrize('account', ['root', '_radonbot'])
def test_root_and_bot_accounts_cannot_refresh(monkeypatch, tmp_path, account):
    monkeypatch.setattr(refresh.os, 'geteuid', lambda: 0 if account == 'root' else 999)
    monkeypatch.setattr(refresh.pwd, 'getpwuid', lambda _: SimpleNamespace(pw_name=account))
    with pytest.raises(PermissionError):
        refresh.refresh(GitHub(), home=tmp_path)


def test_absent_installations_are_never_created(tmp_path, monkeypatch):
    monkeypatch.setattr(refresh, '_operator_uid', lambda: os.getuid())
    github = GitHub()
    assert refresh.refresh(github, home=tmp_path)['status'] == 'not-installed'
    assert not github.calls and not (tmp_path / '.local').exists()


def test_post_activation_fsync_error_does_not_delete_current_release(installation, monkeypatch):
    home, bases = installation
    original = refresh._sync_directory
    def fail(path):
        if path == bases['radon-operator-releases']:
            raise OSError('injected directory fsync failure')
        original(path)
    monkeypatch.setattr(refresh, '_sync_directory', fail)
    with pytest.raises(OSError):
        refresh.refresh(GitHub(), home=home)
    assert (bases['radon-operator-releases'] / 'current/unattended_release.py').is_file()


@pytest.mark.parametrize('condition', ['queued', 'other-sha', 'latest-failed', 'too-many-jobs'])
def test_only_complete_latest_exact_main_ci_can_activate(installation, monkeypatch, condition):
    home, bases = installation
    before = pointers(bases)
    github, original = GitHub(), GitHub.api
    def api(path):
        row = original(github, path)
        if 'workflow_runs' in row:
            if condition == 'queued':
                row['workflow_runs'][0]['status'] = 'in_progress'
            elif condition == 'other-sha':
                row['workflow_runs'][0]['head_sha'] = NEW_SHA
            elif condition == 'latest-failed':
                row['workflow_runs'].append({'head_sha': SHA, 'id': 8, 'status': 'completed', 'conclusion': 'failure'})
        elif condition == 'too-many-jobs' and 'jobs' in row:
            row['total_count'] = 101
        return row
    monkeypatch.setattr(github, 'api', api)
    assert refresh.refresh(github, home=home)['status'] == 'ci-pending'
    assert pointers(bases) == before


def test_installation_without_current_is_not_bootstrapped(installation):
    home, bases = installation
    for base in bases.values():
        (base / 'current').unlink()
    github = GitHub()
    assert refresh.refresh(github, home=home)['status'] == 'not-installed'
    assert not github.calls
    assert all(not (base / 'current').exists() for base in bases.values())


@pytest.mark.parametrize('sha', ['main', 'a' * 39, 'A' * 40, 'a' * 40 + '/other'])
def test_invalid_main_sha_never_downloads_or_activates(installation, sha):
    home, bases = installation
    before = pointers(bases)
    github = GitHub()
    github.sha = sha
    with pytest.raises(ValueError, match='invalid main commit'):
        refresh.refresh(github, home=home)
    assert pointers(bases) == before
    assert not github.calls


def test_all_nested_module_directories_are_durable_before_release_activation(installation, monkeypatch):
    home, bases = installation
    synced, original = [], refresh._sync_directory
    def record(path):
        synced.append(path)
        original(path)
    monkeypatch.setattr(refresh, '_sync_directory', record)
    assert refresh.refresh(GitHub(), home=home)['status'] == 'updated'
    for name, child in (('radon-operator-releases', 'ci'), ('radon-toolchain-updater', 'utils')):
        base = bases[name]
        release = (base / 'current').resolve()
        assert release / child in synced
        assert synced.index(release / child) < synced.index(release)
        assert synced.index(release) < synced.index(base / 'releases')
        assert synced.index(base / 'releases') < synced.index(base)
