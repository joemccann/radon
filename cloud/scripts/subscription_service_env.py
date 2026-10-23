#!/usr/bin/env python3
"""Publish the subscription keeper's private, bounded environment file.

The derived ``TURSO_AUTH_TOKEN`` is ``TURSO_SUBSCRIPTION_KEEPER_AUTH_TOKEN``
from the canonical env. Publication fails when that value is missing or equal
to the operator ``TURSO_AUTH_TOKEN``. Token values are never printed.
"""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import shlex
import stat
import sys

# Canonical inputs. The operator token is read only so it can be rejected
# when the keeper token is the same secret.
REQUIRED_SOURCE_KEYS = (
    "TURSO_DB_URL",
    "TURSO_AUTH_TOKEN",
    "TURSO_SUBSCRIPTION_KEEPER_AUTH_TOKEN",
    "PUSHOVER_USER",
    "PUSHOVER_TOKEN",
)
# Derived file. TURSO_AUTH_TOKEN here is the keeper token, not the operator token.
OUTPUT_KEYS = ("TURSO_DB_URL", "TURSO_AUTH_TOKEN", "PUSHOVER_USER", "PUSHOVER_TOKEN")
OPTIONAL_KEYS = ("CLAUDE_CONFIG_DIR", "CODEX_HOME")
KEEPER_TOKEN_KEY = "TURSO_SUBSCRIPTION_KEEPER_AUTH_TOKEN"
_READ_KEYS = REQUIRED_SOURCE_KEYS + OPTIONAL_KEYS
MAX_SOURCE_BYTES = 1_048_576


def _directory(path: Path, owner_uid: int) -> int:
    """Walk directory components without following links; return a pinned fd."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("environment directory must be an absolute canonical path")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            meta = os.fstat(fd)
            if meta.st_uid not in {0, owner_uid}:
                raise ValueError("environment directory owner is invalid")
            if meta.st_mode & 0o022 and not meta.st_mode & stat.S_ISVTX:
                raise ValueError("environment directory is writable by another account")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _payload(source: Path, owner_uid: int) -> bytes:
    parent = _directory(source.parent, owner_uid)
    try:
        fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != owner_uid or meta.st_mode & 0o022:
            raise ValueError("canonical environment ownership or mode is invalid")
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            raw = stream.read(MAX_SOURCE_BYTES + 1)
        if len(raw) > MAX_SOURCE_BYTES:
            raise ValueError("canonical environment exceeds the supported size")
    finally:
        if fd >= 0:
            os.close(fd)
    values = {}
    secrets = {}
    for line in raw.decode("utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key in _READ_KEYS:
            try:
                parsed = shlex.split(value.strip(), comments=False)
            except ValueError:
                raise ValueError(f"invalid service environment value: {key}") from None
            if not parsed or (len(parsed) == 1 and not parsed[0].strip()):
                if key in OPTIONAL_KEYS:
                    values.pop(key, None)
                    secrets.pop(key, None)
                    continue
                raise ValueError(f"empty service environment value: {key}")
            if len(parsed) != 1:
                raise ValueError(f"malformed service environment value: {key}")
            values[key] = value
            secrets[key] = parsed[0]
    missing = [key for key in REQUIRED_SOURCE_KEYS if key not in values]
    if missing:
        raise ValueError("missing service environment keys: " + ", ".join(missing))
    if secrets[KEEPER_TOKEN_KEY] == secrets["TURSO_AUTH_TOKEN"]:
        raise ValueError("subscription keeper token matches TURSO_AUTH_TOKEN")
    values["TURSO_AUTH_TOKEN"] = values[KEEPER_TOKEN_KEY]
    return (
        "\n".join(f"{key}={values[key]}" for key in OUTPUT_KEYS + OPTIONAL_KEYS if key in values) + "\n"
    ).encode()


def publish(source: Path, target: Path, *, owner_uid: int = 0) -> None:
    """Retain the previous complete file unless a complete replacement is ready."""
    source, target = Path(source), Path(target)
    data = _payload(source, owner_uid)
    parent = _directory(target.parent.parent, owner_uid)
    try:
        try:
            os.mkdir(target.parent.name, 0o700, dir_fd=parent)
        except FileExistsError:
            pass
        directory = os.open(target.parent.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    finally:
        os.close(parent)
    try:
        meta = os.fstat(directory)
        if meta.st_uid != owner_uid or meta.st_mode & 0o022:
            raise ValueError("private service directory ownership or mode is invalid")
        os.fchmod(directory, 0o700)
        try:
            existing = os.stat(target.name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and (not stat.S_ISREG(existing.st_mode) or existing.st_uid != owner_uid):
            raise ValueError("service environment destination is not a canonical file")
        temporary = f".{target.name}.{secrets.token_hex(12)}"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fchmod(stream.fileno(), 0o600)
                os.fsync(stream.fileno())
            os.rename(temporary, target.name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
    finally:
        os.close(directory)


def main(argv: list[str]) -> int:
    if os.geteuid() != 0 or len(argv) != 2:
        print("root and explicit service environment paths are required", file=sys.stderr)
        return 64
    try:
        publish(Path(argv[0]), Path(argv[1]))
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"service environment publication failed: {type(exc).__name__}", file=sys.stderr)
        return 78
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
