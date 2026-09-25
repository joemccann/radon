"""Root reads privileged artifacts from its own clone of the pinned remote.

`radon-deploy-root` verbs (sync-control-plane, refresh-control-plane,
install-units, sync-scheduled-units, publish-caddy) install bytes as root.
The radon-owned checkout store at /home/radon/radon/.git is not a safe place
to read them from: its object files and alternates are writable by the
service account, and a plain git read does not re-hash an object. Root keeps
a root-owned bare clone, fetched from the pinned remote with object checking,
and only the HEAD commit id is ever read from the checkout store.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_sync_control_plane import (  # noqa: E402
    ROOT_HELPER,
    _commit_all,
    _git,
    _helper_env,
    _init_release_repo,
    _run_sync,
    _write_executable,
    function_body,
)

HELPER_REL = "cloud/scripts/deploy-root-helper.sh"
ORIGINAL = "#!/bin/bash\n# tip helper\n"
FORGED = "#!/bin/bash\n# forged in the checkout store\n"


def _object_file(git_dir: Path, sha: str) -> Path:
    return git_dir / "objects" / sha[:2] / sha[2:]


def _write_loose(objects: Path, sha: str, kind: str, body: bytes) -> None:
    """A loose object file NAMED `sha` whose content is `body`."""
    path = objects / sha[:2] / sha[2:]
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.chmod(0o644)
        path.unlink()
    path.write_bytes(zlib.compress(f"{kind} {len(body)}\0".encode() + body))


def _release(tmp_path: Path) -> tuple[Path, Path, str]:
    """radon's checkout (loose objects) plus an independent bare "GitHub"."""
    repo, _ = _init_release_repo(tmp_path)
    remote = tmp_path / "github.git"
    subprocess.run(
        ["git", "clone", "-q", "--bare", "--no-local", str(repo), str(remote)],
        check=True,
        capture_output=True,
    )
    victim = _git(repo, "rev-parse", f"HEAD:{HELPER_REL}")
    assert _object_file(repo / ".git", victim).is_file()
    return repo, remote, victim


def _capturing_env(tmp_path: Path, repo: Path, remote: Path) -> tuple[dict[str, str], Path]:
    env, _ = _helper_env(tmp_path, repo, remote=remote)
    captured = tmp_path / "bootstrap-saw-helper"
    runner = tmp_path / "capturing-runner"
    _write_executable(
        runner,
        f'#!/bin/bash\ncat "$RADON_BOOTSTRAP_CLOUD_ROOT/scripts/deploy-root-helper.sh" > {captured}\n',
    )
    env["RADON_TEST_BOOTSTRAP_RUNNER"] = str(runner)
    env["RADON_TEST_PROVISION_ROOT"] = str(tmp_path / "provision")
    return env, captured


def _fetch_failing_git(tmp_path: Path) -> Path:
    real = shutil.which("git")
    assert real
    wrapper = tmp_path / "git-no-fetch"
    _write_executable(
        wrapper,
        f"""#!/bin/bash
for arg in "$@"; do
  [[ "$arg" == fetch ]] && {{ echo "fatal: unable to access remote" >&2; exit 128; }}
done
exec {real} "$@"
""",
    )
    return wrapper


def _checkout_serves(repo: Path, sha: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "cat-file", "blob", sha],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


class TestCheckoutStoreTamperingNeverReachesRoot:
    def test_rewritten_object_file(self, tmp_path: Path) -> None:
        repo, remote, victim = _release(tmp_path)
        _write_loose(repo / ".git" / "objects", victim, "blob", FORGED.encode())
        assert _checkout_serves(repo, victim) == FORGED
        env, captured = _capturing_env(tmp_path, repo, remote)

        result = _run_sync(env)

        assert result.returncode == 0, result.stdout + result.stderr
        assert captured.read_text(encoding="utf-8") == ORIGINAL

    def test_alternates_entry(self, tmp_path: Path) -> None:
        repo, remote, victim = _release(tmp_path)
        loose = _object_file(repo / ".git", victim)
        loose.chmod(0o644)
        loose.unlink()
        alternate = tmp_path / "planted-objects"
        _write_loose(alternate, victim, "blob", FORGED.encode())
        info = repo / ".git" / "objects" / "info"
        info.mkdir(parents=True, exist_ok=True)
        (info / "alternates").write_text(f"{alternate}\n", encoding="utf-8")
        assert _checkout_serves(repo, victim) == FORGED
        env, captured = _capturing_env(tmp_path, repo, remote)

        result = _run_sync(env)

        assert result.returncode == 0, result.stdout + result.stderr
        assert captured.read_text(encoding="utf-8") == ORIGINAL

    def test_replace_ref(self, tmp_path: Path) -> None:
        repo, remote, victim = _release(tmp_path)
        forged = subprocess.run(
            ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
            input=FORGED, check=True, capture_output=True, text=True,
        ).stdout.strip()
        _git(repo, "replace", victim, forged)
        assert _checkout_serves(repo, victim) == FORGED
        env, captured = _capturing_env(tmp_path, repo, remote)

        result = _run_sync(env)

        assert result.returncode == 0, result.stdout + result.stderr
        assert captured.read_text(encoding="utf-8") == ORIGINAL


