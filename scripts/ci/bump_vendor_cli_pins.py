#!/usr/bin/env python3
"""Verify a vendor CLI release before any pin file changes.

SHA-512 stays on every pin and is checked against the bytes fetched from
the publisher's own host: manifests come only from the pinned manifest
host, artifacts only from the allowlisted download hosts. Antigravity's
Darwin pin keeps Google's team id for the installer's codesign check; this
script verifies no signature. Architecture is taken from the artifact URL
or the npm os/cpu fields. A mismatch raises and writes nothing. Cursor has
no pin file; the rung is unwired.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LINUX_PIN = ROOT / "docker/app/vendor-clis/antigravity.json"
DARWIN_PIN = ROOT / "scripts/runner/antigravity-darwin.json"
NPM_PIN = ROOT / "docker/app/vendor-clis/package.json"
TEAM_ID = "EQHXZ8M8AV"
ARCH_TOKEN = {"amd64": "linux-x64", "arm64": "linux-arm", "darwin-arm64": "darwin-arm"}
NPM_PACKAGES = (
    "@anthropic-ai/claude-code",
    "@openai/codex",
    "@xai-official/grok",
)
NPM_ARCH = (
    ("linux", "x64"),
    ("linux", "arm64"),
    ("darwin", "arm64"),
)


def sha512_hex(payload: bytes) -> str:
    return hashlib.sha512(payload).hexdigest()


def require_sha512_hex(payload: bytes, expected: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{128}", expected or "") or sha512_hex(payload) != expected:
        raise ValueError("checksum mismatch")


def require_npm_integrity(payload: bytes, integrity: str) -> None:
    algo, _, digest = (integrity or "").partition("-")
    if algo != "sha512" or not digest:
        raise ValueError("npm pin is not sha512")
    if base64.b64encode(hashlib.sha512(payload).digest()).decode("ascii") != digest:
        raise ValueError("npm checksum mismatch")


def require_arch(url: str, arch: str) -> None:
    token = ARCH_TOKEN[arch]
    if token not in (url or ""):
        raise ValueError(f"arch {arch} is not in the artifact url")


# host -> required path prefix. Manifests name the download url, so the
# download hosts are pinned here and not taken from the manifest.
TRUSTED_DOWNLOADS = {"storage.googleapis.com": "/antigravity-public/", "registry.npmjs.org": "/"}
TRUSTED_MANIFESTS = {"antigravity-cli-auto-updater-974169037036.us-central1.run.app": "/manifests/"}


def require_trusted_url(url: str, trusted: dict[str, str] = TRUSTED_DOWNLOADS) -> None:
    parsed = urllib.parse.urlsplit(url or "")
    host = parsed.hostname or ""
    if parsed.scheme != "https" or parsed.netloc != host or host not in trusted:
        raise ValueError("url host is not trusted")
    if not parsed.path.startswith(trusted[host]):
        raise ValueError("url host path is not trusted")


def require_team(team_id: str) -> None:
    if team_id != TEAM_ID:
        raise ValueError("signer team id is not Google's")


def _platform(manifest: dict, *keys: str) -> dict:
    platforms = manifest.get("platforms") or {}
    for key in keys:
        row = platforms.get(key)
        if isinstance(row, dict) and row.get("url") and row.get("sha512"):
            return row
    raise ValueError("manifest is missing a pinned platform")


def verified_agy_pins(manifest: dict, blobs: dict[str, bytes]) -> tuple[dict, dict]:
    """Linux and Darwin pin documents after checksum, arch, and team checks."""
    if not isinstance(manifest, dict) or not manifest.get("version"):
        raise ValueError("manifest has no version")
    linux = {}
    for arch, keys in (("amd64", ("amd64", "linux-x64")), ("arm64", ("arm64", "linux-arm", "linux-arm64"))):
        row = _platform(manifest, *keys)
        require_arch(row["url"], arch)
        payload = blobs.get(row["url"])
        if payload is None:
            raise ValueError(f"missing bytes for {arch}")
        require_sha512_hex(payload, row["sha512"])
        linux[arch] = {"url": row["url"], "sha512": row["sha512"]}
    darwin_row = _platform(manifest, "darwin-arm64", "darwin-arm")
    require_arch(darwin_row["url"], "darwin-arm64")
    darwin_bytes = blobs.get(darwin_row["url"])
    if darwin_bytes is None:
        raise ValueError("missing bytes for darwin-arm64")
    require_sha512_hex(darwin_bytes, darwin_row["sha512"])
    require_team(str(darwin_row.get("team_id") or TEAM_ID))
    current = json.loads(LINUX_PIN.read_text(encoding="utf-8"))
    linux_pin = {
        "version": str(manifest["version"]),
        "source": current.get("source"),
        "manifest_base": current.get("manifest_base"),
        "platforms": linux,
    }
    darwin_pin = {
        "version": str(manifest["version"]),
        "url": darwin_row["url"],
        "sha512": darwin_row["sha512"],
        "team_id": TEAM_ID,
    }
    return linux_pin, darwin_pin


def npm_platforms(meta: dict) -> set[tuple[str, str]]:
    """os/cpu pairs declared on a registry document."""
    found = set()
    optional = meta.get("optionalDependencies") or {}
    for name in optional:
        for system, cpu in NPM_ARCH:
            if name.endswith(f"-{system}-{cpu}"):
                found.add((system, cpu))
    os_list, cpu_list = meta.get("os") or [], meta.get("cpu") or []
    if os_list and cpu_list:
        for system in os_list:
            for cpu in cpu_list:
                found.add((system, cpu))
    return found


def require_npm_arches(meta: dict) -> None:
    found = npm_platforms(meta)
    missing = [pair for pair in NPM_ARCH if pair not in found]
    if missing:
        raise ValueError(f"release is missing arches {missing}")


def write_pins(linux_pin: dict, darwin_pin: dict) -> None:
    LINUX_PIN.write_text(json.dumps(linux_pin, indent=2) + "\n", encoding="utf-8")
    DARWIN_PIN.write_text(json.dumps(darwin_pin, indent=2) + "\n", encoding="utf-8")


def write_npm_versions(versions: dict[str, str]) -> None:
    document = json.loads(NPM_PIN.read_text(encoding="utf-8"))
    for name, version in versions.items():
        if name not in document["dependencies"]:
            raise ValueError(f"unknown npm pin {name}")
        document["dependencies"][name] = version
    NPM_PIN.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def fetch_bytes(url: str, timeout: float = 120) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()


PLATFORM_MANIFEST = {
    "amd64": "linux_amd64.json",
    "arm64": "linux_arm64.json",
    "darwin-arm64": "darwin_arm64.json",
}


def apply_agy() -> bool:
    """Download each official platform manifest, verify it, and write pins."""
    current = json.loads(LINUX_PIN.read_text(encoding="utf-8"))
    base = str(current["manifest_base"])
    if not base.endswith("/"):
        base += "/"
    require_trusted_url(base, TRUSTED_MANIFESTS)
    combined: dict = {"version": None, "platforms": {}}
    blobs: dict[str, bytes] = {}
    for arch, filename in PLATFORM_MANIFEST.items():
        doc = json.loads(fetch_bytes(base + filename).decode("utf-8"))
        url, digest = doc["url"], doc["sha512"]
        require_trusted_url(url)
        require_arch(url, arch)
        payload = fetch_bytes(url)
        require_sha512_hex(payload, digest)
        version = str(doc["version"])
        if combined["version"] is None:
            combined["version"] = version
        elif combined["version"] != version:
            raise ValueError("Antigravity platform versions disagree")
        row = {"url": url, "sha512": digest}
        if doc.get("team_id"):
            row["team_id"] = str(doc["team_id"])
        combined["platforms"][arch] = row
        blobs[url] = payload
    linux_pin, darwin_pin = verified_agy_pins(combined, blobs)
    previous = json.loads(DARWIN_PIN.read_text(encoding="utf-8"))
    changed = current.get("version") != linux_pin["version"] or previous.get("sha512") != darwin_pin["sha512"]
    if changed:
        write_pins(linux_pin, darwin_pin)
    print(f"antigravity {linux_pin['version']} checksum, host and arches ok; darwin team id pinned for the installer")
    return changed


def apply_npm() -> bool:
    """Verify each npm CLI tarball, then move package.json to that version."""
    import subprocess

    document = json.loads(NPM_PIN.read_text(encoding="utf-8"))
    versions: dict[str, str] = {}
    changed = False
    for name in NPM_PACKAGES:
        meta = json.loads(fetch_bytes(f"https://registry.npmjs.org/{name}/latest").decode("utf-8"))
        require_npm_arches(meta)
        dist = meta["dist"]
        require_trusted_url(dist["tarball"])
        require_npm_integrity(fetch_bytes(dist["tarball"]), dist["integrity"])
        version = str(meta["version"])
        versions[name] = version
        if document["dependencies"].get(name) != version:
            changed = True
        print(f"{name} {version} sha512 and arches ok")
    if changed:
        write_npm_versions(versions)
        subprocess.run(
            ["npm", "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund"],
            cwd=NPM_PIN.parent, check=True,
        )
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify vendor CLI pins before an update")
    parser.add_argument("--apply", action="store_true", help="Check upstream releases and write verified pins")
    args = parser.parse_args(argv)
    print("cursor: unwired, no pin JSON")
    if not args.apply:
        print("pass --apply to check upstream releases")
        return 0
    apply_agy()
    apply_npm()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
