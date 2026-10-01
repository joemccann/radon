"""REL-296 / R-715: unpublished history and public names need the same gate.

All private-looking identifiers are synthetic and assembled only in fixtures.
The git repositories are disposable; no remote is configured or contacted.
"""
from __future__ import annotations

import json
import subprocess

import pytest

import ir_push_gate as gate

ACCOUNT = 'U' + '7654321'


def git(repo, *args):
    return subprocess.run(['git', *args], cwd=repo, text=True,
                          capture_output=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, 'init', '-q', '-b', 'main')
    git(tmp_path, 'config', 'user.name', 'Fixture')
    git(tmp_path, 'config', 'user.email', 'fixture@example.invalid')
    (tmp_path / 'app.py').write_text('value = 1\n')
    git(tmp_path, 'add', 'app.py')
    git(tmp_path, 'commit', '-qm', 'base')
    git(tmp_path, 'switch', '-qc', 'fix/example')
    return tmp_path


def commit(repo, path, content, message):
    (repo / path).write_text(content)
    git(repo, 'add', path)
    git(repo, 'commit', '-qm', message)


@pytest.mark.parametrize('removal', ['edit', 'delete', 'rename'])
def test_an_identifier_removed_at_head_is_still_published(repo, removal):
    path = 'incident.txt'
    commit(repo, path, f'account {ACCOUNT}\n', 'incident evidence')
    if removal == 'edit':
        commit(repo, path, 'account removed\n', 'sanitize evidence')
    elif removal == 'delete':
        git(repo, 'rm', path)
        git(repo, 'commit', '-qm', 'remove evidence')
    else:
        git(repo, 'mv', path, 'sanitized.txt')
        commit(repo, 'sanitized.txt', 'account removed\n', 'rename and sanitize')
    with pytest.raises(gate.IrPushRefused, match='ib_account_id') as exc:
        gate.check_publish(repo=repo, base='main', ref='fix/example',
                           env={'GROK_PAGE_AUTOPUSH': '1'})
    assert ACCOUNT not in str(exc.value)


def test_a_private_branch_name_is_refused_without_git():
    with pytest.raises(gate.IrPushRefused, match='ib_account_id') as exc:
        gate.check_publish(ref=f'fix/{ACCOUNT}',
                           env={'GROK_PAGE_AUTOPUSH': '1'})
    assert ACCOUNT not in str(exc.value)


def test_a_private_filename_refuses_without_echoing_the_identifier(repo):
    commit(repo, ACCOUNT + '.txt', 'safe content\n', 'add evidence')
    found = gate.scan_commit_range(repo, 'main', 'fix/example')
    assert any('ib_account_id' in f for f in found)
    assert ACCOUNT not in json.dumps(found)


def test_empty_private_filename_is_not_hidden_by_absent_patch_headers(repo):
    commit(repo, ACCOUNT + '.txt', '', 'empty evidence')
    with pytest.raises(gate.IrPushRefused, match='ib_account_id') as exc:
        gate.check_publish(repo=repo, base='main', ref='fix/example',
                           env={'GROK_PAGE_AUTOPUSH': '1'})
    assert ACCOUNT not in str(exc.value)


def test_added_line_starting_with_pluses_is_still_scanned(repo):
    commit(repo, 'app.py', '++' + ACCOUNT + '\n', 'evidence')
    assert any('ib_account_id' in f for f in gate.scan_commit_range(repo, 'main', 'fix/example'))


def test_binary_patch_is_scanned_without_text_conversion(repo):
    commit(repo, 'blob.bin', '\x00account ' + ACCOUNT + '\n', 'binary evidence')
    assert any('ib_account_id' in f for f in gate.scan_commit_range(repo, 'main', 'fix/example'))
