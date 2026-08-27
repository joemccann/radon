"""Tests for VPS deployment scripts (setup-vps.sh, deploy.sh, migrate-data.sh).

Scripts are analyzed via string/regex matching and file permission checks.
They are NOT executed (they target a remote VPS environment).
"""

import os
from pathlib import Path
import re
import stat

import pytest


def contract_keys() -> list[str]:
    """`cloud/config/required-env.txt` as check-env.py's `required_keys()`
    reads it: comment lines stripped, not a raw substring search."""
    contract = Path(__file__).resolve().parents[1] / "config" / "required-env.txt"
    return [
        line.strip()
        for line in contract.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def setup_vps_path(scripts_dir):
    return scripts_dir / "setup-vps.sh"


@pytest.fixture
def setup_vps(setup_vps_path):
    return setup_vps_path.read_text()


@pytest.fixture
def deploy_path(scripts_dir):
    return scripts_dir / "deploy.sh"


@pytest.fixture
def deploy(deploy_path):
    return deploy_path.read_text()


@pytest.fixture
def migrate_path(scripts_dir):
    return scripts_dir / "migrate-data.sh"


@pytest.fixture
def migrate(migrate_path):
    return migrate_path.read_text()


@pytest.fixture
def all_scripts(setup_vps, deploy, migrate):
    return [setup_vps, deploy, migrate]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_executable(path):
    return os.access(path, os.X_OK)


def has_bash_shebang(content):
    return content.startswith("#!/bin/bash")


def has_strict_mode(content):
    return "set -euo pipefail" in content


EXPECTED_SERVICE_FILES = [
    "radon-ib-gateway.service",
    "radon-nextjs.service",
    "radon-api.service",
    "radon-relay.service",
    "radon-monitor.service",
    "radon-refresh.service",
    "radon-refresh.timer",
]


# ===========================================================================
# setup-vps.sh
# ===========================================================================

class TestSetupVpsFileBasics:
    def test_file_exists(self, setup_vps_path):
        assert setup_vps_path.exists()

    def test_is_executable(self, setup_vps_path):
        assert is_executable(setup_vps_path)

    def test_starts_with_bash_shebang(self, setup_vps):
        assert has_bash_shebang(setup_vps)

    def test_uses_strict_mode(self, setup_vps):
        assert has_strict_mode(setup_vps)


class TestSetupVpsRepoCloning:
    def test_clones_radon_repo(self, setup_vps):
        assert "radon.git" in setup_vps

    def test_clones_radon_cloud_repo(self, setup_vps):
        assert "radon-cloud.git" in setup_vps

    def test_uses_git_clone(self, setup_vps):
        assert "git clone" in setup_vps


class TestSetupVpsPythonVenv:
    def test_creates_venv_with_python313(self, setup_vps):
        assert "python3.13" in setup_vps
        assert re.search(r"-m\s+venv", setup_vps)

    def test_installs_from_requirements(self, setup_vps):
        assert "requirements.txt" in setup_vps

    def test_pip_install(self, setup_vps):
        assert "pip" in setup_vps and "install" in setup_vps


class TestSetupVpsNextjsBuild:
    def test_bun_frozen_install(self, setup_vps):
        assert "bun install --frozen-lockfile" in setup_vps

    def test_bun_run_build(self, setup_vps):
        assert "bun run build" in setup_vps


class TestSetupVpsCaddy:
    def test_installs_caddy_from_official_repos(self, setup_vps):
        assert "cloudsmith.io/public/caddy" in setup_vps

    def test_copies_caddyfile(self, setup_vps):
        assert "/etc/caddy/" in setup_vps
        assert "Caddyfile" in setup_vps


class TestSetupVpsSystemd:
    def test_copies_service_files_to_systemd(self, setup_vps):
        assert "/etc/systemd/system/" in setup_vps
        assert re.search(r'\bcp\b.*systemd', setup_vps)

    def test_daemon_reload(self, setup_vps):
        assert "systemctl daemon-reload" in setup_vps

    def test_enables_all_services(self, setup_vps):
        assert "systemctl enable" in setup_vps

    def test_starts_ib_gateway_first(self, setup_vps):
        # Bootstrap starts the full stack only through the locked operator,
        # which owns Gateway control + deploy lock and correct ordering.
        start_fn = setup_vps[setup_vps.index("start_services() {"):]
        start_fn = start_fn[: start_fn.index("# -- Firewall")]
        assert "/usr/local/bin/radon start" in start_fn
        assert "systemctl start radon-nextjs" not in start_fn
        assert "radon-ib-gateway-control start" not in start_fn

    def test_waits_between_ib_gateway_and_other_services(self, setup_vps):
        # Ordering and settle waits live in the operator CLI, not setup-vps.
        operator = Path(__file__).resolve().parents[1] / "scripts" / "operator-radon.sh"
        text = operator.read_text(encoding="utf-8")
        assert "gateway_control start" in text or "GATEWAY_CONTROL" in text
        assert "PERSISTENT_UNITS" in text

    def test_references_all_seven_service_files(self, setup_vps):
        for svc in EXPECTED_SERVICE_FILES:
            assert svc in setup_vps, f"Missing service file reference: {svc}"


class TestSetupVpsPrerequisites:
    def test_installs_docker_from_official_repo(self, setup_vps):
        assert "download.docker.com" in setup_vps

    def test_installs_python_from_deadsnakes(self, setup_vps):
        assert "deadsnakes" in setup_vps

    def test_installs_nodejs_from_nodesource(self, setup_vps):
        assert "nodesource" in setup_vps


class TestSetupVpsRootCheck:
    def test_requires_root(self, setup_vps):
        assert "EUID" in setup_vps


class TestSetupVpsEnvValidation:
    def test_checks_env_before_starting_services(self, setup_vps):
        assert ".env" in setup_vps
        # Check call order in main(), not function definition order
        main_match = re.search(r'main\(\)\s*\{(.*?)\}', setup_vps, re.DOTALL)
        assert main_match, "main() function not found"
        main_body = main_match.group(1)
        assert "validate_env" in main_body
        env_pos = main_body.index("validate_env")
        start_pos = main_body.index("start_services")
        assert env_pos < start_pos

    def test_validates_required_keys(self, setup_vps):
        required = (
            Path(__file__).resolve().parents[1] / "config" / "required-env.txt"
        ).read_text()
        assert "config/required-env.txt" in setup_vps
        assert "TWS_USERID" in required
        assert "DOMAIN" in required
        assert "NODE_ENV" in required

    def test_requires_the_equibles_key_the_scheduled_units_construct_with(self):
        # Five radon-equibles-*.timer oneshots construct EquiblesClient(), which
        # raises EquiblesAuthError when EQUIBLES_API_KEY is unset. Without the
        # key in this contract the deploy preflight passed and every one of them
        # failed on every fire, leaving 13F and filing-forensics permanently
        # empty for every ticker.
        #
        # Parse the contract the way check-env.py's required_keys() does. A
        # substring check against the raw file survives `# EQUIBLES_API_KEY`,
        # which the preflight skips — T-163 verbatim, with this test green.
        assert "EQUIBLES_API_KEY" in contract_keys()


class TestSetupVpsFirewall:
    def test_opens_port_80(self, setup_vps):
        assert "80" in setup_vps
        assert re.search(r"ufw allow 80", setup_vps)

    def test_opens_port_443(self, setup_vps):
        assert "443" in setup_vps
        assert re.search(r"ufw allow 443", setup_vps)


class TestSetupVpsSudoers:
    def test_configures_sudoers_for_passwordless_restart(self, setup_vps):
        assert "config/sudoers.d/radon-deploy" in setup_vps
        assert '"$visudo_bin" -cf "$candidate"' in setup_vps


class TestSetupVpsIdempotency:
    def test_skips_clone_if_dir_exists(self, setup_vps):
        assert re.search(r'if.*-d.*\.git', setup_vps)
        assert "already cloned" in setup_vps or "skipping" in setup_vps.lower()

    def test_skips_venv_if_exists(self, setup_vps):
        assert re.search(r'if.*-d.*venv', setup_vps, re.IGNORECASE)

    def test_skips_caddy_if_installed(self, setup_vps):
        assert "command -v caddy" in setup_vps


# ===========================================================================
# deploy.sh
# ===========================================================================

class TestDeployFileBasics:
    def test_file_exists(self, deploy_path):
        assert deploy_path.exists()

    def test_is_executable(self, deploy_path):
        assert is_executable(deploy_path)

    def test_starts_with_bash_shebang(self, deploy):
        assert has_bash_shebang(deploy)

    def test_uses_strict_mode(self, deploy):
        assert has_strict_mode(deploy)


class TestDeployGitOperations:
    def test_saves_previous_commit(self, deploy):
        assert re.search(r"prev_commit.*=.*current_commit|rev-parse HEAD", deploy)

    def test_uses_git_fetch(self, deploy):
        assert "git fetch" in deploy or "fetch origin main" in deploy

    def test_resets_to_the_explicit_requested_sha(self, deploy):
        assert 'reset --hard "$requested_sha"' in deploy
        assert '"$requested_sha" != "$origin_tip"' in deploy


class TestDeployDependencies:
    def test_installs_python_deps(self, deploy):
        assert "requirements.txt" in deploy
        assert "pip install" in deploy

    def test_builds_nextjs(self, deploy):
        assert "bun install --frozen-lockfile" in deploy
        assert "bun run build" in deploy


class TestDeployServiceRestart:
    def test_restarts_services(self, deploy):
        assert "restart-managed" in deploy


class TestDeployHealthCheck:
    def test_health_check_with_curl(self, deploy):
        assert "localhost:8321/health" in deploy
        assert "curl" in deploy

    def test_retry_logic(self, deploy):
        assert re.search(r"attempt|RETRIES|retries", deploy)
        assert re.search(r"while|for", deploy)

    def test_multiple_attempts(self, deploy):
        assert "HEALTH_RETRIES" in deploy
        retries_match = re.search(r"HEALTH_RETRIES=(\d+)", deploy)
        assert retries_match is not None
        retries = int(retries_match.group(1))
        assert retries > 1


class TestDeployLiveNotify:
    def test_notifies_after_successful_deploy(self, deploy):
        success = deploy[deploy.index('log_success "Deploy successful:'):]
        assert "notify_release_live" in success
        assert "scripts/deploy_notify.py" in deploy
        assert "Never P1" in deploy

    def test_rollback_does_not_claim_live(self, deploy):
        rollback = deploy[deploy.index("rollback()"):]
        next_fn = re.search(r"\n\w+\(\)", rollback[1:])
        section = rollback[: next_fn.start()] if next_fn else rollback[:800]
        assert "notify_release_live" not in section


class TestDeployRollback:
    def test_has_rollback_function(self, deploy):
        assert re.search(r"rollback\s*\(\)", deploy)

    def test_rollback_restores_previous_commit(self, deploy):
        assert "reset --hard" in deploy
        assert "prev_commit" in deploy

    def test_rollback_exits_with_code_1(self, deploy):
        rollback_section = deploy[deploy.index("rollback()") :]
        next_function = re.search(r"\n\w+\(\)", rollback_section[1:])
        if next_function:
            rollback_body = rollback_section[: next_function.start() + 1]
        else:
            rollback_body = rollback_section
        assert "exit 1" in rollback_body


class TestDeploySuccessOutput:
    def test_prints_success_with_commit(self, deploy):
        assert re.search(r"Deploy successful.*short_log|log_success.*Deploy", deploy)


# ===========================================================================
# migrate-data.sh
# ===========================================================================

class TestMigrateFileBasics:
    def test_file_exists(self, migrate_path):
        assert migrate_path.exists()

    def test_is_executable(self, migrate_path):
        assert is_executable(migrate_path)

    def test_starts_with_bash_shebang(self, migrate):
        assert has_bash_shebang(migrate)

    def test_uses_strict_mode(self, migrate):
        assert has_strict_mode(migrate)


class TestMigrateHelpFlag:
    def test_has_help_flag(self, migrate):
        assert re.search(r"-h\|--help|--help\)", migrate)

    def test_has_usage_function(self, migrate):
        assert re.search(r"usage\s*\(\)", migrate)


class TestMigrateOptions:
    def test_has_source_option(self, migrate):
        assert "--source" in migrate

    def test_has_target_option(self, migrate):
        assert "--target" in migrate

    def test_default_source(self, migrate):
        assert "~/dev/apps/finance/radon" in migrate or "dev/apps/finance/radon" in migrate

    def test_default_target(self, migrate):
        assert "radon@ib-gateway:~/radon" in migrate


class TestMigrateSnapshotCreation:
    def test_creates_tar_gz(self, migrate):
        assert "tar.gz" in migrate or "tar czf" in migrate

    def test_archives_data_json_files(self, migrate):
        assert re.search(r"tar czf", migrate)


class TestMigrateTransfer:
    def test_transfers_via_scp(self, migrate):
        assert "scp" in migrate

    def test_extracts_on_vps_via_ssh(self, migrate):
        assert "ssh" in migrate
        assert re.search(r"ssh.*tar xzf", migrate)


class TestMigrateVerification:
    def test_verifies_file_count(self, migrate):
        assert "file count" in migrate.lower() or re.search(
            r"wc -l|count", migrate
        )

    def test_compares_local_and_remote_counts(self, migrate):
        assert re.search(r"expected_count|remote_count|mismatch", migrate)


class TestMigrateRollback:
    def test_keeps_snapshot_for_rollback(self, migrate):
        assert re.search(r"[Rr]ollback|snapshot.*kept|preserved", migrate)


# ===========================================================================
# Cross-cutting concerns
# ===========================================================================

class TestColoredOutput:
    def test_all_scripts_use_ansi_codes(self, all_scripts):
        ansi_pattern = re.compile(r"\\033\[")
        for script in all_scripts:
            assert ansi_pattern.search(script), "Script missing ANSI color codes"


class TestLoggingFunctions:
    @pytest.mark.parametrize("func", ["log_info", "log_success", "log_error"])
    def test_all_scripts_define_logging_functions(self, all_scripts, func):
        for i, script in enumerate(all_scripts):
            assert func in script, (
                f"Script index {i} missing {func} function"
            )


class TestDirectorySafety:
    def test_no_unguarded_cd(self, all_scripts):
        """Verify no script uses cd without being inside a function body.

        A bare 'cd' at the top level that never returns would change state
        for subsequent commands. We allow cd inside functions since the
        subshell or function scope contains it, and we allow cd followed
        by a return to previous dir.
        """
        bare_cd_pattern = re.compile(r"^cd\s", re.MULTILINE)
        for script in all_scripts:
            matches = bare_cd_pattern.findall(script)
            assert len(matches) == 0, (
                "Found bare top-level 'cd' outside function scope"
            )


class TestNoHardcodedSecrets:
    SECRET_PATTERNS = [
        re.compile(r"password\s*=\s*['\"].+['\"]", re.IGNORECASE),
        re.compile(r"api_key\s*=\s*['\"].+['\"]", re.IGNORECASE),
        re.compile(r"secret\s*=\s*['\"].+['\"]", re.IGNORECASE),
        re.compile(r"token\s*=\s*['\"][A-Za-z0-9]{20,}['\"]", re.IGNORECASE),
    ]

    def test_no_hardcoded_secrets(self, all_scripts):
        for script in all_scripts:
            for pattern in self.SECRET_PATTERNS:
                assert not pattern.search(script), (
                    f"Possible hardcoded secret found: {pattern.pattern}"
                )
