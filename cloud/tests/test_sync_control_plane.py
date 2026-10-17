"""R-429, R-430: a push to main must never need a human bootstrap over SSH.

Two things broke the hands-off deploy on 2026-08-29. A helper edit
(b8541271) made every deploy abort at the control-plane preflight until root
ran bootstrap. Then REL-138's Type=notify drop-ins timed out inside the
container (systemd drops sd_notify from PIDs outside the unit cgroup), the
relay drop-in was hot-patched to Type=simple, and the installed file no
longer matched the manifest, aborting every deploy again.

The fix has one privileged verb, `radon-deploy-root sync-control-plane`: root
extracts `cloud/` at the GitHub main tip (same trust anchor as install-units)
and runs that tip's own bootstrap, which validates, installs atomically and
rewrites the manifest. The deploy job calls it before deploy.sh owns the
deploy lock. The notify half lives in test_app_runtime.py.
"""

from __future__ import annotations

import os
import pathlib
import re
import shlex
import subprocess
import sys

import yaml
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from test_refresh_control_plane import API_UNIT, _run_preflight  # noqa: E402

CLOUD_ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO_ROOT = CLOUD_ROOT.parent
ROOT_HELPER = CLOUD_ROOT / "scripts" / "deploy-root-helper.sh"
DEPLOY = CLOUD_ROOT / "scripts" / "deploy.sh"
SYNC = CLOUD_ROOT / "scripts" / "sync-control-plane.sh"
SUDOERS = CLOUD_ROOT / "config" / "sudoers.d" / "radon-deploy"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def function_body(script: str, name: str) -> str:
    match = re.search(
        rf"^{name}\(\)\s*\{{\s*\n(.+?)\n\}}\s*$",
        script,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"{name}() missing"
    return match.group(1)


def _write_executable(path: pathlib.Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True, stderr=subprocess.STDOUT
    ).strip()


def _commit_all(repo: pathlib.Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=cp@test", "-c", "user.name=cp", "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _init_release_repo(tmp_path: pathlib.Path, *, with_bootstrap: bool = True) -> tuple[pathlib.Path, str]:
    repo = tmp_path / "release"
    scripts = repo / "cloud" / "scripts"
    services = repo / "cloud" / "services" / "radon-relay.service.d"
    scripts.mkdir(parents=True)
    services.mkdir(parents=True)
    if with_bootstrap:
        (scripts / "bootstrap-control-plane.sh").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    (scripts / "deploy-root-helper.sh").write_text("#!/bin/bash\n# tip helper\n", encoding="utf-8")
    for name in ("operator-radon.sh", "ib-gateway-control.sh", "radon-app-runtime.sh"):
        (scripts / name).write_text("#!/bin/bash\n# trusted helper\n", encoding="utf-8")
    (services / "runtime-container.conf").write_text("[Service]\nType=notify\n", encoding="utf-8")
    for name in ("ib_operator_hold.py",):
        dependency = repo / "scripts" / "utils" / name
        dependency.parent.mkdir(parents=True, exist_ok=True)
        dependency.write_text("# trusted utility\n", encoding="utf-8")
    (repo / "README.md").write_text("not part of cloud/\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    sha = _commit_all(repo, "release")
    return repo, sha


def _helper_env(
    tmp_path: pathlib.Path,
    repo: pathlib.Path,
    *,
    remote: pathlib.Path | None = None,
    bootstrap_exit: int = 0,
) -> tuple[dict[str, str], pathlib.Path]:
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    fake_systemctl = tmp_path / "systemctl"
    _write_executable(fake_systemctl, "#!/bin/bash\nexit 0\n")
    fake_sync = tmp_path / "sync"
    _write_executable(fake_sync, "#!/bin/bash\nexit 0\n")
    fake_rm = tmp_path / "rm"
    _write_executable(fake_rm, '#!/bin/bash\nexec /bin/rm "$@"\n')
    bootstrap_log = tmp_path / "bootstrap.log"
    fake_bootstrap_runner = tmp_path / "bootstrap-runner"
    _write_executable(
        fake_bootstrap_runner,
        f"""#!/bin/bash
printf 'root=%s script=%s\\n' "${{RADON_BOOTSTRAP_CLOUD_ROOT:-}}" "$1" >> {shlex.quote(str(bootstrap_log))}
( cd "$RADON_BOOTSTRAP_CLOUD_ROOT" && find . -type f | sort ) >> {shlex.quote(str(bootstrap_log))}
exit {bootstrap_exit}
""",
    )
    env = {
        **os.environ,
        "RADON_DEPLOY_HELPER_TEST_MODE": "1",
        "RADON_TEST_SYSTEMCTL": str(fake_systemctl),
        "RADON_TEST_RM": str(fake_rm),
        "RADON_TEST_SYNC": str(fake_sync),
        "RADON_TEST_ACTIVE_STATE_FILE": str(state / "active-units"),
        "RADON_TEST_REPLICA_PREFIX": str(tmp_path / "replica.db"),
        "RADON_TEST_GIT_DIR": str(repo / ".git"),
        "RADON_TEST_UNIT_REMOTE": str(remote if remote is not None else repo),
        "RADON_TEST_BOOTSTRAP_RUNNER": str(fake_bootstrap_runner),
    }
    return env, bootstrap_log


def _run_sync(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ROOT_HELPER), "sync-control-plane"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestHelperVerb:
    def test_installs_the_main_tip_bundle_through_its_own_bootstrap(self, tmp_path):
        repo, sha = _init_release_repo(tmp_path)
        env, bootstrap_log = _helper_env(tmp_path, repo)
        result = _run_sync(env)
        assert result.returncode == 0, result.stdout + result.stderr
        assert sha in result.stdout
        log = bootstrap_log.read_text(encoding="utf-8")
        store = tmp_path / "state" / "provision"
        root_line = log.splitlines()[0]
        assert root_line.startswith(f"root={store}/control-plane-sync.")
        assert root_line.endswith("/cloud/scripts/bootstrap-control-plane.sh")
        # The extraction is cloud/ at the tip commit, nothing else.
        assert "./scripts/deploy-root-helper.sh" in log
        assert "./services/radon-relay.service.d/runtime-container.conf" in log
        assert "README.md" not in log
        # Root-owned staging tree is gone once bootstrap returns.
        assert not list(store.glob("control-plane-sync.*"))

    def test_root_fetches_a_tip_the_checkout_store_has_not(self, tmp_path):
        # Root reads its own clone of the pinned remote, so the radon-owned
        # checkout store neither gates nor supplies the bundle.
        repo, _ = _init_release_repo(tmp_path)
        remote = tmp_path / "remote"
        subprocess.run(["git", "clone", "-q", str(repo), str(remote)], check=True)
        (remote / "cloud" / "scripts" / "deploy-root-helper.sh").write_text("#!/bin/bash\n# newer\n", encoding="utf-8")
        tip = _commit_all(remote, "newer tip not fetched locally")
        env, bootstrap_log = _helper_env(tmp_path, repo, remote=remote)
        result = _run_sync(env)
        assert result.returncode == 0, result.stdout + result.stderr
        assert tip in result.stdout
        assert bootstrap_log.exists()

    def test_tip_read_ignores_the_callers_repository_config(self, tmp_path):
        # sudo keeps the caller's cwd, and radon owns every repo it can cd
        # into. The tip read must come from the pinned remote whatever that
        # repo's config says.
        repo, sha = _init_release_repo(tmp_path)
        other = tmp_path / "other"
        subprocess.run(["git", "clone", "-q", str(repo), str(other)], check=True)
        (other / "README.md").write_text("other\n", encoding="utf-8")
        _commit_all(other, "other main")
        caller = tmp_path / "caller"
        caller.mkdir()
        _git(caller, "init", "-q")
        _git(caller, "config", f"url.{other}.insteadOf", str(repo))
        env, _ = _helper_env(tmp_path, repo)
        result = subprocess.run(
            ["bash", str(ROOT_HELPER), "sync-control-plane"],
            env=env,
            cwd=caller,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert sha in result.stdout

    def test_refuses_a_tip_without_a_bootstrap(self, tmp_path):
        repo, _ = _init_release_repo(tmp_path, with_bootstrap=False)
        env, bootstrap_log = _helper_env(tmp_path, repo)
        result = _run_sync(env)
        assert result.returncode == 66, result.stdout + result.stderr
        assert "no control-plane bootstrap" in result.stderr
        assert not bootstrap_log.exists()
        assert not list((tmp_path / "state" / "provision").glob("control-plane-sync.*"))

    def test_propagates_bootstrap_lock_refusal_and_cleans_up(self, tmp_path):
        repo, _ = _init_release_repo(tmp_path)
        env, bootstrap_log = _helper_env(tmp_path, repo, bootstrap_exit=75)
        result = _run_sync(env)
        assert result.returncode == 75, result.stdout + result.stderr
        assert bootstrap_log.exists()
        assert not list((tmp_path / "state" / "provision").glob("control-plane-sync.*"))

    def test_records_the_installed_tip_only_after_bootstrap_succeeds(self, tmp_path):
        repo, sha = _init_release_repo(tmp_path)
        floor = tmp_path / "state" / "provision" / "control-plane-floor"
        env, _ = _helper_env(tmp_path, repo, bootstrap_exit=1)
        assert _run_sync(env).returncode == 1
        assert not floor.exists()
        env, _ = _helper_env(tmp_path, repo)
        result = _run_sync(env)
        assert result.returncode == 0, result.stdout + result.stderr
        assert floor.read_text(encoding="utf-8") == f"{sha}\n"


class TestContracts:
    def test_sudoers_and_helper_pin_the_verb(self):
        helper = ROOT_HELPER.read_text(encoding="utf-8")
        sudoers = SUDOERS.read_text(encoding="utf-8")
        assert "/usr/local/sbin/radon-deploy-root sync-control-plane" in sudoers
        # Still no way for radon to hand root an arbitrary tree: the verb takes
        # no argument and reads through the GitHub main tip only.
        assert "sync-control-plane *" not in sudoers
        assert "sync-control-plane-privileged" not in sudoers
        body = function_body(helper, "sync_control_plane")
        assert "resolve_fetched_main_tip" in body
        assert 'archive --format=tar "$tip" cloud' in body
        assert "RADON_BOOTSTRAP_CLOUD_ROOT=" in body
        assert 'mktemp -d "${PROVISION_ROOT}/control-plane-sync.' in body
        tip = function_body(helper, "resolve_fetched_main_tip")
        assert 'remote_sha="$(remote_main_sha)"' in tip
        assert "ls-remote --refs" in function_body(helper, "remote_main_sha")
        assert "github_origin_is_allowed" in tip
        assert 'provision_store_has_tip "$remote_sha"' in tip

    def test_the_tip_is_read_over_git_protocol_v1(self):
        """2026-09-02: from the VPS, an unauthenticated protocol-v2 `ls-remote`
        against the public repo got HTTP 401 on the `ls-refs` POST ("could not
        read Username for 'https://github.com'"), so the first real
        sync-control-plane run failed every deploy with exit 69. Protocol v1
        reads the refs from the `info/refs` GET, which answers 200."""
        helper = ROOT_HELPER.read_text(encoding="utf-8")
        for fn in ("resolve_fetched_main_tip", "resolve_trusted_main_tip"):
            assert 'remote_sha="$(remote_main_sha)"' in function_body(helper, fn), fn
        assert helper.count("ls-remote --refs") == 1
        body = function_body(helper, "remote_main_sha")
        assert "-C /" in body and "GIT_CONFIG_GLOBAL=/dev/null" in body
        assert '-c protocol.version=1 ls-remote --refs "$UNIT_REMOTE" refs/heads/main' in body

    def test_verb_has_its_own_deadline_and_never_cancels_radon_jobs(self):
        helper = ROOT_HELPER.read_text(encoding="utf-8")
        assert "readonly ROOT_SYNC_ACTION_TIMEOUT=300" in helper
        selector = function_body(helper, "root_action_timeout")
        assert "sync-control-plane)" in selector
        assert "ROOT_SYNC_ACTION_TIMEOUT" in selector
        assert "sync-control-plane" not in function_body(helper, "action_queues_radon_jobs")

    def test_deploy_job_recovers_then_syncs_then_deploys_and_prestage_does_neither(self):
        ci = CI.read_text(encoding="utf-8")
        deploy_job = ci.split("\n  deploy:\n", 1)[1]
        stage_job = ci.split("\n  stage-release:\n", 1)[1].split("\n  deploy:\n", 1)[0]
        assert "sync-control-plane.sh" not in stage_job
        assert "RADON_DEPLOY_RECOVER_ONLY" not in stage_job
        recover_at = deploy_job.index('RADON_DEPLOY_RECOVER_ONLY=1 bash "$RUNNER/cloud/scripts/deploy.sh" "$SHA"')
        sync_at = deploy_job.index('sync-control-plane.sh')
        deploy_at = deploy_job.index('\n            bash "$RUNNER/cloud/scripts/deploy.sh" "$SHA"')
        assert recover_at < sync_at < deploy_at
        # A runner predating the flag would run a full deploy under it.
        assert "grep -q 'RADON_DEPLOY_RECOVER_ONLY' \"$RUNNER/cloud/scripts/deploy.sh\"" in deploy_job
        # Runs from the immutable runner of the tested SHA, tolerant of a
        # release that predates the script.
        assert 'if [[ -f "$RUNNER/cloud/scripts/sync-control-plane.sh" ]]; then' in deploy_job

    def _supervisor(self, tmp_path, *, journal: bool, recover_only: str, recovery_rc: int = 0):
        journal_file = tmp_path / "transition.json"
        if journal:
            journal_file.write_text("{}", encoding="utf-8")
        shell = f"""
set -euo pipefail
source {DEPLOY}
# macOS test hosts have neither util-linux flock nor GNU timeout.
flock() {{ return 0; }}
timeout() {{ shift 3; "$@"; }}
recover_pending_transition() {{ printf recovered > {tmp_path / 'recovered'}; return {recovery_rc}; }}
supervise_deploy_command bash -c 'printf deployed > {tmp_path / "deployed"}'
"""
        return subprocess.run(
            ["bash", "-c", shell],
            env={
                **os.environ,
                "RADON_DEPLOY_LOCK_FILE": str(tmp_path / "deploy.lock"),
                "RADON_DEPLOY_TRANSITION_JOURNAL": str(journal_file),
                "RADON_DEPLOY_RECOVER_ONLY": recover_only,
                "DEPLOY_TIMEOUT": "30",
                "DEPLOY_KILL_AFTER": "5",
            },
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_recover_only_pass_recovers_a_pending_journal_and_deploys_nothing(self, tmp_path):
        result = self._supervisor(tmp_path, journal=True, recover_only="1")
        assert result.returncode == 0, result.stdout + result.stderr
        assert (tmp_path / "recovered").exists()
        assert not (tmp_path / "deployed").exists()
        assert "Recovery pass complete" in result.stdout + result.stderr

    def test_recover_only_pass_is_a_noop_without_a_journal(self, tmp_path):
        result = self._supervisor(tmp_path, journal=False, recover_only="1")
        assert result.returncode == 0, result.stdout + result.stderr
        assert not (tmp_path / "recovered").exists()
        assert not (tmp_path / "deployed").exists()

    def test_recover_only_pass_still_refuses_when_recovery_fails(self, tmp_path):
        result = self._supervisor(tmp_path, journal=True, recover_only="1", recovery_rc=1)
        assert result.returncode == 76
        assert not (tmp_path / "deployed").exists()

    def test_without_the_flag_the_supervisor_recovers_then_deploys(self, tmp_path):
        result = self._supervisor(tmp_path, journal=True, recover_only="0")
        assert result.returncode == 0, result.stdout + result.stderr
        assert (tmp_path / "recovered").exists()
        assert (tmp_path / "deployed").exists()

    def _post_deploy_prune(self, tmp_path, *, deploy_rc: int, granted: bool = True, stage: str = "0"):
        log = tmp_path / "sudo.log"
        lock = tmp_path / "deploy.lock"
        probe = (
            "import fcntl,sys; f=open(sys.argv[1],'r+'); "
            "fcntl.flock(f, fcntl.LOCK_EX|fcntl.LOCK_NB); print('free')"
        )
        shell = f"""
set -euo pipefail
source {DEPLOY}
flock() {{ return 0; }}
timeout() {{ if [[ "$1" == --signal=* ]]; then shift 3; else shift; fi; "$@"; }}
sudo() {{
  printf '%s\\n' "$*" >> {log}
  if [[ "$1" == "-n" && "$2" == "-l" ]]; then return {0 if granted else 1}; fi
  # The pruner must find the deploy lock free (FD 9 closed).
  if [[ "$*" == *" prune" ]]; then
    {sys.executable} -c {shlex.quote(probe)} {lock} >> {log} 2>&1 || echo held >> {log}
  fi
  return 0
}}
supervise_deploy_command bash -c 'exit {deploy_rc}'
"""
        result = subprocess.run(
            ["bash", "-c", shell],
            env={**os.environ, "RADON_DEPLOY_LOCK_FILE": str(lock),
                 "RADON_DEPLOY_TRANSITION_JOURNAL": str(tmp_path / "none.json"),
                 "RADON_DEPLOY_STAGE": stage,
                 "DEPLOY_TIMEOUT": "30", "DEPLOY_KILL_AFTER": "5"},
            capture_output=True, text=True, timeout=30,
        )
        return result, (log.read_text(encoding="utf-8").splitlines() if log.exists() else [])

    def test_green_deploy_prunes_app_images_after_releasing_the_deploy_lock(self, tmp_path):
        import fcntl
        result, lines = self._post_deploy_prune(tmp_path, deploy_rc=0)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "-n /usr/local/sbin/radon-app-runtime prune" in lines
        assert "free" in lines and "held" not in lines
        # The real supervisor holds an flock on FD 9 while the body runs; the
        # prune call sits after `exec 9>&-` in the same function.
        body = function_body(DEPLOY.read_text(encoding="utf-8"), "supervise_deploy_command")
        assert body.rindex("exec 9>&-") < body.index("prune_app_images_after_deploy")
        del fcntl

    def test_failed_deploy_does_not_prune(self, tmp_path):
        result, lines = self._post_deploy_prune(tmp_path, deploy_rc=3)
        assert result.returncode == 3
        assert not any("prune" in line for line in lines)

    def test_green_prestage_does_not_prune(self, tmp_path):
        """2026-10-11, CI runs for 964a58a1 and 4262b4c8: prestage and the
        unlocked prepull job run in parallel. Prestage exited 0, its supervisor
        pruned every SHA tag outside the green marker, and that removed the
        target pair prepull had just pulled ("exact release image pair is
        incomplete after pull"). Only a deploy moves the green marker, so only
        a deploy prunes."""
        result, lines = self._post_deploy_prune(tmp_path, deploy_rc=0, stage="1")
        assert result.returncode == 0, result.stdout + result.stderr
        assert not any("prune" in line for line in lines)

    def test_ungranted_prune_is_skipped_without_failing_the_deploy(self, tmp_path):
        result, lines = self._post_deploy_prune(tmp_path, deploy_rc=0, granted=False)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "-n /usr/local/sbin/radon-app-runtime prune" not in lines
        assert "not granted" in result.stdout + result.stderr

    def _green(self, tmp_path, existing: str | None, sha: str) -> str:
        marker = tmp_path / "green"
        if existing is not None:
            marker.write_text(existing, encoding="utf-8")
        shell = f"set -euo pipefail\nsource {DEPLOY}\nwrite_green_marker {sha}\n"
        result = subprocess.run(["bash", "-c", shell], env={**os.environ, "RADON_DEPLOY_GREEN_MARKER": str(marker)},
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        return marker.read_text(encoding="utf-8")

    def test_green_marker_records_the_release_it_replaced(self, tmp_path):
        a, b, c = "a" * 40, "b" * 40, "c" * 40
        assert self._green(tmp_path, None, a) == f"{a}\n"
        assert self._green(tmp_path, f"{a}\n", b) == f"{b}\n{a}\n"
        assert self._green(tmp_path, f"{b}\n{a}\n", c) == f"{c}\n{b}\n"
        # Redeploying the green SHA keeps its rollback pair.
        assert self._green(tmp_path, f"{c}\n{b}\n", c) == f"{c}\n{b}\n"
        assert self._green(tmp_path, "garbage\n", a) == f"{a}\n"

    def _prepull(self, tmp_path, *, granted: bool, pull_rc: int = 0):
        log = tmp_path / "sudo.log"
        shell = f"""
set -euo pipefail
source {DEPLOY}
sudo() {{
  printf '%s\\n' "$*" >> {log}
  if [[ "$1" == "-n" && "$2" == "-l" ]]; then return {0 if granted else 1}; fi
  return {pull_rc}
}}
timeout() {{ shift; "$@"; }}
prepull_app_images {'f' * 40}
"""
        result = subprocess.run(["bash", "-c", shell], env={**os.environ}, capture_output=True, text=True, timeout=30)
        return result, log

    def test_deploy_prepulls_the_release_image_pair_before_restart(self):
        deploy = DEPLOY.read_text(encoding="utf-8")
        main = function_body(deploy, "main")
        assert main.index("build_staged_release") < main.index('prepull_app_images "$requested_sha"') < main.index("restart_services")

    def test_prepull_runs_the_exact_sudo_verb_and_fails_before_teardown(self, tmp_path):
        result, log = self._prepull(tmp_path, granted=True, pull_rc=1)
        assert result.returncode != 0, result.stdout + result.stderr
        assert "prepull failed" in result.stdout + result.stderr
        assert f"-n /usr/local/sbin/radon-app-runtime pull {'f' * 40}" in log.read_text(encoding="utf-8").splitlines()

    def test_prepull_fails_before_teardown_when_the_verb_is_not_granted(self, tmp_path):
        result, log = self._prepull(tmp_path, granted=False)
        assert result.returncode != 0
        assert "not granted" in result.stdout + result.stderr
        assert f"-n /usr/local/sbin/radon-app-runtime pull {'f' * 40}" not in log.read_text(encoding="utf-8")

    def test_prestage_skips_instead_of_failing_when_control_plane_is_not_ready(self, tmp_path):
        shell = f"""
set -euo pipefail
source {DEPLOY}
preflight_control_plane() {{ return 1; }}
preflight_env() {{ printf reached > {tmp_path / 'env-preflight'}; return 0; }}
main {'0' * 40}
"""
        result = subprocess.run(
            ["bash", "-c", shell],
            env={**os.environ, "RADON_DEPLOY_STAGE": "1"},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "skipping prestage" in result.stdout + result.stderr
        assert not (tmp_path / "env-preflight").exists()

    def test_full_deploy_still_aborts_when_control_plane_is_not_ready(self, tmp_path):
        shell = f"""
set -euo pipefail
source {DEPLOY}
preflight_control_plane() {{ return 1; }}
preflight_env() {{ printf reached > {tmp_path / 'env-preflight'}; return 0; }}
main {'0' * 40}
"""
        result = subprocess.run(
            ["bash", "-c", shell],
            env={**os.environ, "RADON_DEPLOY_STAGE": "0"},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "Aborting deploy" in result.stdout + result.stderr
        assert not (tmp_path / "env-preflight").exists()


def _fake_sudo(tmp_path: pathlib.Path, *, granted: bool, exits: list[int]) -> tuple[pathlib.Path, pathlib.Path]:
    log = tmp_path / "sudo.log"
    counter = tmp_path / "sudo.count"
    counter.write_text("0", encoding="utf-8")
    exits_literal = " ".join(str(code) for code in exits)
    sudo = tmp_path / "sudo"
    _write_executable(
        sudo,
        f"""#!/bin/bash
printf '%s\\n' "$*" >> {shlex.quote(str(log))}
if [[ "$1" == "-n" && "$2" == "-l" ]]; then
  exit {0 if granted else 1}
fi
n="$(cat {shlex.quote(str(counter))})"
codes=({exits_literal})
printf '%s' "$((n + 1))" > {shlex.quote(str(counter))}
exit "${{codes[$n]:-0}}"
""",
    )
    return sudo, log


def _rejected(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "control-plane-rejected"


def _run_sync_script(tmp_path: pathlib.Path, sudo: pathlib.Path) -> subprocess.CompletedProcess[str]:
    fake_sleep = tmp_path / "sleep"
    _write_executable(fake_sleep, f"#!/bin/bash\nprintf 'sleep %s\\n' \"$1\" >> {shlex.quote(str(tmp_path / 'sudo.log'))}\n")
    return subprocess.run(
        ["bash", str(SYNC)],
        env={
            **os.environ,
            "RADON_SYNC_SUDO": str(sudo),
            "RADON_SYNC_SLEEP": str(fake_sleep),
            "RADON_SYNC_RETRIES": "3",
            "RADON_SYNC_RETRY_WAIT": "7",
            "RADON_CONTROL_PLANE_REJECTED": str(_rejected(tmp_path)),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestRadonSideScript:
    HELPER = "/usr/local/sbin/radon-deploy-root"

    def test_skips_when_the_verb_is_not_granted_yet(self, tmp_path):
        sudo, log = _fake_sudo(tmp_path, granted=False, exits=[0])
        result = _run_sync_script(tmp_path, sudo)
        assert result.returncode == 0
        assert "not granted yet" in result.stderr
        assert f"-n {self.HELPER} sync-control-plane" not in log.read_text(encoding="utf-8")

    def test_runs_the_exact_sudo_verb_once_when_granted(self, tmp_path):
        sudo, log = _fake_sudo(tmp_path, granted=True, exits=[0])
        result = _run_sync_script(tmp_path, sudo)
        assert result.returncode == 0, result.stderr
        lines = log.read_text(encoding="utf-8").splitlines()
        assert lines == [
            f"-n -l -- {self.HELPER} sync-control-plane",
            f"-n {self.HELPER} sync-control-plane",
        ]
        assert "matches the GitHub main tip" in result.stdout

    def test_retries_only_the_lock_held_exit_then_hands_off_to_preflight(self, tmp_path):
        sudo, log = _fake_sudo(tmp_path, granted=True, exits=[75, 75, 0])
        result = _run_sync_script(tmp_path, sudo)
        assert result.returncode == 0, result.stderr
        text = log.read_text(encoding="utf-8")
        assert text.count(f"-n {self.HELPER} sync-control-plane") == 3
        assert text.count("sleep 7") == 2

    def test_any_other_failure_is_propagated_without_retry(self, tmp_path):
        # Used to exit 0 and "defer to deploy.sh preflight", whose differs arm
        # then reinstalled the bundle bootstrap had just REJECTED, with the app
        # tier stopped, through a refresh path with fewer validators. R-437.
        sudo, log = _fake_sudo(tmp_path, granted=True, exits=[66])
        result = _run_sync_script(tmp_path, sudo)
        assert result.returncode == 66
        assert "exit 66" in result.stderr
        text = log.read_text(encoding="utf-8")
        assert text.count(f"-n {self.HELPER} sync-control-plane") == 1
        assert "sleep" not in text
        assert _rejected(tmp_path).read_text(encoding="utf-8").strip() == "66"

    def test_bootstrap_rejection_and_root_deadline_are_hard_failures(self, tmp_path):
        # 1: bootstrap `die` (bash -n, visudo, node --check, systemd-analyze).
        # 124: the helper TERM/KILLed an overrunning bootstrap (R-440).
        for code in (1, 124):
            sub = tmp_path / f"exit-{code}"
            sub.mkdir()
            sudo, log = _fake_sudo(sub, granted=True, exits=[code])
            result = _run_sync_script(sub, sudo)
            assert result.returncode == code, result.stderr
            assert f"exit {code}" in result.stderr
            assert log.read_text(encoding="utf-8").count(f"-n {self.HELPER} sync-control-plane") == 1
            assert _rejected(sub).read_text(encoding="utf-8").strip() == str(code)

    def test_success_clears_a_previous_rejection(self, tmp_path):
        _rejected(tmp_path).write_text("66\n", encoding="utf-8")
        sudo, _ = _fake_sudo(tmp_path, granted=True, exits=[0])
        result = _run_sync_script(tmp_path, sudo)
        assert result.returncode == 0, result.stderr
        assert not _rejected(tmp_path).exists()

    def test_exhausted_lock_retries_still_hand_off_and_keep_a_previous_rejection(self, tmp_path):
        # The 75 path is unchanged: deploy.sh takes the lock itself and recovers
        # a pending transition. A rejection recorded earlier is not cleared by
        # a sync that never ran bootstrap.
        _rejected(tmp_path).write_text("1\n", encoding="utf-8")
        sudo, log = _fake_sudo(tmp_path, granted=True, exits=[75, 75, 75])
        result = _run_sync_script(tmp_path, sudo)
        assert result.returncode == 0, result.stderr
        assert "still blocked" in result.stderr
        assert log.read_text(encoding="utf-8").count(f"-n {self.HELPER} sync-control-plane") == 3
        assert _rejected(tmp_path).read_text(encoding="utf-8").strip() == "1"

    def test_not_granted_writes_no_rejection(self, tmp_path):
        sudo, _ = _fake_sudo(tmp_path, granted=False, exits=[0])
        result = _run_sync_script(tmp_path, sudo)
        assert result.returncode == 0
        assert not _rejected(tmp_path).exists()


class TestRejectedBundleNeverReachesTeardown:
    """R-437: a bundle bootstrap rejected must abort deploy.sh's preflight, not
    be re-applied "after promote" by refresh-control-plane(-privileged) while
    stop_services_for_transition has the app tier down."""

    def test_rejected_bundle_aborts_preflight_before_any_teardown(self, tmp_path):
        sudo, _ = _fake_sudo(tmp_path, granted=True, exits=[1])
        sync = _run_sync_script(tmp_path, sudo)
        assert sync.returncode == 1, sync.stderr
        rejected = _rejected(tmp_path)
        assert rejected.is_file()

        result = _run_preflight(
            tmp_path, API_UNIT, extra_env={"RADON_CONTROL_PLANE_REJECTED": str(rejected)}
        )
        combined = result.stdout + result.stderr
        assert result.returncode != 0, combined
        assert "rejected" in combined
        assert API_UNIT in combined
        assert "will apply it after promote" not in combined

        deploy = DEPLOY.read_text(encoding="utf-8")
        main = function_body(deploy, "main")
        assert main.index("preflight_control_plane") < main.index("restart_services")
        assert "stop_services_for_transition" not in main
        assert "stop_services_for_transition" in function_body(deploy, "restart_services")

    def test_without_a_rejection_the_services_arm_still_defers_to_refresh(self, tmp_path):
        result = _run_preflight(
            tmp_path, API_UNIT, extra_env={"RADON_CONTROL_PLANE_REJECTED": str(tmp_path / "absent")}
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_deploy_job_does_not_tolerate_a_sync_failure(self):
        ci = CI.read_text(encoding="utf-8")
        deploy_job = ci.split("\n  deploy:\n", 1)[1]
        assert "set -euo pipefail" in deploy_job.split("sync-control-plane.sh", 1)[0]
        sync_lines = [
            line.strip() for line in deploy_job.splitlines()
            if line.strip().startswith("bash ") and "sync-control-plane.sh" in line
        ]
        assert sync_lines == ['bash "$RUNNER/cloud/scripts/sync-control-plane.sh"']


class TestLegacyRunnerRefusal:
    """R-440: a KILLed bootstrap (helper deadline, no trap runs) leaves the
    bundle half-installed with readiness withdrawn. The next deploy job must
    not route a host whose app units already carry the container drop-ins to
    the legacy runner, which knows nothing about them."""

    def test_deploy_job_refuses_the_legacy_runner_on_a_bootstrapped_host(self):
        workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
        script = next(
            step["with"]["script"]
            for step in workflow["jobs"]["deploy"]["steps"]
            if "ssh-action" in str(step.get("uses", ""))
        )
        marker_check = script.index(
            'if [[ ! -e "$RADON_CONTROL_PLANE_READY" && ! -L "$RADON_CONTROL_PLANE_READY" ]]; then'
        )
        legacy_call = script.index("deploy_with_legacy_runner\n", marker_check)
        guard = script[marker_check:legacy_call]
        assert "compgen -G '/etc/systemd/system/radon-*.service.d/runtime-container.conf'" in guard
        assert "exit 78" in guard
        assert "bootstrap-control-plane.sh" in guard


class TestDatabaseBrownoutNeverReachesTeardown:
    """2026-09-25 P1: a deploy stopped the serving release during a Turso
    brownout. The restarted API could not boot, the deploy gate AND the
    rollback gate failed, and production stayed down until Turso answered.
    Teardown must wait on a bounded database probe, like the image prepull."""

    def _preflight(self, tmp_path, *, probe_exits: list[int]):
        calls = tmp_path / "probe.log"
        exits = tmp_path / "exits"
        exits.write_text("\n".join(str(code) for code in probe_exits) + "\n")
        shell = f"""
set -euo pipefail
source {DEPLOY}
run_with_cloud_env() {{
  printf '%s\\n' "$*" >> {calls}
  local code
  code="$(head -n1 {exits})"
  sed -i.bak 1d {exits}
  return "$code"
}}
sleep() {{ return 0; }}
preflight_database
"""
        result = subprocess.run(["bash", "-c", shell], env={**os.environ}, capture_output=True, text=True, timeout=30)
        return result, calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []

    def test_main_probes_the_database_after_prepull_and_before_restart(self):
        main = function_body(DEPLOY.read_text(encoding="utf-8"), "main")
        assert main.index('prepull_app_images "$requested_sha"') < main.index("preflight_database") < main.index("restart_services")

    def test_an_unanswering_database_refuses_teardown_after_bounded_retries(self, tmp_path):
        result, calls = self._preflight(tmp_path, probe_exits=[124, 124, 124])
        combined = result.stdout + result.stderr
        assert result.returncode != 0, combined
        assert "refusing teardown" in combined
        assert len(calls) == 3

    def test_a_recovered_database_lets_the_deploy_proceed(self, tmp_path):
        result, calls = self._preflight(tmp_path, probe_exits=[124, 0])
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(calls) == 2

    def test_the_probe_is_a_bounded_read_from_the_current_release(self, tmp_path):
        _, calls = self._preflight(tmp_path, probe_exits=[0])
        assert "timeout" in calls[0]
        assert "SELECT 1" in calls[0]


def test_sync_refuses_unisolated_candidate_before_running_its_bootstrap(tmp_path):
    repo, _ = _init_release_repo(tmp_path)
    candidate = repo / "cloud/scripts/bootstrap-control-plane.sh"
    candidate.write_text('#!/bin/bash\n"$PYTHON_BIN" -c \'import ast\'\n')
    _commit_all(repo, "candidate without interpreter isolation")
    env, bootstrap_log = _helper_env(tmp_path, repo)
    result = _run_sync(env)
    assert result.returncode == 76, result.stdout + result.stderr
    assert not bootstrap_log.exists()
    assert "interpreter isolation" in result.stderr


def test_expected_sha_sync_refuses_stale_commit_before_bootstrap(tmp_path):
    repo, _ = _init_release_repo(tmp_path)
    env, log = _helper_env(tmp_path, repo)
    result = subprocess.run(["bash", str(ROOT_HELPER), "sync-control-plane", "a" * 40],
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 76, result.stdout + result.stderr
    assert not log.exists()


def test_expected_sha_sync_prints_an_exact_install_receipt(tmp_path):
    repo, sha = _init_release_repo(tmp_path)
    env, log = _helper_env(tmp_path, repo)
    result = subprocess.run(["bash", str(ROOT_HELPER), "sync-control-plane", sha],
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert log.exists()
    assert f"installed_sha={sha} host_role=combined" in result.stdout


def _advanceable_main(tmp_path):
    repo, expected = _init_release_repo(tmp_path)
    (repo / 'README.md').write_text('concurrent main update\n')
    advanced = _commit_all(repo, 'advance main during expected sync')
    _git(repo, 'update-ref', 'refs/heads/main', expected)
    return repo, expected, advanced


def test_expected_sha_sync_resolves_role_before_final_tip_admission(tmp_path):
    repo, expected, advanced = _advanceable_main(tmp_path)
    env, log = _helper_env(tmp_path, repo)
    # Resolving the host role may block on local configuration. Main can
    # advance during that work, before the candidate bootstrap is admitted.
    role = f'''read_host_role() {{
  git -C {shlex.quote(str(repo))} update-ref refs/heads/main {advanced}
  printf 'combined\\n'
}}'''
    script = re.sub(r'^read_host_role\(\) \{.*?^\}', lambda _: role,
                    ROOT_HELPER.read_text(), count=1, flags=re.MULTILINE | re.DOTALL)
    helper = tmp_path / 'role-race-helper'
    _write_executable(helper, script)
    result = subprocess.run(['bash', str(helper), 'sync-control-plane', expected],
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 76, result.stdout + result.stderr
    assert not log.exists()
    assert 'installed_sha=' not in result.stdout


def test_expected_sha_sync_never_receipts_main_that_moves_during_bootstrap(tmp_path):
    repo, expected, advanced = _advanceable_main(tmp_path)
    env, log = _helper_env(tmp_path, repo)
    runner = pathlib.Path(env['RADON_TEST_BOOTSTRAP_RUNNER'])
    _write_executable(runner, f'''#!/bin/bash
printf 'bootstrap ran\\n' >> {shlex.quote(str(log))}
git -C {shlex.quote(str(repo))} update-ref refs/heads/main {advanced}
exit 0
''')
    result = subprocess.run(['bash', str(ROOT_HELPER), 'sync-control-plane', expected],
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 76, result.stdout + result.stderr
    assert log.exists()
    assert 'installed_sha=' not in result.stdout


@pytest.mark.parametrize("invocation", [
    '"$python_bin" -c', '${python_bin} -c', '"${python_bin:-/usr/bin/python3}" -c',
    '/usr/bin/python3 -c', 'python3.13 -u -c', '"$PYTHON_BIN" \\\n-c',
])
def test_sync_rejects_alternate_unisolated_python_invocations(tmp_path, invocation):
    repo, _ = _init_release_repo(tmp_path)
    (repo / "cloud/scripts/bootstrap-control-plane.sh").write_text(
        "#!/bin/bash\n" + invocation + " 'import ast'\n")
    _commit_all(repo, "unisolated interpreter spelling")
    env, log = _helper_env(tmp_path, repo)
    result = _run_sync(env)
    assert result.returncode == 76, result.stdout + result.stderr
    assert not log.exists()


@pytest.mark.parametrize("name", [
    "deploy-root-helper.sh", "operator-radon.sh", "ib-gateway-control.sh", "radon-app-runtime.sh",
])
def test_sync_requires_every_isolation_checked_script(tmp_path, name):
    repo, _ = _init_release_repo(tmp_path)
    path = repo / "cloud/scripts" / name
    if path.exists():
        path.unlink()
    _commit_all(repo, "missing required helper")
    env, log = _helper_env(tmp_path, repo)
    result = _run_sync(env)
    assert result.returncode == 76, result.stdout + result.stderr
    assert not log.exists()
