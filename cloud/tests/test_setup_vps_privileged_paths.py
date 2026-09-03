"""setup-vps.sh privileged paths: root never follows a radon-replaceable path.

Provisioning runs as root and used to chmod/chown the env file, and
install/cp units, drop-ins, the Caddyfile, helpers, sudoers and polkit rules
straight out of the radon-owned checkout. All four of those commands follow
symlinks, so a link planted by the service account turned a root copy or
chown into an arbitrary-file primitive. These tests run the provisioning
functions against a radon-writable fake checkout plus stub binaries on PATH
(bash 3.2 compatible, no root needed) and pin a static contract over every
privileged line in the script.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "scripts" / "setup-vps.sh"
PLAYBOOK = ROOT.parent / "docs" / "security-audit-playbook.md"

REFUSAL = "not a regular file"
# The one loop in preflight_checks that refuses every link root would write through.
SSH_GUARD = "for ssh_path in /home/radon/.ssh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _function_body(script: str, name: str) -> str:
    match = re.search(
        rf"^{name}\(\)\s*\{{\s*\n(.+?)\n\}}\s*$", script, re.DOTALL | re.MULTILINE
    )
    assert match, f"{name}() function not found in setup-vps.sh"
    return match.group(1)


def _service_files() -> list[str]:
    text = SETUP.read_text(encoding="utf-8")
    match = re.search(r"readonly SERVICE_FILES=\(([^)]+)\)", text, re.DOTALL)
    assert match
    return match.group(1).split()


def _mode(path: Path) -> str:
    return oct(path.stat().st_mode & 0o7777)


def _stub(fake_bin: Path, name: str, log: Path, exit_code: int = 0) -> None:
    _write_executable(
        fake_bin / name,
        f"#!/bin/sh\nprintf '%s\\n' \"{name} $*\" >> {log!s}\nexit {exit_code}\n",
    )


def _run_setup_function(
    function: str, fake_bin: Path, extra_env: dict[str, str], cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    shell = f"""
set -euo pipefail
source {SETUP!s}
{function}
"""
    return subprocess.run(
        ["bash", "-c", shell],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "RADON_SETUP_SOURCE_ONLY": "1",
            **extra_env,
        },
        capture_output=True,
        text=True,
        cwd=cwd,
    )


@pytest.fixture
def harness(tmp_path: Path) -> dict[str, Path]:
    """Radon-writable fake checkout, a root-only file to point links at, and
    the env every function run shares."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    cloud = tmp_path / "cloud"
    for sub in (
        "services/radon-.service.d",
        "caddy",
        "scripts",
        "config/sudoers.d",
        "config/polkit",
    ):
        (cloud / sub).mkdir(parents=True)
    for unit in _service_files():
        (cloud / "services" / unit).write_text(f"[Unit]\nDescription={unit}\n")
    (cloud / "services" / "radon-.service.d" / "common.conf").write_text(
        "[Service]\nEnvironment=RADON_DB_NO_REPLICA=1\n"
    )
    (cloud / "services" / "journald-radon.conf").write_text("[Journal]\nSystemMaxUse=1G\n")
    (cloud / "caddy" / "Caddyfile").write_text("candidate\n")
    (cloud / "scripts" / "operator-radon.sh").write_text("#!/bin/bash\nprintf staged\\\\n\n")
    for policy in ("radon-deploy", "radon-monitor", "radon-ops"):
        (cloud / "config" / "sudoers.d" / policy).write_text(f"# {policy}\n")
    (cloud / "config" / "polkit" / "50-radon-services.rules").write_text("// rule\n")

    victim = tmp_path / "root-only"
    victim.write_text("root-only secret\n")
    victim.chmod(0o600)
    (tmp_path / "app").mkdir()
    return {
        "bin": fake_bin,
        "cloud": cloud,
        "victim": victim,
        "stage": tmp_path / "stage",
        "app": tmp_path / "app",
        "tmp": tmp_path,
    }


def _base_env(h: dict[str, Path]) -> dict[str, str]:
    return {
        "RADON_CLOUD_DIR": str(h["cloud"]),
        "RADON_APP_DIR": str(h["app"]),
        "RADON_SETUP_STAGE_DIR": str(h["stage"]),
        "RADON_HELPER_SKIP_CHOWN": "1",
        "RADON_POLICY_SKIP_CHOWN": "1",
        "RADON_SKIP_POLKIT_RELOAD": "1",
    }


