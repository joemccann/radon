#!/usr/bin/env python3
"""Refresh the root-installed runner from exact deployed public main, never bot input."""
from __future__ import annotations
import contextlib
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import ssl
import stat
import subprocess
import tarfile
import tempfile
import urllib.request

BASE = Path('/private/var/root/radon-runner-update')
PREFIX = Path('/usr/local/radon-runner')
ORIGIN = 'https://github.com/joemccann/radon.git'
API = 'https://api.github.com/repos/joemccann/radon/'
# Forced: the store is shallow, so a moved main is never a fast-forward of its tip.
MAIN_REFSPEC = '+refs/heads/main:refs/heads/main'
LOOPS = ('documentation', 'ci-performance', 'performance', 'testing', 'reliability', 'security', 'security-deepsec')
SERVICES = tuple('system/com.radon.runner.' + loop for loop in LOOPS) + ('system/com.radon.runner-perf-probe',)
ENV = {'HOME': '/var/root', 'PATH': '/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'C',
       'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'}


def protected(path):
    for item in (path, *path.parents):
        info = item.stat(follow_symlinks=False)
        if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise RuntimeError('root update source/state is not protected')


def command(args, *, binary=False, allow_missing=False):
    result = subprocess.run(args, env=ENV, stdin=subprocess.DEVNULL, capture_output=True, timeout=900)
    if allow_missing and result.returncode == 113 and args[:2] == ['/bin/launchctl', 'print'] and args[2:] in [[name] for name in SERVICES]:
        return 'state = not running\n'
    if result.returncode: raise RuntimeError(f'{Path(args[0]).name} failed (exit {result.returncode})')
    return result.stdout if binary else result.stdout.decode().strip()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


def public_json(path):
    # No environment proxies, tokens, gh configuration or SSH credentials.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request = urllib.request.Request(API + path, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'radon-root-runner-update'})
    with opener.open(request, timeout=30) as response:
        if response.geturl() != API + path: raise RuntimeError('public metadata redirected')
        payload = response.read(4_000_001)
    if len(payload) > 4_000_000: raise RuntimeError('public metadata exceeded bound')
    return json.loads(payload)


def approved_run(sha):
    runs = public_json('actions/workflows/ci.yml/runs?branch=main&event=push&per_page=10&head_sha=' + sha)['workflow_runs']
    matches = [r for r in runs if r.get('head_sha') == sha and r.get('head_branch') == 'main' and r.get('event') == 'push' and r.get('path') == '.github/workflows/ci.yml']
    if not matches: raise RuntimeError('exact main CI is not registered')
    run = max(matches, key=lambda r: (r['id'], r.get('run_attempt', 1)))
    if run.get('status') != 'completed' or run.get('conclusion') != 'success': raise RuntimeError('latest exact main CI is not green')
    jobs = []
    for page in range(1, 6):
        batch = public_json(f'actions/runs/{int(run["id"])}/jobs?per_page=100&filter=latest&page={page}')['jobs']
        jobs.extend(batch)
        if len(batch) < 100: break
    deploy = [j for j in jobs if j.get('name') == 'Deploy to VPS']
    if len(deploy) != 1 or deploy[0].get('status') != 'completed' or deploy[0].get('conclusion') != 'success':
        raise RuntimeError('exact main production deployment is not green')
    return run['id']


