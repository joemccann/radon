"""Bounded first-gateway rollback. Reads only trusted Git blobs and fixed host paths.

The control-plane floor never moves backwards. This receipt authorizes only the
first three-image target to restore its observed, previously serving two-image
release before the old root helper performs ordinary recovery.
"""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{40}")
MANIFEST = '/var/lib/radon/control-plane-manifest.sha256'
READY = '/var/lib/radon/control-plane-ready'
PROVISION = Path('/opt/radon-provision')
RECEIPT = PROVISION / 'model-gateway-cutover'
COMMITTED_STATE = Path('/var/lib/radon-deploy')
GIT_ENV = { 'PATH':'/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM':'1', 'GIT_CONFIG_GLOBAL':'/dev/null', 'GIT_TERMINAL_PROMPT':'0' }
ALLOWLIST = {'/usr/local/sbin/radon-deploy-root': {'source': 'cloud/scripts/deploy-root-helper.sh', 'mode': 493}, '/usr/local/bin/radon-ib-gateway-control': {'source': 'cloud/scripts/ib-gateway-control.sh', 'mode': 493}, '/usr/local/sbin/radon-ib-hold': {'source': 'cloud/scripts/ib-operator-hold.sh', 'mode': 493}, '/usr/local/lib/radon/ib_operator_hold.py': {'source': 'scripts/utils/ib_operator_hold.py', 'mode': 420}, '/usr/local/bin/radon': {'source': 'cloud/scripts/operator-radon.sh', 'mode': 493}, '/usr/local/lib/radon/drift_audit.py': {'source': 'cloud/scripts/drift_audit.py', 'mode': 420}, '/usr/local/lib/radon/disk_cleanup.py': {'source': 'cloud/scripts/disk_cleanup.py', 'mode': 420}, '/usr/local/sbin/radon-app-runtime': {'source': 'cloud/scripts/radon-app-runtime.sh', 'mode': 493}, '/usr/local/sbin/radon-docker-gw': {'source': 'cloud/scripts/radon-docker-gw.sh', 'mode': 493}, '/usr/local/sbin/radon-ib-gateway-credential': {'source': 'cloud/scripts/radon-ib-gateway-credential.py', 'mode': 493}, '/etc/radon/ib-gateway-compose.yml': {'source': 'cloud/docker-compose.yml', 'mode': 420}, '/etc/sudoers.d/radon-deploy': {'source': 'cloud/config/sudoers.d/radon-deploy', 'mode': 288}, '/etc/sudoers.d/radon-monitor': {'source': 'cloud/config/sudoers.d/radon-monitor', 'mode': 288}, '/etc/sudoers.d/radon-ops': {'source': 'cloud/config/sudoers.d/radon-ops', 'mode': 288}, '/etc/sudoers.d/radon-caddy': {'source': 'cloud/config/sudoers.d/radon-caddy', 'mode': 288}, '/etc/polkit-1/rules.d/50-radon-services.rules': {'source': 'cloud/config/polkit/50-radon-services.rules', 'mode': 420}, '/etc/radon/seccomp/chromium.json': {'source': 'cloud/config/seccomp/chromium.json', 'mode': 420}, '/etc/systemd/system/radon-health.service': {'source': 'cloud/services/radon-health.service', 'mode': 420}, '/etc/systemd/system/radon-ib-gateway-preheld-restart.service': {'source': 'cloud/services/radon-ib-gateway-preheld-restart.service', 'mode': 420}, '/etc/systemd/system/radon-ib-watchdog.service': {'source': 'cloud/services/radon-ib-watchdog.service', 'mode': 420}, '/etc/systemd/system/radon-ib-watchdog.timer': {'source': 'cloud/services/radon-ib-watchdog.timer', 'mode': 420}, '/etc/systemd/system/radon-ib-gateway.service': {'source': 'cloud/services/radon-ib-gateway.service', 'mode': 420}, '/etc/systemd/system/radon-ib-gateway-remote.service': {'source': 'cloud/services/radon-ib-gateway-remote.service', 'mode': 420}, '/etc/systemd/system/radon-api.service': {'source': 'cloud/services/radon-api.service', 'mode': 420}, '/etc/systemd/system/radon-monitor.service': {'source': 'cloud/services/radon-monitor.service', 'mode': 420}, '/etc/systemd/system/radon-uw-stream.service': {'source': 'cloud/services/radon-uw-stream.service', 'mode': 420}, '/etc/systemd/system/radon-uw-research.service': {'source': 'cloud/services/radon-uw-research.service', 'mode': 420}, '/etc/systemd/system/radon-uw-research.timer': {'source': 'cloud/services/radon-uw-research.timer', 'mode': 420}, '/etc/systemd/system/radon-relay.service': {'source': 'cloud/services/radon-relay.service', 'mode': 420}, '/etc/systemd/system/radon-portfolio-sync.service': {'source': 'cloud/services/radon-portfolio-sync.service', 'mode': 420}, '/etc/systemd/system/radon-portfolio-sync.timer': {'source': 'cloud/services/radon-portfolio-sync.timer', 'mode': 420}, '/etc/systemd/system/radon-refresh.service': {'source': 'cloud/services/radon-refresh.service', 'mode': 420}, '/etc/systemd/system/radon-refresh.timer': {'source': 'cloud/services/radon-refresh.timer', 'mode': 420}, '/etc/systemd/system/radon-db-backup.service': {'source': 'cloud/services/radon-db-backup.service', 'mode': 420}, '/etc/systemd/system/radon-db-backup.timer': {'source': 'cloud/services/radon-db-backup.timer', 'mode': 420}, '/etc/systemd/system/radon-disk-cleanup.service': {'source': 'cloud/services/radon-disk-cleanup.service', 'mode': 420}, '/etc/systemd/system/radon-disk-cleanup.timer': {'source': 'cloud/services/radon-disk-cleanup.timer', 'mode': 420}, '/etc/systemd/system/radon-drift-audit.service': {'source': 'cloud/services/radon-drift-audit.service', 'mode': 420}, '/etc/systemd/system/radon-drift-audit.timer': {'source': 'cloud/services/radon-drift-audit.timer', 'mode': 420}, '/etc/systemd/system/radon-nextjs-db-watchdog.service': {'source': 'cloud/services/radon-nextjs-db-watchdog.service', 'mode': 420}, '/etc/systemd/system/radon-nextjs-db-watchdog.timer': {'source': 'cloud/services/radon-nextjs-db-watchdog.timer', 'mode': 420}, '/etc/systemd/system/radon-api.service.d/runtime-container.conf': {'source': 'cloud/services/radon-api.service.d/runtime-container.conf', 'mode': 420}, '/etc/systemd/system/radon-nextjs.service.d/runtime-container.conf': {'source': 'cloud/services/radon-nextjs.service.d/runtime-container.conf', 'mode': 420}, '/etc/systemd/system/radon-relay.service.d/runtime-container.conf': {'source': 'cloud/services/radon-relay.service.d/runtime-container.conf', 'mode': 420}, '/etc/systemd/system/radon-monitor.service.d/runtime-container.conf': {'source': 'cloud/services/radon-monitor.service.d/runtime-container.conf', 'mode': 420}, '/etc/systemd/system/radon-uw-stream.service.d/runtime-container.conf': {'source': 'cloud/services/radon-uw-stream.service.d/runtime-container.conf', 'mode': 420}, '/etc/systemd/system/radon-uw-research.service.d/runtime-container.conf': {'source': 'cloud/services/radon-uw-research.service.d/runtime-container.conf', 'mode': 420}, '/etc/systemd/system/radon-newsfeed.service.d/runtime-container.conf': {'source': 'cloud/services/radon-newsfeed.service.d/runtime-container.conf', 'mode': 420}, '/etc/systemd/system/radon-research.service': {'source': 'cloud/services/radon-research.service', 'mode': 420}, '/etc/systemd/system/radon-research.service.d/runtime-container.conf': {'source': 'cloud/services/radon-research.service.d/runtime-container.conf', 'mode': 420}, '/etc/systemd/system/radon-subscription-vault.service': {'source': 'cloud/services/radon-subscription-vault.service', 'mode': 420}, '/etc/systemd/system/radon-subscription-vault.service.d/runtime-container.conf': {'source': 'cloud/services/radon-subscription-vault.service.d/runtime-container.conf', 'mode': 420}, '/etc/systemd/system/radon-ai-cycle.service.d/runtime-container.conf': {'source': 'cloud/services/radon-ai-cycle.service.d/runtime-container.conf', 'mode': 420}, '/etc/systemd/system/radon-ai-cycle-backfill.service.d/runtime-container.conf': {'source': 'cloud/services/radon-ai-cycle-backfill.service.d/runtime-container.conf', 'mode': 420}, '/etc/systemd/system/radon-aa-frontier-refresh.service.d/runtime-container.conf': {'source': 'cloud/services/radon-aa-frontier-refresh.service.d/runtime-container.conf', 'mode': 420}, '/etc/systemd/system/radon-research-archive.service.d/runtime-container.conf': {'source': 'cloud/services/radon-research-archive.service.d/runtime-container.conf', 'mode': 420}, '/etc/systemd/system/radon-research-asset-archive.service.d/runtime-container.conf': {'source': 'cloud/services/radon-research-asset-archive.service.d/runtime-container.conf', 'mode': 420}, '/etc/systemd/system/radon-marketdesk.service.d/runtime-container.conf': {'source': 'cloud/services/radon-marketdesk.service.d/runtime-container.conf', 'mode': 420}, '/etc/systemd/system/radon-marketdesk-pdf.service.d/runtime-container.conf': {'source': 'cloud/services/radon-marketdesk-pdf.service.d/runtime-container.conf', 'mode': 420}, '/usr/local/lib/radon/subscription_service_env.py': {'source': 'cloud/scripts/subscription_service_env.py', 'mode': 420}, '/usr/local/lib/radon/media_publish.py': {'source': 'cloud/scripts/media_publish.py', 'mode': 420}, '/etc/systemd/system/radon-media-publish.service': {'source': 'cloud/services/radon-media-publish.service', 'mode': 420}, '/etc/systemd/system/radon-media-publish.path': {'source': 'cloud/services/radon-media-publish.path', 'mode': 420}, '/etc/systemd/system/radon-media-publish.timer': {'source': 'cloud/services/radon-media-publish.timer', 'mode': 420}, '/usr/lib/tmpfiles.d/radon-deploy.conf': {'source': 'cloud/config/tmpfiles.d/radon-deploy.conf', 'mode': 420}, '/etc/systemd/system/radon-model-gateway.service': {'source': 'cloud/services/radon-model-gateway.service', 'mode': 420}, '/etc/systemd/system/radon-model-gateway.service.d/runtime-container.conf': {'source': 'cloud/services/radon-model-gateway.service.d/runtime-container.conf', 'mode': 420}, '/etc/systemd/system/radon-subscription-tokens.service': {'source': 'cloud/services/radon-subscription-tokens.service', 'mode': 420}, '/usr/local/lib/radon/model_clients.py': {'source': 'cloud/scripts/model_clients.py', 'mode': 420}, '/etc/systemd/system/radon-.service.d/common.conf': {'source': 'cloud/services/radon-.service.d/common.conf', 'mode': 420}}
ALLOWLIST['/usr/local/lib/radon/model_gateway_rollback.py'] = {'source':'cloud/scripts/model_gateway_rollback.py','mode':0o644}
ALLOWLIST['/usr/local/lib/radon/model_transport.py'] = {'source':'cloud/scripts/model_transport.py','mode':0o644}
ALLOWLIST['/usr/local/lib/radon/mac_model_gateway.py'] = {'source':'cloud/scripts/mac_model_gateway.py','mode':0o644}
ALLOWLIST['/usr/local/lib/radon/model_pacing.py'] = {'source':'cloud/scripts/model_pacing.py','mode':0o644}