def _link_to_victim(h: dict[str, Path], relative: str) -> Path:
    path = h["cloud"] / relative
    path.unlink()
    path.symlink_to(h["victim"])
    return path


def _assert_victim_untouched(h: dict[str, Path]) -> None:
    assert h["victim"].read_text() == "root-only secret\n"
    assert _mode(h["victim"]) == "0o600"


def _stage_leftovers(h: dict[str, Path]) -> list[Path]:
    return list(h["stage"].iterdir()) if h["stage"].exists() else []


# ── (a) env file: chmod/chown never follow a symlink ──────────────────


class TestEnvFileGuard:
    def _env_stubs(self, h: dict[str, Path]) -> Path:
        log = h["tmp"] / "privileged.log"
        for tool in ("chown", "chmod", "sudo", "python3.13"):
            _stub(h["bin"], tool, log)
        _stub(h["bin"], "systemctl", h["tmp"] / "systemctl.log", exit_code=3)
        return log

    @pytest.mark.parametrize("function", ["validate_env", "setup_node", "write_mcp_env"])
    def test_symlinked_env_is_refused_before_chmod_or_chown(
        self, harness: dict[str, Path], function: str
    ) -> None:
        log = self._env_stubs(harness)
        link = harness["tmp"] / "env"
        link.symlink_to(harness["victim"])
        result = _run_setup_function(
            function, harness["bin"], {**_base_env(harness), "RADON_DEPLOY_ENV_FILE": str(link)}
        )
        assert result.returncode != 0
        assert REFUSAL in result.stdout + result.stderr
        assert not log.exists(), f"root touched the link target: {log.read_text()}"
        assert not (harness["app"] / "web" / ".env").exists()
        _assert_victim_untouched(harness)

    def test_validate_env_regular_file_is_chmod_then_chown(
        self, harness: dict[str, Path]
    ) -> None:
        log = self._env_stubs(harness)
        env = harness["tmp"] / "env"
        env.write_text("TURSO_URL=x\n")
        result = _run_setup_function(
            "validate_env", harness["bin"], {**_base_env(harness), "RADON_DEPLOY_ENV_FILE": str(env)}
        )
        assert result.returncode == 0, result.stderr
        calls = log.read_text().splitlines()
        assert calls[:2] == [f"chmod 0640 {env}", f"chown root:radon {env}"]

    def test_setup_node_regular_file_is_chmod_then_chown(
        self, harness: dict[str, Path]
    ) -> None:
        log = self._env_stubs(harness)
        env = harness["tmp"] / "env"
        env.write_text("NEXT_PUBLIC_X=1\nSECRET=2\n")
        _run_setup_function(
            "setup_node", harness["bin"], {**_base_env(harness), "RADON_DEPLOY_ENV_FILE": str(env)}
        )
        # The later `install -o radon` of web/.env cannot succeed unprivileged;
        # the wire assertion is that the guarded pair ran against the real file.
        calls = log.read_text().splitlines()
        assert calls[:2] == [f"chmod 0640 {env}", f"chown root:radon {env}"]


# ── (b) symlinked checkout sources are refused ────────────────────────


