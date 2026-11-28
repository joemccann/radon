"""CIP-039: read the client-payload label from image-inspect JSON.

Nine successful main deploys (44979169331 through 45638350375) printed
"model clients staged from exact CI image" even though the published
gateway config for 6dd0418dea2ea8059eee17f3a11d3c72015e952c carries
radon.model-clients.payload-sha256. Podman
`image inspect --format '{{index .Config.Labels "..."}}'` prints
`<no value>` for that key. Default `image inspect` JSON does not.
"""
from __future__ import annotations

import json
import os

import pytest

from test_model_clients_darwin import payload as darwin_payload
from test_model_clients_install import module, payload
from test_model_clients_reuse import DONOR, IMAGE_ID, NEW, installed

PAYLOAD = "ab" * 32
OTHER = "cd" * 32
LABEL = "radon.model-clients.payload-sha256"


def _podman(ids, labels, *, where="config"):
    """Go-template label reads miss. JSON inspect carries the OCI label."""

    def run(argv, **_kwargs):
        assert argv[0] in ("/usr/bin/podman", "/usr/bin/docker")

        class Result:
            returncode = 1
            stdout = ""
            stderr = ""

        if argv[1:4] == ["image", "inspect", "--format"]:
            fmt = argv[4]
            tag = argv[-1].rsplit(":", 1)[-1]
            if "{{.Id}}" in fmt:
                Result.returncode = 0 if tag in ids else 1
                Result.stdout = (ids[tag] + "\n") if tag in ids else ""
                return Result()
            if "Labels" in fmt:
                Result.returncode = 0 if tag in ids else 1
                Result.stdout = "<no value>\n" if tag in ids else ""
                return Result()
            return Result()
        if argv[1:3] == ["image", "inspect"] and "--format" not in argv:
            tag = argv[-1].rsplit(":", 1)[-1]
            if tag not in ids:
                return Result()
            value = labels.get(tag)
            label_map = {LABEL: value} if value else {}
            body = {"Id": ids[tag]}
            if where == "config":
                body["Config"] = {"Labels": label_map}
            else:
                body["Labels"] = label_map
            Result.returncode = 0
            Result.stdout = json.dumps([body])
            return Result()
        if argv[1] == "images":
            Result.returncode = 0
            return Result()
        return Result()

    return run


def _seed(tmp_path):
    api, root, exports = installed(tmp_path)
    api.install_from_tar(payload(), root, DONOR, owner_uid=os.getuid())
    api.publish_darwin_bundle(darwin_payload(), exports / "releases", DONOR, owner_uid=os.getuid())
    return api, root, exports


def _reuse(api, root, exports):
    return api.reuse_identical_image_clients(
        "/usr/bin/podman",
        "ghcr.io/joemccann/radon-model-gateway:" + NEW,
        NEW,
        root=root,
        exports=exports / "releases",
        owner_uid=os.getuid(),
    )


def test_podman_template_miss_reuses_when_json_label_matches(tmp_path, monkeypatch):
    api, root, exports = _seed(tmp_path)
    ids = {DONOR: IMAGE_ID, NEW: "sha256:" + ("ef" * 32)}
    monkeypatch.setattr(api.subprocess, "run", _podman(ids, {DONOR: PAYLOAD, NEW: PAYLOAD}))
    monkeypatch.setattr(api, "export_from_image", lambda *args, **kwargs: pytest.fail("export"))
    assert _reuse(api, root, exports) is True
    proof = json.loads((root / NEW / "manifest.json").read_text())
    donor = json.loads((root / DONOR / "manifest.json").read_text())
    assert proof["release_sha"] == NEW
    assert proof["sha256"] == donor["sha256"]
    assert not (root / NEW).is_symlink()


def test_top_level_podman_labels_reuse_when_config_labels_are_absent(tmp_path, monkeypatch):
    api, root, exports = _seed(tmp_path)
    ids = {DONOR: IMAGE_ID, NEW: "sha256:" + ("ef" * 32)}
    monkeypatch.setattr(
        api.subprocess, "run", _podman(ids, {DONOR: PAYLOAD, NEW: PAYLOAD}, where="top")
    )
    monkeypatch.setattr(api, "export_from_image", lambda *args, **kwargs: pytest.fail("export"))
    assert _reuse(api, root, exports) is True


def test_json_label_mismatch_does_not_reuse(tmp_path, monkeypatch):
    api, root, exports = _seed(tmp_path)
    ids = {DONOR: IMAGE_ID, NEW: "sha256:" + ("ef" * 32)}
    monkeypatch.setattr(api.subprocess, "run", _podman(ids, {DONOR: PAYLOAD, NEW: OTHER}))
    assert _reuse(api, root, exports) is False
    assert not (root / NEW).exists()


def test_missing_json_label_does_not_reuse(tmp_path, monkeypatch):
    api, root, exports = _seed(tmp_path)
    ids = {DONOR: IMAGE_ID, NEW: "sha256:" + ("ef" * 32)}
    monkeypatch.setattr(api.subprocess, "run", _podman(ids, {DONOR: PAYLOAD}))
    assert _reuse(api, root, exports) is False
    assert not (root / NEW).exists()


def test_malformed_json_label_does_not_reuse(tmp_path, monkeypatch):
    api, root, exports = _seed(tmp_path)
    ids = {DONOR: IMAGE_ID, NEW: "sha256:" + ("ef" * 32)}
    monkeypatch.setattr(api.subprocess, "run", _podman(ids, {DONOR: PAYLOAD, NEW: "not-a-hash"}))
    assert _reuse(api, root, exports) is False
    assert not (root / NEW).exists()


def test_payload_label_reader_rejects_template_no_value(tmp_path, monkeypatch):
    api = module()
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        class Result:
            returncode = 0
            stdout = "<no value>\n"
            stderr = ""
        if "--format" in argv:
            return Result()
        Result.stdout = json.dumps([{"Config": {"Labels": {LABEL: PAYLOAD}}}])
        return Result()

    monkeypatch.setattr(api.subprocess, "run", run)
    assert api._payload_label("/usr/bin/podman", "ghcr.io/joemccann/radon-model-gateway:" + NEW) == PAYLOAD
    assert any("--format" not in argv for argv in calls)
    assert all("index .Config.Labels" not in " ".join(argv) for argv in calls)
