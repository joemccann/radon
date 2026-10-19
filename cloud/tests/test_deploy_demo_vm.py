"""Demo VM backend deploy: cloud/scripts/deploy-demo-vm{,-remote}.sh.

2026-10-18: the demo VM ran commit 02db257b, 847 commits behind main in
scripts/, shipped by a manual ssh+tar that carried 2,193 macOS ``._*`` files,
and 8 routes the demo frontend calls 404'd. These suites pin the replacement:
an exact-sha git-archive ship that never writes the VM's data/ seed, writes
REVISION, health-gates through the Caddy Host pin, asserts isolation inside the
running container, and rolls back on any failure.

The remote half runs here against a fake /opt/radon-demo with stub docker,
curl and flock; the operator half runs with stub ssh and curl.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from _bash_toolchain import MODERN_BASH, requires_modern_bash

pytestmark = requires_modern_bash

CLOUD = Path(__file__).resolve().parents[1]
REPO = CLOUD.parent
LOCAL = CLOUD / "scripts" / "deploy-demo-vm.sh"
REMOTE = CLOUD / "scripts" / "deploy-demo-vm-remote.sh"

SHA = "a" * 40
OLD_SEED = '{"ticker": "AAPL", "seed": true}\n'
TOKEN = "svc-token-never-in-argv-7f3e"


def _stub(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _tarball(path: Path, members: dict[str, str]) -> Path:
    with tarfile.open(path, "w") as tar:
        for name, content in members.items():
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


GOOD_MEMBERS = {
    "scripts/api/server.py": "# new server\n",
    "scripts/api/__init__.py": "",
    "requirements.txt": "fastapi\n",
    "pyproject.toml": "[project]\n",
}


# --------------------------------------------------------------------------
# Remote half
# --------------------------------------------------------------------------


@pytest.fixture
def vm(tmp_path: Path):
    root = tmp_path / "opt-radon-demo"
    app = root / "app"
    (app / "data" / "flow_reports").mkdir(parents=True)
    (app / "data" / "flow_reports" / "AAPL.json").write_text(OLD_SEED)
    (app / "scripts" / "api").mkdir(parents=True)
    (app / "scripts" / "api" / "server.py").write_text("# old server\n")
    (app / "requirements.txt").write_text("fastapi\n")
    (root / "docker-compose.yml").write_text("services: {}\n")
    (root / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (root / ".env").write_text(
        "# demo\nRADON_API_TEST_MODE=1\nTURSO_DB_URL=libsql://radon-demo-joemccann.example.turso.io\n"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    log = f'printf "%s %s\\n" "$(basename "$0")" "$*" >> "{calls}"\n'

    # The "container" serves whatever tree app/ holds right now: /health
    # reports app/REVISION, so a rollback to a REVISION-less tree drops it.
    _stub(bin_dir / "docker", log + f"""
ROOT="{root}"
rev() {{ cat "$ROOT/app/REVISION" 2>/dev/null | tr -d '\\n'; }}
if [[ "$1" == compose ]]; then
  shift
  case "$1" in
    ps) echo cid123 ;;
    build) exit "${{STUB_BUILD_RC:-0}}" ;;
    up) exit 0 ;;
    exec)
      shift 3  # exec -T api
      case "$1" in
        curl)
          [[ "${{STUB_INTERNAL:-ok}}" == ok ]] || exit 7
          r="$(rev)"
          if [[ -n "$r" ]]; then printf '{{"status":"ok","revision":"%s"}}' "$r"; else printf '{{"status":"ok"}}'; fi ;;
        printenv)
          case "$2" in
            RADON_API_TEST_MODE) echo "${{STUB_TEST_MODE-1}}" ;;
            TURSO_DB_URL) echo "${{STUB_DB_URL-libsql://radon-demo-joemccann.example.turso.io}}" ;;
            RADON_SERVICE_TOKEN) echo "{TOKEN}" ;;
          esac ;;
      esac ;;
  esac
  exit 0
fi
case "$1" in
  inspect) echo sha256:oldimage ;;
  image) exit "${{STUB_NO_ROLLBACK_IMAGE:-0}}" ;;
  tag) exit 0 ;;
esac
""")
    # Edge curl through Caddy: STUB_EDGE=400 reproduces the Host-pin trap.
    _stub(bin_dir / "curl", log + f"""
ROOT="{root}"
url="${{@: -1}}"
code=200
[[ "${{STUB_EDGE:-ok}}" == ok ]] || code="$STUB_EDGE"
want_code=0; header_stdin=0
for a in "$@"; do
  [[ "$a" == "%{{http_code}}" ]] && want_code=1
  [[ "$a" == "@-" ]] && header_stdin=1
