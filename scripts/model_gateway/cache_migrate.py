"""Copy only CI-pinned model bytes, executed inside the gateway image as UID1000."""
from __future__ import annotations
import hashlib
import fcntl
import math
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path
MANIFEST=Path('/home/radon/radon/scripts/model_gateway/model-artifacts.json')

def _directory(path, *, read_only_source=False):
    info=path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or (not read_only_source and info.st_mode & 0o022):
        raise ValueError('model cache directory is not private')

def _private_parent(path):
    if path.exists() or path.is_symlink():
        _directory(path);return
    _private_parent(path.parent)
    path.mkdir(mode=0o700)
    _directory(path)

def _verified(path,expected):
    if path.stat().st_size!=expected['size']:raise ValueError('model artifact size mismatch')
    h=hashlib.sha256()
    with path.open('rb') as source:
        while raw:=source.read(1048576):h.update(raw)
    if h.hexdigest()!=expected['sha256']:raise ValueError('model artifact digest mismatch')

def migrate(source,destination,revision,files):
    source,destination=Path(source),Path(destination)
    if not re.fullmatch(r'[0-9a-f]{40}',revision):raise ValueError('model revision is not immutable')
    # The legacy HF model tree is historically0775. It is mounted read-only;
    # byte size+CI-pinned digest, checked again after copy, is the trust proof.
    _directory(source,read_only_source=True)
    snapshot=source/'snapshots'/revision
    if snapshot.is_symlink() or not snapshot.is_dir():raise ValueError('model snapshot is unsafe')
    source_root=source.resolve(strict=True)
    selected=[]
    for name,expected in files.items():
        if not re.fullmatch(r'[a-zA-Z0-9_.-]+',name) or name.startswith('.'):
            raise ValueError('model artifact path is not allowed')
        path=(snapshot/name).resolve(strict=True)
        if not path.is_relative_to(source_root) or not path.is_file():raise ValueError('model artifact escapes source')
        _verified(path,expected);selected.append((name,path,expected))
    if not selected:raise ValueError('empty model manifest')
    _private_parent(destination)
    snapshots=destination/'snapshots';_private_parent(snapshots)
    final=snapshots/revision
    if final.exists() or final.is_symlink():
        _directory(final)
        for name,_,expected in selected:
            if (final/name).is_symlink():raise ValueError('migrated model has unsafe link')
            _verified(final/name,expected)
    else:
        started=time.monotonic()
        with tempfile.TemporaryDirectory(prefix='.migrate-',dir=snapshots) as stage_name:
            stage=Path(stage_name)
            for name,path,expected in selected:
                if time.monotonic()-started>120:raise TimeoutError('model migration deadline exceeded')
                target=stage/name
                with path.open('rb') as reader,target.open('xb') as writer:
                    shutil.copyfileobj(reader,writer,1048576);writer.flush();os.fsync(writer.fileno())
                target.chmod(0o600);_verified(target,expected)
            os.rename(stage,final)
    refs=destination/'refs';_private_parent(refs)
    if (refs/'main').is_symlink():raise ValueError('unsafe model reference')
    with tempfile.NamedTemporaryFile(mode='w',prefix='.ref-',dir=refs,delete=False) as stream:
        temporary=Path(stream.name);stream.write(revision);stream.flush();os.fsync(stream.fileno())
    temporary.chmod(0o600);os.replace(temporary,refs/'main')
    return {'revision':revision,'files':files}

_PACING_TIMES = frozenset({'next_at', 'cooldown_until', 'tripped_until', 'last_429_at'})
_PACING_KEYS = _PACING_TIMES | {'consecutive_429', 'last_auth_failure'}
# Fixed root-captured aliases; no provider HOME directory is admitted.
_PACING_NAMES = ('host.json', 'managed-unit.json', *(unit + '.json' for unit in (
    'radon-api.service', 'radon-research.service', 'radon-research-archive.service',
    'radon-marketdesk.service', 'radon-marketdesk-pdf.service', 'radon-monitor.service',
    'radon-uw-research.service', 'radon-ai-cycle.service', 'radon-ai-cycle-backfill.service',
    'radon-aa-frontier-refresh.service', 'radon-newsfeed.service', 'radon-nextjs.service',
    'radon-relay.service', 'radon-model-gateway.service',
    'radon-subscription-vault.service', 'radon-uw-stream.service')))


def _timestamp(value):
    try:
        return type(value) in (int, float) and value >= 0 and math.isfinite(value)
    except OverflowError:
        return False


def _pacing_record(value):
    if not isinstance(value, dict) or set(value) - _PACING_KEYS:
        raise ValueError('unrecognized pacing state')
    for key, item in value.items():
        if key in _PACING_TIMES and not _timestamp(item):
            raise ValueError('invalid pacing timestamp')
        if key == 'consecutive_429' and (type(item) is not int or item < 0):  # gitleaks:allow -- public numeric pacing field, not a credential
            raise ValueError('invalid pacing counter')
        if key == 'last_auth_failure' and (not isinstance(item, dict) or set(item) != {'at', 'status'} or not _timestamp(item['at']) or type(item['status']) is not int or item['status'] not in {401, 403}):
            raise ValueError('invalid pacing authorization state')
    return value


