#!/usr/bin/env python3
"""Collect a bounded performance snapshot as a dedicated unprivileged account.

Installed root-owned. The daemon accepts no arguments; ``read`` exposes only
validated JSON and ``provision`` is an administrator-only installation step.
Neither mode accepts a path, command, credential, or environment override.
"""
from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import fcntl
import grp
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import selectors
import stat
import subprocess
import sys
import time

ROOT_UID = 0
HELPER_USER = '_radonprobe'
READ_GROUP = '_radonprobe_read'
BASE = Path('/private/var/db/radon-perf-probe')
RUNNER = Path('/usr/local/radon-runner')
LOG_PATH = Path('/private/var/log/radon-perf-probe.log')
MAX_BYTES = 1_000_000
MAX_AGE = 15 * 60
SSH_SECONDS = 180


def _secure(path, owners, *, directory=False, mode=None, sticky=False, gid=None):
    info = path.lstat()
    allowed_writes = sticky and stat.S_ISDIR(info.st_mode) and info.st_mode & stat.S_ISVTX
    if (info.st_uid not in owners or stat.S_ISLNK(info.st_mode)
            or (not allowed_writes and info.st_mode & 0o022)
            or (directory and not stat.S_ISDIR(info.st_mode))
            or (not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))
            or (mode is not None and stat.S_IMODE(info.st_mode) != mode)
            or (gid is not None and info.st_gid != gid)):
        raise RuntimeError('snapshot path ownership or permissions are invalid')
    return info


def _base():
    # Resolve no path component supplied by the consuming agent. Sticky system
    # temporary ancestors are accepted only for the isolated unit-test tree.
    for parent in reversed(BASE.parents):
        _secure(parent, {0, ROOT_UID}, directory=True, sticky=True)
    _secure(BASE, {ROOT_UID}, directory=True, mode=0o755)


def _helper_uid():
    uid = pwd.getpwnam(HELPER_USER).pw_uid
    if uid == 0:
        raise RuntimeError('snapshot helper must be unprivileged')
    return uid


def _open_checked(path, owners, *, directory=False, mode=None, gid=None):
    prior = _secure(path, owners, directory=directory, mode=mode, gid=gid)
    flags = os.O_RDONLY | os.O_NOFOLLOW | (os.O_DIRECTORY if directory else 0)
    fd = os.open(path, flags)
    current = os.fstat(fd)
    if (current.st_dev, current.st_ino) != (prior.st_dev, prior.st_ino):
        os.close(fd)
        raise RuntimeError('snapshot path changed while opening')
    return fd


def _read(path, owners, *, mode=None, gid=None):
    fd = _open_checked(path, owners, mode=mode, gid=gid)
    with os.fdopen(fd, 'rb') as stream:
        payload = stream.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError('snapshot exceeds size limit')
    return payload