done
if [[ "$url" == */health/lite ]]; then
  if [[ $header_stdin == 1 ]] && grep -q "X-Radon-Service-Token: {TOKEN}" ; then code=200; else code=401; fi
fi
if [[ $want_code == 1 ]]; then printf '%s' "$code"; exit 0; fi
[[ "$code" == 200 ]] || exit 22
r="$(cat "$ROOT/app/REVISION" 2>/dev/null | tr -d '\\n')"
printf '{{"status":"ok","revision":"%s"}}' "$r"
""")
    # flock(1) is absent on macOS; lock the inherited fd with fcntl, which is
    # the same open-file-description lock the VM's flock(1) takes.
    _stub(bin_dir / "flock", f"""
exec "{sys.executable}" -c 'import fcntl,sys
try:
    fcntl.flock(int(sys.argv[1]), fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit(1)' "$2"
""")

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "DEMO_ROOT": str(root),
        "DEMO_HEALTH_TIMEOUT_SECS": "0",
        "DEMO_HEALTH_POLL_SECS": "0",
    }

    class VM:
        def __init__(self):
            self.root = root
            self.app = app
            self.calls = calls
            self.tmp = tmp_path

        def run(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [MODERN_BASH, str(REMOTE), *args],
                env={**env, **extra},
                capture_output=True,
                text=True,
                timeout=60,
            )

        def deploy(self, members=None, sha=SHA, **extra):
            tar = _tarball(self.tmp / f"{sha[:8]}.tar", members or GOOD_MEMBERS)
            return self.run("deploy", sha, str(tar), **extra), tar

        def log(self) -> str:
            return calls.read_text() if calls.exists() else ""

    return VM()


def _seed(app: Path) -> str:
    return (app / "data" / "flow_reports" / "AAPL.json").read_text()


class TestRemoteDeploy:
    def test_success_swaps_tree_writes_revision_and_keeps_seed(self, vm):
        result, tar = vm.deploy()
        assert result.returncode == 0, result.stderr + result.stdout
        assert f"LIVE {SHA}" in result.stdout
        assert (vm.app / "REVISION").read_text().strip() == SHA
        assert (vm.app / "scripts" / "api" / "server.py").read_text() == "# new server\n"
        assert _seed(vm.app) == OLD_SEED
        # Previous tree, seed included, kept for rollback.
        assert (vm.root / "app.bak" / "scripts" / "api" / "server.py").read_text() == "# old server\n"
        assert _seed(vm.root / "app.bak") == OLD_SEED
        log = vm.log()
        assert "docker tag sha256:oldimage radon-demo-api:rollback" in log
        assert "docker compose build api" in log
        assert "docker compose up -d api" in log
        assert not tar.exists()
        assert not list(vm.app.rglob("._*"))

    def test_rollback_tag_precedes_build(self, vm):
        result, _ = vm.deploy()
        assert result.returncode == 0
        log = vm.log()
        assert log.index("radon-demo-api:rollback") < log.index("compose build api")

    def test_health_lite_token_never_in_argv(self, vm):
        result, _ = vm.deploy()
        assert result.returncode == 0
        assert TOKEN not in vm.log()
        assert TOKEN not in result.stdout + result.stderr

    def test_rerun_of_live_sha_is_a_noop(self, vm):
        first, _ = vm.deploy()
        assert first.returncode == 0
        vm.calls.write_text("")
        second, tar = vm.deploy()
        assert second.returncode == 0, second.stderr
        assert f"already live: {SHA}" in second.stdout
        assert "compose build" not in vm.log()
        assert "docker tag" not in vm.log()
        assert not tar.exists()

    @pytest.mark.parametrize("bad", ["abc123", "A" * 40, "main", "a" * 39, "a" * 41])
    def test_refuses_non_40_hex_sha(self, vm, bad):
        result, _ = vm.deploy(sha=bad)
        assert result.returncode != 0
        assert "40 lowercase hex" in result.stderr
        assert "compose" not in vm.log()


class TestRemoteRefusals:
    def test_archive_with_appledouble_is_refused_before_swap(self, vm):
        members = {**GOOD_MEMBERS, "scripts/._server.py": "junk"}
        result, _ = vm.deploy(members=members)
        assert result.returncode != 0
        assert "AppleDouble" in result.stderr
        assert (vm.app / "scripts" / "api" / "server.py").read_text() == "# old server\n"
        assert "docker tag" not in vm.log()

    def test_archive_with_data_is_refused_and_seed_untouched(self, vm):
        members = {**GOOD_MEMBERS, "data/flow_reports/AAPL.json": "OVERWRITE"}
        result, _ = vm.deploy(members=members)
        assert result.returncode != 0
        assert "data/" in result.stderr
        assert _seed(vm.app) == OLD_SEED
        assert not (vm.app / "REVISION").exists()

    def test_missing_seed_refuses(self, vm):
        import shutil

        shutil.rmtree(vm.app / "data")
        result, _ = vm.deploy()
        assert result.returncode != 0
        assert "flow_reports seed" in result.stderr

    def test_env_without_test_mode_refuses(self, vm):
        (vm.root / ".env").write_text("TURSO_DB_URL=libsql://radon-demo-x.turso.io\n")
        result, _ = vm.deploy()
        assert result.returncode != 0
        assert "RADON_API_TEST_MODE=1" in result.stderr
        assert "compose build" not in vm.log()

    def test_env_pointing_at_prod_db_refuses(self, vm):
        (vm.root / ".env").write_text(
            "RADON_API_TEST_MODE=1\nTURSO_DB_URL=libsql://radon-joemccann.turso.io?x=radon-demo\n"
        )
        result, _ = vm.deploy()
        assert result.returncode != 0
        assert "PROD" in result.stderr

    def test_concurrent_run_is_refused(self, vm):
        lock = vm.root / ".deploy.lock"
        holder = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import fcntl,sys,time\n"
                f"f=open({str(lock)!r},'w'); fcntl.flock(f, fcntl.LOCK_EX)\n"
                "print('held', flush=True); time.sleep(30)",
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout.readline().strip() == "held"
            result, _ = vm.deploy()
        finally:
            holder.kill()
            holder.wait()
        assert result.returncode != 0
        assert "another demo deploy" in result.stderr
        assert "compose" not in vm.log()


class TestRemoteRollback:
    def _assert_rolled_back(self, vm, result):
        assert result.returncode != 0
        assert "ROLLBACK complete" in result.stdout
        assert (vm.app / "scripts" / "api" / "server.py").read_text() == "# old server\n"
        assert not (vm.app / "REVISION").exists()
        assert _seed(vm.app) == OLD_SEED
        assert (vm.root / "app.failed" / "REVISION").read_text().strip() == SHA
        log = vm.log()
        assert "docker tag radon-demo-api:rollback radon-demo-api:latest" in log
        assert "docker compose up -d --no-build api" in log

    def test_test_mode_off_in_container_rolls_back(self, vm):
        result, _ = vm.deploy(STUB_TEST_MODE="0")
        assert "RADON_API_TEST_MODE != 1" in result.stdout
        self._assert_rolled_back(vm, result)

    def test_prod_db_in_container_rolls_back(self, vm):
        result, _ = vm.deploy(STUB_DB_URL="libsql://radon-joemccann.turso.io")
        self._assert_rolled_back(vm, result)

    def test_container_db_without_demo_marker_rolls_back(self, vm):
        result, _ = vm.deploy(STUB_DB_URL="")
        assert "lacks radon-demo" in result.stdout
        self._assert_rolled_back(vm, result)

    def test_host_pin_400_at_the_edge_rolls_back(self, vm):
        # 127.0.0.1 health stays green while Caddy-proxied requests 400.
        result, _ = vm.deploy(STUB_EDGE="400")
        assert "edge /health failed" in result.stdout
        self._assert_rolled_back(vm, result)

    def test_build_failure_rolls_back(self, vm):
        result, _ = vm.deploy(STUB_BUILD_RC="1")
        self._assert_rolled_back(vm, result)

    def test_explicit_rollback_mode(self, vm):
        first, _ = vm.deploy()
        assert first.returncode == 0
        result = vm.run("rollback")
        assert result.returncode == 0, result.stderr
        assert (vm.app / "scripts" / "api" / "server.py").read_text() == "# old server\n"
        assert _seed(vm.app) == OLD_SEED

    def test_rollback_without_image_refuses(self, vm):
        result = vm.run("rollback", STUB_NO_ROLLBACK_IMAGE="1")
        assert result.returncode != 0
        assert "no radon-demo-api:rollback" in result.stderr


# --------------------------------------------------------------------------
# Operator half
# --------------------------------------------------------------------------


def _head_sha() -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def operator(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "ssh.log"
    shipped = tmp_path / "shipped.tar"
    remote_script = tmp_path / "remote-script.sh"
    # ssh stub: the archive upload lands in shipped.tar, the remote script
    # body in remote-script.sh; STUB_REMOTE_OUT is what the VM "prints".
    _stub(bin_dir / "ssh", f"""
printf '%s\\n' "$*" >> "{calls}"
case "$2" in
  *"cat >"*) cat > "{shipped}" ;;
  *"bash -s -- deploy"*) cat > "{remote_script}"; printf '%s\\n' "${{STUB_REMOTE_OUT:-[demo-deploy] LIVE}}"; exit "${{STUB_REMOTE_RC:-0}}" ;;
  *"bash -s -- rollback"*) cat > /dev/null ;;
