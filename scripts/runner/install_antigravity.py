#!/usr/bin/env python3
"""Install the Mac gateway's pinned Antigravity CLI without its mutable installer.

The VPS image pins Linux builds in docker/app/vendor-clis/antigravity.json; this
pins the same version's macOS build. Root runs it from install.sh: the archive
must match the pinned SHA-512, hold a regular `antigravity` member, and carry
Google's Developer ID signature (team_id) for arm64 before it replaces
$PREFIX/gateway/bin/agy atomically. A matching stamp skips the download.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

MAX_BYTES = 256 * 1024 * 1024
URL = re.compile(r"https://storage\.googleapis\.com/antigravity-public/antigravity-cli/[0-9.]+-[0-9]+/darwin-arm/cli_mac_arm64\.tar\.gz")


def validate(pin: dict) -> None:
    if not isinstance(pin.get("url"), str) or not URL.fullmatch(pin["url"]):
        raise ValueError("Antigravity pin url is not the official macOS artifact")
    if not isinstance(pin.get("sha512"), str) or not re.fullmatch(r"[0-9a-f]{128}", pin["sha512"]):
        raise ValueError("Antigravity pin sha512 is invalid")
    if not isinstance(pin.get("team_id"), str) or not re.fullmatch(r"[A-Z0-9]{10}", pin["team_id"]):
        raise ValueError("Antigravity pin team_id is invalid")


def fetch(url: str) -> bytes:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=120) as response:
        if response.geturl() != url:
            raise ValueError("Antigravity download redirected")
        payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError("Antigravity download exceeds bounds")
    return payload


def signature(path: Path, team_id: str) -> None:
    subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(path)], check=True, capture_output=True, timeout=60)
    detail = subprocess.run(["/usr/bin/codesign", "-dv", "--verbose=2", str(path)], check=True, capture_output=True, text=True, timeout=60).stderr
    if f"TeamIdentifier={team_id}" not in detail.splitlines():
        raise ValueError("Antigravity signature is not Google's")
    subprocess.run(["/usr/bin/lipo", "-verify_arch", "arm64", str(path)], check=True, capture_output=True, timeout=30)


def install(pin: dict, dest: Path, *, fetch=fetch, verify_signature=signature) -> None:
    validate(pin)
    stamp = dest.parent / ".agy-pin"
    if dest.is_file() and not dest.is_symlink() and stamp.is_file() and stamp.read_text().strip() == pin["sha512"]:
        return
    payload = fetch(pin["url"])
    if hashlib.sha512(payload).hexdigest() != pin["sha512"]:
        raise ValueError("Antigravity checksum mismatch")
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        try:
            member = archive.getmember("antigravity")
        except KeyError:
            member = None
        if member is None or not member.isfile():
            raise ValueError("Antigravity archive has no regular antigravity member")
        binary = archive.extractfile(member).read()
    dest.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".agy-", dir=dest.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(binary)
            stream.flush()
            os.fchmod(stream.fileno(), 0o755)
            os.fsync(stream.fileno())
        verify_signature(temporary, pin["team_id"])
        os.replace(temporary, dest)
    finally:
        temporary.unlink(missing_ok=True)
    stamp.write_text(pin["sha512"] + "\n")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: install_antigravity.py PIN.json DEST", file=sys.stderr)
        return 64
    install(json.loads(Path(argv[0]).read_text()), Path(argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
