"""Load reviewed, immutable model snapshots and verify every required asset."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import stat

MANIFEST = json.loads(Path(__file__).with_name('model-artifacts.json').read_text())
CHRONOS_REVISION = MANIFEST['chronos']['revision']


def _snapshot(kind):
    from huggingface_hub import snapshot_download
    record=MANIFEST[kind]
    cache=os.environ.get('FASTEMBED_CACHE_PATH') if kind=='embedding' else None
    folder=Path(snapshot_download(repo_id=record['repo'],revision=record['revision'],
                                  allow_patterns=list(record['files']),**({'cache_dir':cache} if cache else {})))
    for name,expected in record['files'].items():
        path=folder/name
        # HF snapshots may symlink to their content-addressed cache blobs.
        info=path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size!=expected['size']:
            raise ValueError('model artifact size mismatch')
        digest=hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):
                digest.update(chunk)
        if digest.hexdigest()!=expected['sha256']:
            raise ValueError('model artifact digest mismatch')
    return str(folder)


def embedding_snapshot():
    return _snapshot('embedding')


def chronos_snapshot():
    return _snapshot('chronos')