def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def regular(path, owner_uid):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != owner_uid or info.st_mode & 0o022:
        raise ValueError('unsafe rollback file')
    return path.read_bytes()


def trusted_dir(path, owner_uid):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner_uid or info.st_mode & 0o022:
        raise ValueError('unsafe rollback directory')


def git(repo, *args):
    # No shell, hooks, filters, application imports, system or operator config.
    return subprocess.check_output(['/usr/bin/git', '-C', str(repo), *args], env=GIT_ENV, timeout=20)


def blob(repo, sha, source):
    return git(repo, 'show', f'{sha}:{source}')


def _identities(repo, previous, target):
    if not SHA.fullmatch(previous) or not SHA.fullmatch(target) or previous == target:
        raise ValueError('invalid rollback identity')
    git(repo, 'merge-base', '--is-ancestor', previous, target)
    # Only a transition introducing this exact service can create a receipt.
    if not blob(repo, target, 'cloud/services/radon-model-gateway.service'):
        raise ValueError('target has no gateway')
    result = subprocess.run(['/usr/bin/git', '-C', str(repo), 'cat-file', '-e', f'{previous}:cloud/services/radon-model-gateway.service'], env=GIT_ENV, timeout=20, capture_output=True)
    if result.returncode == 0:
        raise ValueError('previous release already has a gateway')


