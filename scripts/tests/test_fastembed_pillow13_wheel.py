"""fastembed's published metadata blocks Pillow 13. The materializer only
raises that cap on the official 0.8.0 wheel."""

import hashlib
import io
import zipfile

from deps.materialize_fastembed_pillow13 import (
    OFFICIAL_SHA256,
    patch_wheel,
    write_wheel,
)


def _wheel(pillow_spec: str) -> bytes:
    meta = (
        "Metadata-Version: 2.1\n"
        "Name: fastembed\n"
        "Version: 0.8.0\n"
        f"Requires-Dist: pillow ({pillow_spec}) ; python_version == \"3.13\"\n"
        "Requires-Dist: pillow (>=10.3.0,<13.0) ; python_version >= \"3.10\" and python_version < \"3.13\"\n"
        "Requires-Dist: pillow (>=12.0.0,<13.0) ; python_version >= \"3.14\"\n"
    ).encode()
    record = "fastembed-0.8.0.dist-info/METADATA,,\nfastembed-0.8.0.dist-info/RECORD,,\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("fastembed-0.8.0.dist-info/METADATA", meta)
        archive.writestr("fastembed-0.8.0.dist-info/RECORD", record)
        archive.writestr("fastembed/__init__.py", "x = 1\n")
    return buf.getvalue()


def test_patch_raises_only_the_pillow_caps():
    original = _wheel(">=11.0.0,<13.0")
    patched = patch_wheel(original)
    src = zipfile.ZipFile(io.BytesIO(original))
    dst = zipfile.ZipFile(io.BytesIO(patched))
    assert src.read("fastembed/__init__.py") == dst.read("fastembed/__init__.py")
    meta = dst.read("fastembed-0.8.0.dist-info/METADATA").decode()
    assert ",<13.0)" not in meta
    assert meta.count(",<14.0)") == 3


def test_write_wheel_rejects_a_different_official_hash(tmp_path):
    raw = _wheel(">=11.0.0,<13.0")
    assert hashlib.sha256(raw).hexdigest() != OFFICIAL_SHA256
    try:
        write_wheel(raw, tmp_path / "fastembed-0.8.0-py3-none-any.whl")
    except ValueError as exc:
        assert "sha256" in str(exc)
    else:
        raise AssertionError("expected sha256 mismatch")
