#!/usr/bin/env python3
"""Stage an approved reviewer outside scans; promote only during idle maintenance."""
from __future__ import annotations
import argparse
import contextlib
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
import pwd
from pathlib import Path
import re
import shutil
import stat
import sys
import subprocess
import tempfile
# Isolated Python (-I) omits the script directory. Load only this helper's
# own trusted source/install root, never the working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.atomic_io import atomic_save, verified_load

PACKAGE_PATHS = ('package.json', 'package-lock.json', 'node_modules')
LOOPS = ('documentation', 'ci-performance', 'performance', 'testing', 'reliability', 'security', 'security-deepsec')


def clean_environment():
    return {k: v for k, v in os.environ.items() if k in {'HOME', 'USER', 'LOGNAME', 'LANG', 'TMPDIR'} } | {
        'PATH': '/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin',
        'DISABLE_AUTOUPDATER': '1', 'HOMEBREW_NO_AUTO_UPDATE': '1',
        'NPM_CONFIG_USERCONFIG': '/dev/null', 'NPM_CONFIG_IGNORE_SCRIPTS': 'true',
    }


def run(args, *, cwd=None, timeout=120, include_stderr=False):
    # Output stays in the private log, never emitted into a public job/report.
    result = subprocess.run(args, cwd=cwd, env=clean_environment(), capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        if cwd:
            diagnostic = Path(cwd) / 'maintenance-error.private.log'
            fd = os.open(diagnostic, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as stream: stream.write(result.stdout + '\n' + result.stderr)
        raise RuntimeError(f'{Path(args[0]).name} failed (exit {result.returncode})')
    return (result.stdout + result.stderr if include_stderr else result.stdout).strip()


@contextlib.contextmanager
def maintenance_gate(policy):
    path = Path(policy['maintenance_gate'])
    marker = Path(policy['maintenance_ready'])
    # Every ancestor is trusted: a bot cannot replace the coordination inode.
    for item in (path.parent, *path.parent.parents):
        info = item.stat(follow_symlinks=False)
        if info.st_uid != 0 or info.st_mode & 0o022 or not stat.S_ISDIR(info.st_mode):
            raise RuntimeError('maintenance gate parent is not root-owned and protected')
    for item in (path, marker):
        info = item.stat(follow_symlinks=False)
        if info.st_uid != 0 or info.st_mode & 0o022 or not stat.S_ISREG(info.st_mode):
            raise RuntimeError('maintenance coordination is not root-owned and protected')
    ready = json.loads(marker.read_text())
    if ready.get('version') != 1 or sorted(ready.get('loops', [])) != sorted(LOOPS):
        raise RuntimeError('cooperative runner installation is incomplete')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        # Verify the opened inode as well as its pathname.
        info = os.fstat(fd)
        if info.st_uid != 0 or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
            raise RuntimeError('maintenance gate inode changed')
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('maintenance deferred: a runner holds the shared gate')
        yield
    finally:
        os.close(fd)


def package_manifest(policy):
    for key in ('deepsec', 'codex'):
        if not re.fullmatch(r'\d+\.\d+\.\d+', policy[key]):
            raise ValueError(f'{key} requires an exact stable version')
    return {'private': True, 'name': 'radon-deepsec-reviewed-toolchain', 'version': '1.0.0',
            'dependencies': {'deepsec': policy['deepsec']},
            'overrides': {'@openai/codex': policy['codex'], '@openai/codex-sdk': policy['codex']}}


def runners_idle(output):
    return re.search(r'^\s*state = not running\s*$', output, re.M) is not None


def require_idle():
    # Inspect service state, never delete or reclaim a runner lock.
    for loop in LOOPS:
        if not runners_idle(run(['/bin/launchctl', 'print', f'system/com.radon.runner.{loop}'])):
            raise RuntimeError('maintenance deferred: a nightly runner is active or unverifiable')
    # A detached reviewer can outlive a launchd service. Any matching process
    # makes maintenance unsafe, including other users whose cwd is unreadable.
    processes = run(['/bin/ps', '-axo', 'comm='])
    if any(re.search(r'(^|/)(codex|claude|deepsec|grok|agy)$', x.strip()) for x in processes.splitlines()):
        raise RuntimeError('maintenance deferred: an agent or reviewer is active')


def codex_update_command(brew):
    return [brew, 'upgrade', '--cask', 'codex']


def rollback(workspace, backup):
    for name in PACKAGE_PATHS:
        old, current = backup / name, workspace / name
        if old.exists():
            if current.is_dir(): shutil.rmtree(current)
            elif current.exists(): current.unlink()
            old.rename(current)


def promote(workspace, stage, recovery_root=None):
    recovery_root = recovery_root or workspace.parent
    recovery_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = recovery_root / f'{workspace.name}.rollback-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")}'
    backup.mkdir(mode=0o700)
    # The journal survives kills. A nonempty journal refuses the next update
    # until explicit rollback; no new release overwrites recovery evidence.
    journal = recovery_root / f'{workspace.name}.promotion.json'
    if journal.exists():
        raise RuntimeError('unfinished promotion: run rollback with its recorded backup')
    atomic_save(str(journal), {'backup': str(backup), 'workspace': str(workspace)})
    try:
        for name in PACKAGE_PATHS:
            if (workspace / name).exists(): (workspace / name).rename(backup / name)
            (stage / name).rename(workspace / name)
    except BaseException:
        rollback(workspace, backup)
        journal.unlink()
        raise
    journal.unlink()
    return backup


def require_subscription_status(status):
    if not re.search(r'^\s*logged in using chatgpt\s*$', status, re.I | re.M):
        raise RuntimeError('reviewer maintenance requires a ChatGPT subscription login')


def smoke(stage, policy, *, inference):
    for package, version in [('deepsec', policy['deepsec']), ('@openai/codex', policy['codex']), ('@openai/codex-sdk', policy['codex'])]:
        data = json.loads((stage / 'node_modules' / package / 'package.json').read_text())
        if data['version'] != version: raise RuntimeError('installed package differs from approved release')
    run([str(stage / 'node_modules/.bin/deepsec'), '--version'], cwd=stage)
    codex = str(stage / 'node_modules/.bin/codex')
    if policy['codex'] not in run([codex, '--version'], cwd=stage): raise RuntimeError('bundled Codex version mismatch')
    if inference:
        # Both login and SDK inference must work as the account that runs scans.
        require_subscription_status(run([codex, 'login', 'status'], cwd=stage, include_stderr=True))
        script = '''import {Codex} from '@openai/codex-sdk';
const sdk = new Codex({codexPathOverride:process.argv[1],config:{features:{plugins:false,remote_plugin:false}}});
const t = sdk.startThread({model:process.argv[2],sandboxMode:'read-only',approvalPolicy:'never',skipGitRepoCheck:true});
const result = await t.run('Reply exactly RADON_TOOLCHAIN_OK. Do not use tools.');
if(result.finalResponse.trim() !== 'RADON_TOOLCHAIN_OK')process.exit(1);
'''
        with tempfile.TemporaryDirectory(prefix='radon-reviewer-smoke-') as fixture:
            # Node resolves the SDK in stage, but the model's working directory
            # is an empty fixture, never the private state or operator repo.
            script = script.replace("skipGitRepoCheck:true", 'skipGitRepoCheck:true,workingDirectory:' + json.dumps(fixture))
            run(['/opt/homebrew/bin/node', '--input-type=module', '-e', script, codex, policy['smoke_model']], cwd=stage, timeout=180)


def resolved_policy(policy):
    candidate = dict(policy)
    for name, package in [('deepsec', 'deepsec'), ('codex', '@openai/codex')]:
        version = run(['/opt/homebrew/bin/npm', 'view', package, 'dist-tags.latest', '--registry=https://registry.npmjs.org'])
        if not re.fullmatch(r'\d+\.\d+\.\d+', version):
            raise RuntimeError('registry stable release is not an exact version')
        baseline = tuple(map(int, policy[name].split('.')))
        target = tuple(map(int, version.split('.')))
        if target < baseline or (name == 'deepsec' and target[0] != baseline[0]):
            raise RuntimeError('registry release is outside the approved upgrade family')
        candidate[name] = version
    # CLI and SDK must publish the same version before the pair can move.
    run(['/opt/homebrew/bin/npm', 'view', '@openai/codex-sdk@' + candidate['codex'], 'version', '--registry=https://registry.npmjs.org'])
    return candidate


def candidate_allowed(candidate, policy):
    if candidate == policy: return True
    if not policy.get('automatic_stable_updates'): return False
    for name in ('deepsec', 'codex'):
        if not re.fullmatch(r'\d+\.\d+\.\d+', candidate.get(name, '')): return False
        baseline = tuple(map(int, policy[name].split('.')))
        target = tuple(map(int, candidate[name].split('.')))
        if target < baseline or (name == 'deepsec' and target[0] != baseline[0]): return False
    return {k:v for k,v in candidate.items() if k not in ('deepsec','codex')} == {k:v for k,v in policy.items() if k not in ('deepsec','codex')}


def stage_release(root, policy, package_dir):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    stage = Path(tempfile.mkdtemp(prefix='candidate-', dir=root))
    if json.loads((package_dir / 'package.json').read_text()) != package_manifest(policy):
        raise RuntimeError('reviewed package manifest differs from policy')
    for name in ('package.json', 'package-lock.json'):
        shutil.copyfile(package_dir / name, stage / name)
    run(['/opt/homebrew/bin/npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], cwd=stage, timeout=600)
    smoke(stage, policy, inference=True)
    lock_sha = hashlib.sha256((stage / 'package-lock.json').read_bytes()).hexdigest()
    atomic_save(str(stage / 'verified.json'), {'policy': policy, 'lock_sha256': lock_sha, 'verified_at': datetime.now(timezone.utc).isoformat()})
    return stage


def require_owned_deepsec(workspace):
    home = Path.home().resolve()
    expected = home / 'radon-runner/work/security-deepsec/.deepsec'
    state = home / 'radon-runner/state/security-deepsec'
    if pwd.getpwuid(os.geteuid()).pw_name != '_radonbot' or workspace.resolve() != expected or Path(os.environ.get('RADON_RUNNER_LOOP_STATE', '/')).resolve() != state:
        raise RuntimeError('private reviewer maintenance requires its dedicated bot runner workspace')
    # The installed runner's per-loop lock owns this tree. Verify caller
    # lineage, without touching, reading or reclaiming the runner's lock.
    rows = run(['/bin/ps', '-axo', 'pid=,ppid=,uid=,comm=']).splitlines()
    processes = {}
    for row in rows:
        fields = row.strip().split(None, 3)
        if len(fields) == 4: processes[int(fields[0])] = (int(fields[1]), int(fields[2]), fields[3])
    ancestors = {os.getpid()}
    pid = os.getpid()
    while pid in processes and processes[pid][0] not in ancestors:
        pid = processes[pid][0]; ancestors.add(pid)
    service = run(['/bin/launchctl', 'print', 'system/com.radon.runner.security-deepsec'])
    match = re.search(r'^\s*pid = (\d+)\s*$', service, re.M)
    if not match or int(match[1]) not in ancestors or runners_idle(service):
        raise RuntimeError('private reviewer maintenance must descend from its live runner')
    for pid, (_, uid, command) in processes.items():
        if uid != os.geteuid() or pid in ancestors: continue
        if Path(command.split()[0]).name not in ('node', 'bun', 'codex', 'deepsec'): continue
        cwd = run(['/usr/sbin/lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn'])
        for line in cwd.splitlines():
            if line.startswith('n'):
                path = Path(line[1:]).resolve()
                if path == expected.parent or expected.parent in path.parents:
                    raise RuntimeError('private reviewer maintenance deferred: a reviewer is already active')
    return state


def ensure_deepsec(workspace, policy, package_dir):
    workspace = workspace.resolve()
    state = require_owned_deepsec(workspace)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / 'toolchain-maintenance.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('private reviewer maintenance is already running')
        require_owned_deepsec(workspace)
        recovery = state / 'toolchain-rollback'
        recovery.mkdir(mode=0o700, exist_ok=True)
        journal = recovery / f'{workspace.name}.promotion.json'
        if journal.exists():
            interrupted = verified_load(str(journal))
            backup = Path(interrupted['backup']).resolve()
            if interrupted['workspace'] != str(workspace) or backup.parent != recovery.resolve():
                raise RuntimeError('private reviewer promotion journal has an unexpected scope')
            rollback(workspace, backup); journal.unlink()
        # Start with the source-controlled recovery lock. A newer official
        # stable family may be staged under the same isolated policy.
        candidate = dict(policy)
        try: candidate = resolved_policy(policy) if policy.get('automatic_stable_updates') else dict(policy)
        except (RuntimeError, OSError, subprocess.TimeoutExpired): pass
        # Never downgrade a working newer pair after a catalog outage.
        try:
            observed = {k: json.loads((workspace / 'node_modules' / pkg / 'package.json').read_text())['version'] for k,pkg in [('deepsec','deepsec'), ('codex','@openai/codex')]}
            installed = dict(policy, **observed)
            if candidate_allowed(installed, policy):
                for key in ('deepsec','codex'):
                    if tuple(map(int, installed[key].split('.'))) > tuple(map(int, candidate[key].split('.'))):
                        candidate[key] = installed[key]
        except (OSError, KeyError, ValueError): pass
        try:
            smoke(workspace, candidate, inference=True)
            return {'status': 'reviewer-ready', 'versions': {k:candidate[k] for k in ('deepsec','codex')}}
        except (OSError, RuntimeError, ValueError): pass
        root = state / 'toolchain-candidates'
        root.mkdir(mode=0o700, exist_ok=True)
        if candidate == policy:
            stage = stage_release(root, candidate, package_dir)
        else:
            with tempfile.TemporaryDirectory(dir=root, prefix='resolved-') as directory:
                source = Path(directory)
                (source / 'package.json').write_text(json.dumps(package_manifest(candidate)))
                run(['/opt/homebrew/bin/npm', 'install', '--package-lock-only', '--ignore-scripts', '--no-audit', '--no-fund', '--registry=https://registry.npmjs.org'], cwd=source, timeout=600)
                stage = stage_release(root, candidate, source)
        require_owned_deepsec(workspace)
        backup = promote(workspace, stage, recovery)
        try: smoke(workspace, candidate, inference=True)
        except BaseException:
            rollback(workspace, backup); raise
        return {'status': 'reviewer-recovered', 'backup': str(backup), 'versions': {k:candidate[k] for k in ('deepsec','codex')}}


def tool_busy(tool, app=None, *, operator_native=False):
    # A managed Codex app-server is a different runtime from the shared cask.
    # Its permanent presence does not imply shared-CLI inference is running.
    for row in run(['/bin/ps', '-axo', 'uid=,comm=']).splitlines():
        fields = row.strip().split(None, 1)
        uid, command = (int(fields[0]), fields[1]) if len(fields) == 2 and fields[0].isdigit() else (os.geteuid(), row.strip())
        if app:
            if f'/Applications/{app}/Contents/' in command: return True
            continue
        if operator_native and uid != os.geteuid(): continue
        executable = command.split()[0] if command else ''
        if tool == 'codex' and ('/app-server-daemon/' in executable or '/Applications/ChatGPT.app/' in executable): continue
        if Path(executable).name == tool: return True
    if tool == 'codex':
        # The shared cask may be a fallback later in these active cycles.
        for loop in ('documentation','ci-performance','performance','testing','reliability'):
            try:
                if not runners_idle(run(['/bin/launchctl','print',f'system/com.radon.runner.{loop}'])): return True
            except (OSError, RuntimeError, subprocess.TimeoutExpired): return True
    return False


def update_desktop(policy):
    brew = '/opt/homebrew/bin/brew'
    installed = run([brew, 'list', '--cask']).splitlines()
    # Catalog refresh is safe while applications are open. Each installation
    # has its own activity gate; one busy tool cannot starve unrelated apps.
    run([brew, 'update'], timeout=600)
    results = []
    for cask, app in policy['desktop_casks'].items():
        if cask not in installed:
            results.append({'tool': cask, 'status': 'not-managed-by-homebrew'}); continue
        if tool_busy(cask, app):
            results.append({'tool': cask, 'status': 'deferred-running-tool'}); continue
        try:
            run([brew, 'upgrade', '--cask', cask], timeout=900)
            results.append({'tool': cask, 'status': 'updated-or-current'})
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            results.append({'tool': cask, 'status': 'update-failed', 'reason': str(exc)})
    return results


def update_native():
    result = []
    for relative, args in [('.local/bin/claude', ['update']), ('.grok/bin/grok', ['update', '--stable']), ('.local/bin/agy', ['update']), ('.local/bin/muse', ['--version'])]:
        binary = Path.home() / relative
        if not binary.exists(): continue
        if tool_busy(binary.name, operator_native=True):
            result.append({'tool': binary.name, 'status': 'deferred-running-tool'}); continue
        try:
            run((['/usr/bin/env', 'MUSE_UPDATE_INTERVAL_SECONDS=0'] if binary.name == 'muse' else []) + [str(binary), *args], timeout=900)
            result.append({'tool': binary.name, 'status': 'updated-or-current'})
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            result.append({'tool': binary.name, 'status': 'update-failed', 'reason': str(exc)})
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['stage', 'refresh', 'maintain', 'promote', 'rollback', 'desktop', 'operator', 'verify', 'ensure-deepsec', 'native'])
    p.add_argument('--policy', type=Path, default=Path(__file__).parent / 'runner/toolchain-policy.json')
    p.add_argument('--package-dir', type=Path, default=Path(__file__).parent / 'runner/toolchain')
    p.add_argument('--release-root', type=Path, default=Path.home() / '.local/share/radon-toolchain')
    p.add_argument('--workspace', type=Path)
    p.add_argument('--stage', type=Path)
    a = p.parse_args(argv)
    if a.action == 'native':
        account = pwd.getpwuid(os.geteuid())
        if account.pw_name != '_radonbot' or Path.home().resolve() != Path(account.pw_dir).resolve():
            raise RuntimeError('native bot maintenance requires the dedicated bot account and home')
    policy = json.loads(a.policy.read_text())
    a.release_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.ExitStack() as stack:
        lock = stack.enter_context((a.release_root / 'maintenance.lock').open('w'))
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('another maintenance process holds the lock')
        if a.action in ('maintain', 'promote', 'rollback', 'native'):
            stack.enter_context(maintenance_gate(policy))
        if a.action in ('desktop', 'operator', 'native'):
            result = [] if a.action == 'native' else update_desktop(policy)
            if a.action in ('operator', 'native'):
                result.extend(update_native())
        elif a.action == 'ensure-deepsec':
            if not a.workspace: raise ValueError('ensure-deepsec requires --workspace')
            result = ensure_deepsec(a.workspace, policy, a.package_dir)
        elif a.action in ('stage', 'refresh', 'maintain'):
            if a.action == 'stage':
                result = {'stage': str(stage_release(a.release_root, policy, a.package_dir))}
            else:
                if not policy.get('automatic_stable_updates'): raise RuntimeError('automatic stable updates are disabled')
                require_idle()
                candidate = resolved_policy(policy)
                with tempfile.TemporaryDirectory(dir=a.release_root, prefix='resolved-') as directory:
                    package_dir = Path(directory)
                    (package_dir / 'package.json').write_text(json.dumps(package_manifest(candidate)))
                    run(['/opt/homebrew/bin/npm', 'install', '--package-lock-only', '--ignore-scripts', '--no-audit', '--no-fund', '--registry=https://registry.npmjs.org'], cwd=package_dir, timeout=600)
                    stage = stage_release(a.release_root, candidate, package_dir)
                result = {'stage': str(stage), 'versions': {k:candidate[k] for k in ('deepsec','codex')}}
                if a.action == 'maintain':
                    if not a.workspace: raise ValueError('maintain requires --workspace')
                    require_idle()
                    backup = promote(a.workspace, stage)
                    try: smoke(a.workspace, candidate, inference=True)
                    except BaseException:
                        rollback(a.workspace, backup); raise
                    result.update(status='promoted', backup=str(backup))
        elif a.action == 'verify':
            installed = json.loads((a.workspace / 'package.json').read_text())
            candidate = dict(policy, deepsec=installed['dependencies']['deepsec'], codex=installed['overrides']['@openai/codex'])
            if not candidate_allowed(candidate, policy): raise RuntimeError('installed reviewer is outside approved upgrade family')
            smoke(a.workspace, candidate, inference=True); result = {'status': 'reviewer-smoke-passed'}
        elif a.action == 'rollback':
            require_idle(); rollback(a.workspace, a.stage)
            (a.workspace.parent / f'{a.workspace.name}.promotion.json').unlink(missing_ok=True)
            result = {'status': 'rolled-back'}
        else:
            require_idle()
            verified = verified_load(str(a.stage / 'verified.json'))
            if not candidate_allowed(verified['policy'], policy) or verified['lock_sha256'] != hashlib.sha256((a.stage / 'package-lock.json').read_bytes()).hexdigest():
                raise RuntimeError('candidate has changed since smoke verification')
            smoke(a.stage, verified['policy'], inference=True)
            backup = promote(a.workspace, a.stage)
            try: smoke(a.workspace, verified['policy'], inference=True)
            except BaseException:
                rollback(a.workspace, backup); raise
            result = {'status': 'promoted', 'backup': str(backup)}
        health = a.release_root / 'last-result.json'
        atomic_save(str(health), {'action': a.action, 'completed_at': datetime.now(timezone.utc).isoformat(), 'result': result})
        print(json.dumps(result))
        if isinstance(result, list) and any(x.get('status') == 'update-failed' for x in result): return 75
    return 0


if __name__ == '__main__':
    try: raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({'status': 'incomplete', 'reason': str(exc)})); raise SystemExit(75)
