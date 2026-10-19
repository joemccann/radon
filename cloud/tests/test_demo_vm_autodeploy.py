"""Pull-based demo VM auto-deploy: cloud/demo-vm/.

2026-10-18: after the VM was redeployed by hand, the operator asked that it
never drift again. The VM cannot be pushed to (fw-radon-demo admits SSH only
from the recovery /32), so radon-demo-autodeploy.timer pulls the latest green
main every 15 minutes. These tests pin the agent's decisions with a fake
GitHub and a fake deploy, plus the units and the one-time installer.
"""
from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

from _bash_toolchain import MODERN_BASH, requires_modern_bash

CLOUD = Path(__file__).resolve().parents[1]
REPO = CLOUD.parent
DEMO_VM = CLOUD / "demo-vm"
LOCAL = CLOUD / "scripts" / "deploy-demo-vm.sh"

_spec = importlib.util.spec_from_file_location("demo_vm_autodeploy", DEMO_VM / "demo_vm_autodeploy.py")
agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent)

LIVE = "1" * 40
TARGET = "2" * 40
API = agent.API


def _codeload(sha: str, members: dict[str, str]) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        top = tarfile.TarInfo(f"radon-{sha}")
        top.type = tarfile.DIRTYPE
        tar.addfile(top)
        for name, content in members.items():
            data = content.encode()
            info = tarfile.TarInfo(f"radon-{sha}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


FULL_TREE = {
    "scripts/api/server.py": "# server\n",
    "requirements.txt": "fastapi\n",
    "pyproject.toml": "[project]\n",
    "cloud/scripts/deploy-demo-vm-remote.sh": "#!/usr/bin/env bash\n# remote v2\n",
    "cloud/demo-vm/demo_vm_autodeploy.py": "# agent v2\n",
    "web/app/page.tsx": "export default 1\n",
    "scripts/._server.py": "appledouble\n",
}


class FakeGitHub:
    def __init__(self, green: str | None = TARGET, trees: dict[str, dict[str, str]] | None = None):
        self.green = green
        self.trees = trees or {}
        self.calls: list[str] = []
        self.archive = _codeload(TARGET, FULL_TREE)

    def __call__(self, url: str, timeout: float = 30) -> bytes:
        self.calls.append(url)
        if "/actions/workflows/ci.yml/runs" in url:
            runs = [] if self.green is None else [{"head_sha": self.green, "head_commit": {"message": "a\x01b"}}]
            # Raw control character, as GitHub returns in commit messages.
            return json.dumps({"workflow_runs": runs}).replace("\\u0001", "\x01").encode()
        if "/git/trees/" in url:
            sha = url.rsplit("/", 1)[1]
            tree = self.trees.get(sha, {})
            return json.dumps({"tree": [{"path": p, "sha": s} for p, s in tree.items()]}).encode()
        if url.startswith(agent.CODELOAD):
            return self.archive
        raise AssertionError(f"unexpected fetch {url}")


@pytest.fixture
def vm(tmp_path: Path):
    root = tmp_path / "opt-radon-demo"
    (root / "app").mkdir(parents=True)
    (root / "app" / "REVISION").write_text(LIVE + "\n")
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "deploy-demo-vm-remote.sh").write_text("# remote v1\n")
    (lib / "demo_vm_autodeploy.py").write_text("# agent v1\n")
    state = tmp_path / "state"
    return root, lib, state


class Deploys:
    def __init__(self, code: int = 0):
        self.code = code
        self.calls: list[tuple[Path, str, list[str]]] = []

    def __call__(self, script: Path, sha: str, tar_path: Path) -> int:
        with tarfile.open(tar_path) as tar:
            names = tar.getnames()
        self.calls.append((script, sha, names))
        return self.code


def _run(gh, deploy, vm):
    root, lib, state = vm
    return agent.run(fetch=gh, deploy=deploy, demo_root=root, lib_dir=lib, state_dir=state)


CHANGED = {LIVE: {"scripts": "t1", "requirements.txt": "r1"}, TARGET: {"scripts": "t2", "requirements.txt": "r1"}}


def test_noop_when_live_is_latest_green(vm):
    gh, deploy = FakeGitHub(green=LIVE), Deploys()
    assert _run(gh, deploy, vm) == 0
    assert deploy.calls == []
    assert not any(agent.CODELOAD in c for c in gh.calls)


def test_noop_without_a_green_run(vm):
    gh, deploy = FakeGitHub(green=None), Deploys()
    assert _run(gh, deploy, vm) == 0
    assert deploy.calls == []


def test_unchanged_backend_skips_the_build(vm):
    same = {"scripts": "t1", "requirements.txt": "r1", "pyproject.toml": "p1"}
    gh, deploy = FakeGitHub(trees={LIVE: same, TARGET: same}), Deploys()
    assert _run(gh, deploy, vm) == 0
    assert deploy.calls == []
    assert not any(agent.CODELOAD in c for c in gh.calls)


