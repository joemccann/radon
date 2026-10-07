"""DS-2026-10-05-05: immutable vendor payloads for secret-bearing probes."""
from __future__ import annotations

import io
import json
import re
import runpy
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
VENDORS = ROOT / "docker/app/vendor-clis"
DOCKERFILE = ROOT / "docker/app/Dockerfile.python"


def test_vendor_dependencies_are_exact_and_integrity_locked() -> None:
    package = json.loads((VENDORS / "package.json").read_text())
    lock = json.loads((VENDORS / "package-lock.json").read_text())
    assert set(package["dependencies"]) == {
        "@anthropic-ai/claude-code", "@openai/codex", "@xai-official/grok"
    }
    for name, version in package["dependencies"].items():
        assert re.fullmatch(r"\d+\.\d+\.\d+", version)
        assert lock["packages"][f"node_modules/{name}"]["version"] == version
    for name, entry in lock["packages"].items():
        if name:
            assert entry["resolved"].startswith("https://registry.npmjs.org/")
            assert entry["integrity"].startswith("sha512-")
    for cpu in ("x64", "arm64"):
        for name in (f"@anthropic-ai/claude-code-linux-{cpu}", f"@openai/codex-linux-{cpu}", f"@xai-official/grok-linux-{cpu}"):
            assert f"node_modules/{name}" in lock["packages"]


def test_native_image_disables_hooks_and_checks_unprivileged_binaries() -> None:
    text = DOCKERFILE.read_text()
    assert re.search(r"FROM node:\d+\.\d+\.\d+-bookworm-slim@sha256:[0-9a-f]{64} AS vendor-clis", text)
    assert "npm ci --ignore-scripts --include=optional" in text
    assert "node install-native.cjs" in text
    assert "python /opt/radon-clis/install-antigravity.py" in text
    assert "chmod -R a-w /opt/radon-clis" in text
    assert "ENV DISABLE_AUTOUPDATER=1" in text
    assert text.index("USER radon") < text.index('for cli, version in {"claude"')
    assert 'assert executable.stat().st_uid == 0' in text
    assert 'assert not os.access(executable.parent, os.W_OK)' in text
    assert 'subprocess.run([cli, "--version"]' in text


def test_antigravity_pins_both_architectures_without_mutable_installer() -> None:
    pins = json.loads((VENDORS / "antigravity.json").read_text())
    assert set(pins["platforms"]) == {"amd64", "arm64"}
    for pin in pins["platforms"].values():
        assert pin["url"].startswith("https://storage.googleapis.com/antigravity-public/antigravity-cli/" + pins["version"] + "-")
        assert re.fullmatch(r"[0-9a-f]{128}", pin["sha512"])
    installer = (VENDORS / "install-antigravity.py").read_text()
    assert "install.sh" not in installer
    assert "hashlib.sha512(payload).hexdigest() != pin" in installer
    assert "archive.extractall" not in installer
    assert 'archive.getmember("antigravity")' in installer
    assert "member.isfile()" in installer


@pytest.mark.parametrize("machine", ["x86_64", "aarch64"])
def test_changed_native_payload_is_rejected_before_extraction(machine: str) -> None:
    with patch("platform.machine", return_value=machine), patch("urllib.request.urlopen", return_value=io.BytesIO(b"tampered native artifact")), patch("tarfile.open") as archive:
        with pytest.raises(RuntimeError, match="checksum mismatch"):
            runpy.run_path(str(VENDORS / "install-antigravity.py"))
        archive.assert_not_called()