def _parent_fd(root, logical, create=False, *, owner_uid=0):
    if logical not in ALLOWLIST and logical not in {MANIFEST, READY}:
        raise ValueError('target is outside rollback allowlist')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(root, flags)
    walked = ''
    def check(directory, logical_path):
        info = os.fstat(directory)
        owners = {owner_uid}
        # Existing control-plane state lives in this exact radon-owned0750
        # directory. It is the only non-root ancestor, and the pinned dirfd
        # makes atomic replacement independent of an attacker path exchange.
        if owner_uid == 0 and logical_path == '/var/lib/radon':
            owners.add(1000)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in owners or info.st_mode & 0o022:
            raise ValueError('unsafe rollback destination ancestor')
    try:
        check(fd, '')
        for part in Path(logical).parts[1:-1]:
            if create:
                try: os.mkdir(part, 0o755, dir_fd=fd)
                except FileExistsError: pass
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd); fd = child
            walked += '/' + part
            check(fd, walked)
        return fd, Path(logical).name
    except BaseException:
        os.close(fd); raise


def _atomic(root, logical, raw, mode, owner_uid):
    fd, name = _parent_fd(root, logical, create=True, owner_uid=owner_uid)
    temporary = '.gateway-rollback-' + os.urandom(12).hex()
    try:
        if raw is None:
            try: os.unlink(name, dir_fd=fd)
            except FileNotFoundError: pass
        else:
            out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=fd)
            try:
                with os.fdopen(out, 'wb', closefd=False) as stream:
                    stream.write(raw); stream.flush(); os.fsync(out)
                os.fchmod(out, mode)
                if os.geteuid() == 0: os.fchown(out, owner_uid, 0)
            finally: os.close(out)
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        try: os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError: pass
        os.close(fd)