def test_changed_backend_deploys_with_the_installed_remote_and_refreshes_tooling(vm):
    root, lib, state = vm
    gh, deploy = FakeGitHub(trees=CHANGED), Deploys(0)
    assert _run(gh, deploy, vm) == 0
    [(script, sha, names)] = deploy.calls
    # The last known-good installed script deploys, never the incoming one.
    assert script == lib / "deploy-demo-vm-remote.sh"
    assert sha == TARGET
    assert "scripts/api/server.py" in names and "requirements.txt" in names
    assert not any(n.startswith("web") or n.startswith("cloud") for n in names)
    assert not any("._" in n for n in names)
    assert f"{agent.CODELOAD}/{TARGET}" in gh.calls
    # Tooling refreshed from the deployed commit only after success.
    assert (lib / "deploy-demo-vm-remote.sh").read_text().endswith("# remote v2\n")
    assert (lib / "demo_vm_autodeploy.py").read_text() == "# agent v2\n"
    assert not list((root / ".deploy-incoming").iterdir())


def test_failed_deploy_is_remembered_and_not_retried(vm):
    root, lib, state = vm
    gh, deploy = FakeGitHub(trees=CHANGED), Deploys(1)
    assert _run(gh, deploy, vm) == 1
    assert (state / "failed-sha").read_text().strip() == TARGET
    # Tooling is NOT refreshed from a commit that failed to deploy.
    assert (lib / "deploy-demo-vm-remote.sh").read_text() == "# remote v1\n"
    again = Deploys(0)
    assert _run(FakeGitHub(trees=CHANGED), again, vm) == 0
    assert again.calls == []


def test_a_new_green_sha_is_tried_after_a_failure(vm):
    root, lib, state = vm
    state.mkdir()
    (state / "failed-sha").write_text("3" * 40 + "\n")
    deploy = Deploys(0)
    assert _run(FakeGitHub(trees=CHANGED), deploy, vm) == 0
    assert len(deploy.calls) == 1
    assert not (state / "failed-sha").exists()


def test_missing_revision_deploys(vm):
    root, lib, state = vm
    (root / "app" / "REVISION").unlink()
    deploy = Deploys(0)
    assert _run(FakeGitHub(), deploy, vm) == 0
    assert len(deploy.calls) == 1


def test_refuses_without_an_installed_remote_script(vm):
    root, lib, state = vm
    (lib / "deploy-demo-vm-remote.sh").unlink()
    deploy = Deploys(0)
    assert _run(FakeGitHub(trees=CHANGED), deploy, vm) == 1
    assert deploy.calls == []


def test_tarball_without_server_is_refused():
    archive = _codeload(TARGET, {"requirements.txt": "x\n"})
    with pytest.raises(RuntimeError, match="scripts/api/server.py"):
        agent.build_backend_tar(archive, TARGET)


def test_green_run_query_targets_main_push_success():
    gh = FakeGitHub()
    assert agent.latest_green_main_sha(gh) == TARGET
    url = gh.calls[0]
    for part in ("ci.yml/runs", "branch=main", "event=push", "status=success"):
        assert part in url


def test_units():
    service = (DEMO_VM / "radon-demo-autodeploy.service").read_text()
    timer = (DEMO_VM / "radon-demo-autodeploy.timer").read_text()
    assert "Type=oneshot" in service
    assert "ExecStart=/usr/bin/python3 -I /usr/local/lib/radon-demo/demo_vm_autodeploy.py" in service
    assert "OnCalendar=*:0/15" in timer
    assert "Persistent=true" in timer


def test_units_are_not_in_the_prod_unit_tree():
    # cloud/services/ units install on the prod app host via install-units.
    assert not list((CLOUD / "services").glob("radon-demo-autodeploy.*"))


@requires_modern_bash
def test_install_autodeploy_dry_run_contacts_no_host(tmp_path: Path):
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
    sha = head.stdout.strip()
    ls = subprocess.run(
        ["git", "-C", str(REPO), "cat-file", "-e", f"{sha}:cloud/demo-vm/demo_vm_autodeploy.py"], capture_output=True
    )
    if ls.returncode != 0:
        pytest.skip("HEAD does not carry cloud/demo-vm yet (uncommitted working tree)")
    ssh = tmp_path / "ssh"
    ssh.write_text("#!/usr/bin/env bash\necho CONTACTED >&2\nexit 99\n")
    ssh.chmod(0o755)
    result = subprocess.run(
        [MODERN_BASH, str(LOCAL), "--install-autodeploy", "--dry-run", sha],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/opt/homebrew/bin", "SSH": str(ssh), "TMPDIR": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    assert "DRY RUN" in result.stdout
    assert "CONTACTED" not in result.stderr
