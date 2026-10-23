#!/usr/bin/env python3
"""Write a Pillow 13-capable fastembed 0.8.0 wheel under vendor/.

fastembed 0.8.0 and 0.9.0 declare pillow<13. qdrant/fastembed#830 raises the
cap to <14 and reports image tests passing on Pillow 13.0.0. That release has
not shipped. This downloads the official 0.8.0 wheel, checks its sha256, and
rewrites only the three Requires-Dist pillow caps. vendor/ is gitignored.
"""

from __future__ import annotations

import base64
import hashlib
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

OFFICIAL_URL = (
    "https://files.pythonhosted.org/packages/2a/e8/"
    "26b7d78bb8972498c467ca34cb12ee2e60d26ba5eae6d8443189a1af37a5/"
    "fastembed-0.8.0-py3-none-any.whl"
)
OFFICIAL_SHA256 = "40bee672657574a1009e35ec50030a55f2b426842cb011845379817641bbbbd0"
WHEEL_NAME = "fastembed-0.8.0-py3-none-any.whl"
_PILLOW_CAPS = 3


def _record_hash(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).decode().rstrip("=")


def patch_wheel(raw: bytes) -> bytes:
    """Return the wheel with pillow caps raised from <13.0 to <14.0."""
    src = zipfile.ZipFile(io.BytesIO(raw))
    meta_name = next(name for name in src.namelist() if name.endswith(".dist-info/METADATA"))
    record_name = next(name for name in src.namelist() if name.endswith(".dist-info/RECORD"))
    meta = src.read(meta_name).decode().replace(",<13.0)", ",<14.0)")
    if meta.count(",<14.0)") != _PILLOW_CAPS or ",<13.0)" in meta:
        raise ValueError("fastembed METADATA pillow caps were not the expected three")
    meta_bytes = meta.encode()
    record_lines: list[str] = []
    for line in src.read(record_name).decode().splitlines():
        path = line.split(",", 1)[0]
        if path == meta_name:
            record_lines.append(f"{path},{_record_hash(meta_bytes)},{len(meta_bytes)}")
        else:
            record_lines.append(line)
    record_bytes = ("\n".join(record_lines) + "\n").encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == meta_name:
                data = meta_bytes
            elif info.filename == record_name:
                data = record_bytes
            copied = zipfile.ZipInfo(filename=info.filename, date_time=info.date_time)
            copied.compress_type = info.compress_type
            copied.external_attr = info.external_attr
            dst.writestr(copied, data)
    patched = out.getvalue()
    verify = zipfile.ZipFile(io.BytesIO(patched))
    if verify.read(meta_name) != meta_bytes:
        raise ValueError("patched wheel METADATA did not round-trip")
    return patched


def wheel_path(root: Path | None = None) -> Path:
    repo = root or Path(__file__).resolve().parents[2]
    return repo / "vendor" / WHEEL_NAME


def write_wheel(raw: bytes, dest: Path) -> None:
    if hashlib.sha256(raw).hexdigest() != OFFICIAL_SHA256:
        raise ValueError("official fastembed wheel sha256 mismatch")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(patch_wheel(raw))


def main() -> int:
    dest = wheel_path()
    with urllib.request.urlopen(OFFICIAL_URL, timeout=60) as response:
        raw = response.read()
    write_wheel(raw, dest)
    print(dest)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"fastembed wheel: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
