"""Adversarial regression tests for the production deploy boundary."""

from __future__ import annotations

import fcntl
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "deploy.sh"
ROOT_HELPER = ROOT / "scripts" / "deploy-root-helper.sh"
SETUP = ROOT / "scripts" / "setup-vps.sh"
ENV_EXAMPLE = ROOT / ".env.example"
SUDOERS = ROOT / "config" / "sudoers.d" / "radon-deploy"
ROOT_CI = ROOT.parent / ".github" / "workflows" / "ci.yml"
GITLEAKS_CONFIG = ROOT / ".gitleaks.toml"
REQUIRED_ENV = ROOT / "config" / "required-env.txt"
ENV_CHECKER = ROOT / "scripts" / "check-env.py"
JOURNAL_HELPER = ROOT / "scripts" / "deploy-journal.py"

MANAGED_SERVICES = (
    "radon-nextjs.service",
    "radon-api.service",
    "radon-relay.service",
    "radon-monitor.service",
    "radon-newsfeed.service",
)


def function_body(script: str, name: str) -> str:
    match = re.search(
        rf"^{name}\(\)\s*\{{\s*\n(.+?)\n\}}\s*$",
        script,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"{name}() missing"
    return match.group(1)


@pytest.fixture(scope="module")
def deploy_text() -> str:
    return DEPLOY.read_text(encoding="utf-8")


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _fake_supervisor_tools(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "flock",
        f"""#!{sys.executable}
import fcntl
import sys
fd = int(sys.argv[-1])
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit(75)
""",
    )
    _write_executable(
        fake_bin / "timeout",
        f"""#!{sys.executable}
import os
import signal
import subprocess
import sys
args = sys.argv[1:]
kill_after = 1.0
while args and args[0].startswith("--"):
    option = args.pop(0)
    if option.startswith("--kill-after="):
        kill_after = float(option.split("=", 1)[1].removesuffix("s"))
duration = float(args.pop(0).removesuffix("s"))
child = subprocess.Popen(args, start_new_session=True)
try:
    raise SystemExit(child.wait(timeout=duration))
except subprocess.TimeoutExpired:
    os.killpg(child.pid, signal.SIGTERM)
    try:
        child.wait(timeout=kill_after)
    except subprocess.TimeoutExpired:
        marker = os.environ.get("FORCED_KILL_MARKER")
        if marker:
            open(marker, "w", encoding="utf-8").write("forced\\n")
        os.killpg(child.pid, signal.SIGKILL)
        child.wait()
    raise SystemExit(124)
""",
    )
    return fake_bin, tmp_path / "deploy.lock"


def _process_is_running(pid: int) -> bool:
    result = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
    )
    state = result.stdout.strip()
    return result.returncode == 0 and bool(state) and not state.startswith("Z")


ARTIFACTS = ("node_modules", "web/node_modules", "web/.next", "web/.env", ".venv")


def _create_release_artifacts(root: Path, label: str) -> dict[str, bytes]:
    sentinels: dict[str, bytes] = {}
    for relative in ARTIFACTS:
        path = root / relative
        if relative == "web/.env":
            path.parent.mkdir(parents=True, exist_ok=True)
            sentinel = path
        elif relative == ".venv":
            (path / "bin").mkdir(parents=True, exist_ok=True)
            sentinel = path / "bin" / "python"
            sentinel.write_text(f"#!/bin/sh\nprintf '{label}-runtime-ok\\n'\n", encoding="utf-8")
            sentinel.chmod(0o755)
            sentinels[str(sentinel.relative_to(root))] = sentinel.read_bytes()
            continue
        else:
            path.mkdir(parents=True, exist_ok=True)
            sentinel = path / "sentinel"
        sentinel.write_bytes(f"{label}:{relative}\n".encode())
        sentinels[str(sentinel.relative_to(root))] = sentinel.read_bytes()
    return sentinels


def _assert_release_snapshot(root: Path, snapshot: dict[str, bytes]) -> None:
    for relative, expected in snapshot.items():
        assert (root / relative).read_bytes() == expected
    runtime = subprocess.run(
        [str(root / ".venv" / "bin" / "python")], capture_output=True, text=True
    )
    assert runtime.returncode == 0
    assert runtime.stdout.strip() == "old-runtime-ok"


def _write_transition_journal(
    path: Path,
    requested: str,
    previous: str,
    stage: Path,
    backup: Path,
    phase: str,
) -> None:
    subprocess.run(
        [
            sys.executable,
            str(JOURNAL_HELPER),
            "write",
            str(path),
            requested,
            previous,
            str(stage),
            str(backup),
            phase,
        ],
        check=True,
    )


def _transition_env(live: Path, releases: Path, journal: Path) -> dict[str, str]:
    return {
        **os.environ,
        "RADON_DIR": str(live),
        "RADON_CLOUD_DIR": str(ROOT),
        "RADON_RELEASES_DIR": str(releases),
        "RADON_DEPLOY_TRANSITION_JOURNAL": str(journal),
        "RADON_DEPLOY_JOURNAL_PYTHON": sys.executable,
        "RADON_SYSTEM_PYTHON": sys.executable,
    }


def _recovery_shell(calls: Path) -> str:
    return f"""
set -euo pipefail
source {DEPLOY!s}
sudo() {{ printf '%s\\n' "$*" >> {calls!s}; }}
git() {{ return 0; }}
check_units_active() {{ return 0; }}
sleep() {{ return 0; }}
recover_pending_transition
"""


