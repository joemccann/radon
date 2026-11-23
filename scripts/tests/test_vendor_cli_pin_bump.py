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
