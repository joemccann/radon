"""Regression contracts for interruption-safe production deploys.

These tests pin the failures observed on 2026-07-08: two canceled SSH deploys
left a partial stack, then the successor mistook Git HEAD equality for a green
release and exited without rebuilding, restarting, or gating.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "deploy.sh"
COMPOSE = ROOT / "docker-compose.yml"
RUN_WITH_ENV = ROOT / "scripts" / "run_with_env.py"
SETUP = ROOT / "scripts" / "setup-vps.sh"
POST_SETUP = ROOT / "scripts" / "post-setup.sh"
CHECK_ENV = ROOT / "scripts" / "check-env.py"


@pytest.fixture(scope="module")
def deploy_text() -> str:
    return DEPLOY.read_text(encoding="utf-8")


def _body(script: str, name: str) -> str:
    match = re.search(
        rf"^{name}\(\)\s*\{{\s*\n(.+?)\n\}}\s*$",
        script,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"{name}() missing"
    return match.group(1)


class TestDeployOwnership:
    def test_nonblocking_cross_process_lock_is_acquired_first(self, deploy_text: str) -> None:
        body = _body(deploy_text, "supervise_deploy_command")
        assert "flock" in body and re.search(r"flock\s+(?:-[^\s]*n|--nonblock)", body)
        assert body.find("flock") < body.find("timeout")
        assert "9>&-" in body

    @pytest.mark.skipif(
        shutil.which("flock") is None or shutil.which("timeout") is None,
        reason="GNU/Linux flock and timeout are not installed",
    )
    def test_second_process_cannot_acquire_held_lock(self, tmp_path: Path) -> None:
        lock = tmp_path / "deploy.lock"
        ready = tmp_path / "holder.ready"
        env = {
            **os.environ,
            "RADON_DEPLOY_LOCK_FILE": str(lock),
            "DEPLOY_TIMEOUT": "30",
        }
        holder = subprocess.Popen(
            [
                "bash",
                "-c",
                f"source {DEPLOY!s}; supervise_deploy_command bash -c 'printf ready > {ready!s}; sleep 30'",
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and holder.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            if not ready.exists():
                holder.terminate()
                stdout, stderr = holder.communicate(timeout=3)
                pytest.fail(
                    "lock holder never entered the supervised command\n"
                    f"returncode={holder.returncode}\nstdout={stdout}\nstderr={stderr}"
                )
            contender = subprocess.run(
                [
                    "bash",
                    "-c",
                    f"source {DEPLOY!s}; if supervise_deploy_command bash -c true; then exit 0; else exit $?; fi",
                ],
                env={**env, "DEPLOY_TIMEOUT": "3"},
                capture_output=True,
                text=True,
                timeout=3,
            )
            assert contender.returncode == 75
        finally:
            holder.terminate()
            holder.wait(timeout=3)

    def test_signal_trap_recovers_stopped_services(self, deploy_text: str) -> None:
        assert re.search(r"trap\s+[^\n]+\bTERM\b[^\n]+\bINT\b", deploy_text)
        body = _body(deploy_text, "recover_services_after_interrupt")
        assert "DEPLOY_ROOT_HELPER" in body
        assert "recover" in body

    def test_signal_handler_starts_every_managed_service(self, tmp_path: Path) -> None:
        calls = tmp_path / "sudo-calls"
        command = f"""
set -euo pipefail
source {DEPLOY!s}
sudo() {{ printf '%s\\n' "$*" >> {calls!s}; }}
DEPLOY_TEARDOWN_STARTED=1
DEPLOY_SERVICES_RECOVERED=0
handle_deploy_signal TERM
"""
        result = subprocess.run(["bash", "-c", command], capture_output=True, text=True)
        assert result.returncode == 143
        invocation = calls.read_text(encoding="utf-8")
        assert invocation.endswith("radon-deploy-root recover\n")

    def test_nonzero_exit_after_teardown_recovers_services_and_preserves_status(
        self, tmp_path: Path
    ) -> None:
        calls = tmp_path / "sudo-calls"
        unremovable_tmp = tmp_path / "web-env-directory"
        unremovable_tmp.mkdir()
        command = f"""
set -euo pipefail
source {DEPLOY!s}
sudo() {{ printf '%s\\n' "$*" >> {calls!s}; return 91; }}
DEPLOY_TEARDOWN_STARTED=1
DEPLOY_SERVICES_RECOVERED=0
WEB_ENV_TMP={unremovable_tmp!s}
trap 'handle_deploy_exit "$?"' EXIT
bash -c 'exit 23'
"""
        result = subprocess.run(["bash", "-c", command], capture_output=True, text=True)

        assert result.returncode == 23
        assert unremovable_tmp.is_dir()
        invocation = calls.read_text(encoding="utf-8")
        assert invocation.endswith("radon-deploy-root recover\n")

    def test_completed_transition_does_not_run_exit_recovery(self, tmp_path: Path) -> None:
        calls = tmp_path / "sudo-calls"
        command = f"""
