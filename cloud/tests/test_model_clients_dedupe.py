"""Identical client payloads share immutable inodes; retention keeps current+previous."""
from __future__ import annotations

import io
import json
import os
import sys
import tarfile
import time
from pathlib import Path

import pytest

from test_model_clients_darwin import payload as darwin_payload
from test_model_clients_install import SHA, module, payload

OTHER = "b" * 40
THIRD = "c" * 40


def regular_files(release: Path):
    for folder, _dirs, files in os.walk(release, followlinks=False):
        for name in files:
            path = Path(folder, name)
            if not path.is_symlink():
                yield path.relative_to(release), path.lstat()


def payload_with(extra: dict[str, bytes]) -> io.BytesIO:
    original = payload()
    result = io.BytesIO()
    with tarfile.open(fileobj=original, mode="r") as source, tarfile.open(fileobj=result, mode="w") as archive:
        for member in source.getmembers():
            data = source.extractfile(member).read() if member.isfile() else None
            if member.name in extra:
                data = extra[member.name]
                member.size = len(data)
            archive.addfile(member, io.BytesIO(data) if data is not None else None)
    result.seek(0)
    return result


def age(path: Path, seconds: float) -> None:
    when = time.time() - seconds
    os.utime(path, (when, when))


def test_identical_linux_payload_hardlinks_previous_release_and_writes_a_new_receipt(tmp_path):
    api = module(); root = tmp_path / "clients"; uid = os.getuid()
    first = api.install_from_tar(payload(), root, SHA, owner_uid=uid)
    second = api.install_from_tar(payload(), root, OTHER, owner_uid=uid)
    old = dict(regular_files(first)); new = dict(regular_files(second))
    assert set(old) == set(new)
    for relative, info in new.items():
        if relative == Path("manifest.json"):
            assert info.st_ino != old[relative].st_ino
        else:
            assert info.st_ino == old[relative].st_ino, relative
            assert not info.st_mode & 0o222
    assert json.loads((second / "manifest.json").read_text())["release_sha"] == OTHER
    assert json.loads((first / "manifest.json").read_text())["release_sha"] == SHA
    assert not any(path.is_symlink() and os.readlink(path).startswith("..") and SHA in os.readlink(path) for path in second.rglob("*"))
    # Both receipts verify independently; recovery accepts the linked tree.
    api._verify_linux_release(root, SHA, uid)
    api._verify_linux_release(root, OTHER, uid)
    exports = tmp_path / "exports"; (exports / "releases").mkdir(parents=True)
    for release in (SHA, OTHER):
        api.publish_darwin_bundle(darwin_payload(), exports / "releases", release, owner_uid=uid)
    api.recover_clients(SHA, root=root, exports=exports, owner_uid=uid)
    assert (root / "current").resolve() == first


def test_removing_previous_release_leaves_linked_release_intact(tmp_path):
    api = module(); root = tmp_path / "clients"; uid = os.getuid()
    first = api.install_from_tar(payload(), root, SHA, owner_uid=uid)
    second = api.install_from_tar(payload(), root, OTHER, owner_uid=uid)
    api._remove_stage(first)
    api._verify_linux_release(root, OTHER, uid)
    assert (second / "bin/node").read_bytes() == b"image node payload"


def test_changed_linux_payload_is_not_linked(tmp_path):
    api = module(); root = tmp_path / "clients"; uid = os.getuid()
    first = api.install_from_tar(payload(), root, SHA, owner_uid=uid)
    second = api.install_from_tar(payload_with({"bin/grok": b"new CLI payload"}), root, OTHER, owner_uid=uid)
    old = dict(regular_files(first))
    assert all(info.st_ino != old[relative].st_ino for relative, info in regular_files(second) if relative in old)
    assert (second / "bin/grok").read_bytes() == b"new CLI payload"


def test_extra_file_in_payload_is_not_linked(tmp_path):
    api = module(); root = tmp_path / "clients"; uid = os.getuid()
    first = api.install_from_tar(payload(), root, SHA, owner_uid=uid)
    raw = payload(); extended = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="r") as source, tarfile.open(fileobj=extended, mode="w") as archive:
        for member in source.getmembers():
            archive.addfile(member, source.extractfile(member) if member.isfile() else None)
        extra = tarfile.TarInfo("bin/extra"); extra.size = 1; archive.addfile(extra, io.BytesIO(b"x"))
    extended.seek(0)
    second = api.install_from_tar(extended, root, OTHER, owner_uid=uid)
    old = dict(regular_files(first))
    assert all(info.st_ino != old[relative].st_ino for relative, info in regular_files(second) if relative in old)