def _read_pacing(path, *, destination=False, marker=False):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(descriptor)
        owners = {os.getuid()} if destination else {0, os.getuid()}
        if not stat.S_ISREG(info.st_mode) or info.st_uid not in owners or info.st_mode & 0o022 or info.st_size > (128 if marker else 65536):
            raise ValueError('unsafe pacing state file')
        raw = os.read(descriptor, (128 if marker else 65536) + 1)
        if len(raw) > (128 if marker else 65536):
            raise ValueError('pacing state exceeds bounds')
    finally:
        os.close(descriptor)
    if marker:
        value = float(raw)
        if not _timestamp(value):
            raise ValueError('invalid pacing trip marker')
        return value
    return _pacing_record(json.loads(raw))


def _merge_pacing(current, incoming):
    for key, value in incoming.items():
        if key == 'last_auth_failure':
            previous = current.get(key)
            if previous is None or value['at'] > previous['at']:
                current[key] = dict(value)
        else:
            current[key] = max(current.get(key, 0), value)


def _pacing_write(path, raw):
    descriptor, name = tempfile.mkstemp(prefix='.pacing-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fchmod(stream.fileno(), 0o600); os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _pacing_receipts(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {}
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022 or info.st_size > 65536:
            raise ValueError('unsafe pacing import receipt')
        raw = os.read(descriptor, 65537)
        if len(raw) > 65536: raise ValueError('pacing receipt exceeds bounds')
    finally: os.close(descriptor)
    value = json.loads(raw)
    if not isinstance(value, dict) or len(value) > 32 or any(not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', name) or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest) for name, digest in value.items()):
        raise ValueError('invalid pacing import receipt')
    return value


def migrate_pacing(sources, destination):
    """Conservatively merge only numeric account clocks under the live limiter lock."""
    if len(sources) > 32:
        raise ValueError('too many pacing sources')
    destination = Path(destination)
    collected = []; source_count = 0; source_names = set()
    for source in map(Path, sources):
        record = _read_pacing(source)
        trip = _read_pacing(Path(str(source) + '.tripped'), marker=True)
        if record is not None or trip is not None:
            if source.name in source_names or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', source.name):
                raise ValueError('ambiguous pacing source identity')
            source_names.add(source.name)
            value = dict(record or {})
            if trip is not None: value['tripped_until'] = max(value.get('tripped_until', 0), trip)
            digest = hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()
            collected.append((source.name, digest, value))
    if not collected and not destination.exists() and not destination.is_symlink() and not Path(str(destination) + '.tripped').exists():
        return {'source_count': 0, 'preserved_deadline': 0}
    _private_parent(destination.parent)
    descriptor = os.open(str(destination) + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError('unsafe pacing state lock')
        os.fchmod(descriptor, 0o600)
        deadline = time.monotonic() + .25
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB); break
            except BlockingIOError:
                if time.monotonic() >= deadline: raise TimeoutError('pacing migration lock unavailable') from None
                time.sleep(.01)
        receipt_path = destination.with_name(destination.name + '.imports.json')
        receipts = _pacing_receipts(receipt_path)
        existing = _read_pacing(destination, destination=True)
        if receipts and existing is None:
            raise ValueError('pacing state is missing for an existing import receipt')
        current = dict(existing or {})
        trip = _read_pacing(Path(str(destination) + '.tripped'), destination=True, marker=True)
        if trip is not None: current['tripped_until'] = max(current.get('tripped_until', 0), trip)
        for name, digest, incoming in collected:
            if receipts.get(name) != digest:
                _merge_pacing(current, incoming)
                receipts[name] = digest
                source_count += 1
        _pacing_record(current)
        _pacing_write(destination, (json.dumps(current, sort_keys=True, allow_nan=False) + '\n').encode())
        if 'tripped_until' in current:
            _pacing_write(Path(str(destination) + '.tripped'), (str(current['tripped_until']) + '\n').encode())
        # Mark a snapshot imported only after its stronger account state is durable.
        _pacing_write(receipt_path, (json.dumps(receipts, sort_keys=True) + '\n').encode())
        return {'source_count': source_count, 'preserved_deadline': max((current.get(key, 0) for key in ('next_at', 'cooldown_until', 'tripped_until')), default=0)}
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN); os.close(descriptor)


def main():
    if os.geteuid()==0:raise ValueError('model migration must run unprivileged')
    manifest=json.loads(MANIFEST.read_text())
    configs={
      'chronos':('/source/chronos','/cache/huggingface/hub/models--amazon--chronos-2'),
      'embedding':('/source/embedding','/cache/fastembed/models--qdrant--bge-small-en-v1.5-onnx-q'),
    }
    proofs={}
    for key,(source,destination) in configs.items():
        if Path(source).exists():
            entry=manifest[key]
            proofs[key]=migrate(source,destination,entry['revision'],entry['files'])
    pacing_root = Path('/source/nvidia-pacing')
    if pacing_root.exists() or pacing_root.is_symlink():
        _directory(pacing_root, read_only_source=True)
    pacing = migrate_pacing([pacing_root / name for name in _PACING_NAMES], Path('/cache/nvidia-rate.json'))
    print(json.dumps({'verified_models':sorted(proofs),'verified_file_count':sum(len(row['files']) for row in proofs.values()),'pacing_sources':pacing['source_count'],'pacing_preserved_deadline':pacing['preserved_deadline']},sort_keys=True))
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:
        print('model cache migration refused: '+type(exc).__name__,file=sys.stderr)
        raise SystemExit(78)