@pytest.mark.parametrize("ignore_term", [False, True], ids=["term", "forced-kill"])
def test_supervisor_kills_descendants_without_recovery_before_transition(
    tmp_path: Path, ignore_term: bool
) -> None:
    fake_bin, lock_file = _fake_supervisor_tools(tmp_path)
    descendant_pid = tmp_path / "descendant.pid"
    recovery_log = tmp_path / "recovery.log"
    forced_kill = tmp_path / "forced-kill"
    worker = tmp_path / "worker.sh"
    trap = "trap '' TERM\n" if ignore_term else ""
    _write_executable(
        worker,
        f"""#!/bin/bash
set -euo pipefail
{trap}sleep 30 &
printf '%s\n' "$!" > {descendant_pid!s}
wait
""",
    )
    shell = f"""
set -euo pipefail
source {DEPLOY!s}
sudo() {{ printf '%s\\n' "$*" >> {recovery_log!s}; }}
exit_code=0
supervise_deploy_command bash {worker!s} || exit_code=$?
exit "$exit_code"
"""
    result = subprocess.run(
        ["bash", "-c", shell],
        env={
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "RADON_DEPLOY_LOCK_FILE": str(lock_file),
            "RADON_DEPLOY_ROOT_HELPER": "/usr/local/sbin/radon-deploy-root",
            "DEPLOY_TIMEOUT": "1",
            "DEPLOY_KILL_AFTER": "1",
            "FORCED_KILL_MARKER": str(forced_kill),
        },
        capture_output=True,
        text=True,
        timeout=6,
    )
    assert result.returncode == 124, result.stderr
    assert descendant_pid.exists(), result.stderr
    pid = int(descendant_pid.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2
    while _process_is_running(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _process_is_running(pid), f"descendant {pid} survived deploy timeout"
    assert forced_kill.exists() is ignore_term
    assert not recovery_log.exists()
    with lock_file.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_supervisor_preserves_non_timeout_child_status_without_recovery(tmp_path: Path) -> None:
    fake_bin, lock_file = _fake_supervisor_tools(tmp_path)
    recovery_log = tmp_path / "recovery.log"
    shell = f"""
set -euo pipefail
source {DEPLOY!s}
sudo() {{ printf '%s\\n' "$*" >> {recovery_log!s}; }}
exit_code=0
supervise_deploy_command bash -c 'exit 23' || exit_code=$?
exit "$exit_code"
"""
    result = subprocess.run(
        ["bash", "-c", shell],
        env={
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "RADON_DEPLOY_LOCK_FILE": str(lock_file),
            "DEPLOY_TIMEOUT": "5",
            "DEPLOY_KILL_AFTER": "1",
        },
        capture_output=True,
        text=True,
        timeout=3,
    )
    assert result.returncode == 23
    assert not recovery_log.exists()


@pytest.mark.skipif(
    shutil.which("timeout") is None,
    reason="GNU timeout is required for the external-signal process-group test",
)
def test_external_term_holds_lock_through_recovery_and_kills_descendants(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "flock",
        f"""#!{sys.executable}
import fcntl
import sys
try:
    fcntl.flock(int(sys.argv[-1]), fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit(75)
""",
    )
    worker_state = tmp_path / "worker-state"
    recovery_started = tmp_path / "recovery-started"
    recovery_log = tmp_path / "recovery.log"
    journal = tmp_path / "transition.json"
    # Journal must appear only after the worker starts. A pre-existing journal
    # is recovered at supervisor entry (abandoned prior deploy); this case is a
    # mid-transition TERM while the current deploy owns the lock.
    lock_file = tmp_path / "deploy.lock"
    worker = tmp_path / "worker.sh"
    _write_executable(
        worker,
        f"""#!/bin/bash
set -euo pipefail
trap '' TERM
printf 'pending\\n' > {journal!s}
sleep 30 &
printf '%s %s %s\\n' "$$" "$!" "$(ps -o pgid= -p $$ | tr -d ' ')" > {worker_state!s}
wait
""",
    )
    outer_shell = f"""
set -euo pipefail
source {DEPLOY!s}
recover_pending_transition() {{
  printf 'start\\n' >> {recovery_log!s}
  : > {recovery_started!s}
  sleep 1
  rm -f {journal!s}
  printf 'done\\n' >> {recovery_log!s}
}}
supervise_deploy_command bash {worker!s}
"""
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "RADON_DEPLOY_LOCK_FILE": str(lock_file),
        "RADON_DEPLOY_ROOT_HELPER": "/fixed/root-helper",
        "RADON_DEPLOY_TRANSITION_JOURNAL": str(journal),
        "DEPLOY_TIMEOUT": "30",
        "DEPLOY_KILL_AFTER": "1",
    }
    outer = subprocess.Popen(
        ["bash", "-c", outer_shell],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    process_group = None
    try:
        deadline = time.monotonic() + 3
        while not worker_state.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert worker_state.exists(), "supervised worker never started"
        worker_pid, descendant_pid, process_group = map(
            int, worker_state.read_text(encoding="utf-8").split()
        )

        outer.send_signal(signal.SIGTERM)
        deadline = time.monotonic() + 4
        while not recovery_started.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert recovery_started.exists(), "outer TERM did not invoke recovery"

        competitor_shell = f"""
set -euo pipefail
source {DEPLOY!s}
recover_pending_transition() {{ return 0; }}
supervise_deploy_command bash -c true
"""
        competitor = subprocess.run(
            ["bash", "-c", competitor_shell],
            env=env,
            capture_output=True,
            text=True,
            timeout=3,
        )
        assert competitor.returncode == 75, "deploy lock released before recovery completed"

        stdout, stderr = outer.communicate(timeout=5)
        assert outer.returncode == 143, stdout + stderr
        assert recovery_log.read_text(encoding="utf-8").splitlines() == ["start", "done"]
        for pid in (worker_pid, descendant_pid):
            assert not _process_is_running(pid), f"process {pid} survived outer TERM"
    finally:
        if outer.poll() is None:
            outer.kill()
            outer.wait(timeout=2)
        if process_group is not None:
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(
    shutil.which("timeout") is None,
    reason="GNU timeout is required for the external-signal mapping test",
)
@pytest.mark.skipif(
    sys.platform == "darwin",
    reason=(
        "bash signal propagation differs on macOS: the supervised worker never "
        "exits within the harness deadline (communicate(timeout=5) -> SIGKILL, "
        "returncode -9). deploy.sh targets Linux; CI keeps full coverage. "
        "TEST_AUDIT.md T-002."
    ),
)
@pytest.mark.parametrize(
    ("sent_signal", "expected_status"),
    [(signal.SIGINT, 130), (signal.SIGHUP, 129)],
    ids=["int", "hup"],
)
def test_external_signal_status_is_preserved_after_recovery(
    tmp_path: Path, sent_signal: signal.Signals, expected_status: int
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "flock",
        f"""#!{sys.executable}
import fcntl
import sys
try:
    fcntl.flock(int(sys.argv[-1]), fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit(75)
""",
    )
    started = tmp_path / "started"
    recovery_log = tmp_path / "recovery.log"
    journal = tmp_path / "transition.json"
    worker = tmp_path / "worker.sh"
    # Mid-transition signal: worker owns a durable journal so recovery runs
    # before the supervisor exits with the original signal status.
    _write_executable(
        worker,
        f"""#!/bin/bash
set -euo pipefail
printf 'pending\\n' > {journal!s}
: > {started!s}
sleep 30
""",
    )
    shell = f"""
set -euo pipefail
source {DEPLOY!s}
recover_pending_transition() {{
  printf 'recovered\\n' >> {recovery_log!s}
  rm -f {journal!s}
}}
supervise_deploy_command bash {worker!s}
"""
    outer = subprocess.Popen(
        ["bash", "-c", shell],
        env={
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "RADON_DEPLOY_LOCK_FILE": str(tmp_path / "deploy.lock"),
            "RADON_DEPLOY_ROOT_HELPER": "/fixed/root-helper",
            "RADON_DEPLOY_TRANSITION_JOURNAL": str(journal),
            "DEPLOY_TIMEOUT": "30",
            "DEPLOY_KILL_AFTER": "1",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 3
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert started.exists(), "supervised worker never started"
        outer.send_signal(sent_signal)
        stdout, stderr = outer.communicate(timeout=5)
        assert outer.returncode == expected_status, stdout + stderr
        assert recovery_log.read_text(encoding="utf-8").splitlines() == ["recovered"]
        assert not journal.exists()
    finally:
        if outer.poll() is None:
            outer.kill()
            outer.wait(timeout=2)


class TestSupervisorStructure:
    def test_outer_supervisor_owns_lock_and_closes_it_from_child(self, deploy_text: str) -> None:
        body = function_body(deploy_text, "supervise_deploy_command")
        executable = "\n".join(
            line for line in body.splitlines() if not line.lstrip().startswith("#")
        )
        assert executable.find("flock -n") < executable.find("timeout ")
        assert "--foreground" not in body
        assert re.search(r"timeout .* 9>&-", " ".join(body.splitlines()))
        assert "recover" in body
        assert "acquire_deploy_lock" not in function_body(deploy_text, "main")

    def test_sourcing_guard_and_internal_recursion_guard_remain(self, deploy_text: str) -> None:
        assert '${BASH_SOURCE[0]}' in deploy_text
        assert "RADON_DEPLOY_INTERNAL" in deploy_text


class TestDurableReleaseTransition:
    PREVIOUS = "1" * 40
    REQUESTED = "2" * 40

    @pytest.mark.parametrize("backed_up_count", range(len(ARTIFACTS) + 1))
    def test_fresh_process_recovers_every_partial_backup_boundary(
        self, tmp_path: Path, backed_up_count: int
    ) -> None:
        releases = tmp_path / "releases"
        live = tmp_path / "live"
        stage = releases / "stage.target"
        backup = releases / "backup.previous"
        journal = tmp_path / "transition.json"
        calls = tmp_path / "sudo.log"
        releases.mkdir()
        stage.mkdir()
        backup.mkdir()
        old = _create_release_artifacts(live, "old")
        _create_release_artifacts(stage, "target")
        for relative in ARTIFACTS[:backed_up_count]:
            destination = backup / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            (live / relative).rename(destination)
        _write_transition_journal(
            journal, self.REQUESTED, self.PREVIOUS, stage, backup, "backup_started"
        )

        result = subprocess.run(
            ["bash", "-c", _recovery_shell(calls)],
            env=_transition_env(live, releases, journal),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        _assert_release_snapshot(live, old)
        assert not journal.exists()
        assert calls.read_text(encoding="utf-8").splitlines() == [
            "/usr/local/sbin/radon-deploy-root stop-clean",
            "/usr/local/sbin/radon-deploy-root revert-units",
            "/usr/local/sbin/radon-deploy-root recover",
            "/usr/local/sbin/radon-deploy-root verify-restored",
            "/usr/local/sbin/radon-deploy-root commit-transition",
        ]

    @pytest.mark.parametrize("promoted_count", range(5))
    def test_fresh_process_recovers_every_partial_promotion_boundary(
        self, tmp_path: Path, promoted_count: int
    ) -> None:
        releases = tmp_path / "releases"
        live = tmp_path / "live"
        stage = releases / "stage.target"
        backup = releases / "backup.previous"
        journal = tmp_path / "transition.json"
        calls = tmp_path / "sudo.log"
        releases.mkdir()
        stage.mkdir()
        backup.mkdir()
        old = _create_release_artifacts(live, "old")
        _create_release_artifacts(stage, "target")
        for relative in ARTIFACTS:
            destination = backup / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            (live / relative).rename(destination)
        for relative in ARTIFACTS[:promoted_count]:
            destination = live / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            (stage / relative).rename(destination)
        _write_transition_journal(
            journal, self.REQUESTED, self.PREVIOUS, stage, backup, "promoting"
        )

        result = subprocess.run(
            ["bash", "-c", _recovery_shell(calls)],
            env=_transition_env(live, releases, journal),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        _assert_release_snapshot(live, old)
        assert not journal.exists()

    @pytest.mark.parametrize("venv_state", ["missing", "partial", "installed"])
    def test_fresh_process_restores_exact_venv_at_every_creation_boundary(
        self, tmp_path: Path, venv_state: str
    ) -> None:
        releases = tmp_path / "releases"
        live = tmp_path / "live"
        stage = releases / "stage.target"
        backup = releases / "backup.previous"
        journal = tmp_path / "transition.json"
        calls = tmp_path / "sudo.log"
        releases.mkdir()
        stage.mkdir()
        backup.mkdir()
        old = _create_release_artifacts(live, "old")
        _create_release_artifacts(stage, "target")
        for relative in ARTIFACTS:
            destination = backup / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            (live / relative).rename(destination)
        for relative in ARTIFACTS[:-1]:
            destination = live / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            (stage / relative).rename(destination)
        if venv_state != "missing":
            (live / ".venv" / "bin").mkdir(parents=True)
            (live / ".venv" / "bin" / "python").write_text(
                "partial-target-runtime\n" if venv_state == "partial" else "installed-target-runtime\n",
                encoding="utf-8",
            )
        _write_transition_journal(
            journal, self.REQUESTED, self.PREVIOUS, stage, backup, "promoting"
        )

        result = subprocess.run(
            ["bash", "-c", _recovery_shell(calls)],
            env=_transition_env(live, releases, journal),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        _assert_release_snapshot(live, old)

    @pytest.mark.parametrize("failed_move", range(1, len(ARTIFACTS) + 5))
    def test_injected_failure_at_every_artifact_move_restores_exact_release(
        self, tmp_path: Path, failed_move: int
    ) -> None:
        releases = tmp_path / "releases"
        live = tmp_path / "live"
        stage = releases / "stage.target"
        backup = releases / "backup.previous"
        journal = tmp_path / "transition.json"
        calls = tmp_path / "sudo.log"
        releases.mkdir()
        stage.mkdir()
        backup.mkdir()
        old = _create_release_artifacts(live, "old")
        _create_release_artifacts(stage, "target")
        _write_transition_journal(
            journal, self.REQUESTED, self.PREVIOUS, stage, backup, "prepared"
        )
        shell = f"""
set -euo pipefail
source {DEPLOY!s}
STAGED_RELEASE_DIR={stage!s}
RELEASE_BACKUP_DIR={backup!s}
move_count=0
mv() {{
  move_count=$((move_count + 1))
  if [[ "$move_count" -eq {failed_move} ]]; then return 44; fi
  command mv "$@"
}}
git() {{ return 0; }}
sudo() {{ printf '%s\\n' "$*" >> {calls!s}; }}
check_units_active() {{ return 0; }}
sleep() {{ return 0; }}
status=0
activate_staged_release {self.REQUESTED} {self.PREVIOUS} || status=$?
[[ "$status" -eq 44 ]]
recover_pending_transition
"""
        result = subprocess.run(
            ["bash", "-c", shell],
            env=_transition_env(live, releases, journal),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        _assert_release_snapshot(live, old)

    @pytest.mark.parametrize(
        "failed_phase", ["backup_started", "backup_complete", "promoting", "target_unverified"]
    )
    def test_injected_phase_write_failure_restores_exact_release(
        self, tmp_path: Path, failed_phase: str
    ) -> None:
        releases = tmp_path / "releases"
        live = tmp_path / "live"
        stage = releases / "stage.target"
        backup = releases / "backup.previous"
        journal = tmp_path / "transition.json"
        calls = tmp_path / "sudo.log"
        releases.mkdir()
        stage.mkdir()
        backup.mkdir()
        old = _create_release_artifacts(live, "old")
        _create_release_artifacts(stage, "target")
        _write_transition_journal(
            journal, self.REQUESTED, self.PREVIOUS, stage, backup, "prepared"
        )
        shell = f"""
set -euo pipefail
source {DEPLOY!s}
STAGED_RELEASE_DIR={stage!s}
RELEASE_BACKUP_DIR={backup!s}
real_write_transition_phase=""
write_transition_phase() {{
  if [[ "$1" == {failed_phase} ]]; then return 45; fi
  "$JOURNAL_PYTHON" "$JOURNAL_HELPER" phase "$TRANSITION_JOURNAL_FILE" "$1"
}}
install_target_python_env() {{ mkdir -p "$VENV_DIR/bin"; printf target > "$VENV_DIR/bin/python"; }}
git() {{ return 0; }}
sudo() {{ printf '%s\\n' "$*" >> {calls!s}; }}
check_units_active() {{ return 0; }}
sleep() {{ return 0; }}
status=0
activate_staged_release {self.REQUESTED} {self.PREVIOUS} || status=$?
[[ "$status" -eq 45 ]]
recover_pending_transition
"""
        result = subprocess.run(
            ["bash", "-c", shell],
            env=_transition_env(live, releases, journal),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        _assert_release_snapshot(live, old)

    def test_injected_target_reset_failure_restores_exact_release(self, tmp_path: Path) -> None:
        releases = tmp_path / "releases"
        live = tmp_path / "live"
        stage = releases / "stage.target"
        backup = releases / "backup.previous"
        journal = tmp_path / "transition.json"
        calls = tmp_path / "sudo.log"
        releases.mkdir()
        stage.mkdir()
        backup.mkdir()
        old = _create_release_artifacts(live, "old")
        _create_release_artifacts(stage, "target")
        _write_transition_journal(
            journal, self.REQUESTED, self.PREVIOUS, stage, backup, "prepared"
        )
        shell = f"""
set -euo pipefail
source {DEPLOY!s}
STAGED_RELEASE_DIR={stage!s}
RELEASE_BACKUP_DIR={backup!s}
reset_failed=0
git() {{
  if [[ "$*" == *"reset --hard {self.REQUESTED}"* && "$reset_failed" -eq 0 ]]; then
    reset_failed=1
    return 46
  fi
  return 0
}}
sudo() {{ printf '%s\\n' "$*" >> {calls!s}; }}
check_units_active() {{ return 0; }}
sleep() {{ return 0; }}
status=0
activate_staged_release {self.REQUESTED} {self.PREVIOUS} || status=$?
[[ "$status" -eq 46 ]]
recover_pending_transition
"""
        result = subprocess.run(
            ["bash", "-c", shell],
            env=_transition_env(live, releases, journal),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        _assert_release_snapshot(live, old)

    @pytest.mark.parametrize("venv_failure", ["before-create", "partial-install"])
    def test_injected_venv_failure_restores_exact_old_runtime(
        self, tmp_path: Path, venv_failure: str
    ) -> None:
        releases = tmp_path / "releases"
        live = tmp_path / "live"
        stage = releases / "stage.target"
        backup = releases / "backup.previous"
        journal = tmp_path / "transition.json"
        calls = tmp_path / "sudo.log"
        releases.mkdir()
        stage.mkdir()
        backup.mkdir()
        old = _create_release_artifacts(live, "old")
        _create_release_artifacts(stage, "target")
        _write_transition_journal(
            journal, self.REQUESTED, self.PREVIOUS, stage, backup, "prepared"
        )
        partial = "mkdir -p \"$VENV_DIR/bin\"; printf partial > \"$VENV_DIR/bin/python\";" if venv_failure == "partial-install" else ""
        shell = f"""
set -euo pipefail
source {DEPLOY!s}
STAGED_RELEASE_DIR={stage!s}
RELEASE_BACKUP_DIR={backup!s}
install_target_python_env() {{ {partial} return 47; }}
git() {{ return 0; }}
sudo() {{ printf '%s\\n' "$*" >> {calls!s}; }}
check_units_active() {{ return 0; }}
sleep() {{ return 0; }}
status=0
activate_staged_release {self.REQUESTED} {self.PREVIOUS} || status=$?
[[ "$status" -eq 47 ]]
recover_pending_transition
"""
        result = subprocess.run(
            ["bash", "-c", shell],
            env=_transition_env(live, releases, journal),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        _assert_release_snapshot(live, old)

    def test_verified_journal_finalizes_target_and_marker_without_rollback(
        self, tmp_path: Path
    ) -> None:
        releases = tmp_path / "releases"
        live = tmp_path / "live"
        stage = releases / "stage.target"
        backup = releases / "backup.previous"
        journal = tmp_path / "transition.json"
        marker = tmp_path / "green-marker"
        calls = tmp_path / "sudo.log"
        releases.mkdir()
        stage.mkdir()
        backup.mkdir()
        target = _create_release_artifacts(live, "target")
        _create_release_artifacts(backup, "old")
        _write_transition_journal(
            journal, self.REQUESTED, self.PREVIOUS, stage, backup, "verified"
        )
        shell = f"""
set -euo pipefail
source {DEPLOY!s}
current_commit() {{ printf '%s\\n' {self.REQUESTED}; }}
deploy_gate() {{ return 0; }}
record_deploy_marker() {{ return 0; }}
sudo() {{ printf '%s\\n' "$*" >> {calls!s}; }}
recover_pending_transition
"""
        result = subprocess.run(
            ["bash", "-c", shell],
            env={
                **_transition_env(live, releases, journal),
                "RADON_DEPLOY_GREEN_MARKER": str(marker),
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        for relative, expected in target.items():
            assert (live / relative).read_bytes() == expected
        assert marker.read_text(encoding="utf-8") == f"{self.REQUESTED}\n"
        assert not journal.exists()
        assert calls.read_text(encoding="utf-8").splitlines() == [
            "/usr/local/sbin/radon-deploy-root commit-transition",
            "-n -l -- /usr/local/sbin/radon-deploy-root sync-scheduled-units",
            "/usr/local/sbin/radon-deploy-root sync-scheduled-units",
        ]

    def test_verified_journal_finalizes_when_unit_sync_refuses_stale_head(
        self, tmp_path: Path
    ) -> None:
        releases = tmp_path / "releases"
        live = tmp_path / "live"
        stage = releases / "stage.target"
        backup = releases / "backup.previous"
        journal = tmp_path / "transition.json"
        marker = tmp_path / "green-marker"
        calls = tmp_path / "sudo.log"
        releases.mkdir()
        stage.mkdir()
        backup.mkdir()
        target = _create_release_artifacts(live, "target")
        _create_release_artifacts(backup, "old")
        _write_transition_journal(
            journal, self.REQUESTED, self.PREVIOUS, stage, backup, "verified"
        )
        shell = f"""
set -euo pipefail
source {DEPLOY!s}
current_commit() {{ printf '%s\\n' {self.REQUESTED}; }}
deploy_gate() {{ return 0; }}
record_deploy_marker() {{ return 0; }}
log_warn() {{ :; }}
sudo() {{
  printf '%s\\n' "$*" >> {calls!s}
  [[ "$*" == "/usr/local/sbin/radon-deploy-root sync-scheduled-units" ]] && return 76
  return 0
}}
recover_pending_transition
"""
        result = subprocess.run(
            ["bash", "-c", shell],
            env={
                **_transition_env(live, releases, journal),
                "RADON_DEPLOY_GREEN_MARKER": str(marker),
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        for relative, expected in target.items():
            assert (live / relative).read_bytes() == expected
        assert marker.read_text(encoding="utf-8") == f"{self.REQUESTED}\n"
        assert not journal.exists()

    def test_verified_journal_with_missing_venv_rolls_back_instead_of_finalizing(
        self, tmp_path: Path
    ) -> None:
        releases = tmp_path / "releases"
        live = tmp_path / "live"
        stage = releases / "stage.target"
        backup = releases / "backup.previous"
        journal = tmp_path / "transition.json"
        calls = tmp_path / "sudo.log"
        releases.mkdir()
        stage.mkdir()
        backup.mkdir()
        _create_release_artifacts(live, "target")
        shutil.rmtree(live / ".venv")
        old = _create_release_artifacts(backup, "old")
        _write_transition_journal(
            journal, self.REQUESTED, self.PREVIOUS, stage, backup, "verified"
        )
        shell = f"""
set -euo pipefail
source {DEPLOY!s}
current_commit() {{ printf '%s\\n' {self.REQUESTED}; }}
deploy_gate() {{ return 0; }}
git() {{ return 0; }}
sudo() {{ printf '%s\\n' "$*" >> {calls!s}; }}
check_units_active() {{ return 0; }}
sleep() {{ return 0; }}
recover_pending_transition
"""
        result = subprocess.run(
            ["bash", "-c", shell],
            env=_transition_env(live, releases, journal),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        _assert_release_snapshot(live, old)
        assert calls.read_text(encoding="utf-8").splitlines() == [
            "/usr/local/sbin/radon-deploy-root stop-clean",
            "/usr/local/sbin/radon-deploy-root revert-units",
            "/usr/local/sbin/radon-deploy-root recover",
            "/usr/local/sbin/radon-deploy-root verify-restored",
            "/usr/local/sbin/radon-deploy-root commit-transition",
        ]

    def test_verified_finalization_orders_marker_topology_commit_and_cleanup(
        self, tmp_path: Path
    ) -> None:
        releases = tmp_path / "releases"
        live = tmp_path / "live"
        stage = releases / "stage.target"
        backup = releases / "backup.previous"
        journal = tmp_path / "transition.json"
        events = tmp_path / "events.log"
        releases.mkdir()
        stage.mkdir()
        backup.mkdir()
        _create_release_artifacts(live, "target")
        _create_release_artifacts(backup, "old")
        _write_transition_journal(
            journal, self.REQUESTED, self.PREVIOUS, stage, backup, "verified"
        )
        shell = f"""
set -euo pipefail
source {DEPLOY!s}
current_commit() {{ printf '%s\\n' {self.REQUESTED}; }}
deploy_gate() {{ return 0; }}
record_deploy_marker() {{ return 0; }}
write_green_marker() {{
  [[ -f {journal!s} ]] || return 91
  printf '%s\\n' green >> {events!s}
}}
sudo() {{
  case "$*" in
    "/usr/local/sbin/radon-deploy-root commit-transition")
      [[ "$(cat {events!s})" == green ]] || return 93
      [[ -f {journal!s} ]] || return 94
      printf '%s\\n' commit >> {events!s}
      ;;
    "-n -l -- /usr/local/sbin/radon-deploy-root sync-scheduled-units")
      return 0
      ;;
    "/usr/local/sbin/radon-deploy-root sync-scheduled-units")
      [[ "$(tail -n 1 {events!s})" == commit ]] || return 96
      printf '%s\\n' sync >> {events!s}
      ;;
    *)
      return 92
      ;;
  esac
}}
clear_transition_journal() {{
  [[ "$(tail -n 1 {events!s})" == sync ]] || return 95
  printf '%s\\n' cleanup >> {events!s}
  rm -f -- {journal!s}
}}
recover_pending_transition
"""
        result = subprocess.run(
            ["bash", "-c", shell],
            env=_transition_env(live, releases, journal),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert events.read_text(encoding="utf-8").splitlines() == [
            "green",
            "commit",
            "sync",
            "cleanup",
        ]
        assert not journal.exists()

    def test_marker_failure_leaves_verified_target_recoverable_by_fresh_process(
        self, tmp_path: Path
    ) -> None:
        releases = tmp_path / "releases"
        live = tmp_path / "live"
        stage = releases / "stage.target"
        backup = releases / "backup.previous"
        journal = tmp_path / "transition.json"
        marker = tmp_path / "green-marker"
        releases.mkdir()
        stage.mkdir()
        backup.mkdir()
        target = _create_release_artifacts(live, "target")
        _create_release_artifacts(backup, "old")
        _write_transition_journal(
            journal, self.REQUESTED, self.PREVIOUS, stage, backup, "verified"
        )
        failing_shell = f"""
set -euo pipefail
source {DEPLOY!s}
current_commit() {{ printf '%s\\n' {self.REQUESTED}; }}
deploy_gate() {{ return 0; }}
record_deploy_marker() {{ return 0; }}
write_green_marker() {{ return 52; }}
handle_deploy_exit 52
"""
        result = subprocess.run(
            ["bash", "-c", failing_shell],
            env={
                **_transition_env(live, releases, journal),
                "RADON_DEPLOY_GREEN_MARKER": str(marker),
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 52
        assert journal.exists()
        for relative, expected in target.items():
            assert (live / relative).read_bytes() == expected

        recovery_shell = f"""
set -euo pipefail
source {DEPLOY!s}
current_commit() {{ printf '%s\\n' {self.REQUESTED}; }}
deploy_gate() {{ return 0; }}
record_deploy_marker() {{ return 0; }}
sudo() {{ return 0; }}
recover_pending_transition
"""
        recovered = subprocess.run(
            ["bash", "-c", recovery_shell],
            env={
                **_transition_env(live, releases, journal),
                "RADON_DEPLOY_GREEN_MARKER": str(marker),
            },
            capture_output=True,
            text=True,
        )
        assert recovered.returncode == 0, recovered.stdout + recovered.stderr
        assert marker.read_text(encoding="utf-8") == f"{self.REQUESTED}\n"
        assert not journal.exists()

    @pytest.mark.skipif(
        shutil.which("timeout") is None,
        reason="GNU timeout is required for SIGKILL supervisor recovery",
    )
    def test_outer_supervisor_recovers_partial_release_after_inner_sigkill(
        self, tmp_path: Path
    ) -> None:
        releases = tmp_path / "releases"
        live = tmp_path / "live"
        stage = releases / "stage.target"
        backup = releases / "backup.previous"
        journal = tmp_path / "transition.json"
        calls = tmp_path / "sudo.log"
        releases.mkdir()
        stage.mkdir()
        backup.mkdir()
        old = _create_release_artifacts(live, "old")
        _create_release_artifacts(stage, "target")
        worker = tmp_path / "kill-during-promotion.py"
        worker.write_text(
            f"""import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

live = Path({str(live)!r})
stage = Path({str(stage)!r})
backup = Path({str(backup)!r})
artifacts = {ARTIFACTS!r}
for relative in artifacts:
    destination = backup / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(live / relative, destination)
for relative in artifacts[:2]:
    destination = live / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(stage / relative, destination)
subprocess.run([
    sys.executable, {str(JOURNAL_HELPER)!r}, "write", {str(journal)!r},
    {self.REQUESTED!r}, {self.PREVIOUS!r}, {str(stage)!r}, {str(backup)!r}, "promoting"
], check=True)
os.kill(os.getpid(), signal.SIGKILL)
""",
            encoding="utf-8",
        )
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        _write_executable(
            fake_bin / "flock",
            f"""#!{sys.executable}
import fcntl
import sys
try:
    fcntl.flock(int(sys.argv[-1]), fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit(75)
""",
        )
        shell = f"""
set -euo pipefail
source {DEPLOY!s}
sudo() {{ printf '%s\\n' "$*" >> {calls!s}; }}
git() {{ return 0; }}
check_units_active() {{ return 0; }}
sleep() {{ return 0; }}
status=0
supervise_deploy_command {sys.executable} {worker!s} || status=$?
exit "$status"
"""
        result = subprocess.run(
            ["bash", "-c", shell],
            env={
                **_transition_env(live, releases, journal),
                "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                "RADON_DEPLOY_LOCK_FILE": str(tmp_path / "deploy.lock"),
                "DEPLOY_TIMEOUT": "5",
                "DEPLOY_KILL_AFTER": "1",
            },
            capture_output=True,
            text=True,
            timeout=8,
        )
        assert result.returncode == 137, result.stdout + result.stderr
        _assert_release_snapshot(live, old)
        assert not journal.exists()

    def test_journal_helper_fsyncs_file_and_directory_and_uses_private_mode(
        self, tmp_path: Path
    ) -> None:
        releases = tmp_path / "releases"
        stage = releases / "stage"
        backup = releases / "backup"
        journal = tmp_path / "transition.json"
        stage.mkdir(parents=True)
        backup.mkdir()
        _write_transition_journal(
            journal, self.REQUESTED, self.PREVIOUS, stage, backup, "prepared"
        )
        assert stat.S_IMODE(journal.stat().st_mode) == 0o600
        helper = JOURNAL_HELPER.read_text(encoding="utf-8")
        assert "os.replace" in helper
        assert helper.count("os.fsync") >= 2


class TestRootHelper:
    def _root_helper_fixture(
        self, tmp_path: Path, *, list_mode: str = "ok"
    ) -> tuple[dict[str, str], Path, Path, Path, Path]:
        units = {
            "radon-nextjs.service": ("active", "simple"),
            "radon-api.service": ("active", "simple"),
            "radon-relay.service": ("active", "notify"),
            "radon-monitor.service": ("active", "simple"),
            "radon-newsfeed.service": ("active", "simple"),
            "radon-demo-mirror.service": ("active", "simple"),
            "radon-margin-debt-refresh.timer": ("active", "timer"),
            "radon-margin-debt-refresh.service": ("active", "oneshot"),
            "radon-inactive-job.service": ("inactive", "oneshot"),
            "radon-beta-api.service": ("active", "simple"),
            "radon-ib-gateway.service": ("active", "oneshot"),
            "radon-ib-gateway-preheld-restart.service": ("inactive", "oneshot"),
        }
        state_file = tmp_path / "systemctl-state.json"
        state_file.write_text(
            __import__("json").dumps(
                {
                    "list_mode": list_mode,
                    "units": {name: {"state": state, "type": kind} for name, (state, kind) in units.items()},
                }
            ),
            encoding="utf-8",
        )
        systemctl_log = tmp_path / "systemctl.log"
        fake_systemctl = tmp_path / "systemctl"
        _write_executable(
            fake_systemctl,
            f"""#!{sys.executable}
import json
import sys
from pathlib import Path

state_path = Path({str(state_file)!r})
log_path = Path({str(systemctl_log)!r})
data = json.loads(state_path.read_text())
args = sys.argv[1:]
if args[0] == "--no-block":
    args = args[1:]
command = args[0]
if command in {{"list-unit-files", "list-units"}}:
    if data["list_mode"] == "fail":
        raise SystemExit(9)
    names = sorted(data["units"])
    if data["list_mode"] == "empty":
        names = []
    elif data["list_mode"] == "missing-core":
        names.remove("radon-api.service")
    if command == "list-unit-files" and "radon-demo-mirror.service" in names:
        names.remove("radon-demo-mirror.service")
    for name in names:
        print(name, "enabled")
    raise SystemExit(0)
if command == "show":
    unit = args[1]
    if data["list_mode"] == "show-fail" and unit == "radon-demo-mirror.service":
        raise SystemExit(9)
    if "--property=Type" in args:
        print(data["units"].get(unit, {{"type": "simple"}})["type"])
    else:
        print(data["units"].get(unit, {{"state": "inactive"}})["state"])
    raise SystemExit(0)
if command == "reset-failed":
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(command + " " + " ".join(args[1:]) + "\\n")
    for unit in args[1:]:
        if unit not in data["units"]:
            continue
        data["units"][unit]["start_limited"] = False
        if data["units"][unit]["state"] == "failed":
            data["units"][unit]["state"] = "inactive"
    state_path.write_text(json.dumps(data))
    raise SystemExit(0)
if command in {{"stop", "start", "restart"}}:
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(command + " " + " ".join(args[1:]) + "\\n")
    for unit in args[1:]:
        if unit not in data["units"]:
            continue
        if command == "stop" and data["units"][unit]["state"] == "failed":
            continue
        if command == "stop" or data["units"][unit]["type"] == "oneshot":
            data["units"][unit]["state"] = "inactive"
        elif data["units"][unit].get("start_limited"):
            data["units"][unit]["state"] = "failed"
        else:
            data["units"][unit]["state"] = "active"
    state_path.write_text(json.dumps(data))
    raise SystemExit(0)
raise SystemExit(2)
""",
        )
        rm_log = tmp_path / "rm.log"
        fake_rm = tmp_path / "rm"
        _write_executable(
            fake_rm,
            f"""#!{sys.executable}
import sys
from pathlib import Path
paths = [Path(value) for value in sys.argv[1:] if value != "-f"]
if any("replica.db" in str(path) for path in paths):
    with Path({str(rm_log)!r}).open("a", encoding="utf-8") as handle:
        handle.write("replica-clean\\n")
for path in paths:
    path.unlink(missing_ok=True)
""",
        )
        fake_sync = tmp_path / "sync"
        _write_executable(fake_sync, "#!/bin/bash\nexit 0\n")
        active_state = tmp_path / "active-units"
        env = {
            **os.environ,
            "RADON_DEPLOY_HELPER_TEST_MODE": "1",
            "RADON_TEST_SYSTEMCTL": str(fake_systemctl),
            "RADON_TEST_RM": str(fake_rm),
            "RADON_TEST_SYNC": str(fake_sync),
            "RADON_TEST_ACTIVE_STATE_FILE": str(active_state),
            "RADON_TEST_REPLICA_PREFIX": str(tmp_path / "replica.db"),
        }
        return env, state_file, systemctl_log, rm_log, active_state

    def test_quiesces_dynamic_main_tree_units_and_restores_exact_scheduled_state(
        self, tmp_path: Path
    ) -> None:
        import json

        env, state_file, systemctl_log, rm_log, active_state = self._root_helper_fixture(tmp_path)
        stopped = subprocess.run(
            ["bash", str(ROOT_HELPER), "stop-clean"], env=env, capture_output=True, text=True
        )
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        data = json.loads(state_file.read_text(encoding="utf-8"))["units"]
        for unit in (
            "radon-nextjs.service",
            "radon-demo-mirror.service",
            "radon-margin-debt-refresh.timer",
            "radon-margin-debt-refresh.service",
        ):
            assert data[unit]["state"] == "inactive"
        for excluded in (
            "radon-beta-api.service",
            "radon-ib-gateway.service",
            "radon-ib-gateway-preheld-restart.service",
        ):
            expected = "inactive" if "preheld" in excluded else "active"
            assert data[excluded]["state"] == expected
        stop_lines = systemctl_log.read_text(encoding="utf-8").splitlines()
        assert stop_lines[0].startswith("stop ") and ".timer" in stop_lines[0]
        assert stop_lines[1].startswith("stop ") and ".service" in stop_lines[1]
        snapshot = active_state.read_text(encoding="utf-8")
        assert "radon-demo-mirror.service" in snapshot
        assert "radon-margin-debt-refresh.timer" in snapshot
        assert "radon-margin-debt-refresh.service\toneshot" in snapshot
        assert "radon-inactive-job.service" not in snapshot
        assert "radon-beta-api.service" not in snapshot
        assert "radon-ib-gateway.service" not in snapshot
        assert rm_log.read_text(encoding="utf-8").strip() == "replica-clean"

        for _boundary in range(12):
            data = json.loads(state_file.read_text(encoding="utf-8"))["units"]
            assert data["radon-margin-debt-refresh.timer"]["state"] == "inactive"

        recovered = subprocess.run(
            ["bash", str(ROOT_HELPER), "recover"], env=env, capture_output=True, text=True
        )
        assert recovered.returncode == 0, recovered.stdout + recovered.stderr
        data = json.loads(state_file.read_text(encoding="utf-8"))["units"]
        assert data["radon-demo-mirror.service"]["state"] == "active"
        assert data["radon-margin-debt-refresh.timer"]["state"] == "active"
        assert data["radon-margin-debt-refresh.service"]["state"] == "inactive"
        assert data["radon-inactive-job.service"]["state"] == "inactive"
        starts = "\n".join(systemctl_log.read_text(encoding="utf-8").splitlines()[2:])
        assert "radon-margin-debt-refresh.service" not in starts
        assert "radon-inactive-job.service" not in starts
        assert active_state.exists(), "snapshot must survive until the release is verified"
        verified = subprocess.run(
            ["bash", str(ROOT_HELPER), "verify-restored"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert verified.returncode == 0, verified.stdout + verified.stderr
        committed = subprocess.run(
            ["bash", str(ROOT_HELPER), "commit-transition"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert committed.returncode == 0, committed.stdout + committed.stderr
        assert not active_state.exists()
        assert not Path(f"{active_state}.inventory").exists()

    def test_durable_snapshot_survives_simulated_reboot_run_cleanup(
        self, tmp_path: Path
    ) -> None:
        import json

        env, state_file, _systemctl_log, _rm_log, _active_state = self._root_helper_fixture(
            tmp_path
        )
        durable_state = tmp_path / "var" / "lib" / "radon" / "deploy" / "active-units"
        volatile_run = tmp_path / "run"
        volatile_run.mkdir()
        env["RADON_TEST_ACTIVE_STATE_FILE"] = str(durable_state)
        env["RADON_TEST_ROOT_LOCK_FILE"] = str(volatile_run / "root.lock")

        stopped = subprocess.run(
            ["bash", str(ROOT_HELPER), "stop-clean"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        assert durable_state.exists()

        shutil.rmtree(volatile_run)
        volatile_run.mkdir()
        recovered = subprocess.run(
            ["bash", str(ROOT_HELPER), "recover"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert recovered.returncode == 0, recovered.stdout + recovered.stderr
        units = json.loads(state_file.read_text(encoding="utf-8"))["units"]
        assert units["radon-demo-mirror.service"]["state"] == "active"
        assert units["radon-margin-debt-refresh.timer"]["state"] == "active"

    def test_failed_start_limited_core_is_quiescent_and_recovered(self, tmp_path: Path) -> None:
        import json

        env, state_file, systemctl_log, _rm_log, _active = self._root_helper_fixture(tmp_path)
        data = json.loads(state_file.read_text(encoding="utf-8"))
        data["units"]["radon-api.service"].update(
            {"state": "failed", "start_limited": True}
        )
        state_file.write_text(json.dumps(data), encoding="utf-8")

        stopped = subprocess.run(
            ["bash", str(ROOT_HELPER), "stop-clean"], env=env, capture_output=True, text=True
        )
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr

        recovered = subprocess.run(
            ["bash", str(ROOT_HELPER), "recover"], env=env, capture_output=True, text=True
        )
        assert recovered.returncode == 0, recovered.stdout + recovered.stderr
        data = json.loads(state_file.read_text(encoding="utf-8"))["units"]
        assert data["radon-api.service"]["state"] == "active"
        calls = systemctl_log.read_text(encoding="utf-8").splitlines()
        reset_index = next(i for i, call in enumerate(calls) if call.startswith("reset-failed "))
        start_index = next(i for i, call in enumerate(calls) if call.startswith("start "))
        assert reset_index < start_index
        assert set(calls[reset_index].split()[1:]) == set(MANAGED_SERVICES)

    @pytest.mark.parametrize("list_mode", ["fail", "empty", "missing-core", "show-fail"])
    def test_inventory_failure_never_stops_units_or_deletes_replica(
        self, tmp_path: Path, list_mode: str
    ) -> None:
        env, _state, systemctl_log, rm_log, _active = self._root_helper_fixture(
            tmp_path, list_mode=list_mode
        )
        result = subprocess.run(
            ["bash", str(ROOT_HELPER), "stop-clean"], env=env, capture_output=True, text=True
        )
        assert result.returncode != 0
        assert not systemctl_log.exists()
        assert not rm_log.exists()

    def test_active_preheld_gateway_adapter_blocks_after_timers_before_services(
        self, tmp_path: Path
    ) -> None:
        import json

        env, state_file, systemctl_log, rm_log, active_state = self._root_helper_fixture(tmp_path)
        data = json.loads(state_file.read_text(encoding="utf-8"))
        data["units"]["radon-ib-gateway-preheld-restart.service"]["state"] = "active"
        state_file.write_text(json.dumps(data), encoding="utf-8")
        result = subprocess.run(
            ["bash", str(ROOT_HELPER), "stop-clean"], env=env, capture_output=True, text=True
        )
        assert result.returncode == 66
        lines = systemctl_log.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1 and lines[0].startswith("stop ") and ".timer" in lines[0]
        assert "radon-ib-gateway-preheld-restart.service" not in lines[0]
        assert active_state.exists()
        assert not rm_log.exists()

    @pytest.mark.skipif(
        shutil.which("timeout") is None,
        reason="GNU timeout is required for root-action process-group test",
    )
    def test_root_action_timeout_kills_hanging_privileged_descendants(
        self, tmp_path: Path
    ) -> None:
        late_mutation = tmp_path / "late-mutation"
        fake_systemctl = tmp_path / "systemctl"
        _write_executable(
            fake_systemctl,
            f"""#!{sys.executable}
import signal
import subprocess
import sys
if len(sys.argv) > 1 and sys.argv[1] == "list-jobs":
    raise SystemExit(0)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([
    sys.executable, "-c",
    "import time; time.sleep(3); open({str(late_mutation)!r}, 'w').write('late')",
])
child.wait()
""",
        )
        fake_rm = tmp_path / "rm"
        _write_executable(fake_rm, "#!/bin/bash\nexec /bin/rm \"$@\"\n")
        fake_sync = tmp_path / "sync"
        _write_executable(fake_sync, "#!/bin/bash\nexit 0\n")
        result = subprocess.run(
            ["bash", str(ROOT_HELPER), "stop-clean"],
            env={
                **os.environ,
                "RADON_DEPLOY_HELPER_TEST_MODE": "1",
                "RADON_TEST_SYSTEMCTL": str(fake_systemctl),
                "RADON_TEST_RM": str(fake_rm),
                "RADON_TEST_SYNC": str(fake_sync),
                "RADON_TEST_ACTIVE_STATE_FILE": str(tmp_path / "active-units"),
                "RADON_TEST_REPLICA_PREFIX": str(tmp_path / "replica.db"),
                "RADON_TEST_TIMEOUT": shutil.which("timeout") or "",
                "RADON_TEST_ROOT_ACTION_TIMEOUT": "1",
                "RADON_TEST_ROOT_KILL_AFTER": "1",
            },
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode in {124, 137}
        time.sleep(2)
        assert not late_mutation.exists(), "timed-out root descendant mutated state later"

    @pytest.mark.skipif(
        shutil.which("flock") is None,
        reason="flock is required for root lifecycle serialization",
    )
    def test_root_lifecycle_lock_prevents_overlapping_recovery(self, tmp_path: Path) -> None:
        entered = tmp_path / "entered"
        fake_systemctl = tmp_path / "systemctl"
        _write_executable(
            fake_systemctl,
            f"#!/bin/bash\nif [[ \"$1\" == list-jobs ]]; then exit 0; fi\n: > {entered!s}\nsleep 3\nexit 9\n",
        )
        fake_rm = tmp_path / "rm"
        _write_executable(fake_rm, "#!/bin/bash\nexec /bin/rm \"$@\"\n")
        fake_sync = tmp_path / "sync"
        _write_executable(fake_sync, "#!/bin/bash\nexit 0\n")
        env = {
            **os.environ,
            "RADON_DEPLOY_HELPER_TEST_MODE": "1",
            "RADON_TEST_SYSTEMCTL": str(fake_systemctl),
            "RADON_TEST_RM": str(fake_rm),
            "RADON_TEST_SYNC": str(fake_sync),
            "RADON_TEST_ACTIVE_STATE_FILE": str(tmp_path / "active-units"),
            "RADON_TEST_REPLICA_PREFIX": str(tmp_path / "replica.db"),
            "RADON_TEST_FLOCK": shutil.which("flock") or "",
            "RADON_TEST_ROOT_LOCK_FILE": str(tmp_path / "root.lock"),
            "RADON_TEST_ROOT_LOCK_WAIT": "1",
        }
        first = subprocess.Popen(
            ["bash", str(ROOT_HELPER), "stop-clean"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 2
            while not entered.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert entered.exists()
            started = time.monotonic()
            second = subprocess.run(
                ["bash", str(ROOT_HELPER), "verify-restored"],
                env=env,
                capture_output=True,
                text=True,
                timeout=3,
            )
            assert second.returncode == 70
            assert time.monotonic() - started < 2
        finally:
            first.wait(timeout=5)

    @pytest.mark.parametrize(
        ("stop_result", "active_state"),
        [("fail", "inactive"), ("ok", "active")],
        ids=["stop-command-fails", "unit-remains-active"],
    )
    def test_stop_failure_or_active_unit_never_deletes_replica(
        self, tmp_path: Path, stop_result: str, active_state: str
    ) -> None:
        assert ROOT_HELPER.is_file(), "fixed root deploy helper missing"
        fake_systemctl = tmp_path / "systemctl"
        fake_rm = tmp_path / "rm"
        fake_sync = tmp_path / "sync"
        rm_log = tmp_path / "rm.log"
        _write_executable(
            fake_systemctl,
            f"""#!/bin/bash
set -euo pipefail
[[ "$1" == --no-block ]] && shift
case "$1" in
  list-unit-files)
    printf '%s\\n' \
      'radon-nextjs.service enabled' \
      'radon-api.service enabled' \
      'radon-relay.service enabled' \
      'radon-monitor.service enabled' \
      'radon-newsfeed.service enabled'
    ;;
  list-units)
    printf '%s\\n' \
      'radon-nextjs.service loaded active running' \
      'radon-api.service loaded active running' \
      'radon-relay.service loaded active running' \
      'radon-monitor.service loaded active running' \
      'radon-newsfeed.service loaded active running'
    ;;
  stop) [[ {stop_result} == ok ]] ;;
  show)
    if [[ "$2" == radon-ib-gateway-preheld-restart.service ]]; then
      printf 'inactive\\n'
    elif [[ "$*" == *"--property=Type"* ]]; then
      printf 'simple\\n'
    else
      printf '%s\\n' {active_state}
    fi
    ;;
  start|restart) exit 0 ;;
  *) exit 2 ;;
esac
""",
        )
        _write_executable(
            fake_rm,
            f"""#!/bin/bash
set -euo pipefail
if [[ "$*" == *replica.db* ]]; then printf '%s\\n' "$*" >> {rm_log!s}; fi
exec /bin/rm "$@"
""",
        )
        _write_executable(fake_sync, "#!/bin/bash\nexit 0\n")
        result = subprocess.run(
            ["bash", str(ROOT_HELPER), "stop-clean"],
            env={
                **os.environ,
                "RADON_DEPLOY_HELPER_TEST_MODE": "1",
                "RADON_TEST_SYSTEMCTL": str(fake_systemctl),
                "RADON_TEST_RM": str(fake_rm),
                "RADON_TEST_SYNC": str(fake_sync),
                "RADON_TEST_ACTIVE_STATE_FILE": str(tmp_path / "active-units"),
                "RADON_TEST_REPLICA_PREFIX": str(tmp_path / "replica.db"),
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert not rm_log.exists(), "replica deletion ran before a verified clean stop"

    def test_helper_and_sudoers_have_fixed_command_surface(self) -> None:
        assert ROOT_HELPER.is_file()
        helper = ROOT_HELPER.read_text(encoding="utf-8")
        for service in MANAGED_SERVICES:
            assert service in helper
        for suffix in ("", "-wal", "-shm", "-info"):
            assert f"replica.db{suffix}" in helper
        assert "stop-clean" in helper and "recover" in helper
        assert 'case "$1" in' in helper and "eval " not in helper
        sudoers = SUDOERS.read_text(encoding="utf-8")
        assert "radon-*" not in sudoers
        assert "/usr/bin/systemctl" not in sudoers
        assert "/usr/bin/rm" not in sudoers
        for action in (
            "stop-clean",
            "restart-managed",
            "recover",
            "verify-restored",
            "verify-control-plane",
            "commit-transition",
            "install-units",
            "sync-scheduled-units",
        ):
            assert f"/usr/local/sbin/radon-deploy-root {action}" in sudoers

    def test_deploy_stop_failure_arms_exit_recovery(self, tmp_path: Path) -> None:
        calls = tmp_path / "sudo.log"
        shell = f"""
set -euo pipefail
source {DEPLOY!s}
wait_for_gateway_ready() {{ return 0; }}
sudo() {{
  printf '%s\\n' "$*" >> {calls!s}
  [[ "$*" != *" stop-clean" ]]
}}
trap 'handle_deploy_exit "$?"' EXIT
restart_services
"""
        result = subprocess.run(
            ["bash", "-c", shell],
            env={**os.environ, "RADON_DEPLOY_ROOT_HELPER": "/fixed/root-helper"},
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert calls.read_text(encoding="utf-8").splitlines() == [
            "/fixed/root-helper stop-clean",
            "/fixed/root-helper recover",
        ]

    def test_setup_validates_temp_sudoers_then_atomically_installs_helper(self) -> None:
        setup = SETUP.read_text(encoding="utf-8")
        configure = function_body(setup, "configure_sudoers")
        assert "mktemp" in configure
        assert configure.find("visudo -cf") < configure.find("mv -f")
        installer = function_body(setup, "install_deploy_root_helper")
        assert "install -m 0755 -o root -g root" in installer
        assert "mv -f" in installer
        main = function_body(setup, "main")
        assert main.find("install_deploy_root_helper") < main.find("configure_sudoers")


class TestCaddyDeployment:
    def test_caddy_candidate_is_validated_before_atomic_install(self) -> None:
        setup = SETUP.read_text(encoding="utf-8")
        body = function_body(setup, "configure_caddy")
        validate = body.find("validate --config")
        promote = body.find("mv -f")
        assert 0 <= validate < promote
        assert "restart caddy" not in body
        assert "|| true" not in body

    @pytest.mark.parametrize("failure", ["validation", "reload"])
    def test_caddy_failure_preserves_known_good_live_config(
        self, tmp_path: Path, failure: str
    ) -> None:
        cloud = tmp_path / "cloud"
        (cloud / "caddy").mkdir(parents=True)
        (cloud / "caddy" / "Caddyfile").write_text("candidate\n", encoding="utf-8")
        live = tmp_path / "etc" / "Caddyfile"
        live.parent.mkdir()
        live.write_text("known-good\n", encoding="utf-8")
        caddy_log = tmp_path / "caddy.log"
        systemctl_log = tmp_path / "systemctl.log"
        fake_caddy = tmp_path / "caddy"
        _write_executable(
            fake_caddy,
            f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {caddy_log!s}\n"
            + ("exit 9\n" if failure == "validation" else "exit 0\n"),
        )
        fake_systemctl = tmp_path / "systemctl"
        _write_executable(
            fake_systemctl,
            f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {systemctl_log!s}\nexit 0\n",
        )
        timeout_command = "/usr/bin/false" if failure == "reload" else "/usr/bin/true"
        shell = f"""
set -euo pipefail
source {SETUP!s}
chown() {{ return 0; }}
configure_caddy
"""
        result = subprocess.run(
            ["bash", "-c", shell],
            env={
                **os.environ,
                "RADON_SETUP_SOURCE_ONLY": "1",
                "RADON_CLOUD_DIR": str(cloud),
                "RADON_CADDY_CONFIG_PATH": str(live),
                "RADON_CADDY_LOG_DIR": str(tmp_path / "log"),
                "RADON_CADDY_BIN": str(fake_caddy),
                "RADON_CADDY_SYSTEMCTL": str(fake_systemctl),
                "RADON_CADDY_TIMEOUT": timeout_command,
                "RADON_CADDY_SYNC": "/usr/bin/true",
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert live.read_text(encoding="utf-8") == "known-good\n"
        assert "validate --config" in caddy_log.read_text(encoding="utf-8")
        assert not systemctl_log.exists()

    def test_timed_out_reload_reloads_restored_known_good_config(self, tmp_path: Path) -> None:
        cloud = tmp_path / "cloud"
        (cloud / "caddy").mkdir(parents=True)
        (cloud / "caddy" / "Caddyfile").write_text("candidate\n", encoding="utf-8")
        live = tmp_path / "etc" / "Caddyfile"
        live.parent.mkdir()
        live.write_text("known-good\n", encoding="utf-8")
        fake_caddy = tmp_path / "caddy"
        _write_executable(fake_caddy, "#!/bin/sh\nexit 0\n")
        reloads = tmp_path / "reloads.log"
        count = tmp_path / "reload-count"
        fake_timeout = tmp_path / "timeout"
        _write_executable(
            fake_timeout,
            f"""#!{sys.executable}
from pathlib import Path
live = Path({str(live)!r})
count = Path({str(count)!r})
reloads = Path({str(reloads)!r})
attempt = int(count.read_text()) if count.exists() else 0
count.write_text(str(attempt + 1))
with reloads.open("a", encoding="utf-8") as handle:
    handle.write(live.read_text(encoding="utf-8").strip() + "\\n")
raise SystemExit(124 if attempt == 0 else 0)
""",
        )
        shell = f"""
set -euo pipefail
source {SETUP!s}
chown() {{ return 0; }}
configure_caddy
"""
        result = subprocess.run(
            ["bash", "-c", shell],
            env={
                **os.environ,
                "RADON_SETUP_SOURCE_ONLY": "1",
                "RADON_CLOUD_DIR": str(cloud),
                "RADON_CADDY_CONFIG_PATH": str(live),
                "RADON_CADDY_LOG_DIR": str(tmp_path / "log"),
                "RADON_CADDY_BIN": str(fake_caddy),
                "RADON_CADDY_SYSTEMCTL": "/usr/bin/true",
                "RADON_CADDY_TIMEOUT": str(fake_timeout),
                "RADON_CADDY_SYNC": "/usr/bin/true",
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert live.read_text(encoding="utf-8") == "known-good\n"
        assert reloads.read_text(encoding="utf-8").splitlines() == [
            "candidate",
            "known-good",
        ]


class TestStrengthenedGate:
    REQUIRED_CHECKS = (
        "check_units_active",
        "check_release_topology_restored",
        "check_health_lite",
        "check_nextjs_http",
        "check_relay_listener",
        "check_relay_http",
        "check_units_stable",
    )

    def test_gate_contains_all_code_controlled_checks_and_advisory_ib(self, deploy_text: str) -> None:
        gate = function_body(deploy_text, "deploy_gate")
        for check in self.REQUIRED_CHECKS:
            assert check in gate
        assert "report_isolated_health" in gate
        for function in ("check_nextjs_http", "check_relay_listener", "check_relay_http"):
            body = function_body(deploy_text, function)
            assert "timeout" in body.lower() or "max-time" in body

    @pytest.mark.parametrize("failed_check", REQUIRED_CHECKS)
    def test_each_code_controlled_check_can_fail_gate(self, failed_check: str) -> None:
        overrides = "\n".join(
            f"{check}() {{ {'return 1' if check == failed_check else 'return 0'}; }}"
            for check in self.REQUIRED_CHECKS
        )
        shell = f"""
set -euo pipefail
source {DEPLOY!s}
{overrides}
report_isolated_health() {{ return 0; }}
deploy_gate
"""
        result = subprocess.run(["bash", "-c", shell], capture_output=True, text=True)
        assert result.returncode == 1

    def test_stability_clock_starts_before_surface_probes(self, deploy_text: str) -> None:
        gate = function_body(deploy_text, "deploy_gate")
        snap = gate.index("snapshot_unit_restarts")
        assert snap < gate.index("check_health_lite")
        assert snap < gate.index("check_units_stable")
        stable = function_body(deploy_text, "check_units_stable")
        assert "snapshot_unit_restarts" in stable
        assert "STABILITY_STARTED_AT" in stable

    def test_check_units_stable_sleeps_only_the_remaining_window(
        self, tmp_path: Path
    ) -> None:
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        slept = tmp_path / "slept"
        _write_executable(
            fake_bin / "systemctl",
            f"""#!{sys.executable}
import sys
if sys.argv[1] == "is-active":
    raise SystemExit(0)
if sys.argv[1] == "show":
    print(0)
    raise SystemExit(0)
raise SystemExit(2)
""",
        )
        _write_executable(
            fake_bin / "sleep",
            f"""#!{sys.executable}
from pathlib import Path
import sys
Path({str(slept)!r}).write_text(sys.argv[1], encoding="utf-8")
""",
        )
        shell = f"""
set -euo pipefail
source {DEPLOY!s}
PATH={fake_bin!s}:$PATH
snapshot_unit_restarts
STABILITY_STARTED_AT=$((SECONDS - 7))
check_units_stable
"""
        result = subprocess.run(
            ["bash", "-c", shell],
            env={
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "RADON_DEPLOY_UNIT_STABILITY_SECONDS": "10",
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert slept.read_text(encoding="utf-8").strip() == "3"

    def test_check_units_stable_settles_even_when_units_were_not_restarted(
        self, tmp_path: Path
    ) -> None:
        """Was ``..._skips_sleep_when_units_were_not_restarted``.

        That skip is R-231: the UNITS_RESTARTED=0 branch does not restart
        units, but it DOES ``git reset --hard`` the live checkout and promote
        artifacts while radon-api, radon-monitor and every ``.venv/bin/python``
        timer oneshot are running. It is therefore the one path where an
        unexplained unit failure has no restart to correlate with — the path
        that most needs a settle window, not the one that can skip it. The
        rest of the case (the check runs, the units are inspected, the exit is
        clean) is unchanged.
        """
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        slept = tmp_path / "slept"
        _write_executable(
            fake_bin / "systemctl",
            f"""#!{sys.executable}
import sys
if sys.argv[1] == "is-active":
    raise SystemExit(0)
if sys.argv[1] == "show":
    print(0)
    raise SystemExit(0)
raise SystemExit(2)
""",
        )
        _write_executable(
            fake_bin / "sleep",
            f"""#!{sys.executable}
from pathlib import Path
import sys
Path({str(slept)!r}).write_text(sys.argv[1], encoding="utf-8")
""",
        )
        shell = f"""
set -euo pipefail
source {DEPLOY!s}
PATH={fake_bin!s}:$PATH
UNITS_RESTARTED=0
snapshot_unit_restarts
STABILITY_STARTED_AT=$((SECONDS - 1))
check_units_stable
"""
        result = subprocess.run(
            ["bash", "-c", shell],
            env={
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "RADON_DEPLOY_UNIT_STABILITY_SECONDS": "40",
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert slept.exists(), (
            "the promote path that mutates the live tree under running "
            "services skipped its settle window entirely"
        )

    def test_payload_paths_changed_ignores_docs_and_tests(self, tmp_path: Path) -> None:
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        _write_executable(
            fake_bin / "git",
            f"""#!{sys.executable}
import sys
if sys.argv[1] == "-C":
    sys.argv = sys.argv[3:]
if sys.argv[:2] == ["diff", "--name-only"]:
    print("docs/cloud-services.md")
    print("scripts/tests/test_foo.py")
    print(".github/workflows/ci.yml")
    raise SystemExit(0)
raise SystemExit(2)
""",
        )
        result = subprocess.run(
            ["bash", "-c", f"source {DEPLOY!s}; payload_paths_changed a b"],
            env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, result.stdout + result.stderr

    def test_payload_paths_changed_detects_web_runtime(self, tmp_path: Path) -> None:
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        _write_executable(
            fake_bin / "git",
            f"""#!{sys.executable}
import sys
if sys.argv[1] == "-C":
    sys.argv = sys.argv[3:]
if sys.argv[:2] == ["diff", "--name-only"]:
    print("web/app/page.tsx")
    raise SystemExit(0)
raise SystemExit(2)
""",
        )
        result = subprocess.run(
            ["bash", "-c", f"source {DEPLOY!s}; payload_paths_changed a b"],
            env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_payload_paths_changed_fail_open_when_git_fails(self, tmp_path: Path) -> None:
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        _write_executable(
            fake_bin / "git",
            f"""#!{sys.executable}
import sys
raise SystemExit(128)
""",
        )
        result = subprocess.run(
            ["bash", "-c", f"source {DEPLOY!s}; payload_paths_changed a b"],
            env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_restart_services_skips_helper_when_payloads_match(
        self, deploy_text: str
    ) -> None:
        body = function_body(deploy_text, "restart_services")
        assert "payload_paths_changed" in body
        assert "UNITS_RESTARTED=0" in body
        assert "stop_services_for_transition" in body
        assert "start_services_after_transition" in body

    def test_topology_is_reverified_after_core_stability_hold(self, deploy_text: str) -> None:
        gate = function_body(deploy_text, "deploy_gate")
        stability = gate.index("check_units_stable")
        first_topology = gate.index("check_release_topology_restored")
        second_topology = gate.index("check_release_topology_restored", stability)
        assert first_topology < stability < second_topology

        shell = f"""
set -euo pipefail
source {DEPLOY!s}
topology_calls=0
check_units_active() {{ return 0; }}
check_release_topology_restored() {{
  topology_calls=$((topology_calls + 1))
  (( topology_calls == 1 ))
}}
check_health_lite() {{ return 0; }}
check_nextjs_http() {{ return 0; }}
check_relay_listener() {{ return 0; }}
check_relay_http() {{ return 0; }}
report_isolated_health() {{ return 0; }}
check_units_stable() {{ return 0; }}
deploy_gate
"""
        result = subprocess.run(["bash", "-c", shell], capture_output=True, text=True)
        assert result.returncode == 1

    def test_marker_and_noop_paths_depend_on_strengthened_gate(self, deploy_text: str) -> None:
        main = function_body(deploy_text, "main")
        noop = main.index("green_marker_matches")
        marker = main.index("write_green_marker")
        assert main.index("deploy_gate", noop) < marker
        assert main.rfind("deploy_gate", 0, marker) >= 0

    def test_restart_counter_change_during_stability_window_fails_gate(
        self, tmp_path: Path
    ) -> None:
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        calls = tmp_path / "show-calls"
        _write_executable(
            fake_bin / "systemctl",
            f"""#!{sys.executable}
import sys
from pathlib import Path
calls = Path({str(calls)!r})
if sys.argv[1] == "is-active":
    raise SystemExit(0)
if sys.argv[1] == "show":
    count = int(calls.read_text()) if calls.exists() else 0
    calls.write_text(str(count + 1))
    print(0 if count < {len(MANAGED_SERVICES)} else 1)
    raise SystemExit(0)
raise SystemExit(2)
""",
        )
        result = subprocess.run(
            ["bash", "-c", f"source {DEPLOY!s}; check_units_stable"],
            env={
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "RADON_DEPLOY_UNIT_STABILITY_SECONDS": "0",
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "restart counter changed" in result.stdout + result.stderr

    def test_failed_live_gate_quarantines_marker_and_self_heals(self, tmp_path: Path) -> None:
        sha = "a" * 40
        marker = tmp_path / "green-marker"
        marker.write_text(f"{sha}\n", encoding="utf-8")
        calls = tmp_path / "calls"
        gate_count = tmp_path / "gate-count"
        shell = f"""
set -euo pipefail
source {DEPLOY!s}
preflight_control_plane() {{ return 0; }}
preflight_env() {{ return 0; }}
current_commit() {{ printf '%s\\n' {sha}; }}
git() {{
  case "$*" in
    *"rev-parse origin/main"*) printf '%s\\n' {sha} ;;
    *) return 0 ;;
  esac
}}
verify_tracked_drift_matches_target() {{ return 0; }}
deploy_gate() {{
  local count=0
  [[ ! -f {gate_count!s} ]] || count="$(cat {gate_count!s})"
  count=$((count + 1))
  printf '%s\\n' "$count" > {gate_count!s}
  [[ "$count" -ge 2 ]]
}}
build_staged_release() {{ printf 'build\\n' >> {calls!s}; }}
activate_staged_release() {{ printf 'activate\\n' >> {calls!s}; }}
restart_services() {{ printf 'restart\\n' >> {calls!s}; }}
write_transition_phase() {{ return 0; }}
record_deploy_marker() {{ return 0; }}
finalize_release_artifacts() {{ return 0; }}
sudo() {{ return 0; }}
sleep() {{ return 0; }}
short_log() {{ printf 'test release\\n'; }}
main {sha}
"""
        result = subprocess.run(
            ["bash", "-c", shell],
            env={
                **os.environ,
                "RADON_DEPLOY_GREEN_MARKER": str(marker),
                "RADON_DIR": str(tmp_path / "live"),
                "RADON_CLOUD_DIR": str(ROOT),
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert gate_count.read_text(encoding="utf-8").strip() == "2"
        assert calls.read_text(encoding="utf-8").splitlines() == [
            "build",
            "restart",
        ]
        quarantined = list(tmp_path.glob("green-marker.invalid.*"))
        assert len(quarantined) == 1
        assert quarantined[0].read_text(encoding="utf-8") == f"{sha}\n"
        assert marker.read_text(encoding="utf-8") == f"{sha}\n"


class TestRollbackGate:
    def test_rollback_waits_and_runs_shared_gate(self, deploy_text: str) -> None:
        rollback = function_body(deploy_text, "rollback")
        assert "HEALTH_WAIT_SECONDS" in rollback
        assert "deploy_gate" in rollback
        assert "exit 3" in rollback

    @pytest.mark.parametrize(("gate_status", "expected"), [(0, 1), (1, 3)])
    def test_rollback_reports_truthfully(self, gate_status: int, expected: int) -> None:
        shell = f"""
set -euo pipefail
source {DEPLOY!s}
git() {{ return 0; }}
recover_pending_transition() {{ return 0; }}
sleep() {{ return 0; }}
deploy_gate() {{ return {gate_status}; }}
rollback {'a' * 40}
"""
        result = subprocess.run(["bash", "-c", shell], capture_output=True, text=True)
        assert result.returncode == expected
        output = (result.stdout + result.stderr).lower()
        if gate_status:
            assert "restored" not in output
            assert "rollback gate failed" in output
        else:
            assert "rollback complete" in output

    def _run_rollback(self, tmp_path, prev_sha: str, marker_sha: str | None):
        marker = tmp_path / "green-marker"
        if marker_sha is not None:
            marker.write_text(f"{marker_sha}\n", encoding="utf-8")
        shell = f"""
set -euo pipefail
source {DEPLOY!s}
git() {{ return 0; }}
recover_pending_transition() {{ return 0; }}
sleep() {{ return 0; }}
deploy_gate() {{ return 0; }}
rollback {prev_sha}
"""
        return subprocess.run(
            ["bash", "-c", shell],
            env={**os.environ, "RADON_DEPLOY_GREEN_MARKER": str(marker)},
            capture_output=True,
            text=True,
        )

    def test_rollback_without_green_evidence_never_claims_the_gate_passed(
        self, tmp_path: Path
    ) -> None:
        """T-254: CI run 33239774951 rolled f7b5eeb9 back to e4bc7171, whose own
        ci.yml run (33197706791) concluded `failure` at `pytest (cloud mz)` with
        the deploy job skipped. The log still asserted the release "passed the
        deploy gate". A rollback target with no green-gate record must be
        reported as unverified — and must still complete."""
        prev = "e" * 40
        result = self._run_rollback(tmp_path, prev, marker_sha="b" * 40)
        output = (result.stdout + result.stderr).lower()
        assert result.returncode == 1, output
        assert "rollback complete" in output
        assert "passed the deploy gate" not in output
        assert "no green deploy-gate record" in output

    def test_rollback_with_no_marker_at_all_is_reported_unverified(
        self, tmp_path: Path
    ) -> None:
        result = self._run_rollback(tmp_path, "e" * 40, marker_sha=None)
        output = (result.stdout + result.stderr).lower()
        assert result.returncode == 1, output
        assert "no green deploy-gate record" in output

    def test_rollback_to_a_recorded_green_release_still_reports_it_passed(
        self, tmp_path: Path
    ) -> None:
        prev = "c" * 40
        result = self._run_rollback(tmp_path, prev, marker_sha=prev)
        output = (result.stdout + result.stderr).lower()
        assert result.returncode == 1, output
        assert "passed the deploy gate" in output
        assert "no green deploy-gate record" not in output


class TestFrozenArtifacts:
    def test_deploy_and_setup_use_only_immutable_node_installs(self, deploy_text: str) -> None:
        build = function_body(deploy_text, "build_nextjs_at")
        setup_node = function_body(SETUP.read_text(encoding="utf-8"), "setup_node")
        for body in (build, setup_node):
            assert body.count("bun install --frozen-lockfile") >= 2
            assert "npm install" not in body and "npm ci" not in body
            assert body.count("node_modules/.bin/playwright install chromium chromium-headless-shell") >= 2
        assert "ms-playwright" in build or "PLAYWRIGHT_BROWSERS_PATH" in build
        assert 'BUN_VERSION="1.3.14"' in deploy_text
        assert "bun --version" in build
        seed = function_body(deploy_text, "seed_staged_node_modules")
        assert "cmp -s" in seed
        assert "node_modules" in seed
        assert ".deploy-reuse-live" in seed
        assert "should_rebuild_next" in seed
        assert "cp -a --link" not in seed
        assert build.index("seed_staged_node_modules") < build.index("bun install")
        activate = function_body(deploy_text, "activate_staged_release")
        assert ".deploy-reuse-live" in activate
        assert "live_python_env_matches" in activate
        rebuild = function_body(deploy_text, "should_rebuild_next")
        assert "lib/tools" in rebuild
        assert "web/.next" in rebuild
        assert "should_rebuild_next" in build
        assert "bun run build" in build
        main = function_body(deploy_text, "main")
        assert "RADON_WHEEL_CACHE" in function_body(deploy_text, "prepare_python_wheels")
        staged = function_body(deploy_text, "build_staged_release")
        assert staged.index("build_nextjs_at") < staged.index("wait")
        assert staged.index("prepare_python_wheels") < staged.index("wait")
        assert "&" in staged
        assert main.find("build_staged_release") < main.find("restart_services")
        restart = function_body(deploy_text, "restart_services")
        stop_at = restart.find("stop_services_for_transition")
        assert stop_at >= 0
        assert stop_at < restart.find(
            "activate_staged_release", stop_at
        ) < restart.find("start_services_after_transition")

    def test_seed_staged_node_modules_reuses_live_tree_when_lockfiles_match(
        self, tmp_path: Path
    ) -> None:
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        _write_executable(
            fake_bin / "git",
            f"""#!{sys.executable}
import sys
if "rev-parse" in sys.argv:
    print("abc123")
    raise SystemExit(0)
if "diff" in sys.argv:
    raise SystemExit(0)
raise SystemExit(2)
""",
        )
        live = tmp_path / "live"
        staged = tmp_path / "staged"
        flags = tmp_path / "seeded"
        for root in (live, staged):
            (root / "web").mkdir(parents=True)
            (root / "bun.lock").write_text("root-lock\n", encoding="utf-8")
            (root / "web" / "bun.lock").write_text("web-lock\n", encoding="utf-8")
        (live / "node_modules").mkdir()
        (live / "node_modules" / "sentinel").write_text("root-deps\n", encoding="utf-8")
        (live / "web" / "node_modules").mkdir()
        (live / "web" / "node_modules" / "sentinel").write_text(
            "web-deps\n", encoding="utf-8"
        )
        (live / "web" / ".next").mkdir()
        (live / "web" / ".next" / "BUILD_ID").write_text("live-build\n", encoding="utf-8")
        result = subprocess.run(
            [
                "bash",
                "-c",
                f"source {DEPLOY!s}; seed_staged_node_modules {staged!s}; "
                f'printf "%s %s\\n" "$SEEDED_ROOT_NODE_MODULES" "$SEEDED_WEB_NODE_MODULES" > {flags}',
            ],
            env={
                **os.environ,
                "RADON_DIR": str(live),
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert not (staged / "node_modules").exists()
        assert not (staged / "web" / "node_modules").exists()
        assert not (staged / "web" / ".next").exists()
        assert (live / "node_modules" / "sentinel").read_text(encoding="utf-8") == (
            "root-deps\n"
        )
        marker = (staged / ".deploy-reuse-live").read_text(encoding="utf-8").splitlines()
        assert marker == ["node_modules", "web/node_modules", "web/.next"]
        assert flags.read_text(encoding="utf-8").strip() == "1 1"

    def test_seed_staged_node_modules_copies_when_next_must_rebuild(
        self, tmp_path: Path
    ) -> None:
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        _write_executable(
            fake_bin / "git",
            f"""#!{sys.executable}
import sys
if "rev-parse" in sys.argv:
    print("abc123")
    raise SystemExit(0)
if "diff" in sys.argv:
    print("web/app/page.tsx")
    raise SystemExit(0)
raise SystemExit(2)
""",
        )
        live = tmp_path / "live"
        staged = tmp_path / "staged"
        for root in (live, staged):
            (root / "web").mkdir(parents=True)
            (root / "bun.lock").write_text("root-lock\n", encoding="utf-8")
            (root / "web" / "bun.lock").write_text("web-lock\n", encoding="utf-8")
        (live / "node_modules").mkdir()
        (live / "node_modules" / "sentinel").write_text("root-deps\n", encoding="utf-8")
        (live / "web" / "node_modules").mkdir()
        (live / "web" / "node_modules" / "sentinel").write_text(
            "web-deps\n", encoding="utf-8"
        )
        (live / "web" / ".next").mkdir()
        (live / "web" / ".next" / "BUILD_ID").write_text("live-build\n", encoding="utf-8")
        result = subprocess.run(
            ["bash", "-c", f"source {DEPLOY!s}; seed_staged_node_modules {staged!s}"],
            env={
                **os.environ,
                "RADON_DIR": str(live),
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert not (staged / ".deploy-reuse-live").exists()
        assert (staged / "node_modules" / "sentinel").read_text(encoding="utf-8") == (
            "root-deps\n"
        )
        assert (staged / "web" / ".next" / "BUILD_ID").read_text(encoding="utf-8") == (
            "live-build\n"
        )

    def test_should_rebuild_next_skips_when_inputs_match_and_next_exists(
        self, tmp_path: Path
    ) -> None:
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        _write_executable(
            fake_bin / "git",
            f"""#!{sys.executable}
import sys
if "rev-parse" in sys.argv:
    print("abc123")
    raise SystemExit(0)
if "diff" in sys.argv:
    raise SystemExit(0)
raise SystemExit(2)
""",
        )
        live = tmp_path / "live"
        staged = tmp_path / "staged"
        live.mkdir()
        (staged / "web" / ".next").mkdir(parents=True)
        result = subprocess.run(
            ["bash", "-c", f"source {DEPLOY!s}; should_rebuild_next {staged!s}"],
            env={
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "RADON_DIR": str(live),
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, result.stdout + result.stderr

    def test_should_rebuild_next_when_web_inputs_change(self, tmp_path: Path) -> None:
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        _write_executable(
            fake_bin / "git",
            f"""#!{sys.executable}
import sys
if "rev-parse" in sys.argv:
    print("abc123")
    raise SystemExit(0)
if "diff" in sys.argv:
    print("web/app/page.tsx")
    raise SystemExit(0)
raise SystemExit(2)
""",
        )
        live = tmp_path / "live"
        staged = tmp_path / "staged"
        live.mkdir()
        (staged / "web" / ".next").mkdir(parents=True)
        result = subprocess.run(
            ["bash", "-c", f"source {DEPLOY!s}; should_rebuild_next {staged!s}"],
            env={
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "RADON_DIR": str(live),
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_should_rebuild_next_when_seeded_next_is_missing(self, tmp_path: Path) -> None:
        staged = tmp_path / "staged"
        staged.mkdir()
        result = subprocess.run(
            ["bash", "-c", f"source {DEPLOY!s}; should_rebuild_next {staged!s}"],
            env={**os.environ, "RADON_DIR": str(tmp_path / "live")},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_should_rebuild_next_skips_when_live_next_exists_and_inputs_match(
        self, tmp_path: Path
    ) -> None:
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        _write_executable(
            fake_bin / "git",
            f"""#!{sys.executable}
import sys
if "rev-parse" in sys.argv:
    print("abc123")
    raise SystemExit(0)
if "diff" in sys.argv:
    raise SystemExit(0)
raise SystemExit(2)
""",
        )
        live = tmp_path / "live"
        staged = tmp_path / "staged"
        (live / "web" / ".next").mkdir(parents=True)
        staged.mkdir()
        result = subprocess.run(
            ["bash", "-c", f"source {DEPLOY!s}; should_rebuild_next {staged!s}"],
            env={
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "RADON_DIR": str(live),
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, result.stdout + result.stderr

    def test_seed_staged_node_modules_skips_when_lockfiles_differ(
        self, tmp_path: Path
    ) -> None:
        live = tmp_path / "live"
        staged = tmp_path / "staged"
        (live / "web").mkdir(parents=True)
        (staged / "web").mkdir(parents=True)
        (live / "bun.lock").write_text("live-lock\n", encoding="utf-8")
        (staged / "bun.lock").write_text("staged-lock\n", encoding="utf-8")
        (live / "web" / "bun.lock").write_text("web-lock\n", encoding="utf-8")
        (staged / "web" / "bun.lock").write_text("web-lock\n", encoding="utf-8")
        (live / "node_modules").mkdir()
        (live / "node_modules" / "sentinel").write_text("live\n", encoding="utf-8")
        result = subprocess.run(
            ["bash", "-c", f"source {DEPLOY!s}; seed_staged_node_modules {staged!s}"],
            env={**os.environ, "RADON_DIR": str(live)},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert not (staged / "node_modules").exists()

    def test_prepare_python_wheels_reuses_hashed_host_cache(self, tmp_path: Path) -> None:
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        pip_log = tmp_path / "pip.log"
        _write_executable(
            fake_bin / "sha256sum",
            f"""#!{sys.executable}
print("cafef00d" + "0" * 56)
""",
        )
        _write_executable(
            fake_bin / "pip",
            f"""#!{sys.executable}
from pathlib import Path
Path({str(pip_log)!r}).write_text("called\\n", encoding="utf-8")
raise SystemExit(1)
""",
        )
        release = tmp_path / "release"
        (release / "scripts").mkdir(parents=True)
        (release / "requirements.txt").write_text("fastapi==0.1\\n", encoding="utf-8")
        (release / "scripts" / "requirements-api.txt").write_text(
            "uvicorn==0.1\\n", encoding="utf-8"
        )
        cache_root = tmp_path / "wheel-cache"
        cached = cache_root / ("cafef00d" + "0" * 56)
        cached.mkdir(parents=True)
        (cached / "fastapi-0.1-py3-none-any.whl").write_bytes(b"wheel")
        venv_bin = tmp_path / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        _write_executable(venv_bin / "python", "#!/bin/sh\nexit 0\n")
        (venv_bin / "pip").symlink_to(fake_bin / "pip")
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env['PATH']}"
        env["RADON_SHA256SUM"] = str(fake_bin / "sha256sum")
        env["RADON_WHEEL_CACHE"] = str(cache_root)
        env["VENV_DIR"] = str(tmp_path / "venv")
        result = subprocess.run(
            ["bash", "-c", f"source {DEPLOY!s}; prepare_python_wheels {release!s}"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert not pip_log.exists()
        staged = release / ".deploy-wheels" / "target" / "fastapi-0.1-py3-none-any.whl"
        assert staged.read_bytes() == b"wheel"
        assert (release / ".deploy-wheels" / "target").is_symlink()

    def test_validate_release_artifacts_accepts_live_reuse_marker(
        self, tmp_path: Path
    ) -> None:
        live = tmp_path / "live"
        staged = tmp_path / "staged"
        (live / "web" / ".next").mkdir(parents=True)
        (live / "web" / "node_modules").mkdir(parents=True)
        (live / "node_modules").mkdir()
        (staged / "web").mkdir(parents=True)
        (staged / "web" / ".env").write_text("NEXT_PUBLIC_X=1\n", encoding="utf-8")
        (staged / ".deploy-reuse-live").write_text(
            "node_modules\nweb/node_modules\nweb/.next\n", encoding="utf-8"
        )
        result = subprocess.run(
            ["bash", "-c", f"source {DEPLOY!s}; validate_release_artifacts {staged!s}"],
            env={**os.environ, "RADON_DIR": str(live)},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_install_target_python_env_skips_when_requirements_hash_matches(
        self, tmp_path: Path
    ) -> None:
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        pip_log = tmp_path / "pip.log"
        _write_executable(
            fake_bin / "sha256sum",
            f"""#!{sys.executable}
print("cafef00d" + "0" * 56)
""",
        )
        _write_executable(
            fake_bin / "python3.13",
            f"""#!{sys.executable}
from pathlib import Path
Path({str(pip_log)!r}).write_text("venv-called\\n", encoding="utf-8")
raise SystemExit(1)
""",
        )
        live = tmp_path / "live"
        release = tmp_path / "release"
        venv_bin = live / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        _write_executable(
            venv_bin / "python",
            f"""#!{sys.executable}
import sys
if "-c" in sys.argv:
    raise SystemExit(0)
raise SystemExit(0)
""",
        )
        (live / ".venv" / ".radon-req-hash").write_text(
            "cafef00d" + "0" * 56 + "\n", encoding="utf-8"
        )
        (release / "scripts").mkdir(parents=True)
        (release / "requirements.txt").write_text("fastapi==0.1\n", encoding="utf-8")
        (release / "scripts" / "requirements-api.txt").write_text(
            "uvicorn==0.1\n", encoding="utf-8"
        )
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}:{env['PATH']}"
        env["RADON_DIR"] = str(live)
        env["VENV_DIR"] = str(live / ".venv")
        env["RADON_SHA256SUM"] = str(fake_bin / "sha256sum")
        env["RADON_SYSTEM_PYTHON"] = str(fake_bin / "python3.13")
        result = subprocess.run(
            ["bash", "-c", f"source {DEPLOY!s}; install_target_python_env {release!s}"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert not pip_log.exists()

    def test_candidate_venv_matches_api_runtime_dependency_contract(self, deploy_text: str) -> None:
        wheels = function_body(deploy_text, "prepare_python_wheels")
        install = function_body(deploy_text, "install_target_python_env")
        assert ".radon-req-hash" in install
        validate_python = function_body(deploy_text, "validate_target_python_env")
        setup_python = function_body(SETUP.read_text(encoding="utf-8"), "setup_python")
        for body in (wheels, install, setup_python):
            assert "requirements.txt" in body
            assert "scripts/requirements-api.txt" in body
        for module in ("fastapi", "httptools", "uvicorn", "uvloop", "watchfiles", "websockets"):
            assert module in validate_python
        main = function_body(deploy_text, "main")
        assert "install_python_deps" not in main
        assert ".venv" in deploy_text and "ROLLBACK_ARTIFACTS" in deploy_text

    def test_failed_staged_build_preserves_live_artifacts_and_never_stops_services(
        self, tmp_path: Path
    ) -> None:
        live = tmp_path / "live"
        (live / "web" / ".next").mkdir(parents=True)
        (live / "node_modules").mkdir()
        (live / "web" / "node_modules").mkdir()
        sentinels = {
            live / "web" / ".next" / "BUILD_ID": b"known-green-build\n",
            live / "node_modules" / "root-sentinel": b"root-deps\n",
            live / "web" / "node_modules" / "web-sentinel": b"web-deps\n",
        }
        for path, content in sentinels.items():
            path.write_bytes(content)
        calls = tmp_path / "calls"
        shell = f"""
set -euo pipefail
source {DEPLOY!s}
create_staged_worktree() {{
  STAGED_RELEASE_DIR="$(mktemp -d {tmp_path!s}/stage.XXXXXX)"
}}
build_nextjs_at() {{ return 47; }}
cleanup_staged_release() {{ rm -rf "$STAGED_RELEASE_DIR"; STAGED_RELEASE_DIR=""; }}
sudo() {{ printf '%s\\n' "$*" >> {calls!s}; }}
build_staged_release {'b' * 40}
"""
        result = subprocess.run(
            ["bash", "-c", shell],
            env={
                **os.environ,
                "RADON_DIR": str(live),
                "RADON_CLOUD_DIR": str(ROOT),
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert not calls.exists(), "a failed staged build touched managed services"
        for path, content in sentinels.items():
            assert path.read_bytes() == content

    def test_failed_wheel_build_preserves_live_runtime(self, tmp_path: Path) -> None:
        releases = tmp_path / "releases"
        live = tmp_path / "live"
        stage = releases / "stage.target"
        releases.mkdir()
        stage.mkdir()
        old = _create_release_artifacts(live, "old")
        _create_release_artifacts(stage, "target")
        shell = f"""
set -euo pipefail
source {DEPLOY!s}
create_staged_worktree() {{ STAGED_RELEASE_DIR={stage!s}; }}
build_nextjs_at() {{ return 0; }}
prepare_python_wheels() {{ return 61; }}
cleanup_staged_release() {{ return 0; }}
build_staged_release {'b' * 40}
"""
        result = subprocess.run(
            ["bash", "-c", shell],
            env={
                **os.environ,
                "RADON_DIR": str(live),
                "RADON_CLOUD_DIR": str(ROOT),
                "RADON_RELEASES_DIR": str(releases),
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 61
        _assert_release_snapshot(live, old)

    @pytest.mark.parametrize("function", ["setup_python", "setup_node"])
    def test_setup_rerun_refuses_to_mutate_active_live_runtime(
        self, tmp_path: Path, function: str
    ) -> None:
        calls = tmp_path / "mutation-calls"
        shell = f"""
set -euo pipefail
export RADON_SETUP_SOURCE_ONLY=1
source {SETUP!s}
systemctl() {{ [[ "$*" == *"radon-api.service"* ]]; }}
run_as_radon() {{ printf '%s\\n' "$*" >> {calls!s}; }}
{function}
"""
        result = subprocess.run(["bash", "-c", shell], capture_output=True, text=True)
        assert result.returncode != 0
        assert not calls.exists()
        assert "Refusing in-place dependency mutation" in result.stdout + result.stderr

    def test_post_setup_refuses_active_consumers_before_live_build(self) -> None:
        post_setup = (ROOT / "scripts" / "post-setup.sh").read_text(encoding="utf-8")
        guard = post_setup.index("Refusing live rebuild")
        build = post_setup.index("bun run build")
        assert guard < build
        for unit in ("radon-nextjs.service", "radon-relay.service", "radon-newsfeed.service"):
            assert unit in post_setup[guard - 300 : build]


class TestPrestage:
    SHA = "a" * 40

    def _artifact_tree(self, root: Path) -> None:
        (root / "web" / "node_modules").mkdir(parents=True)
        (root / "web" / ".next").mkdir(parents=True)
        (root / "node_modules").mkdir()
        (root / "web" / ".env").write_text("NEXT_PUBLIC_X=1\n", encoding="utf-8")

    def test_write_and_load_prestage_round_trip(self, tmp_path: Path) -> None:
        staged = tmp_path / "staged"
        self._artifact_tree(staged)
        marker = tmp_path / "prestage"
        result = subprocess.run(
            [
                "bash",
                "-c",
                f"source {DEPLOY!s}; STAGED_RELEASE_DIR={staged!s}; "
                f"write_prestage {self.SHA}; load_prestage {self.SHA}; "
                'printf "%s\\n" "$STAGED_RELEASE_DIR"',
            ],
            env={
                **os.environ,
                "RADON_DIR": str(tmp_path / "live"),
                "RADON_DEPLOY_PRESTAGE": str(marker),
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert staged.resolve().as_posix() in result.stdout
        assert not marker.exists()

    def test_load_prestage_rejects_sha_mismatch(self, tmp_path: Path) -> None:
        staged = tmp_path / "staged"
        self._artifact_tree(staged)
        marker = tmp_path / "prestage"
        marker.write_text(f"{'b' * 40}\n{staged}\n", encoding="utf-8")
        result = subprocess.run(
            ["bash", "-c", f"source {DEPLOY!s}; load_prestage {self.SHA}"],
            env={
                **os.environ,
                "RADON_DIR": str(tmp_path / "live"),
                "RADON_DEPLOY_PRESTAGE": str(marker),
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1

    def test_stage_builds_without_restart_or_gate(self, tmp_path: Path) -> None:
        calls = tmp_path / "calls"
        staged = tmp_path / "staged"
        staged.mkdir()
        marker = tmp_path / "prestage"
        sha = self.SHA
        shell = f"""
set -euo pipefail
source {DEPLOY!s}
preflight_control_plane() {{ return 0; }}
preflight_env() {{ return 0; }}
current_commit() {{ printf '%s\\n' {sha}; }}
git() {{
  case "$*" in
    *"rev-parse origin/main"*) printf '%s\\n' {sha} ;;
    *) return 0 ;;
  esac
}}
verify_tracked_drift_matches_target() {{ return 0; }}
build_staged_release() {{
  printf 'build\\n' >> {calls!s}
  STAGED_RELEASE_DIR={staged!s}
}}
restart_services() {{ printf 'restart\\n' >> {calls!s}; }}
deploy_gate() {{ printf 'gate\\n' >> {calls!s}; return 0; }}
short_log() {{ printf 'log\\n'; }}
RADON_DEPLOY_STAGE=1 main {sha}
"""
        result = subprocess.run(
            ["bash", "-c", shell],
            env={
                **os.environ,
                "RADON_DIR": str(tmp_path / "live"),
                "RADON_CLOUD_DIR": str(ROOT),
                "RADON_DEPLOY_PRESTAGE": str(marker),
                "RADON_DEPLOY_STAGE": "1",
                "RADON_DEPLOY_GREEN_MARKER": str(tmp_path / "green"),
                "RADON_DEPLOY_TRANSITION_JOURNAL": str(tmp_path / "journal.json"),
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert calls.read_text(encoding="utf-8").splitlines() == ["build"]
        assert marker.exists()
        assert marker.read_text(encoding="utf-8").splitlines()[0] == sha

    def test_promote_reuses_prestage_and_skips_build(self, tmp_path: Path) -> None:
        calls = tmp_path / "calls"
        staged = tmp_path / "staged"
        self._artifact_tree(staged)
        marker = tmp_path / "prestage"
        sha = self.SHA
        marker.write_text(f"{sha}\n{staged}\n", encoding="utf-8")
        shell = f"""
set -euo pipefail
source {DEPLOY!s}
preflight_control_plane() {{ return 0; }}
preflight_env() {{ return 0; }}
current_commit() {{ printf '%s\\n' {'0' * 40}; }}
git() {{
  case "$*" in
    *"rev-parse origin/main"*) printf '%s\\n' {sha} ;;
    *) return 0 ;;
  esac
}}
verify_tracked_drift_matches_target() {{ return 0; }}
build_staged_release() {{ printf 'build\\n' >> {calls!s}; }}
restart_services() {{ printf 'restart\\n' >> {calls!s}; }}
deploy_gate() {{ return 0; }}
write_transition_phase() {{ return 0; }}
record_deploy_marker() {{ return 0; }}
write_green_marker() {{ return 0; }}
finalize_release_artifacts() {{ return 0; }}
sync_scheduled_units() {{ return 0; }}
notify_release_live() {{ return 0; }}
sudo() {{ return 0; }}
short_log() {{ printf 'log\\n'; }}
main {sha}
"""
        result = subprocess.run(
            ["bash", "-c", shell],
            env={
                **os.environ,
                "RADON_DIR": str(tmp_path / "live"),
                "RADON_CLOUD_DIR": str(ROOT),
                "RADON_DEPLOY_PRESTAGE": str(marker),
                "RADON_DEPLOY_GREEN_MARKER": str(tmp_path / "green"),
                "RADON_DEPLOY_TRANSITION_JOURNAL": str(tmp_path / "journal.json"),
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert calls.read_text(encoding="utf-8").splitlines() == ["restart"]
        assert not marker.exists()

    def test_supervisor_stage_skips_when_lock_or_journal_busy(self, deploy_text: str) -> None:
        supervise = function_body(deploy_text, "supervise_deploy_command")
        main = function_body(deploy_text, "main")
        assert "RADON_DEPLOY_STAGE" in supervise
        assert "skipping prestage" in supervise
        assert "RADON_DEPLOY_STAGE" in main
        assert "write_prestage" in main
        assert "load_prestage" in main
        assert main.find("write_prestage") < main.find("restart_services")
        assert main.find("load_prestage") < main.find("restart_services")


class TestRequiredEnvironment:
    def test_single_required_key_contract_drives_every_bootstrap_path(self, deploy_text: str) -> None:
        env_example = ENV_EXAMPLE.read_text(encoding="utf-8")
        assert REQUIRED_ENV.is_file()
        assert ENV_CHECKER.is_file()
        keys = [
            line.strip()
            for line in REQUIRED_ENV.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert keys and len(keys) == len(set(keys))
        assert all(re.fullmatch(r"[A-Z][A-Z0-9_]*", key) for key in keys)
        for key in keys:
            assert f"{key}=" in env_example
        for script in (DEPLOY, SETUP, ROOT / "scripts" / "post-setup.sh"):
            text = script.read_text(encoding="utf-8")
            assert "required-env.txt" in text
            assert "check-env.py" in text
        assert "readonly REQUIRED_VARS=(" not in deploy_text
        assert "local required_keys=(" not in SETUP.read_text(encoding="utf-8")

    def test_nonexpanding_checker_reports_names_without_secret_values(self, tmp_path: Path) -> None:
        keys = [
            line.strip()
            for line in REQUIRED_ENV.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        env_file = tmp_path / ".env"
        secret = "never-print-this-secret"
        lines = [f"{key}=value_{key}" for key in keys]
        target = keys[-1]
        lines = [line for line in lines if not line.startswith(f"{target}=")]
        lines.append(f"UNRELATED_SECRET='{secret}$NOT_EXPANDED'")
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        env_file.chmod(0o600)
        result = subprocess.run(
            [sys.executable, str(ENV_CHECKER), str(env_file), str(REQUIRED_ENV)],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        output = result.stdout + result.stderr
        assert target in output
        assert secret not in output

    def test_deployment_guide_never_copies_full_secret_env_into_web_tree(self) -> None:
        guide = (ROOT / "radon-cloud-deployment-guide.html").read_text(encoding="utf-8")
        assert "cp ~/radon-cloud/.env ~/radon/web/.env" not in guide
        assert "run_with_env.py" in guide
        assert "NEXT_PUBLIC_" in guide

    @pytest.mark.parametrize(
        ("trading_mode", "gateway_port"),
        (("live", "4001"), ("paper", "4002")),
    )
    def test_checker_accepts_matching_gateway_mode_and_host_port(
        self, tmp_path: Path, trading_mode: str, gateway_port: str
    ) -> None:
        keys = [
            line.strip()
            for line in REQUIRED_ENV.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assignments = {key: f"value_{key}" for key in keys}
        assignments.update(
            {
                "IB_GATEWAY_MODE": "cloud",
                "IB_GATEWAY_HOST": "127.0.0.1",
                "RADON_MODE": "hetzner",
                "NODE_ENV": "production",
            }
        )
        assignments["TRADING_MODE"] = trading_mode
        assignments["IB_GATEWAY_PORT"] = gateway_port
        env_file = tmp_path / ".env"
        env_file.write_text(
            "".join(f"{key}={value}\n" for key, value in assignments.items()),
            encoding="utf-8",
        )
        env_file.chmod(0o600)

        result = subprocess.run(
            [sys.executable, str(ENV_CHECKER), str(env_file), str(REQUIRED_ENV)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    @pytest.mark.parametrize(
        ("trading_mode", "gateway_port"),
        (("live", "4002"), ("paper", "4001"), ("both", "4001")),
    )
    def test_checker_rejects_ambiguous_or_mismatched_gateway_mode_and_port(
        self, tmp_path: Path, trading_mode: str, gateway_port: str
    ) -> None:
        keys = [
            line.strip()
            for line in REQUIRED_ENV.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assignments = {key: f"value_{key}" for key in keys}
        assignments.update(
            {
                "IB_GATEWAY_MODE": "cloud",
                "IB_GATEWAY_HOST": "127.0.0.1",
                "RADON_MODE": "hetzner",
                "NODE_ENV": "production",
            }
        )
        assignments["TRADING_MODE"] = trading_mode
        assignments["IB_GATEWAY_PORT"] = gateway_port
        env_file = tmp_path / ".env"
        env_file.write_text(
            "".join(f"{key}={value}\n" for key, value in assignments.items()),
            encoding="utf-8",
        )
        env_file.chmod(0o600)

        result = subprocess.run(
            [sys.executable, str(ENV_CHECKER), str(env_file), str(REQUIRED_ENV)],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        output = result.stdout + result.stderr
        assert "TRADING_MODE" in output
        assert "IB_GATEWAY_PORT" in output

    @pytest.mark.parametrize(
        ("key", "unsafe_value"),
        (
            ("IB_GATEWAY_MODE", "docker"),
            ("IB_GATEWAY_HOST", "ib-gateway"),
            ("RADON_MODE", "local"),
            ("NODE_ENV", "development"),
        ),
    )
    def test_skip_required_cannot_bypass_production_safety_invariants(
        self, tmp_path: Path, key: str, unsafe_value: str
    ) -> None:
        keys = [
            line.strip()
            for line in REQUIRED_ENV.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assignments = {name: f"value_{name}" for name in keys}
        assignments.update(
            {
                "IB_GATEWAY_MODE": "cloud",
                "IB_GATEWAY_HOST": "127.0.0.1",
                "RADON_MODE": "hetzner",
                "NODE_ENV": "production",
                "TRADING_MODE": "live",
                "IB_GATEWAY_PORT": "4001",
                key: unsafe_value,
            }
        )
        env_file = tmp_path / ".env"
        env_file.write_text(
            "".join(f"{name}={value}\n" for name, value in assignments.items()),
            encoding="utf-8",
        )
        env_file.chmod(0o600)

        result = subprocess.run(
            [
                sys.executable,
                str(ENV_CHECKER),
                str(env_file),
                str(REQUIRED_ENV),
                "--skip-required",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert key in result.stdout + result.stderr


class TestDeploymentBudgets:
    def test_default_supervisor_budget_covers_cold_install_build_and_gate(
        self, deploy_text: str
    ) -> None:
        match = re.search(r'DEPLOY_TIMEOUT="\$\{DEPLOY_TIMEOUT:-(\d+)\}"', deploy_text)
        assert match
        assert int(match.group(1)) >= 900

    def test_root_helper_has_action_specific_hard_deadlines(self) -> None:
        helper = ROOT_HELPER.read_text(encoding="utf-8")
        assert "readonly ROOT_MUTATION_ACTION_TIMEOUT=180" in helper
        assert "readonly ROOT_VERIFY_ACTION_TIMEOUT=30" in helper
        assert "readonly ROOT_COMMIT_ACTION_TIMEOUT=30" in helper
        selector = function_body(helper, "root_action_timeout")
        for action in ("stop-clean", "restart-managed", "recover"):
            assert action in selector
        assert "verify-restored" in selector
        assert "verify-control-plane" in selector
        assert "commit-transition" in selector
        assert "install-units" in selector
        assert "sync-scheduled-units" in selector
        assert "refresh-control-plane" in selector
        queues = function_body(helper, "action_queues_radon_jobs")
        assert "refresh-control-plane" not in queues
        supervisor = function_body(helper, "supervise_root_action")
        assert "root_action_timeout" in supervisor


class TestCloudSecretScan:
    def test_root_ci_runs_redacted_custom_gitleaks_scan(self) -> None:
        assert ROOT_CI.is_file()
        workflow = yaml.safe_load(ROOT_CI.read_text(encoding="utf-8"))
        steps = workflow["jobs"]["secret-scan"]["steps"]
        checkout = next(step for step in steps if "actions/checkout" in step.get("uses", ""))
        assert checkout["with"]["fetch-depth"] == 0
        commands = "\n".join(str(step.get("run", "")) for step in steps)
        assert "gitleaks" in commands
        assert "gitleaks detect --source ." in commands
        assert "--redact" in commands
        assert "--config cloud/.gitleaks.toml" in commands

    def test_root_ci_runs_full_python313_cloud_suite(self) -> None:
        workflow = yaml.safe_load(ROOT_CI.read_text(encoding="utf-8"))
        jobs = workflow["jobs"]
        pytest_jobs = [
            job
            for job in jobs.values()
            if re.search(
                r"python\s+-m\s+pytest",
                "\n".join(str(step.get("run", "")) for step in job["steps"]),
            )
        ]
        assert len(pytest_jobs) >= 2
        commands_by_job = [
            "\n".join(str(step.get("run", "")) for step in job["steps"])
            for job in pytest_jobs
        ]
        cloud = jobs["cloud-tests"]
        assert "cloud/tests/test_" in str(cloud["strategy"]["matrix"]["include"])
        assert "matrix.paths" in "\n".join(str(step.get("run", "")) for step in cloud["steps"])
        all_commands = [
            "\n".join(str(step.get("run", "")) for step in job["steps"])
            for job in jobs.values()
        ]
        assert any("fail-under=56" in commands for commands in all_commands)
        for job in pytest_jobs:
            setup_uv = next(
                step
                for step in job["steps"]
                if "astral-sh/setup-uv" in step.get("uses", "")
            )
            assert setup_uv["with"]["python-version"] == "3.13"
            commands = "\n".join(str(step.get("run", "")) for step in job["steps"])
            assert re.search(r"python\s+-m\s+pytest(?:\s|$)", commands)
        assert any(
            "requirements-test.txt" in "\n".join(str(step.get("run", "")) for step in job["steps"])
            for job in pytest_jobs
        )

    def test_custom_rules_reject_literal_tws_assignments_and_examples(self) -> None:
        config = tomllib.loads(GITLEAKS_CONFIG.read_text(encoding="utf-8"))
        patterns = [re.compile(rule["regex"]) for rule in config["rules"]]
        assignment = "TWS_" + "PASSWORD=literal_test_credential"
        prose = "credential " + "example: literal_test_credential"
        assert any(pattern.search(assignment) for pattern in patterns)
        assert any(pattern.search(prose) for pattern in patterns)
        assert not any(pattern.search("TWS_PASSWORD=") for pattern in patterns)

    def test_readme_does_not_claim_gitignored_env_is_tracked(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        assert ".env.production is currently tracked" not in readme
        assert "rotation" in readme.lower() and "history" in readme.lower()


class TestExactReleaseTip:
    def test_requested_sha_must_equal_fetched_origin_main_tip(self, deploy_text: str) -> None:
        main = function_body(deploy_text, "main")
        assert "origin_tip" in main
        assert re.search(r'"\$requested_sha"\s*!=\s*"\$origin_tip"', main)
        assert "merge-base --is-ancestor" not in main

    def test_tracked_drift_must_byte_match_target_while_untracked_is_ignored(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
        tracked = repo / "tracked.txt"
        tracked.write_text("old\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "old"], check=True)
        old_sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        tracked.write_text("incoming\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "commit", "-qam", "incoming"], check=True)
        target_sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", old_sha], check=True)
        untracked = repo / "runtime.json"
        untracked.write_text('{"runtime": true}\n', encoding="utf-8")

        def verify(content: str) -> subprocess.CompletedProcess[str]:
            tracked.write_text(content, encoding="utf-8")
            return subprocess.run(
                [
                    "bash",
                    "-c",
                    f"source {DEPLOY!s}; verify_tracked_drift_matches_target {target_sha}",
                ],
                env={**os.environ, "RADON_DIR": str(repo)},
                capture_output=True,
                text=True,
            )

        assert verify("incoming\n").returncode == 0
        assert untracked.read_text(encoding="utf-8") == '{"runtime": true}\n'
        assert verify("operator-only\n").returncode != 0
        assert untracked.read_text(encoding="utf-8") == '{"runtime": true}\n'