esac
""")
    _stub(bin_dir / "curl", """
printf '{"status":"ok","revision":"%s"}' "${STUB_PUBLIC_REVISION:-}"
""")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "DEMO_DEPLOY_LOCK_DIR": str(tmp_path / "lock"),
        "DEMO_EXTERNAL_ATTEMPTS": "2",
        "DEMO_EXTERNAL_POLL_SECS": "0",
    }

    class Operator:
        def __init__(self):
            self.calls = calls
            self.shipped = shipped
            self.remote_script = remote_script
            self.lock = tmp_path / "lock"

        def run(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [MODERN_BASH, str(LOCAL), *args],
                env={**env, **extra},
                capture_output=True,
                text=True,
                timeout=120,
            )

        def ssh_log(self) -> str:
            return calls.read_text() if calls.exists() else ""

    return Operator()


class TestOperatorArgs:
    @pytest.mark.parametrize("bad", ["abc123", "HEAD", "main", "A" * 40, "a" * 39])
    def test_refuses_non_40_hex_sha(self, operator, bad):
        result = operator.run(bad)
        assert result.returncode != 0
        assert "40-char lowercase hex" in result.stderr
        assert operator.ssh_log() == ""

    def test_refuses_unknown_commit(self, operator):
        result = operator.run("f" * 40)
        assert result.returncode != 0
        assert "not found" in result.stderr
        assert operator.ssh_log() == ""

    def test_requires_a_sha(self, operator):
        result = operator.run("--dry-run")
        assert result.returncode != 0
        assert "usage" in result.stderr


class TestOperatorDryRun:
    def test_dry_run_prints_plan_and_contacts_no_host(self, operator):
        sha = _head_sha()
        result = operator.run("--dry-run", sha)
        assert result.returncode == 0, result.stderr
        assert "DRY RUN" in result.stdout
        assert sha in result.stdout
        assert "0 AppleDouble, no data/" in result.stdout
        assert "app/data/ preserved" in result.stdout
        assert operator.ssh_log() == ""
        assert not operator.lock.exists()


class TestOperatorShip:
    def test_ships_clean_exact_sha_archive(self, operator):
        sha = _head_sha()
        result = operator.run(sha, STUB_PUBLIC_REVISION=sha)
        assert result.returncode == 0, result.stderr + result.stdout
        assert f"LIVE {sha}" in result.stdout
        with tarfile.open(operator.shipped) as tar:
            names = tar.getnames()
        assert "scripts/api/server.py" in names
        assert "requirements.txt" in names
        assert "pyproject.toml" in names
        assert not [n for n in names if Path(n).name.startswith("._")]
        assert not [n for n in names if n == "data" or n.startswith("data/")]
        assert not [n for n in names if not n.startswith(("scripts", "requirements.txt", "pyproject.toml"))]
        # The remote half is what runs on the VM.
        assert operator.remote_script.read_text() == REMOTE.read_text()
        log = operator.ssh_log()
        assert f"radon-demo bash -s -- deploy '{sha}' '/opt/radon-demo/.deploy-incoming/{sha}.tar'" in log
        assert "rollback" not in log
        assert not operator.lock.exists()

    def test_external_revision_mismatch_rolls_back(self, operator):
        sha = _head_sha()
        result = operator.run(sha, STUB_PUBLIC_REVISION="b" * 40)
        assert result.returncode != 0
        assert "rolled back" in result.stderr
        assert "bash -s -- rollback" in operator.ssh_log()

    def test_noop_run_never_rolls_back(self, operator):
        sha = _head_sha()
        result = operator.run(
            sha,
            STUB_PUBLIC_REVISION="b" * 40,
            STUB_REMOTE_OUT=f"[demo-deploy] already live: {sha} (no-op)",
        )
        assert result.returncode != 0
        assert "nothing rolled back" in result.stderr
        assert "bash -s -- rollback" not in operator.ssh_log()

    def test_remote_failure_fails_without_second_rollback(self, operator):
        sha = _head_sha()
        result = operator.run(sha, STUB_REMOTE_RC="1", STUB_PUBLIC_REVISION=sha)
        assert result.returncode != 0
        assert "remote deploy failed" in result.stderr
        assert "bash -s -- rollback" not in operator.ssh_log()

    def test_concurrent_operator_run_is_refused(self, operator):
        operator.lock.mkdir()
        result = operator.run(_head_sha())
        assert result.returncode != 0
        assert "another deploy-demo-vm run" in result.stderr
        assert operator.ssh_log() == ""
        assert operator.lock.exists()  # someone else's lock is left alone