def test_corrupt_previous_release_is_never_a_link_donor(tmp_path):
    api = module(); root = tmp_path / "clients"; uid = os.getuid()
    first = api.install_from_tar(payload(), root, SHA, owner_uid=uid)
    (first).chmod(0o755); (first / "bin").chmod(0o755)
    (first / "bin/grok").chmod(0o644); (first / "bin/grok").write_bytes(b"tampered")
    (first / "bin/grok").chmod(0o555); (first / "bin").chmod(0o555); first.chmod(0o555)
    second = api.install_from_tar(payload(), root, OTHER, owner_uid=uid)
    assert (second / "bin/grok").read_bytes() == b"image CLI payload"
    assert (second / "bin/grok").stat().st_ino != (first / "bin/grok").stat().st_ino


def test_retention_keeps_current_and_previous_once_demotion_window_passes(tmp_path):
    api = module(); root = tmp_path / "clients"; uid = os.getuid()
    for number in range(1, 6):
        api.install_from_tar(payload(), root, f"{number:040x}", owner_uid=uid)
    # Recently demoted releases may still back running native sessions.
    assert len([p for p in root.iterdir() if len(p.name) == 40]) == 5
    for number in range(1, 6):
        age(root / f"{number:040x}", 20000 - number)
    api.install_from_tar(payload(), root, f"{6:040x}", owner_uid=uid)
    assert {p.name for p in root.iterdir() if len(p.name) == 40} == {f"{5:040x}", f"{6:040x}"}
    assert (root / "current").resolve() == root / f"{6:040x}"


def test_previous_release_survives_retention_even_when_old(tmp_path):
    api = module(); root = tmp_path / "clients"; uid = os.getuid()
    api.install_from_tar(payload(), root, SHA, owner_uid=uid)
    age(root / SHA, 100000)
    api.install_from_tar(payload(), root, OTHER, owner_uid=uid)
    age(root / SHA, 100000); age(root / OTHER, 50000)
    api.install_from_tar(payload(), root, THIRD, owner_uid=uid)
    # OTHER was just demoted (utime at demotion) and is the rollback target.
    assert {p.name for p in root.iterdir() if len(p.name) == 40} == {OTHER, THIRD}


def test_identical_darwin_bundle_hardlinks_previous_export(tmp_path):
    api = module(); root = tmp_path / "exports"; uid = os.getuid()
    first = api.publish_darwin_bundle(darwin_payload(), root, SHA, owner_uid=uid)
    api.activate_darwin_export(root, SHA, owner_uid=uid)
    second = api.publish_darwin_bundle(darwin_payload(), root, OTHER, owner_uid=uid)
    assert (second / "bundle.tar").stat().st_ino == (first / "bundle.tar").stat().st_ino
    assert (second / "manifest.json").stat().st_ino != (first / "manifest.json").stat().st_ino
    assert json.loads((second / "manifest.json").read_text())["release_sha"] == OTHER
    assert json.loads((first / "manifest.json").read_text())["release_sha"] == SHA
    api._verify_darwin_export(root, SHA, uid)
    api._verify_darwin_export(root, OTHER, uid)


def test_changed_darwin_bundle_is_not_linked(tmp_path):
    api = module(); root = tmp_path / "exports"; uid = os.getuid()
    first = api.publish_darwin_bundle(darwin_payload(), root, SHA, owner_uid=uid)
    second = api.publish_darwin_bundle(_darwin_changed(), root, OTHER, owner_uid=uid)
    assert (second / "bundle.tar").stat().st_ino != (first / "bundle.tar").stat().st_ino


def _darwin_changed():
    raw = io.BytesIO()
    original = darwin_payload()
    with tarfile.open(fileobj=original, mode="r") as source, tarfile.open(fileobj=raw, mode="w") as archive:
        for member in source.getmembers():
            data = source.extractfile(member).read()
            if member.name == "bin/rg":
                data = b"\xcf\xfa\xed\xfe changed rg"; member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    raw.seek(0)
    return raw


def test_corrupt_darwin_donor_is_never_linked(tmp_path):
    api = module(); root = tmp_path / "exports"; uid = os.getuid()
    first = api.publish_darwin_bundle(darwin_payload(), root, SHA, owner_uid=uid)
    first.chmod(0o755); (first / "bundle.tar").chmod(0o644)
    (first / "bundle.tar").write_bytes(b"tampered"); (first / "bundle.tar").chmod(0o444); first.chmod(0o555)
    second = api.publish_darwin_bundle(darwin_payload(), root, OTHER, owner_uid=uid)
    assert (second / "bundle.tar").stat().st_ino != (first / "bundle.tar").stat().st_ino
    api._verify_darwin_export(root, OTHER, uid)