class TestSymlinkedSourceRefused:
    def test_unit_source(self, harness: dict[str, Path]) -> None:
        systemctl_log = harness["tmp"] / "systemctl.log"
        _stub(harness["bin"], "systemctl", systemctl_log)
        _link_to_victim(harness, f"services/{_service_files()[0]}")
        result = _run_setup_function("copy_systemd_services", harness["bin"], _base_env(harness))
        assert result.returncode != 0
        assert REFUSAL in result.stdout + result.stderr
        assert not systemctl_log.exists(), "daemon-reload ran after a refused unit"
        assert _stage_leftovers(harness) == []
        _assert_victim_untouched(harness)

    def test_fleet_dropin_source(self, harness: dict[str, Path]) -> None:
        systemctl_log = harness["tmp"] / "systemctl.log"
        _stub(harness["bin"], "systemctl", systemctl_log)
        _link_to_victim(harness, "services/radon-.service.d/common.conf")
        result = _run_setup_function("install_fleet_dropin", harness["bin"], _base_env(harness))
        assert result.returncode != 0
        assert REFUSAL in result.stdout + result.stderr
        assert not systemctl_log.exists()
        assert _stage_leftovers(harness) == []

    def test_caddyfile_source(self, harness: dict[str, Path]) -> None:
        caddy_log = harness["tmp"] / "caddy.log"
        _stub(harness["bin"], "caddy", caddy_log)
        _stub(harness["bin"], "chown", harness["tmp"] / "chown.log")
        live = harness["tmp"] / "etc" / "Caddyfile"
        live.parent.mkdir()
        live.write_text("known-good\n")
        _link_to_victim(harness, "caddy/Caddyfile")
        result = _run_setup_function(
            "configure_caddy",
            harness["bin"],
            {
                **_base_env(harness),
                "RADON_CADDY_CONFIG_PATH": str(live),
                "RADON_CADDY_LOG_DIR": str(harness["tmp"] / "log"),
                "RADON_CADDY_BIN": str(harness["bin"] / "caddy"),
                "RADON_CADDY_SYSTEMCTL": "/usr/bin/true",
                "RADON_CADDY_TIMEOUT": "/usr/bin/true",
                "RADON_CADDY_SYNC": "/usr/bin/true",
            },
        )
        assert result.returncode != 0
        assert REFUSAL in result.stdout + result.stderr
        assert live.read_text() == "known-good\n"
        assert not caddy_log.exists(), "validate ran on a refused candidate"
        assert list(live.parent.glob("Caddyfile.candidate.*")) == []
        assert _stage_leftovers(harness) == []

    def test_operator_cli_source(self, harness: dict[str, Path]) -> None:
        target = harness["tmp"] / "radon"
        target.write_text("#!/bin/bash\nprintf working\\n\n")
        target.chmod(0o755)
        _link_to_victim(harness, "scripts/operator-radon.sh")
        result = _run_setup_function(
            "install_operator_cli",
            harness["bin"],
            {**_base_env(harness), "RADON_OPERATOR_CLI_TARGET": str(target)},
        )
        assert result.returncode != 0
        assert REFUSAL in result.stdout + result.stderr
        assert target.read_text() == "#!/bin/bash\nprintf working\\n\n"
        assert list(harness["tmp"].glob("radon.tmp.*")) == []
        assert _stage_leftovers(harness) == []

    def test_sudoers_source(self, harness: dict[str, Path]) -> None:
        visudo_log = harness["tmp"] / "visudo.log"
        _stub(harness["bin"], "visudo", visudo_log)
        sudoers_dir = harness["tmp"] / "sudoers.d"
        sudoers_dir.mkdir()
        _link_to_victim(harness, "config/sudoers.d/radon-deploy")
        result = _run_setup_function(
            "configure_sudoers",
            harness["bin"],
            {
                **_base_env(harness),
                "RADON_SUDOERS_DIR": str(sudoers_dir),
                "RADON_VISUDO_BIN": str(harness["bin"] / "visudo"),
            },
        )
        assert result.returncode != 0
        assert REFUSAL in result.stdout + result.stderr
        assert not visudo_log.exists()
        assert list(sudoers_dir.iterdir()) == []

    def test_polkit_source(self, harness: dict[str, Path]) -> None:
        rules_dir = harness["tmp"] / "rules.d"
        rules_dir.mkdir()
        _link_to_victim(harness, "config/polkit/50-radon-services.rules")
        result = _run_setup_function(
            "install_admin_polkit_rule",
            harness["bin"],
            {**_base_env(harness), "RADON_POLKIT_RULES_DIR": str(rules_dir)},
        )
        assert result.returncode != 0
        assert REFUSAL in result.stdout + result.stderr
        assert list(rules_dir.iterdir()) == []


# ── (c) regular sources go through a root-only 0600 stage ─────────────


