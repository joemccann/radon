"""The Mac gateway's Antigravity CLI: pinned bytes, Google's signature, atomic install."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("install_antigravity", REPO / "scripts/runner/install_antigravity.py")
agy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agy)


def archive(members: dict[str, bytes], *, symlink: str | None = None) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(data))
        if symlink:
            info = tarfile.TarInfo(symlink)
            info.type = tarfile.SYMTYPE
            info.linkname = "/bin/sh"
            tar.addfile(info)
    return raw.getvalue()


def pin_for(payload: bytes) -> dict:
    return {"version": "1.3.1", "url": "https://storage.googleapis.com/antigravity-public/antigravity-cli/1.3.1-1/darwin-arm/cli_mac_arm64.tar.gz",
            "sha512": hashlib.sha512(payload).hexdigest(), "team_id": "EQHXZ8M8AV"}


def run(tmp_path, payload, pin, signed=lambda path, team: None):
    fetched = []
    def fetch(url):
        fetched.append(url)
        return payload
    dest = tmp_path / "bin" / "agy"
    agy.install(pin, dest, fetch=fetch, verify_signature=signed)
    return dest, fetched


def test_installs_the_pinned_binary_with_its_stamp(tmp_path):
    payload = archive({"antigravity": b"BINARY"})
    dest, fetched = run(tmp_path, payload, pin_for(payload))
    assert dest.read_bytes() == b"BINARY" and dest.stat().st_mode & 0o777 == 0o755
    assert fetched == [pin_for(payload)["url"]]
    assert (dest.parent / ".agy-pin").read_text().strip() == pin_for(payload)["sha512"]


def test_an_installed_matching_pin_is_not_downloaded_again(tmp_path):
    payload = archive({"antigravity": b"BINARY"})
    run(tmp_path, payload, pin_for(payload))
    _, fetched = run(tmp_path, payload, pin_for(payload))
    assert fetched == []


def test_a_checksum_mismatch_installs_nothing(tmp_path):
    payload = archive({"antigravity": b"BINARY"})
    pin = pin_for(payload) | {"sha512": "0" * 128}
    with pytest.raises(ValueError, match="checksum"):
        run(tmp_path, payload, pin)
    assert not (tmp_path / "bin" / "agy").exists()


def test_a_bad_signature_installs_nothing(tmp_path):
    payload = archive({"antigravity": b"BINARY"})
    def unsigned(path, team):
        raise ValueError("signature")
    with pytest.raises(ValueError, match="signature"):
        run(tmp_path, payload, pin_for(payload), signed=unsigned)
    assert not (tmp_path / "bin" / "agy").exists()
    assert list((tmp_path / "bin").iterdir()) == []


@pytest.mark.parametrize("members,symlink", [({"other": b"x"}, None), ({}, "antigravity")])
def test_an_archive_without_a_regular_antigravity_member_is_refused(tmp_path, members, symlink):
    payload = archive(members, symlink=symlink)
    with pytest.raises(ValueError, match="member"):
        run(tmp_path, payload, pin_for(payload))


@pytest.mark.parametrize("field,value", [("url", "http://storage.googleapis.com/x.tar.gz"), ("url", "https://example.com/agy.tar.gz"),
                                         ("sha512", "abc"), ("team_id", "")])
def test_an_invalid_pin_is_refused_before_any_download(tmp_path, field, value):
    payload = archive({"antigravity": b"BINARY"})
    pin = pin_for(payload) | {field: value}
    with pytest.raises(ValueError):
        _, fetched = run(tmp_path, payload, pin)
