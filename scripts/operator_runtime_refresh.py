#!/usr/bin/env python3
"""Refresh installed operator code from a fully deployed immutable main commit."""
from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import tempfile
import uuid

REPO = 'joemccann/radon'
FILES = {
    'radon-operator-releases': ('unattended_release.py', 'private_security_release.py', 'operator_runtime_refresh.py', 'codex_capability_probe.py', 'ci/check_pr_body.py'),
    'radon-toolchain-updater': ('nightly_toolchain_update.py', 'utils/atomic_io.py'),
}


def _operator_uid():
    uid = os.geteuid()
    if uid == 0 or pwd.getpwuid(uid).pw_name == '_radonbot':
        raise PermissionError('runtime refresh requires the operator account')
    return uid


def _trusted(path, uid):
    if '..' in path.parts:
        raise PermissionError('parent traversal forbidden')
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid not in (0, uid):
            raise PermissionError('untrusted runtime path')
        if info.st_mode & 0o022 and not (item != path and info.st_mode & stat.S_ISVTX):
            raise PermissionError('writable runtime path')


def _installed(base, uid):
    _trusted(base, uid)
    if not base.is_dir():
        raise PermissionError('runtime installation is not a directory')
    current = base / 'current'
    if not current.exists() and not current.is_symlink():
        return None
    info = current.lstat()
    if not stat.S_ISLNK(info.st_mode) or info.st_uid not in (0, uid):
        raise PermissionError('current runtime must be an owned symlink')
    target = current.readlink()
    target = target if target.is_absolute() else base / target
    _trusted(target, uid)
    if not target.is_dir() or not target.is_relative_to(base / 'releases'):
        raise PermissionError('current runtime escapes its release directory')
    return target


def _approved_main(github):
    sha = github.main_sha()
    if not re.fullmatch('[0-9a-f]{40}', sha):
        raise ValueError('invalid main commit')
    runs = github.api(f'repos/{REPO}/actions/workflows/ci.yml/runs?branch=main&event=push&per_page=30')['workflow_runs']
    matching = [run for run in runs if run['head_sha'] == sha]
    if not matching or any(run['status'] != 'completed' for run in runs):
        return None
    latest = max(matching, key=lambda run: (run['id'], run.get('run_attempt', 1)))
    if latest['conclusion'] != 'success':
        return None
    jobs = github.api(f'repos/{REPO}/actions/runs/{latest["id"]}/jobs?per_page=100&filter=latest')
    if jobs.get('total_count', 0) > 100:
        return None
    if not any(job['name'] == 'Deploy to VPS' and job['status'] == 'completed' and job['conclusion'] == 'success' for job in jobs['jobs']):
        return None
    return sha if github.main_sha() == sha else None


def _source(github, sha, relative):
    path = 'scripts/' + relative
    row = github.api(f'repos/{REPO}/contents/{path}?ref={sha}')
    if row.get('type') != 'file' or row.get('encoding') != 'base64' or row.get('path') != path:
        raise ValueError('invalid runtime source metadata')
    content = base64.b64decode(''.join(row['content'].split()), validate=True)
    blob = hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest()
    if row.get('sha') != blob:
        raise ValueError('runtime source digest mismatch')
    ast.parse(content.decode('utf-8'), filename=path)
    return content


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write(path, content, mode):
    with path.open('xb') as stream:
        stream.write(content)
        stream.flush()
        os.fchmod(stream.fileno(), mode)
        os.fsync(stream.fileno())


def refresh(github, *, home=Path.home()):
    """Change code pointers for the next tick; never import candidates or alter policy."""
    uid, installed = _operator_uid(), []
    home = Path(home).absolute()
    for name, files in FILES.items():
        base = home / '.local/share' / name
        if not base.exists() and not base.is_symlink():
            continue
        old = _installed(base, uid)
        if old is not None:
            installed.append((base, old, files))
    if not installed:
        return {'status': 'not-installed'}
    sha = _approved_main(github)
    if sha is None:
        return {'status': 'ci-pending'}
    sources = {}
    try:
        for _, _, files in installed:
            for relative in files:
                sources[relative] = _source(github, sha, relative)
    except RuntimeError:
        # Missing files on pre-controller main commits and unavailable APIs
        # both leave every installed version and authority file untouched.
        return {'status': 'source-unavailable', 'sha': sha}
    staged, updated = [], []
    try:
        for base, old, files in installed:
            for relative in files:
                existing = old / relative
                if existing.exists() or existing.is_symlink():
                    _trusted(existing, uid)
                    if not existing.is_file():
                        raise PermissionError('runtime code is not a regular file')
            if all((old / relative).is_file() and (old / relative).read_bytes() == sources[relative] for relative in files):
                continue
            policies = ('policy.json', 'hosts.json', 'notifications.env') if base.name == 'radon-operator-releases' else ()
            for name in policies:
                _trusted(base / name, uid)
            policy = old / 'toolchain-policy.json' if base.name == 'radon-toolchain-updater' else None
            if policy is not None:
                _trusted(policy, uid)
                policy_bytes = policy.read_bytes()
            releases = base / 'releases'
            _trusted(releases, uid)
            release = Path(tempfile.mkdtemp(prefix=sha + '-', dir=releases))
            staged.append((base, release))
            for relative in files:
                target = release / relative
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                _write(target, sources[relative], 0o500)
            if policy is not None:
                _write(release / policy.name, policy_bytes, 0o600)
            _write(release / 'source.json', (json.dumps({'main_sha': sha}) + '\n').encode(), 0o600)
            module_dirs = {(release / relative).parent for relative in files} - {release}
            for directory in sorted(module_dirs, key=lambda path: len(path.parts), reverse=True):
                _sync_directory(directory)
            _sync_directory(release)
            _sync_directory(releases)
        if staged and _approved_main(github) != sha:
            return {'status': 'main-changed', 'sha': sha}
        for base, release in staged:
            temporary = base / ('.current-' + uuid.uuid4().hex)
            try:
                temporary.symlink_to(release)
                os.replace(temporary, base / 'current')
                updated.append(base.name)
                _sync_directory(base)
            finally:
                temporary.unlink(missing_ok=True)
        return {'status': 'updated' if updated else 'current', 'sha': sha, 'updated': updated}
    finally:
        for base, release in staged:
            if base.name not in updated:
                shutil.rmtree(release)
