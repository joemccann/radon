"""Static guards for scripts/deploy.sh and scripts/setup-vps.sh.

Pins three classes of regression that bit production today:

1. `radon-newsfeed.service` was previously absent from deploy.sh's
   SERVICES array AND from setup-vps.sh's SERVICE_FILES — deploys
   compiled new code but never restarted the unit, and a fresh
   wipe-vps.sh rebuild would never install the unit at all.

2. The libSQL embedded replica got corrupted on every Next.js SIGTERM
   during deploy. deploy.sh now stops nextjs, deletes the replica
   files, then restarts everything — sidestepping the
   SIGTERM-mid-WAL-checkpoint race.

3. The ReplicaWatchdogHandler needs a sudoers rule to stop/start
   Next.js without a password. Previously installed manually on the
   VPS; now provisioned by setup-vps.sh.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SH = REPO_ROOT / "scripts" / "deploy.sh"
SETUP_SH = REPO_ROOT / "scripts" / "setup-vps.sh"


@pytest.fixture(scope="module")
def deploy_sh() -> str:
    return DEPLOY_SH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def setup_sh() -> str:
    return SETUP_SH.read_text(encoding="utf-8")


# ── deploy.sh ─────────────────────────────────────────────────────────


class TestDeployServicesArray:
    def test_radon_newsfeed_present(self, deploy_sh: str) -> None:
        # Pulls the SERVICES=(...) line and asserts the unit name.
        match = re.search(r"^readonly SERVICES=\(([^)]+)\)", deploy_sh, re.MULTILINE)
        assert match, "SERVICES array not found in deploy.sh"
        assert "radon-newsfeed" in match.group(1), (
            "radon-newsfeed missing from deploy.sh SERVICES array — "
            "deploys will not restart the newsfeed scraper."
        )

    def test_all_long_running_units_in_services(self, deploy_sh: str) -> None:
        match = re.search(r"^readonly SERVICES=\(([^)]+)\)", deploy_sh, re.MULTILINE)
        assert match
        services = set(match.group(1).split())
        for required in (
            "radon-nextjs",
            "radon-api",
            "radon-relay",
            "radon-monitor",
            "radon-newsfeed",
        ):
            assert required in services, f"{required} missing from SERVICES"


def _function_body(script: str, name: str) -> str:
    match = re.search(
        rf"^{name}\(\)\s*\{{\s*\n(.+?)\n\}}\s*$",
        script,
        re.DOTALL | re.MULTILINE,
    )
    assert match, f"{name}() function not found in script"
    return match.group(1)


class TestDeployGate:
    """DUR-05: the post-deploy gate must only measure what the deployed
    code controls. On 2026-06-11 the old full-/health gate hung on a
    wedged radon-api, the GitHub step SIGTERMed, and the deploy rolled
    back the fix for the very wedge it was measuring."""

    def test_gate_probes_health_lite_not_full_health(self, deploy_sh: str) -> None:
        assert re.search(
            r'^readonly HEALTH_LITE_URL="http://localhost:8321/health/lite"',
            deploy_sh,
            re.MULTILINE,
        ), "HEALTH_LITE_URL constant missing or not pointing at /health/lite"
        body = _function_body(deploy_sh, "check_health_lite")
        assert "HEALTH_LITE_URL" in body, "check_health_lite must curl HEALTH_LITE_URL"

    def test_gate_functions_never_reference_full_health_url(self, deploy_sh: str) -> None:
        # Full /health stays ONLY in the pre-teardown gateway-readiness wait.
        # IB auth state must never roll back a code deploy.
        for fn in ("deploy_gate", "check_units_active", "check_health_lite",
                   "report_isolated_health"):
            body = _function_body(deploy_sh, fn)
            assert "$HEALTH_URL" not in body and "{HEALTH_URL}" not in body, (
                f"{fn}() references the full /health URL — the post-deploy "
                "gate must not depend on IB gateway state."
            )

    def test_full_health_still_used_by_gateway_readiness_wait(self, deploy_sh: str) -> None:
        # The pre-teardown wait_for_gateway_ready path keeps the full /health
        # (it deliberately reads IB auth state BEFORE tearing services down).
        body = _function_body(deploy_sh, "gateway_data_plane_ready")
        assert "HEALTH_URL" in body

    def test_gate_checks_units_active_for_every_restarted_service(
        self, deploy_sh: str
    ) -> None:
        body = _function_body(deploy_sh, "check_units_active")
        assert '"${SERVICES[@]}"' in body, (
            "check_units_active must iterate the same SERVICES array that "
            "restart_services restarts — a drifting hard-coded list misses units."
        )
        assert "systemctl is-active" in body

    def test_deploy_gate_layers_units_then_lite_then_advisory(self, deploy_sh: str) -> None:
        body = _function_body(deploy_sh, "deploy_gate")
        assert "check_units_active" in body
        assert "check_health_lite" in body
        assert "report_isolated_health" in body

    def test_isolated_status_is_advisory_never_a_rollback_trigger(
        self, deploy_sh: str
    ) -> None:
        # :8330/status is a second opinion; its verdict includes IB state so
        # it must not contribute to gate_rc. And /healthz (vacuous zero-I/O
        # 200) must not be used at all.
        assert ":8330/status" in deploy_sh
        assert "8330/healthz" not in deploy_sh, (
            ":8330/healthz is a vacuous zero-I/O 200 — useless as a sensor"
        )
        gate_body = _function_body(deploy_sh, "deploy_gate")
        advisory_line = next(
            line for line in gate_body.splitlines()
            if "report_isolated_health" in line and not line.lstrip().startswith("#")
        )
        assert "gate_rc" not in advisory_line and "||" not in advisory_line, (
            "report_isolated_health must not feed the gate verdict"
        )
        report_body = _function_body(deploy_sh, "report_isolated_health")
        assert "return 0" in report_body

    def test_main_gates_rollback_on_deploy_gate(self, deploy_sh: str) -> None:
        main_body = _function_body(deploy_sh, "main")
        assert "deploy_gate" in main_body, "main() must run deploy_gate"
        assert "check_health;" not in main_body, (
            "main() still calls the old full-/health check_health gate"
        )

    def test_failed_gate_has_no_unverified_live_override(self, deploy_sh: str) -> None:
        main_body = _function_body(deploy_sh, "main")
        assert "RADON_DEPLOY_NO_GATE" not in main_body
        gate_call = re.search(r"if\s+!\s+deploy_gate;\s+then", main_body)
        assert gate_call, "deploy_gate must run unconditionally in main()"
        after_gate = main_body[gate_call.end():]
        rollback_pos = after_gate.find("rollback")
        assert rollback_pos >= 0

    def test_main_does_not_blind_sleep_before_the_post_restart_gate(self, deploy_sh: str) -> None:
        main_body = _function_body(deploy_sh, "main")
        restart = main_body.index("restart_services")
        gate = main_body.index("if ! deploy_gate")
        between = main_body[restart:gate]
        assert "HEALTH_WAIT_SECONDS" not in between
        assert "sleep" not in between
        surface_retries = int(
            re.search(r"^readonly SURFACE_RETRIES=(\d+)", deploy_sh, re.M).group(1)
        )
        surface_wait = int(
            re.search(r"^readonly SURFACE_RETRY_WAIT=(\d+)", deploy_sh, re.M).group(1)
        )
        surface_timeout = int(
            re.search(r"^readonly SURFACE_TIMEOUT=(\d+)", deploy_sh, re.M).group(1)
        )
        assert surface_retries * (surface_timeout + surface_wait) >= 40

    def test_gate_worst_case_stays_under_two_minutes(self, deploy_sh: str) -> None:
        # The 06-11 failure was a GitHub step SIGTERM at ~3min. Worst case:
        # lite probe = HEALTH_RETRIES * (HEALTH_CURL_TIMEOUT + HEALTH_RETRY_WAIT),
        # plus one advisory curl, plus instant systemctl checks.
        retries = int(re.search(r"^readonly HEALTH_RETRIES=(\d+)", deploy_sh, re.M).group(1))
        wait = int(re.search(r"^readonly HEALTH_RETRY_WAIT=(\d+)", deploy_sh, re.M).group(1))
        curl_to = int(re.search(r"^readonly HEALTH_CURL_TIMEOUT=(\d+)", deploy_sh, re.M).group(1))
        worst_case = retries * (curl_to + wait) + curl_to
        assert worst_case < 120, (
            f"Post-deploy gate worst case {worst_case}s ≥ 120s — must finish "
            "well inside the GitHub Actions step budget"
        )

    def test_deploy_sh_is_sourceable_for_dry_exercise(self, deploy_sh: str) -> None:
        # `source deploy.sh` must load functions without running main(),
        # so operators can dry-exercise the gate against live services.
        assert re.search(r'\$\{?BASH_SOURCE\[0\]\}?"?\s*==\s*"?\$0', deploy_sh), (
            "deploy.sh must guard `main \"$@\"` behind a BASH_SOURCE check"
        )


class TestDeployReplicaCleanup:
    def test_replica_db_prefix_constant_defined(self, deploy_sh: str) -> None:
        helper = (REPO_ROOT / "scripts" / "deploy-root-helper.sh").read_text()
        assert "/home/radon/radon/data/replica.db" in helper

    def test_restart_services_starts_each_core_once_without_replica_cold_fill(self, deploy_sh: str) -> None:
        stop_match = re.search(
            r"^stop_services_for_transition\(\)\s*\{\s*\n(.+?)\n\}\s*$",
            deploy_sh, re.DOTALL | re.MULTILINE,
        )
        start_match = re.search(
            r"^start_services_after_transition\(\)\s*\{\s*\n(.+?)\n\}\s*$",
            deploy_sh, re.DOTALL | re.MULTILINE,
        )
        restart_match = re.search(
            r"^restart_services\(\)\s*\{\s*\n(.+?)\n\}\s*$",
            deploy_sh, re.DOTALL | re.MULTILINE,
        )
        assert stop_match and start_match and restart_match
        executable = stop_match.group(1) + "\n" + restart_match.group(1) + "\n" + start_match.group(1)
        stop_pos = executable.find("stop-clean")
        restart_pos = executable.find("restart-managed")
        assert 0 <= stop_pos < restart_pos
        assert "start-nextjs" not in executable
        assert "sleep 8" not in executable
        assert executable.count("restart-managed") == 1
        assert "sudo rm" not in executable and "sudo systemctl" not in executable

    def test_replica_cleanup_targets_all_four_files(self, deploy_sh: str) -> None:
        # WAL, SHM, INFO are sidecar files; missing any of them on rebuild
        # leaves the replica in a half-corrupt state.
        helper = (REPO_ROOT / "scripts" / "deploy-root-helper.sh").read_text()
        for suffix in ("", "-wal", "-shm", "-info"):
            assert f"REPLICA_PREFIX}}{suffix}" in helper or f"replica.db{suffix}" in helper

    def test_replica_cleanup_invokes_sudo_rm_per_file(self, deploy_sh: str) -> None:
        helper = (REPO_ROOT / "scripts" / "deploy-root-helper.sh").read_text()
        assert '"$RM" -f "${REPLICA_FILES[@]}"' in helper
        assert "sudo rm" not in deploy_sh


# ── setup-vps.sh ──────────────────────────────────────────────────────


class TestSetupServiceFiles:
    def test_newsfeed_in_service_files(self, setup_sh: str) -> None:
        match = re.search(
            r"readonly SERVICE_FILES=\(([^)]+)\)", setup_sh, re.DOTALL
        )
        assert match, "SERVICE_FILES array not found"
        units = match.group(1)
        assert "radon-newsfeed.service" in units, (
            "radon-newsfeed.service missing from SERVICE_FILES — "
            "wipe-vps.sh rebuild will not install/enable the unit."
        )


class TestSetupSudoers:
    def test_deploy_sudoers_includes_stop_and_rm(self, setup_sh: str) -> None:
        sudoers = (REPO_ROOT / "config" / "sudoers.d" / "radon-deploy").read_text()
        assert "radon-*" not in sudoers
        for action in (
            "stop-clean",
            "restart-managed",
            "recover",
            "install-units",
            "sync-scheduled-units",
            "refresh-control-plane",
        ):
            assert f"radon-deploy-root {action}" in sudoers
        assert "radon-deploy-root refresh-control-plane-privileged" not in sudoers
        assert "radon-deploy-root start-nextjs" not in sudoers

    def test_watchdog_sudoers_file_provisioned(self, setup_sh: str) -> None:
        # ReplicaWatchdogHandler in radon-monitor needs its own narrowly
        # scoped sudoers rule. Previously installed manually on the VPS.
        assert '"${CLOUD_DIR}/config/sudoers.d/radon-monitor"' in setup_sh, (
            "setup-vps.sh must install the canonical radon-monitor policy "
            "for the replica watchdog."
        )
        monitor = (REPO_ROOT / "config" / "sudoers.d" / "radon-monitor").read_text()
        # Repair is one atomic operator action under the shared deploy lock —
        # not separate systemctl stop/start lines that can be half-executed.
        assert "systemctl stop radon-nextjs.service" not in monitor
        assert "systemctl start radon-nextjs.service" not in monitor
        assert "/usr/local/bin/radon repair-nextjs-replica" in monitor

    def test_visudo_validation_runs_before_chmod(self, setup_sh: str) -> None:
        # A malformed sudoers file locks out sudo entirely; provisioning
        # must validate via `visudo -cf` before leaving it in place.
        assert '"$visudo_bin" -cf "$candidate"' in setup_sh, (
            "setup-vps.sh must run `visudo -cf` to validate sudoers files"
        )


class TestOperatorCli:
    """`/usr/local/bin/radon` stop/start/restart helper, checked-in copy."""

    OPERATOR_PATH = REPO_ROOT / "scripts" / "operator-radon.sh"

    def test_operator_script_exists(self) -> None:
        assert self.OPERATOR_PATH.is_file(), (
            f"operator-radon.sh missing at {self.OPERATOR_PATH}. setup-vps.sh "
            "install_operator_cli() will fail on a fresh bootstrap."
        )

    def test_operator_script_passes_bash_n(self) -> None:
        result = subprocess.run(
            ["bash", "-n", str(self.OPERATOR_PATH)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, (
            f"operator-radon.sh syntax error:\n{result.stderr}"
        )

    def test_operator_script_auto_enumerates_units(self, setup_sh: str) -> None:
        # Catch a regression where someone replaces the auto-enumerating
        # SERVICES line with a hard-coded list (the bug we just fixed —
        # newly added units were silently excluded from `radon stop`).
        body = self.OPERATOR_PATH.read_text(encoding="utf-8")
        assert "systemctl list-units 'radon-*'" in body, (
            "operator-radon.sh must auto-enumerate radon-* units, not "
            "hard-code a list (which goes stale every time a new timer "
            "lands)."
        )

    def test_setup_vps_installs_operator(self, setup_sh: str) -> None:
        assert "install_operator_cli" in setup_sh, (
            "setup-vps.sh missing install_operator_cli function — a fresh "
            "wipe-vps.sh bootstrap will not install /usr/local/bin/radon."
        )
        # And the function must be called from main().
        main_block = re.search(
            r"^main\(\)\s*\{(.+?)\n\}\s*$", setup_sh,
            re.DOTALL | re.MULTILINE,
        )
        assert main_block, "setup-vps.sh main() function not found"
        assert "install_operator_cli" in main_block.group(1), (
            "install_operator_cli defined but never called from main()"
        )


class TestDefaultEnvFile:
    def test_prefers_etc_radon_env_when_present(self, deploy_sh: str) -> None:
        body = _function_body(deploy_sh, "_default_env_file")
        assert re.search(
            r'if \[\[ -n "\$\{RADON_DEPLOY_ENV_FILE:-\}" \]\]; then.*'
            r'elif \[\[ -f /etc/radon/env \]\]; then.*'
            r'elif \[\[ -f /home/radon/radon-cloud/\.env \]\]; then.*'
            r'\$\{CLOUD_DIR\}/\.env',
            body,
            re.DOTALL,
        ), "_default_env_file must prefer /etc/radon/env over ~/radon-cloud/.env"


class TestSetupEtcRadon:
    def test_creates_etc_radon_dir_mode_0750(self, setup_sh: str) -> None:
        body = _function_body(setup_sh, "create_etc_radon_dir")
        assert "-m 0750" in body
        assert "-o radon" in body
        assert "-g radon" in body
        assert "/etc/radon" in body
        assert "/var/lib/radon/media" in body
        assert not re.search(r"^\s*(mv|cp)\s+", body, re.MULTILINE)
        main = _function_body(setup_sh, "main")
        assert main.index("preflight_checks") < main.index("create_etc_radon_dir")
        assert main.index("create_etc_radon_dir") < main.index("validate_env")


class TestShellSyntax:
    """Belt-and-braces: bash -n catches syntax errors in the scripts."""

    @pytest.mark.parametrize("script", [DEPLOY_SH, SETUP_SH])
    def test_bash_n_passes(self, script: Path) -> None:
        result = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"bash -n failed for {script.name}:\n{result.stderr}"
        )