def test_darwin_retention_keeps_committed_and_previous(tmp_path):
    api = module(); root = tmp_path / "exports"; uid = os.getuid()
    for number in range(1, 5):
        release = f"{number:040x}"
        api.publish_darwin_bundle(darwin_payload(), root, release, owner_uid=uid)
        api.activate_darwin_export(root, release, owner_uid=uid)
    for number in range(1, 5):
        age(root / f"{number:040x}", 20000 - number)
    api.publish_darwin_bundle(darwin_payload(), root, f"{5:040x}", owner_uid=uid)
    api.activate_darwin_export(root, f"{5:040x}", owner_uid=uid)
    assert {p.name for p in root.iterdir() if len(p.name) == 40} == {f"{4:040x}", f"{5:040x}"}


def test_reuse_clone_links_payload_but_never_the_donor_receipt(tmp_path, monkeypatch):
    from test_model_clients_reuse import IMAGE_ID, _inspect, installed
    api, root, exports = installed(tmp_path)
    monkeypatch.setattr(api.subprocess, "run", _inspect({SHA: IMAGE_ID, OTHER: IMAGE_ID}))
    assert api.reuse_identical_image_clients("/usr/bin/docker", "ghcr.io/joemccann/radon-model-gateway:" + OTHER, OTHER, root=root, exports=exports / "releases", owner_uid=os.getuid())
    assert (root / OTHER / "bin/grok").stat().st_ino == (root / SHA / "bin/grok").stat().st_ino
    assert (root / OTHER / "manifest.json").stat().st_ino != (root / SHA / "manifest.json").stat().st_ino
    assert (exports / "releases" / OTHER / "bundle.tar").stat().st_ino == (exports / "releases" / SHA / "bundle.tar").stat().st_ino
    assert json.loads((root / SHA / "manifest.json").read_text())["release_sha"] == SHA
    assert json.loads((exports / "releases" / SHA / "manifest.json").read_text())["release_sha"] == SHA


# --- enforcement uses the same pinned bounds admission charges ---------------

def test_linux_install_refuses_payload_over_pinned_byte_bound(tmp_path, monkeypatch):
    api = module()
    monkeypatch.setattr(api, "LINUX_MAX_PAYLOAD_BYTES", 40)
    with pytest.raises(ValueError, match="bounds"):
        api.install_from_tar(payload(), tmp_path / "clients", SHA, owner_uid=os.getuid())
    assert not (tmp_path / "clients" / SHA).exists()


def test_linux_install_refuses_members_over_pinned_count(tmp_path, monkeypatch):
    api = module()
    monkeypatch.setattr(api, "LINUX_MAX_MEMBERS", 5)
    with pytest.raises(ValueError, match="bounds"):
        api.install_from_tar(payload(), tmp_path / "clients", SHA, owner_uid=os.getuid())


def test_darwin_payload_refuses_bundle_over_pinned_bound(tmp_path, monkeypatch):
    api = module()
    monkeypatch.setattr(api, "DARWIN_MAX_EXPORT_BYTES", 100)
    with pytest.raises(ValueError, match="bounds"):
        api.publish_darwin_bundle(darwin_payload(), tmp_path / "exports", SHA, owner_uid=os.getuid())


def fake_engine(tmp_path: Path, size: int) -> str:
    engine = tmp_path / "engine"
    engine.write_text(f"#!{sys.executable}\nimport sys\nif sys.argv[1] == 'run':\n    sys.stdout.buffer.write(b'x' * {size})\n")
    engine.chmod(0o755)
    return str(engine)


@pytest.mark.parametrize(("darwin", "name"), [(False, "LINUX_MAX_STREAM_BYTES"), (True, "DARWIN_MAX_EXPORT_BYTES")])
def test_export_stream_is_refused_past_the_bound_admission_charged(tmp_path, monkeypatch, darwin, name):
    api = module()
    monkeypatch.setattr(api, name, 1000)
    with open(tmp_path / "stream", "w+b") as target:
        api.export_from_image(fake_engine(tmp_path, 1000), "image", target, darwin=darwin)
        assert target.read() == b"x" * 1000
    with open(tmp_path / "stream2", "w+b") as target, pytest.raises(ValueError, match="bounds"):
        api.export_from_image(fake_engine(tmp_path, 1001), "image", target, darwin=darwin)
