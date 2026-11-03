#!/usr/bin/env python3
"""Hash the files that become the gateway image's exported model clients.

A non-zero exit is fail-closed: the workflow still builds, but omits the
payload label, so deploy exports instead of reusing a receipt.
"""
from __future__ import annotations

import hashlib
import stat
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Paths COPY'd into /opt/radon-clis, /opt/radon-darwin, /usr/local/bin/node
# or the four exported model_gateway helpers. A new COPY into those trees
# must be added here; scripts/tests/test_cip024_model_client_payload.py
# walks Dockerfile.python and fails otherwise. Dockerfile.python itself is
# hashed because the exported `node` is copied out of its pinned node base
# image and the CLI installs run on it: a digest bump must export.
PAYLOAD_FILES = (
    "docker/app/Dockerfile.python",
    "docker/app/vendor-clis/package.json",
    "docker/app/vendor-clis/package-lock.json",
    "docker/app/vendor-clis/install-native.cjs",
    "docker/app/vendor-clis/install-darwin.cjs",
    "docker/app/vendor-clis/antigravity.json",
    "docker/app/vendor-clis/install-antigravity.py",
    "docker/app/chart-runtime/package.json",
    "docker/app/chart-runtime/package-lock.json",
    "scripts/model_gateway/__init__.py",
    "scripts/model_gateway/client.py",
    "scripts/model_gateway/cli.py",
    "scripts/model_gateway/cli_bridge.py",
)


class InputHashError(RuntimeError):
    """The client payload cannot be proven identical. Callers must export."""


def payload_files(repo: Path) -> list[str]:
    found = []
    for relative in PAYLOAD_FILES:
        path = repo / relative
        if path.is_symlink():
            raise InputHashError(f"symlink in client payload inputs: {path}")
        if not path.is_file():
            raise InputHashError(f"missing client payload input: {relative}")
        found.append(relative)
    return found


def hash_files(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files):
        data = files[path]
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def payload_hash(repo: Path = REPO) -> str:
    blob = {}
    for relative in payload_files(repo):
        path = repo / relative
        blob[relative] = (
            stat.S_IMODE(path.stat().st_mode).to_bytes(4, "big") + path.read_bytes()
        )
    return hash_files(blob)


def main() -> int:
    try:
        print(payload_hash())
    except InputHashError as exc:
        print(f"model client payload inputs: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