set -euo pipefail
source {DEPLOY!s}
sudo() {{ printf '%s\\n' "$*" >> {calls!s}; }}
DEPLOY_TEARDOWN_STARTED=0
DEPLOY_SERVICES_RECOVERED=1
trap 'handle_deploy_exit "$?"' EXIT
bash -c 'exit 19'
"""
        result = subprocess.run(["bash", "-c", command], capture_output=True, text=True)

        assert result.returncode == 19
        assert not calls.exists()

    def test_executed_script_has_nonrecursive_global_timeout_wrapper(
        self, deploy_text: str
    ) -> None:
        wrapper = _body(deploy_text, "run_bounded_deploy")
        supervisor = _body(deploy_text, "supervise_deploy_command")
        assert "--foreground" not in supervisor
        assert "--signal=TERM" in supervisor
        assert "--kill-after=" in supervisor
        assert "RADON_DEPLOY_INTERNAL=1" in wrapper or "RADON_DEPLOY_INTERNAL=1" in wrapper.replace("export ", "")
        assert re.search(r'bash\s+"\$0"\s+"\$@"', wrapper)

        execution_guard = deploy_text[deploy_text.rfind("if [[ \"${BASH_SOURCE[0]}\"") :]
        assert "RADON_DEPLOY_INTERNAL" in execution_guard
        assert "run_bounded_deploy" in execution_guard
        assert "run_deploy_internal" in execution_guard

        main = _body(deploy_text, "main")
        assert "elapsed > DEPLOY_TIMEOUT" not in main

    def test_sourcing_does_not_enter_timeout_wrapper(self, tmp_path: Path) -> None:
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        timeout_marker = tmp_path / "timeout-called"
        fake_timeout = fake_bin / "timeout"
        fake_timeout.write_text(
            f"#!/bin/sh\nprintf called > {timeout_marker!s}\nexit 99\n", encoding="utf-8"
        )
        fake_timeout.chmod(0o755)
        fake_flock = fake_bin / "flock"
        fake_flock.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_flock.chmod(0o755)

        result = subprocess.run(
            ["bash", "-c", f"source {DEPLOY!s}; printf sourced"],
            env={**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout == "sourced"
        assert not timeout_marker.exists()

    def test_executed_wrapper_enters_timeout_once_then_runs_inner_deploy(
        self, tmp_path: Path
    ) -> None:
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        timeout_calls = tmp_path / "timeout-calls"
        fake_timeout = fake_bin / "timeout"
        fake_timeout.write_text(
            """#!/bin/bash
set -euo pipefail
printf '%s\\n' "$* internal=${RADON_DEPLOY_INTERNAL:-0}" >> "$TIMEOUT_CALLS"
while [[ "${1:-}" == --* ]]; do shift; done
shift
exec "$@"
""",
            encoding="utf-8",
        )
        fake_timeout.chmod(0o755)
        fake_flock = fake_bin / "flock"
        fake_flock.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_flock.chmod(0o755)

        result = subprocess.run(
            ["bash", str(DEPLOY), "not-a-sha"],
            env={
                **os.environ,
                "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                "TIMEOUT_CALLS": str(timeout_calls),
                "DEPLOY_TIMEOUT": "7",
                "DEPLOY_KILL_AFTER": "2",
                "RADON_DEPLOY_LOCK_FILE": str(tmp_path / "deploy.lock"),
            },
            capture_output=True,
            text=True,
            timeout=3,
        )

        assert result.returncode == 2
        calls = timeout_calls.read_text(encoding="utf-8").splitlines()
        assert len(calls) == 1
        assert "--foreground" not in calls[0]
        assert "--signal=TERM" in calls[0]
        assert "--kill-after=2s" in calls[0]
        assert "7s" in calls[0]
        assert "internal=1" in calls[0]


class TestExplicitReleaseState:
    def test_main_requires_and_validates_explicit_sha(self, deploy_text: str) -> None:
        main = _body(deploy_text, "main")
        assert "requested_sha" in main
        assert re.search(r"\[\[.*requested_sha.*=~.*0-9a-f.*40", main)
        activation = _body(deploy_text, "activate_staged_release")
        assert 'reset --hard "$requested_sha"' in activation

    def test_head_equality_only_short_circuits_for_green_marker(self, deploy_text: str) -> None:
        main = _body(deploy_text, "main")
        equality = main.find('"$prev_commit" == "$requested_sha"')
        marker = main.find("green_marker_matches", equality)
        build = main.find("build_staged_release", equality)
        assert equality >= 0 and marker > equality
        assert build > marker, "missing marker must fall through to rebuild/restart"

    def test_green_marker_is_atomic_private_and_written_after_gate(self, deploy_text: str) -> None:
        marker = _body(deploy_text, "write_green_marker")
        assert "mktemp" in marker and "mv" in marker
        assert "chmod 0600" in marker or "umask 077" in marker

        main = _body(deploy_text, "main")
        assert main.find("deploy_gate") < main.find("write_green_marker")

    def test_green_marker_round_trip_is_private(self, tmp_path: Path) -> None:
        marker = tmp_path / "green-marker"
        sha = "a" * 40
        command = f"""
