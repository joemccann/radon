#!/usr/bin/env python3
"""Pull-based auto-deploy for the demo VM backend (runs ON the demo VM).

radon-demo-autodeploy.timer fires this every 15 minutes. It closes the drift
gap that a hand-run cloud/scripts/deploy-demo-vm.sh left open (the VM sat at
02db257b, 847 commits behind main, from 2026-09-04 to 2026-10-18).

Nothing pushes to the VM: fw-radon-demo admits SSH only from the recovery /32,
and handing the prod host a key to the demo VM would join the two blast
radii. The VM pulls instead, anonymously, from the public repository:

1. The newest ``ci.yml`` run on ``main`` (push) that concluded ``success``
   names the target sha. A green run means the full test gate passed and prod
   deployed it; the demo never runs a commit prod has not.
2. If the live ``/opt/radon-demo/app/REVISION`` already carries the same
   ``scripts`` / ``requirements.txt`` / ``pyproject.toml`` trees, nothing is
   built (two tree reads, no download).
3. Otherwise the commit tarball is downloaded, those three paths are repacked,
   and the INSTALLED (last known-good) ``deploy-demo-vm-remote.sh`` deploys
   them: health gates, isolation gates, automatic rollback.
4. After a green deploy the tooling is refreshed from that same commit, so a
   fix to this file or the remote script ships with the next backend change.

A sha whose deploy failed is remembered and not retried, so a broken build does
not rebuild every 15 minutes. The next green main sha is tried normally. The
daily demo-vm-drift workflow is the alert when the VM stays behind.

Stdlib only; run as ``python3 -I``.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

REPO = "joemccann/radon"
API = f"https://api.github.com/repos/{REPO}"
CODELOAD = f"https://codeload.github.com/{REPO}/tar.gz"
BACKEND_PATHS = ("scripts", "requirements.txt", "pyproject.toml")
TOOLING = {
    "cloud/scripts/deploy-demo-vm-remote.sh": "deploy-demo-vm-remote.sh",
    "cloud/demo-vm/demo_vm_autodeploy.py": "demo_vm_autodeploy.py",
}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

DEMO_ROOT = Path(os.environ.get("DEMO_ROOT", "/opt/radon-demo"))
LIB_DIR = Path(os.environ.get("DEMO_AUTODEPLOY_LIB", "/usr/local/lib/radon-demo"))
STATE_DIR = Path(os.environ.get("DEMO_AUTODEPLOY_STATE", "/var/lib/radon-demo-autodeploy"))
HTTP_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 300


def log(message: str) -> None:
    print(f"[demo-autodeploy] {message}", flush=True)


def http_get(url: str, timeout: float = HTTP_TIMEOUT) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "radon-demo-autodeploy"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https hosts
        return response.read()


def get_json(fetch, url: str):
    # Commit messages in the runs payload can carry raw control characters.
    return json.loads(fetch(url), strict=False)


def latest_green_main_sha(fetch) -> str | None:
    url = (
        f"{API}/actions/workflows/ci.yml/runs"
        "?branch=main&event=push&status=success&per_page=1&exclude_pull_requests=true"
    )
    runs = get_json(fetch, url).get("workflow_runs") or []
    if not runs:
        return None
    sha = runs[0].get("head_sha", "")
    return sha if SHA_RE.match(sha) else None


def backend_trees(fetch, sha: str) -> dict[str, str]:
    """Top-level tree/blob ids for BACKEND_PATHS at ``sha``."""
    tree = get_json(fetch, f"{API}/git/trees/{sha}")
    return {e["path"]: e["sha"] for e in tree.get("tree", []) if e.get("path") in BACKEND_PATHS}


def read_text(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def build_backend_tar(archive: bytes, sha: str) -> tuple[bytes, dict[str, bytes]]:
    """Repack BACKEND_PATHS from a codeload tarball; also return TOOLING files.

    The codeload tarball nests everything under ``<repo>-<sha>/``. The remote
    deploy wants git-archive layout (paths at the root), no AppleDouble
    entries, no top-level data/, and no links that could point outside app/.
    """
    out = io.BytesIO()
    tooling: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as src, tarfile.open(
        fileobj=out, mode="w"
    ) as dst:
        for member in src.getmembers():
            _, _, rel = member.name.partition("/")
            if not rel:
                continue
            if rel in TOOLING and member.isfile():
                handle = src.extractfile(member)
                if handle is not None:
                    tooling[rel] = handle.read()
            top = rel.split("/", 1)[0]
            if top not in BACKEND_PATHS:
                continue
            if any(part.startswith("._") for part in rel.split("/")):
                continue
            if not (member.isfile() or member.isdir()):
                continue
            member.name = rel
            dst.addfile(member, src.extractfile(member) if member.isfile() else None)
    names = set(tarfile.open(fileobj=io.BytesIO(out.getvalue())).getnames())
    for required in ("scripts/api/server.py", "requirements.txt"):
        if required not in names:
            raise RuntimeError(f"tarball for {sha} lacks {required}")
    missing = set(TOOLING) - set(tooling)
    if missing:
        raise RuntimeError(f"tarball for {sha} lacks tooling {sorted(missing)}")
    return out.getvalue(), tooling


def install_tooling(tooling: dict[str, bytes], lib_dir: Path) -> None:
    lib_dir.mkdir(parents=True, exist_ok=True)
    for rel, name in TOOLING.items():
        target = lib_dir / name
        tmp = target.with_suffix(target.suffix + ".new")
        tmp.write_bytes(tooling[rel])
        tmp.chmod(0o755)
        os.replace(tmp, target)


def run_remote_deploy(remote_script: Path, sha: str, tar_path: Path) -> int:
    return subprocess.call(["bash", str(remote_script), "deploy", sha, str(tar_path)])


def run(
    fetch=http_get,
    deploy=run_remote_deploy,
    demo_root: Path = DEMO_ROOT,
    lib_dir: Path = LIB_DIR,
    state_dir: Path = STATE_DIR,
) -> int:
    target = latest_green_main_sha(fetch)
    if not target:
        log("no green main ci.yml run found; nothing to do")
        return 0
    live = read_text(demo_root / "app" / "REVISION")
    if live == target:
        log(f"live revision {target[:12]} is the latest green main; nothing to do")
        return 0

    failed_file = state_dir / "failed-sha"
    if read_text(failed_file) == target:
        log(f"{target[:12]} already failed to deploy; waiting for the next green main sha")
        return 0

    if SHA_RE.match(live):
        if backend_trees(fetch, live) == backend_trees(fetch, target):
            log(f"backend unchanged between live {live[:12]} and {target[:12]}; nothing to build")
            return 0
    else:
        log("live REVISION missing or malformed; deploying")

    remote_script = lib_dir / TOOLING["cloud/scripts/deploy-demo-vm-remote.sh"]
    if not remote_script.is_file():
        log(f"ERROR: {remote_script} is not installed (run deploy-demo-vm.sh --install-autodeploy)")
        return 1

    log(f"deploying {target[:12]} (live {live[:12] or 'none'})")
    archive = fetch(f"{CODELOAD}/{target}", timeout=DOWNLOAD_TIMEOUT)
    backend_tar, tooling = build_backend_tar(archive, target)

    incoming = demo_root / ".deploy-incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=incoming, suffix=".tar", delete=False) as handle:
        handle.write(backend_tar)
        tar_path = Path(handle.name)
    try:
        code = deploy(remote_script, target, tar_path)
    finally:
        tar_path.unlink(missing_ok=True)

    state_dir.mkdir(parents=True, exist_ok=True)
    if code != 0:
        failed_file.write_text(target + "\n")
        log(f"ERROR: deploy of {target[:12]} failed (exit {code}); the remote script rolled back")
        return 1
    failed_file.unlink(missing_ok=True)
    install_tooling(tooling, lib_dir)
    log(f"LIVE {target[:12]}; tooling refreshed from the same commit")
    return 0


def main() -> int:
    if shutil.which("docker") is None:
        log("ERROR: docker not on PATH")
        return 1
    try:
        return run()
    except Exception as exc:  # noqa: BLE001 - a oneshot reports and exits non-zero
        log(f"ERROR: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