class TestRootProvisionStore:
    def test_a_tip_the_checkout_never_fetched_is_fetched_by_root(self, tmp_path: Path) -> None:
        repo, remote, _ = _release(tmp_path)
        newer = tmp_path / "newer"
        subprocess.run(["git", "clone", "-q", str(remote), str(newer)], check=True)
        (newer / HELPER_REL).write_text("#!/bin/bash\n# newer tip\n", encoding="utf-8")
        _commit_all(newer, "newer tip")
        _git(newer, "push", "-q", "origin", "HEAD:main")
        env, captured = _capturing_env(tmp_path, repo, remote)

        result = _run_sync(env)

        assert result.returncode == 0, result.stdout + result.stderr
        assert captured.read_text(encoding="utf-8") == "#!/bin/bash\n# newer tip\n"

    def test_fetch_failure_fails_closed(self, tmp_path: Path) -> None:
        # The checkout store carries the tip; root still refuses to read it.
        repo, remote, _ = _release(tmp_path)
        env, captured = _capturing_env(tmp_path, repo, remote)
        env["RADON_TEST_GIT"] = str(_fetch_failing_git(tmp_path))

        result = _run_sync(env)

        assert result.returncode == 69, result.stdout + result.stderr
        assert "provision store" in result.stderr
        assert not captured.exists()

    def test_first_run_creates_a_root_owned_bare_clone_and_reruns_skip_the_network(
        self, tmp_path: Path
    ) -> None:
        repo, remote, _ = _release(tmp_path)
        env, captured = _capturing_env(tmp_path, repo, remote)
        store = tmp_path / "provision"
        assert not store.exists()

        first = _run_sync(env)

        assert first.returncode == 0, first.stdout + first.stderr
        for path in (store, store / "radon.git", store / "radon.git" / "objects"):
            assert path.is_dir() and not path.is_symlink(), path
            assert path.stat().st_uid == os.getuid(), path
            assert stat.S_IMODE(path.stat().st_mode) & 0o022 == 0, path
        assert _git(store / "radon.git", "rev-parse", "--is-bare-repository") == "true"
        # Staging happens inside the root-owned store and is cleaned up.
        assert not list(store.glob("control-plane-sync.*"))

        captured.unlink()
        env["RADON_TEST_GIT"] = str(_fetch_failing_git(tmp_path))
        second = _run_sync(env)

        assert second.returncode == 0, second.stdout + second.stderr
        assert captured.read_text(encoding="utf-8") == ORIGINAL

    def test_a_writable_or_linked_store_is_refused(self, tmp_path: Path) -> None:
        repo, remote, _ = _release(tmp_path)
        env, captured = _capturing_env(tmp_path, repo, remote)
        store = tmp_path / "provision"
        store.mkdir()
        store.chmod(0o777)

        writable = _run_sync(env)

        assert writable.returncode == 74, writable.stdout + writable.stderr
        assert not captured.exists()

        store.chmod(0o755)
        store.rename(tmp_path / "elsewhere")
        store.symlink_to(tmp_path / "elsewhere")
        linked = _run_sync(env)

        assert linked.returncode == 74, linked.stdout + linked.stderr
        assert not captured.exists()


class TestContract:
    HELPER = ROOT_HELPER.read_text(encoding="utf-8")

    def test_the_checkout_store_supplies_only_the_head_commit_id(self) -> None:
        reads = [
            line.strip()
            for line in self.HELPER.splitlines()
            if '--git-dir="$RADON_GIT_DIR"' in line and not line.lstrip().startswith("#")
        ]
        assert reads, "expected the HEAD claim read"
        for line in reads:
            assert "rev-parse HEAD" in line, line

    def test_production_store_is_root_owned_outside_radon_paths(self) -> None:
        assert "readonly PROVISION_ROOT=/opt/radon-provision" in self.HELPER
        assert 'readonly PROVISION_GIT_DIR="${PROVISION_ROOT}/radon.git"' in self.HELPER

    def test_root_git_ignores_system_and_global_config(self) -> None:
        for name in ("provision_git", "provision_fetch"):
            body = function_body(self.HELPER, name)
            assert "GIT_CONFIG_NOSYSTEM=1" in body, name
            assert "GIT_CONFIG_GLOBAL=/dev/null" in body, name
            assert "GIT_TERMINAL_PROMPT=0" in body, name

    def test_fetch_checks_objects_from_the_pinned_remote_only(self) -> None:
        body = function_body(self.HELPER, "provision_fetch")
        assert "fetch.fsckObjects=true" in body
        assert "transfer.fsckObjects=true" in body
        assert '"$UNIT_REMOTE"' in body
        assert "RADON_GIT_DIR" not in body
        assert re.search(r"\b\d+s\b", body), "fetch must be bounded"

    def test_every_tip_resolver_uses_the_root_store(self) -> None:
        for name in (
            "resolve_trusted_main_tip",
            "resolve_fetched_main_tip",
            "resolve_deployed_control_plane_commit",
        ):
            body = function_body(self.HELPER, name)
            assert "provision_store_has_tip" in body, name