set -euo pipefail
export RADON_DEPLOY_GREEN_MARKER={marker!s}
source {DEPLOY!s}
write_green_marker {sha}
green_marker_matches {sha}
"""
        subprocess.run(["bash", "-c", command], check=True, capture_output=True, text=True)
        assert marker.read_text(encoding="utf-8") == f"{sha}\n"
        assert stat.S_IMODE(marker.stat().st_mode) == 0o600

    def test_turso_marker_uses_explicit_sha_and_cloud_env(self, deploy_text: str) -> None:
        marker = _body(deploy_text, "record_deploy_marker")
        assert "ENV_FILE_DEFAULT" in marker
        assert "/web/.env" not in marker


class TestEnvironmentSafety:
    def test_preflight_checks_private_mode_and_compose_render(self, deploy_text: str) -> None:
        preflight = _body(deploy_text, "preflight_env")
        assert "ENV_CHECKER" in preflight
        assert "0600" in CHECK_ENV.read_text(encoding="utf-8")
        assert "docker compose" in preflight and "config" in preflight
        assert ">/dev/null 2>&1" in preflight

    def test_generated_web_env_contains_only_public_values_and_is_private(
        self, tmp_path: Path
    ) -> None:
        app = tmp_path / "radon"
        web = app / "web"
        web.mkdir(parents=True)
        cloud_env = tmp_path / "cloud.env"
        cloud_env.write_text(
            "NEXT_PUBLIC_ONE=alpha\n"
            "NEXT_PUBLIC_LITERAL='value$NOT_EXPANDED'\n"
            "TURSO_AUTH_TOKEN=secret\n",
            encoding="utf-8",
        )
        cloud_env.chmod(0o600)

        command = f"""
set -euo pipefail
export RADON_DIR={app!s}
export RADON_DEPLOY_ENV_FILE={cloud_env!s}
source {DEPLOY!s}
write_web_env
"""
        subprocess.run(["bash", "-c", command], check=True, capture_output=True, text=True)

        generated = web / ".env"
        assert generated.read_text(encoding="utf-8") == (
            "NEXT_PUBLIC_ONE=alpha\nNEXT_PUBLIC_LITERAL='value$NOT_EXPANDED'\n"
        )
        assert stat.S_IMODE(generated.stat().st_mode) == 0o600

    def test_deploy_code_contains_no_credential_shaped_example(self, deploy_text: str) -> None:
        assert "passwords like" not in deploy_text

    def test_literal_env_runner_does_not_expand_dollar_substrings(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("DEPLOY_TEST_SECRET='prefix$NOT_AN_ENV_REF!suffix'\n", encoding="utf-8")
        env_file.chmod(0o600)

        result = subprocess.run(
            [
                sys.executable,
                str(RUN_WITH_ENV),
                str(env_file),
                "--",
                sys.executable,
                "-c",
                "import os; print(os.environ['DEPLOY_TEST_SECRET'])",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "prefix$NOT_AN_ENV_REF!suffix"

    @pytest.mark.parametrize("script", [SETUP, POST_SETUP])
    def test_bootstrap_web_env_writers_are_public_only_and_private(self, script: Path) -> None:
        text = script.read_text(encoding="utf-8")
        assert "NEXT_PUBLIC_" in text
        assert "install -m 0600" in text
        assert "run_with_env.py" in text
        assert not re.search(r"\bcp\s+[^\n]*radon-cloud/\.env[^\n]*web/\.env", text)

    def test_post_setup_propagates_remote_build_failures(self) -> None:
        text = POST_SETUP.read_text(encoding="utf-8")
        build_line = next(line for line in text.splitlines() if "bun run build" in line)
        assert "set -o pipefail" in build_line

    def test_post_setup_does_not_bypass_two_factor_restart_owner(self) -> None:
        text = POST_SETUP.read_text(encoding="utf-8")
        assert "/usr/local/bin/radon start" in text
        assert not re.search(r"docker compose (?:down|up)", text)


class TestComposeTwoFactorContract:
    def test_timeout_behavior_is_explicit(self) -> None:
        service = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["ib-gateway"]
        environment = service["environment"]
        assert "TWOFA_TIMEOUT_ACTION=exit" in environment
        assert "RELOGIN_AFTER_TWOFA_TIMEOUT=no" in environment

    def test_gateway_has_no_unmanaged_docker_relaunch_loop(self) -> None:
        service = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["ib-gateway"]
        assert service["restart"] == "no"
