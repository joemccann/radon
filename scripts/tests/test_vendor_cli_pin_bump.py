"""Vendor CLI pin bumps keep checksum, team id, and architecture."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.ci import bump_vendor_cli_pins as pins

ROOT = Path(__file__).resolve().parents[2]


def _blob(url: str, body: bytes) -> tuple[str, bytes]:
    return url, body


def test_agy_pins_require_checksum_team_and_arch():
    linux = b"linux-bytes"
    arm = b"arm-bytes"
    darwin = b"darwin-bytes"
    manifest = {
        "version": "9.9.9",
        "platforms": {
            "amd64": {
                "url": "https://example.test/antigravity/linux-x64/cli.tar.gz",
                "sha512": hashlib.sha512(linux).hexdigest(),
            },
            "arm64": {
                "url": "https://example.test/antigravity/linux-arm/cli.tar.gz",
                "sha512": hashlib.sha512(arm).hexdigest(),
            },
            "darwin-arm64": {
                "url": "https://example.test/antigravity/darwin-arm/cli.tar.gz",
                "sha512": hashlib.sha512(darwin).hexdigest(),
                "team_id": pins.TEAM_ID,
            },
        },
    }
    blobs = dict([
        _blob(manifest["platforms"]["amd64"]["url"], linux),
        _blob(manifest["platforms"]["arm64"]["url"], arm),
        _blob(manifest["platforms"]["darwin-arm64"]["url"], darwin),
    ])
    linux_pin, darwin_pin = pins.verified_agy_pins(manifest, blobs)
    assert linux_pin["version"] == "9.9.9"
    assert linux_pin["platforms"]["amd64"]["sha512"] == hashlib.sha512(linux).hexdigest()
    assert darwin_pin["team_id"] == pins.TEAM_ID
    assert "darwin-arm" in darwin_pin["url"]


def test_checksum_mismatch_writes_nothing():
    manifest = {
        "version": "9.9.9",
        "platforms": {
            "amd64": {"url": "https://example.test/linux-x64/cli.tar.gz", "sha512": "ab" * 64},
            "arm64": {"url": "https://example.test/linux-arm/cli.tar.gz", "sha512": "cd" * 64},
            "darwin-arm64": {
                "url": "https://example.test/darwin-arm/cli.tar.gz",
                "sha512": "ef" * 64,
                "team_id": pins.TEAM_ID,
            },
        },
    }
    with pytest.raises(ValueError, match="checksum"):
        pins.verified_agy_pins(manifest, {row["url"]: b"nope" for row in manifest["platforms"].values()})


def test_wrong_team_id_is_refused():
    body = b"darwin"
    url = "https://example.test/darwin-arm/cli.tar.gz"
    manifest = {
        "version": "1.2.3",
        "platforms": {
            "amd64": {"url": "https://example.test/linux-x64/a.tar.gz", "sha512": hashlib.sha512(b"a").hexdigest()},
            "arm64": {"url": "https://example.test/linux-arm/b.tar.gz", "sha512": hashlib.sha512(b"b").hexdigest()},
            "darwin-arm64": {"url": url, "sha512": hashlib.sha512(body).hexdigest(), "team_id": "NOTOURS123"},
        },
    }
    blobs = {
        manifest["platforms"]["amd64"]["url"]: b"a",
        manifest["platforms"]["arm64"]["url"]: b"b",
        url: body,
    }
    with pytest.raises(ValueError, match="team"):
        pins.verified_agy_pins(manifest, blobs)


def test_wrong_arch_token_is_refused():
    body = b"linux"
    digest = hashlib.sha512(body).hexdigest()
    manifest = {
        "version": "1.2.3",
        "platforms": {
            "amd64": {"url": "https://example.test/windows-x64/a.tar.gz", "sha512": digest},
            "arm64": {"url": "https://example.test/linux-arm/b.tar.gz", "sha512": digest},
            "darwin-arm64": {
                "url": "https://example.test/darwin-arm/c.tar.gz",
                "sha512": digest,
                "team_id": pins.TEAM_ID,
            },
        },
    }
    blobs = {row["url"]: body for row in manifest["platforms"].values()}
    with pytest.raises(ValueError, match="arch"):
        pins.verified_agy_pins(manifest, blobs)


def test_apply_refuses_an_artifact_url_off_the_allowlisted_host(monkeypatch, tmp_path):
    # The manifest is fetched from the network, so the download url it names
    # must stay on the publisher's storage host; the manifest itself must
    # come from the pinned manifest host. Nothing off-host is downloaded.
    linux = tmp_path / "antigravity.json"
    linux.write_text(json.dumps({
        "version": "1.0.0",
        "source": "https://antigravity.google/cli/install.sh",
        "manifest_base": "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/",
        "platforms": {},
    }))
    monkeypatch.setattr(pins, "LINUX_PIN", linux)
    monkeypatch.setattr(pins, "DARWIN_PIN", tmp_path / "darwin.json")
    fetched = []

    def fake_fetch(url, timeout=120):
        fetched.append(url)
        return json.dumps({"version": "9.9.9", "url": "https://evil.invalid/linux-x64/cli.tar.gz", "sha512": "ab" * 64}).encode()

    monkeypatch.setattr(pins, "fetch_bytes", fake_fetch)
    with pytest.raises(ValueError, match="host"):
        pins.apply_agy()
    assert fetched == ["https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/linux_amd64.json"]
    assert not (tmp_path / "darwin.json").exists()


def test_apply_refuses_a_manifest_base_off_the_pinned_host(monkeypatch, tmp_path):
    linux = tmp_path / "antigravity.json"
    linux.write_text(json.dumps({"version": "1.0.0", "manifest_base": "https://evil.invalid/manifests/", "platforms": {}}))
    monkeypatch.setattr(pins, "LINUX_PIN", linux)
    monkeypatch.setattr(pins, "fetch_bytes", lambda url, timeout=120: pytest.fail("fetched " + url))
    with pytest.raises(ValueError, match="host"):
        pins.apply_agy()


@pytest.mark.parametrize("url", [
    "https://storage.googleapis.com/antigravity-public/antigravity-cli/1/linux-x64/cli.tar.gz",
    "https://registry.npmjs.org/@openai/codex/-/codex-1.0.0.tgz",
])
def test_trusted_download_hosts(url):
    pins.require_trusted_url(url)


@pytest.mark.parametrize("url", [
    "https://storage.googleapis.com.evil.invalid/antigravity-public/x.tar.gz",
    "https://storage.googleapis.com/other-bucket/x.tar.gz",
    "http://storage.googleapis.com/antigravity-public/x.tar.gz",
    "https://user@storage.googleapis.com/antigravity-public/x.tar.gz",
    "https://registry.npmjs.org.evil.invalid/x.tgz",
    "",
])
def test_untrusted_download_urls_are_refused(url):
    with pytest.raises(ValueError, match="host"):
        pins.require_trusted_url(url)


def test_npm_tarball_host_is_checked_before_download(monkeypatch, tmp_path):
    npm = tmp_path / "package.json"
    npm.write_text(json.dumps({"dependencies": {name: "0.0.1" for name in pins.NPM_PACKAGES}}))
    monkeypatch.setattr(pins, "NPM_PIN", npm)
    fetched = []

    def fake_fetch(url, timeout=120):
        fetched.append(url)
        if url.startswith("https://registry.npmjs.org/"):
            return json.dumps({
                "version": "9.9.9",
                "os": ["linux", "darwin"], "cpu": ["x64", "arm64"],
                "dist": {"tarball": "https://evil.invalid/x.tgz", "integrity": "sha512-" + "a" * 86},
            }).encode()
        pytest.fail("downloaded " + url)

    monkeypatch.setattr(pins, "fetch_bytes", fake_fetch)
    with pytest.raises(ValueError, match="host"):
        pins.apply_npm()
    assert fetched == ["https://registry.npmjs.org/@anthropic-ai/claude-code/latest"]
    assert json.loads(npm.read_text())["dependencies"]["@openai/codex"] == "0.0.1"


def test_pr_body_and_summary_claim_only_the_checks_that_ran():
    # Nothing in the bump verifies a code signature: the Darwin team id is
    # a constant the installer enforces at install time.
    body = (ROOT / "scripts/ci/vendor_cli_pin_pr_body.md").read_text(encoding="utf-8")
    assert "team id stayed" not in body
    assert "verified checksum, team id" not in body
    assert "install" in body.split("## Summary", 1)[1].split("## What changed", 1)[0].lower()
    script = (ROOT / "scripts/ci/bump_vendor_cli_pins.py").read_text(encoding="utf-8")
    assert "team {TEAM_ID}" not in script


def test_npm_integrity_and_required_arches():
    payload = b"claude-tarball"
    digest = __import__("base64").b64encode(hashlib.sha512(payload).digest()).decode()
    pins.require_npm_integrity(payload, f"sha512-{digest}")
    meta = {"optionalDependencies": {
        "@anthropic-ai/claude-code-linux-x64": "1.0.0",
        "@anthropic-ai/claude-code-linux-arm64": "1.0.0",
        "@anthropic-ai/claude-code-darwin-arm64": "1.0.0",
    }}
    pins.require_npm_arches(meta)
    with pytest.raises(ValueError, match="arches"):
        pins.require_npm_arches({"optionalDependencies": {}})
    with pytest.raises(ValueError, match="sha512"):
        pins.require_npm_integrity(payload, "sha256-aaaa")


def test_workflow_opens_a_factory_branch_and_keeps_verification():
    text = (ROOT / ".github/workflows/vendor-cli-latest.yml").read_text(encoding="utf-8")
    assert "cron:" in text
    assert "factory/vendor-cli-" in text
    assert "contents: write" in text
    assert "--apply" in text
    script = (ROOT / "scripts/ci/bump_vendor_cli_pins.py").read_text(encoding="utf-8")
    assert "sha512" in script
    assert "TEAM_ID" in script
    assert "cursor: unwired" in script
    current = json.loads((ROOT / "docker/app/vendor-clis/antigravity.json").read_text())
    assert re_sha(current["platforms"]["amd64"]["sha512"])
    darwin = json.loads((ROOT / "scripts/runner/antigravity-darwin.json").read_text())
    assert darwin["team_id"] == pins.TEAM_ID
    import subprocess
    check = subprocess.run(
        ["python3", "scripts/ci/check_pr_body.py", "--body-file", "scripts/ci/vendor_cli_pin_pr_body.md"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert check.returncode == 0, check.stdout + check.stderr


def re_sha(value: str) -> bool:
    import re
    return bool(re.fullmatch(r"[0-9a-f]{128}", value))