def _config_mode_marker(receipt_fd, owner_uid):
    try: source = os.open('config-parent-mode.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=receipt_fd)
    except FileNotFoundError: return None
    try:
        info = os.fstat(source)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != owner_uid or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > 4096:
            raise ValueError('unsafe config mode marker')
        raw = os.read(source, 4097)
        if len(raw) > 4096: raise ValueError('oversized config mode marker')
        record = json.loads(raw)
        if not isinstance(record, dict): raise ValueError('invalid config mode marker')
        return record
    finally:
        os.close(source)


def _persist_config_mode(receipt_fd, record):
    name = '.config-parent-mode-' + os.urandom(12).hex()
    try:
        out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=receipt_fd)
        with os.fdopen(out, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write((json.dumps(record, sort_keys=True) + '\n').encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, 'config-parent-mode.json', src_dir_fd=receipt_fd, dst_dir_fd=receipt_fd)
        os.fsync(receipt_fd)
    finally:
        try: os.unlink(name, dir_fd=receipt_fd)
        except FileNotFoundError: pass


@contextmanager
def _protected_config_parent(root, receipt, owner_uid):
    """Temporarily seal only the fixed root-owned sticky config directory."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptors = []
    hardened = False
    try:
        receipt_fd = os.open(receipt, flags)
        descriptors.append(receipt_fd)
        receipt_info = os.fstat(receipt_fd)
        if receipt_info.st_uid != owner_uid or receipt_info.st_mode & 0o022:
            raise ValueError('unsafe config mode receipt')
        marker = _config_mode_marker(receipt_fd, owner_uid)
        fd = os.open(root, flags)
        descriptors.append(fd)
        for part in ('etc', 'radon'):
            info = os.fstat(fd)
            if info.st_uid != owner_uid or info.st_mode & 0o022:
                raise ValueError('unsafe rollback config ancestor')
            try: fd = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                if marker is not None: raise ValueError('recorded config directory is missing')
                yield
                return
            descriptors.append(fd)
        info = os.fstat(fd)
        mode = stat.S_IMODE(info.st_mode)
        if info.st_uid != owner_uid:
            raise ValueError('unsafe rollback config owner')
        if marker is not None or mode & 0o022:
            expected_gid = 1000 if owner_uid == 0 else os.getgid()
            expected = {'version': 1, 'path': '/etc/radon', 'dev': info.st_dev, 'ino': info.st_ino,
                        'uid': owner_uid, 'gid': expected_gid, 'prior_mode': 0o1770, 'sealed_mode': 0o1750}
            if info.st_gid != expected_gid or mode not in ({0o1770, 0o1750} if marker is not None else {0o1770}):
                raise ValueError('unsafe rollback config permissions')
            if marker is not None:
                if marker != expected or any(type(marker[key]) is not int for key in expected if key != 'path'):
                    raise ValueError('config mode marker identity differs')
            else:
                # This intent must reach stable storage before permissions
                # change: SIGKILL/power loss cannot run a Python finally block.
                _persist_config_mode(receipt_fd, expected)
            hardened = True
            os.fchmod(fd, 0o1750)
            os.fsync(fd)
        yield
    finally:
        try:
            if hardened:
                current = os.fstat(fd)
                if (current.st_dev, current.st_ino, current.st_uid, current.st_gid) != (info.st_dev, info.st_ino, info.st_uid, info.st_gid):
                    raise ValueError('rollback config identity changed')
                os.fchmod(fd, 0o1770)
                os.fsync(fd)
                os.unlink('config-parent-mode.json', dir_fd=receipt_fd)
                os.fsync(receipt_fd)
        finally:
            for descriptor in reversed(descriptors): os.close(descriptor)


def capture(root, repo, receipt, previous, target, *, owner_uid=0):
    root, repo, receipt = Path(root), Path(repo), Path(receipt)
    trusted_dir(root, owner_uid); trusted_dir(repo, owner_uid)
    _identities(repo, previous, target)
    manifest = regular(root / MANIFEST.lstrip('/'), owner_uid)
    ready = regular(root / READY.lstrip('/'), owner_uid)
    if ready != f'{digest(manifest)}  {MANIFEST}\n'.encode():
        raise ValueError('old readiness does not match old manifest')
    installed = {}
    for line in manifest.decode().splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (\S+) -> (/\S+)', line)
        if not match: raise ValueError('invalid old installed manifest')
        expected, source, logical = match.groups()
        if logical not in ALLOWLIST or logical in installed:
            raise ValueError('unrecognized or duplicate old artifact')
        rule = ALLOWLIST[logical]
        source = source.removeprefix('repo/') if source.startswith('repo/') else 'cloud/' + source
        if source != rule['source']: raise ValueError('old source target mismatch')
        raw = regular(root / logical.lstrip('/'), owner_uid)
        if digest(raw) != expected or blob(repo, previous, source) != raw:
            raise ValueError('installed artifact is not a trusted previous Git blob')
        installed[logical] = raw
    if not {'/usr/local/sbin/radon-app-runtime','/usr/local/sbin/radon-deploy-root'} <= installed.keys():
        raise ValueError('old control plane is incomplete')
    # Older manifests omit some canonical installed units. Preserve each
    # existing allowlisted artifact only with exact previous-Git and mode
    # proof; a genuinely absent artifact remains an absence receipt.
    for logical, rule in ALLOWLIST.items():
        if logical in installed: continue
        destination = root / logical.lstrip('/')
        if not destination.exists() and not destination.is_symlink(): continue
        raw = regular(destination, owner_uid)
        if stat.S_IMODE(destination.stat().st_mode) != rule['mode'] or raw != blob(repo, previous, rule['source']):
            raise ValueError('unmanifested old artifact is not a trusted previous Git blob')
        installed[logical] = raw
    installed.update({MANIFEST:manifest,READY:ready})
    if receipt.exists() or receipt.is_symlink():
        trusted_dir(receipt,owner_uid)
        pending_mode = receipt / 'config-parent-mode.json'
        if pending_mode.exists() or pending_mode.is_symlink():
            raise ValueError('config permission recovery is pending')
        prior=json.loads(regular(receipt/'receipt.json',owner_uid))
        if prior.get('version')!=1 or prior.get('previous_sha')!=previous or not isinstance(prior.get('target_sha'),str) or not SHA.fullmatch(prior['target_sha']):
            raise ValueError('existing receipt belongs to another cutover')
        expected=set(ALLOWLIST)|{MANIFEST,READY}
        seen=set()
        for row in prior.get('files',[]):
            logical=row.get('target')
            if logical not in expected or logical in seen:raise ValueError('invalid existing receipt')
            seen.add(logical)
            raw=installed.get(logical)
            if raw is None:
                destination=root/logical.lstrip('/')
                if row.get('blob') is not None or destination.exists() or destination.is_symlink():
                    raise ValueError('existing rollback absence proof changed')
            else:
                name=row.get('blob')
                if not isinstance(name,str) or not re.fullmatch(r'[0-9]+\.blob',name):raise ValueError('invalid existing receipt blob')
                if row.get('sha256')!=digest(raw) or regular(receipt/name,owner_uid)!=raw:
                    raise ValueError('existing rollback bytes changed')
        if seen!=expected:raise ValueError('incomplete existing cutover receipt')
        if prior['target_sha'] == target: return True
        # Capture's caller already proved the previous release is serving;
        # every installed byte above also matches the old receipt. Retain
        # evidence of that completed recovery before a new attempt begins.
        _identities(repo, previous, prior['target_sha'])
        archive_restored_receipt(receipt, prior['target_sha'], owner_uid=owner_uid)
    receipt.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    trusted_dir(receipt.parent, owner_uid)
    with tempfile.TemporaryDirectory(prefix='.model-cutover-', dir=receipt.parent) as stage_name:
        stage = Path(stage_name); stage.chmod(0o700)
        files = []
        for index,logical in enumerate([*ALLOWLIST,MANIFEST,READY]):
            raw=installed.get(logical)
            record={'target':logical,'mode':ALLOWLIST.get(logical,{'mode':0o644})['mode'],'sha256':None,'blob':None}
            if raw is not None:
                name=f'{index}.blob'; (stage/name).write_bytes(raw); (stage/name).chmod(0o400)
                record.update(sha256=digest(raw),blob=name)
            files.append(record)
        (stage/'receipt.json').write_text(json.dumps({'version':1,'previous_sha':previous,'target_sha':target,'files':files},sort_keys=True)+'\n')
        (stage/'receipt.json').chmod(0o400)
        for path in stage.iterdir():
            with path.open('rb') as stream: os.fsync(stream.fileno())
        os.rename(stage,receipt)
        # TemporaryDirectory tolerates its original path disappearing.
    return True


def restore(root, repo, receipt, previous, target, *, owner_uid=0):
    root,repo,receipt=Path(root),Path(repo),Path(receipt)
    trusted_dir(root,owner_uid);trusted_dir(repo,owner_uid);trusted_dir(receipt,owner_uid)
    data=json.loads(regular(receipt/'receipt.json',owner_uid))
    if data.get('version') != 1 or data.get('previous_sha') != previous or data.get('target_sha') != target:
        raise ValueError('receipt is not for this failed cutover')
    _identities(repo,previous,target)
    expected=set(ALLOWLIST)|{MANIFEST,READY}; observed=set(); prepared=[]
    for row in data['files']:
        logical=row['target']
        if logical not in expected or logical in observed: raise ValueError('invalid rollback target')
        observed.add(logical)
        mode=ALLOWLIST.get(logical,{'mode':0o644})['mode']
        if row['mode'] != mode: raise ValueError('invalid rollback mode')
        raw=None
        if row['blob'] is not None:
            if not re.fullmatch(r'[0-9]+\.blob',row['blob']):raise ValueError('invalid rollback blob')
            raw=regular(receipt/row['blob'],owner_uid)
            if digest(raw) != row['sha256']:raise ValueError('rollback hash mismatch')
            if logical in ALLOWLIST and raw != blob(repo,previous,ALLOWLIST[logical]['source']):
                raise ValueError('rollback blob differs from trusted Git')
        prepared.append((logical,raw,mode))
    if observed != expected:raise ValueError('incomplete rollback receipt')
    with _protected_config_parent(root, receipt, owner_uid):
        # Validate all destination ancestors before replacing any artifact.
        for logical,_,_ in prepared:
            try:
                fd,_ = _parent_fd(root,logical,owner_uid=owner_uid)
            except FileNotFoundError:
                continue
            else:
                os.close(fd)
        # Readiness is published last.
        for logical,raw,mode in sorted(prepared,key=lambda item:item[0]==READY):
            _atomic(root,logical,raw,mode,owner_uid)
    return True


def serving_sha():
    found=set()
    for engine in ('/usr/bin/podman','/usr/bin/docker'):
        if not Path(engine).is_file(): continue
        for unit,repo in [('radon-api.service','radon-python'),('radon-nextjs.service','radon-node')]:
            result=subprocess.run([engine,'inspect','--format','{{.Config.Image}}',unit],env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True,timeout=15)
            if result.returncode:continue
            match=re.fullmatch(r'ghcr\.io/joemccann/'+repo+r':([0-9a-f]{40})',result.stdout.strip())
            if not match:raise ValueError('serving image is not immutable')
            found.add((unit,match.group(1)))
    shas={sha for _,sha in found}
    if len(shas)!=1 or {unit for unit,_ in found}!={'radon-api.service','radon-nextjs.service'}:
        raise ValueError('cannot prove the previous two-image release')
    return shas.pop()


def archive_restored_receipt(receipt, target, *, owner_uid=0):
    """Preserve completed attempt evidence without replacing another receipt."""
    receipt = Path(receipt)
    if not SHA.fullmatch(target): raise ValueError('invalid archived target')
    trusted_dir(receipt.parent, owner_uid)
    trusted_dir(receipt, owner_uid)
    pending_mode = receipt / 'config-parent-mode.json'
    if pending_mode.exists() or pending_mode.is_symlink():
        raise ValueError('config permission recovery is pending')
    data = json.loads(regular(receipt / 'receipt.json', owner_uid))
    if data.get('target_sha') != target: raise ValueError('archive target differs')
    fd = os.open(receipt.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        # mkdtemp reserves a new root-private name. Rename replaces only that
        # empty reservation, never an existing audit or pending receipt.
        archive = Path(tempfile.mkdtemp(prefix=receipt.name + '.restored.' + target + '.', dir=receipt.parent))
        try:
            trusted_dir(archive, owner_uid)
            os.rename(receipt.name, archive.name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        except BaseException:
            try: archive.rmdir()
            except OSError: pass
            raise
        return archive
    finally:
        os.close(fd)


def main(args):
    if os.geteuid()!=0 or args not in (['capture'],['restore']):return 64
    repo=PROVISION/'radon.git'
    try:
        trusted_dir(PROVISION,0);trusted_dir(repo,0)
        if args==['capture']:
            if RECEIPT.exists() or RECEIPT.is_symlink():
                trusted_dir(RECEIPT,0)
                pending_mode=RECEIPT/'config-parent-mode.json'
                if pending_mode.exists() or pending_mode.is_symlink():
                    raise ValueError('config permission recovery is pending')
            if Path('/etc/systemd/system/radon-model-gateway.service').exists():return 0
            if not Path(MANIFEST).exists():return 0 # fresh host has nothing to roll back
            target=git(repo,'rev-parse','refs/remotes/pinned/main').decode().strip()
            # This program was staged by the old helper from the root-owned
            # fetched main commit. Reject any different bootstrap bytes.
            if Path(__file__).read_bytes()!=blob(repo,target,'cloud/scripts/model_gateway_rollback.py'):
                raise ValueError('capture utility is not the trusted target')
            capture(Path('/'),repo,RECEIPT,serving_sha(),target)
            print('first gateway cutover rollback receipt captured')
        else:
            if not RECEIPT.exists():return 3
            data=json.loads(regular(RECEIPT/'receipt.json',0))
            floor=regular(PROVISION/'control-plane-floor',0).decode().strip()
            if not SHA.fullmatch(floor): raise ValueError('invalid control-plane floor')
            trusted_dir(COMMITTED_STATE,0)
            committed=COMMITTED_STATE/'committed-release'
            if committed.exists() or committed.is_symlink():
                if not SHA.fullmatch(regular(committed,0).decode().strip()):
                    raise ValueError('invalid committed release')
                return 3
            target=git(repo,'rev-parse','refs/remotes/pinned/main').decode().strip()
            if not SHA.fullmatch(target): raise ValueError('invalid pinned target')
            # This checkout belongs to the application account. Read only its
            # HEAD as that UID; all executable rollback blobs still come from
            # the separately validated root-owned provision repository.
            previous=subprocess.check_output(['/usr/bin/setpriv','--reuid=1000','--regid=1000','--clear-groups','/usr/bin/git','-C','/home/radon/radon','rev-parse','HEAD'],env=GIT_ENV,timeout=10).decode().strip()
            if data.get('previous_sha')!=previous or data.get('target_sha')!=target:return 3
            restore(Path('/'),repo,RECEIPT,previous,target)
            print('trusted pre-gateway control plane restored; floor preserved')
        return 0
    except Exception as exc:
        print('model gateway cutover rollback refused: '+type(exc).__name__,file=sys.stderr)
        return 78

if __name__=='__main__':raise SystemExit(main(sys.argv[1:]))
