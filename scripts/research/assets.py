"""Content-addressed subscriber assets, served only through authenticated routes.

``research.asset_archive`` copies every asset to B2 and prunes cold local
copies, so ``read_asset`` reads a local miss back from
``<research prefix>assets/<name>`` (bounded, SHA-256 verified) and re-caches
it when the research directory is writable. Every reader goes through it.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time

ASSET_RE = re.compile(r"[a-f0-9]{64}\.(?:png|pdf|json)\Z")
URL_PREFIX = "/api/newsfeed/research/files/"
MAX_BYTES = {"json": 20 * 1024 * 1024, "png": 20 * 1024 * 1024, "pdf": 100 * 1024 * 1024}
#: Read-through bounds. The web proxy (radonFetchResponse) gives the API 30s,
#: so connect plus the whole transfer must finish inside it.
READ_THROUGH_CONNECT_TIMEOUT = 5
READ_THROUGH_READ_TIMEOUT = 10
READ_THROUGH_DEADLINE_SECS = 20
READ_THROUGH_CHUNK = 1024 * 1024
_CLIENTS: dict[tuple, object] = {}
_CLIENTS_LOCK = threading.Lock()


def assets_dir() -> Path:
    return Path(os.environ.get("RADON_RESEARCH_DIR", "/var/lib/radon/research")) / "assets"


def _read_fd(fd: int, extension: str) -> bytes:
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES[extension]:
            raise ValueError("invalid asset size or type")
        data = stream.read(MAX_BYTES[extension] + 1)
    return _validate(data, extension)


def _validate(data: bytes, extension: str) -> bytes:
    if not 0 < len(data) <= MAX_BYTES[extension]:
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
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        except FileNotFoundError:
            fd = None
        data = _read_through(name) if fd is None else _read_fd(fd, name.rsplit(".", 1)[1])
    finally:
        os.close(directory_fd)
    if hashlib.sha256(data).hexdigest() != name.split(".")[0]:
        raise ValueError("asset digest mismatch")
    if fd is None:
        _recache(name, data)
    return data


def _archive_client(cfg: dict):
    """One bounded S3 client per credential set, shared across API threads."""
    key = (cfg["endpoint_url"], cfg["bucket"], cfg["access_key"], cfg["region"])
    with _CLIENTS_LOCK:
        if key not in _CLIENTS:
            from research.archive import _s3_client
            _CLIENTS[key] = _s3_client(cfg, connect_timeout=READ_THROUGH_CONNECT_TIMEOUT,
                                       read_timeout=READ_THROUGH_READ_TIMEOUT, max_attempts=2)
        return _CLIENTS[key]


def archive_key(cfg: dict, name: str) -> str:
    """The B2 object key ``research.asset_archive`` uploads ``name`` to."""
    return f"{cfg['prefix']}assets/{name}"


def _read_through(name: str) -> bytes:
    """Fetch a pruned asset from B2. Absent or unconfigured is a not-found."""
    from research.archive import _is_not_found, s3_config_from_env

    cfg = s3_config_from_env()
    if cfg is None:
        raise FileNotFoundError(name)
    extension = name.rsplit(".", 1)[1]
    started = time.monotonic()
    try:
        response = _archive_client(cfg).get_object(Bucket=cfg["bucket"], Key=archive_key(cfg, name))
    except Exception as exc:  # noqa: BLE001 - classify, then surface as OSError
        if _is_not_found(exc):
            raise FileNotFoundError(name) from None
        raise OSError(f"research asset archive unavailable: {type(exc).__name__}") from None
    body = response["Body"]
    try:
        length = int(response.get("ContentLength", -1))
        if not 0 < length <= MAX_BYTES[extension]:
            raise ValueError("archived asset size invalid")
        chunks, received = [], 0
        while received < length:
            if time.monotonic() - started > READ_THROUGH_DEADLINE_SECS:
                raise TimeoutError("research asset archive read exceeded its deadline")
            block = body.read(min(READ_THROUGH_CHUNK, length - received))
            if not block:
                break
            chunks.append(block)
            received += len(block)
    finally:
        body.close()
    data = b"".join(chunks)
    if len(data) != length:
        raise OSError("archived asset truncated")
    return _validate(data, extension)


def _recache(name: str, data: bytes) -> None:
    """Best effort: the API's research bind is read-only, the jobs' is not."""
    try:
        _write(assets_dir(), name, data)
    except OSError:
        pass


def _write(root: Path, name: str, data: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".asset-", dir=root)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, root / name)
        dir_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


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
    _write(root, name, data)
    return URL_PREFIX + name
