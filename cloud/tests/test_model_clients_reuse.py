"""Identical gateway image ids reuse verified client receipts instead of exporting."""
import json
import os
from pathlib import Path

import pytest

from test_model_clients_darwin import payload as darwin_payload
from test_model_clients_install import SHA, module, payload

DONOR = "b" * 40
NEW = "c" * 40
IMAGE_ID = "sha256:" + ("ab" * 32)


def installed(tmp_path):
    api = module()
    root = tmp_path / "clients"
    exports = tmp_path / "exports"
    (exports / "releases").mkdir(parents=True)
    api.install_from_tar(payload(), root, SHA, owner_uid=os.getuid())
    api.publish_darwin_bundle(darwin_payload(), exports / "releases", SHA, owner_uid=os.getuid())
    return api, root, exports


def _inspect(mapping):
    def run(argv, **_kwargs):
        assert argv[0] in ("/usr/bin/docker", "/usr/bin/podman")
        assert argv[1:4] == ["image", "inspect", "--format"]
        ref = argv[-1]
        tag = ref.rsplit(":", 1)[-1]

        class Result:
            returncode = 0 if tag in mapping else 1
            stdout = (mapping[tag] + "\n") if tag in mapping else ""
            stderr = ""

        return Result()

    return run


def test_same_image_reuses_verified_clients_without_export(tmp_path, monkeypatch):
    api, root, exports = installed(tmp_path)
    api.install_from_tar(payload(), root, DONOR, owner_uid=os.getuid())
    api.publish_darwin_bundle(darwin_payload(), exports / "releases", DONOR, owner_uid=os.getuid())
    monkeypatch.setattr(api.subprocess, "run", _inspect({DONOR: IMAGE_ID, NEW: IMAGE_ID}))
    monkeypatch.setattr(api, "export_from_image", lambda *args, **kwargs: pytest.fail("export"))
    assert api.reuse_identical_image_clients(
        "/usr/bin/docker", "ghcr.io/joemccann/radon-model-gateway:" + NEW, NEW,
        root=root, exports=exports / "releases", owner_uid=os.getuid(),
    )
    promoted = root / NEW
    proof = json.loads((promoted / "manifest.json").read_text())
    donor = json.loads((root / DONOR / "manifest.json").read_text())
    assert proof["release_sha"] == NEW
    assert proof["sha256"] == donor["sha256"]
    assert proof["helper_sha256"] == donor["helper_sha256"]
    assert (promoted / "bin/grok").read_bytes() == (root / DONOR / "bin/grok").read_bytes()
    assert (root / "current").resolve() == promoted
    darwin = exports / "releases" / NEW
    darwin_proof = json.loads((darwin / "manifest.json").read_text())
    assert darwin_proof["release_sha"] == NEW
    assert darwin_proof["sha256"] == json.loads((exports / "releases" / DONOR / "manifest.json").read_text())["sha256"]
    assert not (exports / "releases" / "current").exists()


def test_different_image_id_does_not_reuse_clients(tmp_path, monkeypatch):
    api, root, exports = installed(tmp_path)
    api.install_from_tar(payload(), root, DONOR, owner_uid=os.getuid())
    api.publish_darwin_bundle(darwin_payload(), exports / "releases", DONOR, owner_uid=os.getuid())
    monkeypatch.setattr(api.subprocess, "run", _inspect({DONOR: IMAGE_ID, NEW: "sha256:" + ("cd" * 32)}))
    assert api.reuse_identical_image_clients(
        "/usr/bin/docker", "ghcr.io/joemccann/radon-model-gateway:" + NEW, NEW,
        root=root, exports=exports / "releases", owner_uid=os.getuid(),
    ) is False
    assert not (root / NEW).exists()
    assert (root / "current").resolve() == root / DONOR


def test_corrupt_donor_falls_closed_without_a_new_release(tmp_path, monkeypatch):
    api, root, exports = installed(tmp_path)
    api.install_from_tar(payload(), root, DONOR, owner_uid=os.getuid())
    api.publish_darwin_bundle(darwin_payload(), exports / "releases", DONOR, owner_uid=os.getuid())
    binary = root / DONOR / "bin/grok"
    binary.chmod(0o755)
    binary.write_bytes(b"tampered")
    binary.chmod(0o555)
    monkeypatch.setattr(api.subprocess, "run", _inspect({DONOR: IMAGE_ID, NEW: IMAGE_ID}))
    assert api.reuse_identical_image_clients(
        "/usr/bin/docker", "ghcr.io/joemccann/radon-model-gateway:" + NEW, NEW,
        root=root, exports=exports / "releases", owner_uid=os.getuid(),
    ) is False
    assert not (root / NEW).exists()


def test_existing_release_is_not_treated_as_already_proven(tmp_path, monkeypatch):
    """A rebuilt tag for this SHA must still export. The old tree is not proof."""
    api, root, exports = installed(tmp_path)
    monkeypatch.setattr(api.subprocess, "run", _inspect({SHA: IMAGE_ID}))
    assert api.reuse_identical_image_clients(
        "/usr/bin/docker", "ghcr.io/joemccann/radon-model-gateway:" + SHA, SHA,
        root=root, exports=exports / "releases", owner_uid=os.getuid(),
    ) is False


def test_promotion_tries_reuse_before_export(tmp_path, monkeypatch):
    api = module()
    monkeypatch.setattr(api, "ROOT", tmp_path / "clients")
    monkeypatch.setattr(api, "EXPORT_ROOT", tmp_path / "exports")
    monkeypatch.setattr(api.os, "geteuid", lambda: 0)
    monkeypatch.setattr(api, "check_model_space", lambda: {"filesystems": []})
    monkeypatch.setattr(api, "_trusted_root", lambda *_args, **_kwargs: None)
    seen = {}

    def reuse(engine, image, release, **kwargs):
        seen["reuse"] = (engine, image, release, kwargs.get("owner_uid", 0))
        return True

    monkeypatch.setattr(api, "reuse_identical_image_clients", reuse)
    monkeypatch.setattr(api, "export_from_image", lambda *_args, **_kwargs: pytest.fail("export"))
    assert api.main(["/usr/bin/docker", "ghcr.io/joemccann/radon-model-gateway:" + NEW, NEW]) == 0
    assert seen["reuse"][2] == NEW