def validate(payload):
    if len(payload) > MAX_BYTES:
        raise ValueError('snapshot exceeds size limit')
    def invalid_constant(value):
        raise ValueError('snapshot contains non-finite numbers')
    report = json.loads(payload, parse_constant=invalid_constant)
    if (not isinstance(report, dict) or type(report.get('schema')) is not int or report['schema'] != 1
            # The canonical producer replaces its hostname with host metrics
            # when that section succeeds; failures retain the hostname.
            or not isinstance(report.get('host'), (str, dict)) or not report['host']
            or not isinstance(report.get('errors'), dict)):
        raise ValueError('snapshot schema is invalid')
    taken = datetime.strptime(report['taken_at'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - taken).total_seconds()
    if not -60 <= age <= MAX_AGE:
        raise ValueError('snapshot is stale or future-dated')
    return report


def _read_gid():
    return grp.getgrnam(READ_GROUP).gr_gid


def _public_fd(uid):
    _base()
    return _open_checked(BASE / 'public', {uid}, directory=True, mode=0o750, gid=_read_gid())


def _publish(payload, directory_fd):
    name = '.probe-' + secrets.token_hex(16)
    # Refuse pre-existing links or unusual output objects. All operations below
    # use the held directory inode, including cleanup and the atomic replace.
    try:
        prior = os.stat('prod-probe.json', dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        prior = None
    if prior and (not stat.S_ISREG(prior.st_mode) or prior.st_nlink != 1
                  or prior.st_uid != os.geteuid() or prior.st_gid != _read_gid()
                  or stat.S_IMODE(prior.st_mode) != 0o640):
        raise RuntimeError('snapshot output is not a protected regular file')
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fchown(stream.fileno(), -1, _read_gid())
            os.fchmod(stream.fileno(), 0o640)
            os.fsync(stream.fileno())
        os.replace(name, 'prod-probe.json', src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(name, dir_fd=directory_fd)


def ssh_output(argv):
    # No ssh config, agent socket, inherited tokens, stderr file, or proxy.
    # Stream under both byte and wall-time limits instead of capture_output.
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL,
                               env={'HOME': '/var/empty', 'PATH': '/usr/bin:/bin', 'LANG': 'C'})
    result = bytearray()
    deadline = time.monotonic() + SSH_SECONDS
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise RuntimeError('snapshot SSH deadline exceeded')
                chunk = os.read(process.stdout.fileno(), min(65536, MAX_BYTES + 1 - len(result)))
                if not chunk:
                    break
                result.extend(chunk)
                if len(result) > MAX_BYTES:
                    raise ValueError('snapshot exceeds size limit')
        if process.wait(timeout=max(0.001, deadline - time.monotonic())):
            raise RuntimeError('snapshot SSH failed')
        return bytes(result)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdout.close()


def collect():
    uid = _helper_uid()
    if os.geteuid() != uid:
        raise RuntimeError('snapshot collection requires its dedicated account')
    directory_fd = _public_fd(uid)
    try:
        private = BASE / 'private'
        _secure(private, {uid}, directory=True, mode=0o700)
        _secure(private / 'id_ed25519', {uid}, mode=0o600)
        _secure(private / 'known_hosts', {uid}, mode=0o600)
        config = json.loads(_read(BASE / 'config.json', {ROOT_UID}, mode=0o640, gid=_read_gid()))
        target = config.get('target', '')
        if not isinstance(target, str) or not re.fullmatch(r'[a-z_][a-z0-9_-]*@[A-Za-z0-9][A-Za-z0-9.:-]*', target):
            raise ValueError('snapshot target is invalid')
        options = ['IdentitiesOnly=yes', 'IdentityAgent=none', 'BatchMode=yes', 'StrictHostKeyChecking=yes',
                   'GlobalKnownHostsFile=/dev/null', 'UserKnownHostsFile=' + str(private / 'known_hosts'),
                   'UpdateHostKeys=no', 'ProxyCommand=none', 'ProxyJump=none', 'PermitLocalCommand=no',
                   'RemoteCommand=none', 'ControlMaster=no', 'ControlPath=none', 'ForwardAgent=no',
                   'ClearAllForwardings=yes', 'PasswordAuthentication=no', 'KbdInteractiveAuthentication=no',
                   'ConnectTimeout=20', 'ConnectionAttempts=1', 'ServerAliveInterval=15', 'ServerAliveCountMax=2']
        argv = ['/usr/bin/ssh', '-F', '/dev/null', '-i', str(private / 'id_ed25519')]
        for option in options:
            argv.extend(['-o', option])
        payload = ssh_output([*argv, '-T', target, 'probe'])
        report = validate(payload)
        _publish((json.dumps(report, allow_nan=False, sort_keys=True) + '\n').encode(), directory_fd)
    finally:
        os.close(directory_fd)


def read_snapshot():
    uid = _helper_uid()
    fd = _public_fd(uid)
    try:
        # The parent is a held, validated directory; openat prevents links and
        # validates the file again after open while an atomic refresh occurs.
        file_fd = os.open('prod-probe.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        with os.fdopen(file_fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o640 or info.st_gid != _read_gid()):
                raise RuntimeError('snapshot output permissions are invalid')
            payload = stream.read(MAX_BYTES + 1)
        return validate(payload)
    finally:
        os.close(fd)


def _provision_log(uid):
    for parent in reversed(LOG_PATH.parents):
        _secure(parent, {ROOT_UID}, directory=True)
    directory_fd = _open_checked(LOG_PATH.parent, {ROOT_UID}, directory=True)
    file_fd = None
    try:
        try:
            file_fd = os.open(LOG_PATH.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                              0o600, dir_fd=directory_fd)
        except FileExistsError:
            # Existing bytes and metadata are preserved. Refuse unsafe objects
            # before opening them, including FIFOs and extra hard links.
            file_fd = _open_checked(LOG_PATH, {uid}, mode=0o600, gid=_read_gid())
        else:
            os.fchown(file_fd, uid, _read_gid())
            os.fchmod(file_fd, 0o600)
            os.fsync(file_fd)
            os.fsync(directory_fd)
        held = os.fstat(file_fd)
        named = os.stat(LOG_PATH.name, dir_fd=directory_fd, follow_symlinks=False)
        if (not stat.S_ISREG(held.st_mode) or held.st_uid != uid or held.st_gid != _read_gid()
                or stat.S_IMODE(held.st_mode) != 0o600 or held.st_nlink != 1
                or (held.st_dev, held.st_ino) != (named.st_dev, named.st_ino)):
            raise RuntimeError('snapshot log ownership or permissions are invalid')
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(directory_fd)


def provision():
    if os.geteuid() != 0:
        raise RuntimeError('snapshot provisioning requires administrator installation')
    uid = _helper_uid()
    # Creation happens under root-only parents. Existing helper-owned paths
    # are verified, never chmod/chowned through a potentially changed link.
    for parent in reversed(BASE.parents):
        _secure(parent, {ROOT_UID}, directory=True)
    try:
        BASE.mkdir(mode=0o755)
    except FileExistsError:
        pass
    _base()
    for name, mode in [('private', 0o700), ('public', 0o750)]:
        path = BASE / name
        try:
            path.mkdir(mode=mode)
        except FileExistsError:
            _secure(path, {uid}, directory=True, mode=mode, gid=_read_gid())
        else:
            os.chown(path, uid, _read_gid(), follow_symlinks=False)
    env = _read(RUNNER / 'loops/performance.env', {ROOT_UID}).decode()
    targets = re.findall(r'^PERF_SSH_TARGET=(.+)$', env, re.M)
    if len(targets) != 1:
        raise ValueError('snapshot target configuration is missing')
    target = targets[0].strip()
    if not re.fullmatch(r'[a-z_][a-z0-9_-]*@[A-Za-z0-9][A-Za-z0-9.:-]*', target):
        raise ValueError('snapshot target is invalid')
    # launchd opens stdout/stderr as the daemon uid before Python starts.
    _provision_log(uid)
    name = BASE / ('.config-' + secrets.token_hex(16))
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump({'target': target}, stream)
            stream.flush()
            os.fchown(stream.fileno(), 0, _read_gid())
            os.fchmod(stream.fileno(), 0o640)
            os.fsync(stream.fileno())
        os.replace(name, BASE / 'config.json')
        fd = os.open(BASE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        name.unlink(missing_ok=True)


def main():
    args = sys.argv[1:]
    if args == ['read']:
        print(json.dumps(read_snapshot(), allow_nan=False))
    elif args == ['provision']:
        provision()
    elif not args:
        # Cooperates with the existing root update service; collection never
        # grants any caller an installer or root command surface.
        gate_fd = _open_checked(RUNNER / 'maintenance.lock', {ROOT_UID}, mode=0o644)
        try:
            fcntl.flock(gate_fd, fcntl.LOCK_SH)
            collect()
        finally:
            os.close(gate_fd)
    else:
        raise ValueError('snapshot helper accepts no collection arguments')


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('snapshot=unavailable', file=sys.stderr)
        raise SystemExit(75)