class TestRegularSourceStaged:
    def test_helper_is_staged_0600_then_installed_0755(
        self, harness: dict[str, Path]
    ) -> None:
        # `cmp` is the byte check the staging helper runs against the 0600
        # copy; wrapping it records the mode and location of that copy.
        real_cmp = shutil.which("cmp")
        assert real_cmp
        cmp_log = harness["tmp"] / "cmp.log"
        _write_executable(
            harness["bin"] / "cmp",
            f"""#!/bin/sh
staged="$4"
mode="$(stat -c %a "$staged" 2>/dev/null || stat -f %Lp "$staged")"
printf '%s %s\\n' "$mode" "$staged" >> {cmp_log!s}
exec {real_cmp} "$@"
""",
        )
        target = harness["tmp"] / "radon"
        result = _run_setup_function(
            "install_operator_cli",
            harness["bin"],
            {**_base_env(harness), "RADON_OPERATOR_CLI_TARGET": str(target)},
        )
        assert result.returncode == 0, result.stderr
        mode, staged = cmp_log.read_text().split()
        assert mode == "600"
        assert Path(staged).parent == harness["stage"]
        assert _mode(harness["stage"]) == "0o700"
        assert _stage_leftovers(harness) == []
        assert _mode(target) == "0o755"
        assert target.read_text() == (harness["cloud"] / "scripts" / "operator-radon.sh").read_text()
        assert list(harness["tmp"].glob("radon.tmp.*")) == []

    def test_uncreatable_stage_dir_fails_closed_without_touching_cwd(
        self, harness: dict[str, Path]
    ) -> None:
        # A stage dir that cannot be created must not leave "" as the staged
        # path: cp/install resolve "" to the working directory.
        blocked = harness["tmp"] / "blocked"
        blocked.write_text("not a directory\n")
        cwd = harness["tmp"] / "cwd"
        cwd.mkdir()
        target = harness["tmp"] / "radon"
        result = _run_setup_function(
            "install_operator_cli",
            harness["bin"],
            {
                **_base_env(harness),
                "RADON_SETUP_STAGE_DIR": str(blocked / "stage"),
                "RADON_OPERATOR_CLI_TARGET": str(target),
            },
            cwd=cwd,
        )
        assert result.returncode != 0
        assert "root-only staging file" in result.stdout + result.stderr
        assert list(cwd.iterdir()) == []
        assert not target.exists()
        assert list(harness["tmp"].glob("radon.tmp.*")) == []

    def test_caddy_candidate_is_0600_during_validate_then_0644_live(
        self, harness: dict[str, Path]
    ) -> None:
        caddy_log = harness["tmp"] / "caddy.log"
        _write_executable(
            harness["bin"] / "caddy",
            f"""#!/bin/sh
candidate="$3"
mode="$(stat -c %a "$candidate" 2>/dev/null || stat -f %Lp "$candidate")"
printf '%s\\n' "$mode" >> {caddy_log!s}
exit 0
""",
        )
        _stub(harness["bin"], "chown", harness["tmp"] / "chown.log")
        live = harness["tmp"] / "etc" / "Caddyfile"
        live.parent.mkdir()
        result = _run_setup_function(
            "configure_caddy",
            harness["bin"],
            {
                **_base_env(harness),
                "RADON_CADDY_CONFIG_PATH": str(live),
                "RADON_CADDY_LOG_DIR": str(harness["tmp"] / "log"),
                "RADON_CADDY_BIN": str(harness["bin"] / "caddy"),
                "RADON_CADDY_SYSTEMCTL": "/usr/bin/true",
                "RADON_CADDY_TIMEOUT": "/usr/bin/true",
                "RADON_CADDY_SYNC": "/usr/bin/true",
            },
        )
        assert result.returncode == 0, result.stderr
        assert caddy_log.read_text().split() == ["600"]
        assert _mode(live) == "0o644"
        assert live.read_text() == "candidate\n"
        assert _stage_leftovers(harness) == []


# ── (d) /etc/radon and the radon-replaceable directories ──────────────


class TestDirectoryOwnership:
    def test_etc_radon_is_root_owned_sticky_group_radon(self) -> None:
        body = _function_body(SETUP.read_text(encoding="utf-8"), "create_etc_radon_dir")
        assert 'install -d -m 1770 -o root -g radon "$dir"' in body
        assert 'install -d -m 1770 "$dir"' in body
        assert '-o radon -g radon "$dir"' not in body

    def test_media_dir_link_is_refused_before_install_d(self) -> None:
        body = _function_body(SETUP.read_text(encoding="utf-8"), "create_etc_radon_dir")
        assert body.index('-L "$media"') < body.index("install -d -m 1770")

    def test_ssh_paths_refuse_links_before_root_writes(self) -> None:
        body = _function_body(SETUP.read_text(encoding="utf-8"), "preflight_checks")
        guard = body.index(SSH_GUARD)
        for write in (
            "mkdir -p /home/radon/.ssh",
            "cp /root/.ssh/authorized_keys",
            "chown -R radon:radon /home/radon/.ssh",
            "ssh-keygen -t ed25519",
            ">> /home/radon/.ssh/known_hosts",
        ):
            assert guard < body.index(write), write

    def test_deploy_lock_link_is_refused(self) -> None:
        body = _function_body(SETUP.read_text(encoding="utf-8"), "install_gateway_control")
        assert body.index("-L /home/radon/.radon-deploy.lock") < body.index(
            "/dev/null /home/radon/.radon-deploy.lock"
        )


