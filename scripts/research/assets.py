"""Content-addressed subscriber assets, served only through authenticated routes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

ASSET_RE = re.compile(r"[a-f0-9]{64}\.(?:png|pdf|json)\Z")
URL_PREFIX = "/api/newsfeed/research/files/"
MAX_BYTES = {"json": 20 * 1024 * 1024, "png": 20 * 1024 * 1024, "pdf": 100 * 1024 * 1024}


def assets_dir() -> Path:
    return Path(os.environ.get("RADON_RESEARCH_DIR", "/var/lib/radon/research")) / "assets"


def _read_fd(fd: int, extension: str) -> bytes:
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES[extension]:
            raise ValueError("invalid asset size or type")
        data = stream.read(MAX_BYTES[extension] + 1)
    if len(data) > MAX_BYTES[extension]:
        raise ValueError("asset exceeds size limit")
    if extension == "json":
        from research.manifest import validate_manifest
        validate_manifest(json.loads(data))
        return data
    signature = b"\x89PNG\r\n\x1a\n" if extension == "png" else b"%PDF-"
    if not data.startswith(signature):
        raise ValueError("asset format does not match extension")
    return data


def read_asset(name: str) -> bytes:
    """Open relative to a pinned directory fd, never following a symlink."""
    if not ASSET_RE.fullmatch(name):
        raise ValueError("invalid asset name")
    directory_fd = os.open(assets_dir(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        data = _read_fd(fd, name.rsplit(".", 1)[1])
    finally:
        os.close(directory_fd)
    if hashlib.sha256(data).hexdigest() != name.split(".")[0]:
        raise ValueError("asset digest mismatch")
    return data


def store_asset(path: str | Path) -> str:
    path = Path(path)
    extension = path.suffix.lower().lstrip(".")
    if extension not in MAX_BYTES:
        raise ValueError("only PNG, PDF and JSON research assets are supported")
    data = _read_fd(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), extension)
    name = hashlib.sha256(data).hexdigest() + "." + extension
    root = assets_dir()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("invalid asset directory")
    os.chmod(root, 0o700)
    target = root / name
    if target.exists() or target.is_symlink():
        read_asset(name)
        return URL_PREFIX + name
    fd, temporary = tempfile.mkstemp(prefix=".asset-", dir=root)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        dir_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return URL_PREFIX + name