def git(store, *args, binary=False):
    return command(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false', '-c', 'init.templateDir=', '-c', 'http.followRedirects=false', '--git-dir=' + str(store), *args], binary=binary)


def extract_scripts(payload, destination):
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        for item in archive.getmembers():
            path = Path(item.name)
            if path.is_absolute() or '..' in path.parts or path.parts[0] != 'scripts' or not (item.isfile() or item.isdir()):
                raise RuntimeError('archive contains an unexpected path or link')
        archive.extractall(destination, filter='data')
    for item in destination.rglob('*'):
        item.chmod((0o755 if item.stat().st_mode & 0o111 else 0o644) if item.is_file() else 0o755)
        protected(item)


def write_receipt(path, data):
    source = PREFIX / 'lib/utils/atomic_io.py'
    protected(source)
    spec = importlib.util.spec_from_file_location('root_atomic_io', source)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    module.atomic_save(str(path), data)


def ready_generation(path):
    if not path.exists(): raise RuntimeError('cooperative runner install is incomplete')
    protected(path)
    ready = json.loads(path.read_text())
    if ready.get('version') != 1 or sorted(ready.get('loops', [])) != sorted(LOOPS):
        raise RuntimeError('cooperative runner install is incomplete')
    return ready


def install_proof(pending, gate):
    protected(pending)
    proof = json.loads(pending.read_text())
    prior = proof.get('ready', {})
    if (proof.get('version') != 1 or not re.fullmatch(r'[0-9a-f]{40}', str(proof.get('sha', '')))
            or type(proof.get('run_id')) is not int or proof['run_id'] <= 0
            or prior.get('version') != 1 or sorted(prior.get('loops', [])) != sorted(LOOPS)
            or proof.get('gate_device') != gate.st_dev or proof.get('gate_inode') != gate.st_ino):
        raise RuntimeError('pending installer proof is invalid')
    return prior


def refresh():
    if os.geteuid() != 0: raise RuntimeError('runner self-refresh requires the installed root service')
    # Python itself is launched -I; sanitize before TLS, git or installer use.
    os.environ.clear(); os.environ.update(ENV)
    protected(Path(__file__).absolute())
    protected(BASE.parent)
    BASE.mkdir(mode=0o700, exist_ok=True); protected(BASE)
    gate_path = PREFIX / 'maintenance.lock'; protected(gate_path)
    ready_path = PREFIX / 'maintenance-ready.json'
    pending = BASE / 'pending-install.json'
    gate = gate_path.stat()
    recovering = pending.exists()
    prior = install_proof(pending, gate) if recovering else None
    if ready_path.exists(): ready = ready_generation(ready_path)
    elif recovering: ready = prior
    else: raise RuntimeError('cooperative runner install is incomplete')
    with contextlib.ExitStack() as stack:
        fd = stack.enter_context((PREFIX / 'maintenance.lock').open('r'))
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return {'status': 'deferred-active-runner'}
        if os.fstat(fd.fileno()).st_ino != gate.st_ino or gate_path.stat().st_ino != gate.st_ino:
            raise RuntimeError('maintenance gate changed')
        if ready_path.exists(): ready = ready_generation(ready_path)
        else: ready = install_proof(pending, gate)
        for service in SERVICES:
            state = command(['/bin/launchctl', 'print', service], allow_missing=recovering)
            if not re.search(r'^\s*state = not running\s*$', state, re.M): return {'status': 'deferred-active-runner'}
        store = BASE / 'origin.git'
        if not store.exists(): command(['/usr/bin/git', '-c', 'init.templateDir=', 'init', '--bare', str(store)])
        protected(store)
        git(store, 'fetch', '--no-tags', '--depth=1', ORIGIN, MAIN_REFSPEC)
        sha = git(store, 'rev-parse', 'refs/heads/main')
        if not re.fullmatch(r'[0-9a-f]{40}', sha): raise RuntimeError('main did not resolve to an immutable commit')
        if public_json('git/ref/heads/main')['object']['sha'] != sha: raise RuntimeError('main advanced during update')
        run_id = approved_run(sha)
        receipt = BASE / 'installed.json'
        if receipt.exists(): protected(receipt)
        if receipt.exists() and not pending.exists() and json.loads(receipt.read_text()).get('sha') == sha: return {'status': 'current', 'sha': sha}
        snapshot = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='source-', dir=BASE)))
        extract_scripts(git(store, 'archive', '--format=tar', sha, 'scripts', binary=True), snapshot)
        protected(snapshot)
        if public_json('git/ref/heads/main')['object']['sha'] != sha: raise RuntimeError('main advanced before install')
        # Reconfirm the same run after source validation; a queued/rerun gate is pending.
        if approved_run(sha) != run_id: raise RuntimeError('CI run changed before install')
        write_receipt(pending, {'version': 1, 'sha': sha, 'run_id': run_id, 'ready': ready, 'gate_device': gate.st_dev, 'gate_inode': gate.st_ino})
        command(['/bin/bash', str(snapshot / 'scripts/runner/install.sh'), *LOOPS])
        ready_generation(ready_path)
        write_receipt(receipt, {'sha': sha, 'run_id': run_id})
        pending.unlink()
        directory_fd = os.open(BASE, os.O_RDONLY)
        try: os.fsync(directory_fd)
        finally: os.close(directory_fd)
        return {'status': 'installed', 'sha': sha, 'run_id': run_id}


if __name__ == '__main__':
    try:
        if len(__import__('sys').argv) != 1: raise RuntimeError('runner self-refresh accepts no arguments')
        print(json.dumps(refresh()))
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({'status': 'incomplete', 'reason': str(exc)})); raise SystemExit(75)