# ── (e) static contract over every privileged line ────────────────────

PRIVILEGED = re.compile(r"^\s*(chown|chmod|install|cp)\b")
# Paths the radon account can replace: the checkout, its home, the env file,
# and anything under the radon-owned /var/lib/radon.
RADON_CONTROLLED = re.compile(
    r"\$\{?CLOUD_DIR\}?|\"\$source\"|\$\{?RADON_DIR\}?|\$\{?ENV_FILE\}?|\"\$env_file\""
    r"|/home/radon|\"\$media\""
)
# Privileged lines on radon-controlled paths that are allowed WITHOUT the
# guard or the staging helper, each with its reason.
ALLOWED_UNGUARDED = {
    # Root-owned mktemp source; the target holds only the NEXT_PUBLIC_* lines
    # radon already owns, and `install` unlinks the destination before writing.
    'install -m 0600 -o radon -g radon "$public_env_tmp" "${RADON_DIR}/web/.env"',
}


def _functions(script: str) -> list[tuple[str, int, int]]:
    spans = []
    lines = script.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{", line)
        if not match:
            continue
        end = index
        while end < len(lines) and lines[end] != "}":
            end += 1
        spans.append((match.group(1), index, end))
    return spans


def _enclosing(spans: list[tuple[str, int, int]], line_no: int) -> str:
    for name, start, end in spans:
        if start <= line_no <= end:
            return name
    return ""


class TestStaticContract:
    def test_every_radon_controlled_privileged_line_is_guarded(self) -> None:
        script = SETUP.read_text(encoding="utf-8")
        lines = script.splitlines()
        spans = _functions(script)
        offenders: list[str] = []
        for index, raw in enumerate(lines):
            line = raw.strip()
            if line.startswith("#") or not PRIVILEGED.match(line):
                continue
            if not RADON_CONTROLLED.search(line) or line in ALLOWED_UNGUARDED:
                continue
            function = _enclosing(spans, index)
            span = next(((s, e) for n, s, e in spans if n == function), None)
            body = "\n".join(lines[span[0] + 1 : span[1]]) if span else ""
            if function == "stage_from_checkout":
                # The helper's own copy of "$source" is the guarded chokepoint.
                assert 'require_regular_file "$source"' in body
                continue
            if '"$source"' in line or "CLOUD_DIR" in line:
                offenders.append(f"{index + 1}: {line} (must use stage_from_checkout)")
                continue
            guard = None
            if "ENV_FILE" in line or "$env_file" in line:
                guard = "require_regular_file"
            elif "/home/radon/.ssh" in line:
                guard = SSH_GUARD
            elif "/home/radon/.radon-deploy.lock" in line:
                guard = "-L /home/radon/.radon-deploy.lock"
            elif '"$media"' in line:
                guard = '-L "$media"'
            if guard is None or guard not in body or body.index(guard) > body.index(line):
                offenders.append(f"{index + 1}: {line} (no preceding guard in {function}())")
        assert offenders == [], "\n".join(offenders)

    def test_every_env_file_reader_is_guarded_first(self) -> None:
        script = SETUP.read_text(encoding="utf-8")
        for function in ("validate_env", "setup_node", "write_mcp_env"):
            body = _function_body(script, function)
            guard = body.index("require_regular_file")
            for reader in ("chmod", "chown", "grep -E"):
                if reader in body:
                    assert guard < body.index(reader), (function, reader)

    def test_no_install_or_cp_reads_the_checkout_outside_the_helper(self) -> None:
        script = SETUP.read_text(encoding="utf-8")
        spans = _functions(script)
        direct = [
            f"{index + 1}: {line.strip()}"
            for index, line in enumerate(script.splitlines())
            if PRIVILEGED.match(line)
            and "CLOUD_DIR" in line
            and _enclosing(spans, index) != "stage_from_checkout"
        ]
        assert direct == []

    def test_helper_stages_0600_under_root_only_dir_and_rechecks(self) -> None:
        script = SETUP.read_text(encoding="utf-8")
        assert 'readonly STAGE_DIR="${RADON_SETUP_STAGE_DIR:-/root/.radon-stage}"' in script
        body = _function_body(script, "stage_from_checkout")
        assert 'install -d -m 0700 "$STAGE_DIR"' in body
        assert body.index('require_regular_file "$source"') < body.index("cp --")
        assert body.index("cp --") < body.index("cmp -s")
        assert 'install -m "$mode" "$@" "$staged" "$target"' in body
        # No mapfile / exec {fd} / ${arr[@]} on empty arrays: bash 3.2 runs this.
        assert "mapfile" not in script

    def test_every_checkout_consumer_calls_the_helper(self) -> None:
        script = SETUP.read_text(encoding="utf-8")
        for function in (
            "configure_caddy",
            "copy_systemd_services",
            "install_journald_limits",
            "install_fleet_dropin",
            "install_deploy_root_helper",
            "install_gateway_control",
            "install_operator_cli",
            "install_app_runtime",
            "configure_sudoers",
            "install_admin_polkit_rule",
        ):
            assert "stage_from_checkout" in _function_body(script, function), function


# ── remote installers ─────────────────────────────────────────────────


class TestRemoteInstallerPins:
    DOCKER_FPR = "9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
    NODESOURCE_FPR = "6F71F525282841EEDAF851B42F59B5F99B1BE0B4"
    CADDY_FPR = "65760C51EDEA2017CEA2CA15155B6D79CA56EA34"

    def test_helper_refuses_a_key_that_does_not_match_the_pin(self) -> None:
        script = SETUP.read_text(encoding="utf-8")
        body = _function_body(script, "pin_apt_keyring")
        dearmor = body.index("--dearmor")
        check = body.index("--show-keys")
        assert dearmor < check
        assert "rm -f" in body[check:]
        assert "does not match the pinned fingerprint" in body

    def test_docker_apt_key_is_fingerprint_pinned_before_install(self) -> None:
        script = SETUP.read_text(encoding="utf-8")
        assert f'readonly DOCKER_GPG_FINGERPRINT="{self.DOCKER_FPR}"' in script
        body = _function_body(script, "install_docker")
        pin = body.index("pin_apt_keyring")
        install = body.index("apt install -y docker-ce")
        assert pin < install
        assert "DOCKER_GPG_FINGERPRINT" in body[pin:install]

    def test_nodesource_apt_key_is_fingerprint_pinned_and_setup_script_is_not_piped(
        self,
    ) -> None:
        script = SETUP.read_text(encoding="utf-8")
        assert f'readonly NODESOURCE_GPG_FINGERPRINT="{self.NODESOURCE_FPR}"' in script
        body = _function_body(script, "install_node22")
        pin = body.index("pin_apt_keyring")
        install = body.index("apt install -y nodejs")
        assert pin < install
        assert "NODESOURCE_GPG_FINGERPRINT" in body[pin:install]
        assert "setup_22.x" not in body
        assert "| bash" not in body
        assert "deb.nodesource.com/node_22.x" in body
        assert "signed-by=" in body

    def test_caddy_apt_key_is_fingerprint_pinned_and_sources_list_is_local(self) -> None:
        script = SETUP.read_text(encoding="utf-8")
        assert f'readonly CADDY_GPG_FINGERPRINT="{self.CADDY_FPR}"' in script
        body = _function_body(script, "install_caddy")
        pin = body.index("pin_apt_keyring")
        install = body.index("apt-get install -y caddy")
        assert pin < install
        assert "CADDY_GPG_FINGERPRINT" in body[pin:install]
        assert "debian.deb.txt" not in body
        assert "dl.cloudsmith.io/public/caddy/stable/deb/debian" in body
        assert "signed-by=" in body


# ── playbook invariant ────────────────────────────────────────────────


class TestPlaybookInvariant:
    def test_playbook_records_the_no_dereference_invariant(self) -> None:
        text = PLAYBOOK.read_text(encoding="utf-8")
        assert "never dereferences a path an unprivileged account can replace" in text
        assert "stage_from_checkout" in text
        assert "docker group" in text

    def test_playbook_records_apt_key_pins_and_root_owned_env(self) -> None:
        text = PLAYBOOK.read_text(encoding="utf-8")
        assert "NodeSource" in text
        assert "Caddy" in text
        assert "fingerprint-pinned" in text
        assert "canonical env file is 0640 root:radon" in text
