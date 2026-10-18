"""DS-2026-10-18-04: /etc/radon/mcp.env is the PUBLIC MCP auth boundary.

`radon-mcp.service` terminates anonymous internet traffic and loads this file
for its Clerk verification inputs and operator allowlist. Both writers hardened
the destination against symlink substitution and then published it
`radon:radon` 0600, so the account `radon-mcp` runs as -- which also holds a
sudoers restart grant -- could rewrite the file that decides who the public
endpoint trusts, with no root step.

The publication now belongs to the root helper: root derives the key subset
from the root-owned canonical env and writes `root:radon` 0640, so the service
reads it and nothing unprivileged rewrites it. `deploy.sh` asks for the verb
and keeps its unprivileged write only while a host has not taken the grant.
"""

from __future__ import annotations

import os
import pathlib
import re
import shlex
import stat
import subprocess

import pytest

CLOUD_ROOT = pathlib.Path(__file__).resolve().parent.parent
ROOT_HELPER = CLOUD_ROOT / "scripts" / "deploy-root-helper.sh"
SETUP = CLOUD_ROOT / "scripts" / "setup-vps.sh"
DEPLOY = CLOUD_ROOT / "scripts" / "deploy.sh"
SUDOERS = CLOUD_ROOT / "config" / "sudoers.d" / "radon-deploy"

CANONICAL_ENV = (
    "CLERK_JWKS_URL=https://clerk.example.test/.well-known/jwks.json\n"
    "CLERK_ISSUER=https://clerk.example.test\n"
    "CLERK_SECRET_KEY=sk_live_secret\n"
    "ALLOWED_USER_IDS='user_a,user_b$NOT_EXPANDED'\n"
    "RADON_MCP_APP_BASE=http://127.0.0.1:3000\n"
    "TURSO_AUTH_TOKEN=secret\n"
)
EXPECTED = (
    "CLERK_JWKS_URL=https://clerk.example.test/.well-known/jwks.json\n"
    "CLERK_ISSUER=https://clerk.example.test\n"
    "ALLOWED_USER_IDS='user_a,user_b$NOT_EXPANDED'\n"
    "RADON_MCP_APP_BASE=http://127.0.0.1:3000\n"
)


def _mv_supports_no_target_directory() -> bool:
    """`mv -T` is GNU-only; production and CI are Linux, dev hosts may not be."""
    probe = subprocess.run(["mv", "-T", "--version"], capture_output=True, text=True)
    return "illegal option" not in (probe.stderr or "")


needs_gnu_mv = pytest.mark.skipif(
    not _mv_supports_no_target_directory(),
    reason="the publisher renames with GNU `mv -T`; this host's mv lacks it",
)


def _write_executable(path: pathlib.Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _function_body(script: str, name: str) -> str:
    match = re.search(rf"^{name}\(\)\s*\{{\s*\n(.+?)\n\}}\s*$", script, re.MULTILINE | re.DOTALL)
    assert match, f"{name}() missing"
    return match.group(1)


@pytest.fixture
def helper(tmp_path):
    """Run the real verb in helper test mode with a fake env and target."""
    state = tmp_path / "state"
    state.mkdir()
    for name in ("systemctl", "sync"):
        _write_executable(tmp_path / name, "#!/bin/bash\nexit 0\n")
    _write_executable(tmp_path / "rm", '#!/bin/bash\nexec /bin/rm "$@"\n')
    etc = tmp_path / "etc-radon"
    etc.mkdir()
    source = etc / "env"
    source.write_text(CANONICAL_ENV, encoding="utf-8")
    source.chmod(0o640)
    target = etc / "mcp.env"

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(ROOT_HELPER), "publish-mcp-env"],
            env={
                **os.environ,
                "RADON_DEPLOY_HELPER_TEST_MODE": "1",
                "RADON_TEST_SYSTEMCTL": str(tmp_path / "systemctl"),
                "RADON_TEST_RM": str(tmp_path / "rm"),
                "RADON_TEST_SYNC": str(tmp_path / "sync"),
                "RADON_TEST_ACTIVE_STATE_FILE": str(state / "active-units"),
                "RADON_TEST_REPLICA_PREFIX": str(tmp_path / "replica.db"),
                "RADON_TEST_MCP_ENV_SOURCE": str(source),
                "RADON_TEST_MCP_ENV_TARGET": str(target),
                # The test uid is not root and cannot chown; the publish still
                # has to produce the right content, mode and atomicity.
                "RADON_HELPER_SKIP_CHOWN": "1",
            },
            capture_output=True,
            text=True,
            timeout=60,
        )

    return run, source, target


class TestRootPublisher:
    @needs_gnu_mv
    def test_publishes_only_the_mcp_key_subset_group_readable(self, helper):
        run, _source, target = helper
        result = run()
        assert result.returncode == 0, result.stderr
        assert target.read_text(encoding="utf-8") == EXPECTED
        # 0640: radon-mcp reads it through group radon; nothing writes it.
        assert stat.S_IMODE(target.stat().st_mode) == 0o640
        assert not list(target.parent.glob("mcp.env.*"))

    @needs_gnu_mv
    def test_republishes_over_an_existing_file(self, helper):
        run, _source, target = helper
        target.write_text("ALLOWED_USER_IDS=stale\n", encoding="utf-8")
        assert run().returncode == 0
        assert target.read_text(encoding="utf-8") == EXPECTED

    def test_refuses_a_symlinked_target_without_following_it(self, helper, tmp_path):
        run, _source, target = helper
        victim = tmp_path / "victim"
        victim.write_text("untouched\n", encoding="utf-8")
        target.symlink_to(victim)
        result = run()
        assert result.returncode != 0
        assert victim.read_text(encoding="utf-8") == "untouched\n"

    def test_refuses_a_world_writable_source(self, helper):
        run, source, target = helper
        source.chmod(0o646)
        result = run()
        assert result.returncode != 0
        assert not target.exists()

    def test_refuses_a_symlinked_source(self, helper, tmp_path):
        run, source, target = helper
        real = tmp_path / "planted"
        real.write_text("ALLOWED_USER_IDS=attacker\n", encoding="utf-8")
        real.chmod(0o640)
        source.unlink()
        source.symlink_to(real)
        result = run()
        assert result.returncode != 0
        assert not target.exists()


class TestContracts:
    def test_the_verb_is_granted_and_takes_no_argument(self):
        sudoers = SUDOERS.read_text(encoding="utf-8")
        assert "/usr/local/sbin/radon-deploy-root publish-mcp-env" in sudoers
        assert "publish-mcp-env *" not in sudoers

    def test_root_paths_are_constants_the_caller_cannot_supply(self):
        helper_text = ROOT_HELPER.read_text(encoding="utf-8")
        assert "readonly MCP_ENV_SOURCE=/etc/radon/env" in helper_text
        assert "readonly MCP_ENV_TARGET=/etc/radon/mcp.env" in helper_text
        body = _function_body(helper_text, "publish_mcp_env")
        assert "$MCP_ENV_SOURCE" in body and "$MCP_ENV_TARGET" in body
        assert "chmod 0640" in body
        assert "chown root:radon" in body

    def test_every_writer_derives_the_same_key_subset(self):
        deploy_text = DEPLOY.read_text(encoding="utf-8")
        keys = re.search(r"readonly MCP_ENV_KEYS=('[^']+')", deploy_text)
        assert keys
        assert keys.group(1) in SETUP.read_text(encoding="utf-8")
        assert keys.group(1) in ROOT_HELPER.read_text(encoding="utf-8")

    def test_setup_publishes_root_owned_group_readable(self):
        body = _function_body(SETUP.read_text(encoding="utf-8"), "write_mcp_env")
        assert 'chmod 0640 "$mcp_env_tmp"' in body
        assert 'chown root:radon "$mcp_env_tmp"' in body
        assert 'chown radon:radon' not in body
        assert 'mv -T -f "$mcp_env_tmp" "$mcp_env_target"' in body

    def test_deploy_prefers_the_privileged_verb(self):
        body = _function_body(DEPLOY.read_text(encoding="utf-8"), "write_mcp_env")
        assert "publish-mcp-env" in body
        # The grant ships with the bundle the control-plane sync installs, so
        # the unprivileged write has to survive one release.
        assert "mv -f" in body

    def test_the_production_call_reaches_the_privileged_verb(self, tmp_path):
        """The one real caller passes the canonical env explicitly.

        Selecting the verb on "no arguments were given" made the privileged
        path unreachable in production while every test that called it bare
        passed: `main` calls `write_mcp_env "$ENV_FILE_DEFAULT"`. The choice
        has to be made on the RESOLVED paths.
        """
        deploy_text = DEPLOY.read_text(encoding="utf-8")
        assert 'write_mcp_env "$ENV_FILE_DEFAULT"' in deploy_text
        log = tmp_path / "sudo.log"
        sudo = tmp_path / "sudo"
        _write_executable(
            sudo,
            f"""#!/bin/bash
printf '%s\\n' "$*" >> {shlex.quote(str(log))}
exit 0
""",
        )
        canonical = tmp_path / "env"
        canonical.write_text(CANONICAL_ENV, encoding="utf-8")
        canonical.chmod(0o640)
        command = f"""
set -euo pipefail
export RADON_DIR={tmp_path / "radon"!s}
export RADON_DEPLOY_ENV_FILE={canonical!s}
export RADON_MCP_ENV_FILE={tmp_path / "mcp.env"!s}
export RADON_DEPLOY_ROOT_HELPER={shlex.quote("/usr/local/sbin/radon-deploy-root")}
source {DEPLOY!s}
sudo() {{ {shlex.quote(str(sudo))} "$@"; }}
write_mcp_env "$ENV_FILE_DEFAULT"
"""
        result = subprocess.run(["bash", "-c", command], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        assert lines, "the explicit canonical call never asked for the verb"
        assert lines[-1] == "-n /usr/local/sbin/radon-deploy-root publish-mcp-env", lines
        # The unprivileged write must not also have run.
        assert not (tmp_path / "mcp.env").exists()

    def test_a_non_canonical_destination_stays_on_the_unprivileged_write(self, tmp_path):
        """A dev or test invocation against some other path is not the
        production boundary and must not ask root to publish the real one."""
        log = tmp_path / "sudo.log"
        sudo = tmp_path / "sudo"
        _write_executable(sudo, f"#!/bin/bash\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(log))}\nexit 0\n")
        canonical = tmp_path / "env"
        canonical.write_text(CANONICAL_ENV, encoding="utf-8")
        elsewhere = tmp_path / "elsewhere.env"
        command = f"""
set -euo pipefail
export RADON_DIR={tmp_path / "radon"!s}
export RADON_DEPLOY_ENV_FILE={canonical!s}
export RADON_MCP_ENV_FILE={tmp_path / "mcp.env"!s}
source {DEPLOY!s}
sudo() {{ {shlex.quote(str(sudo))} "$@"; }}
write_mcp_env "$ENV_FILE_DEFAULT" {shlex.quote(str(elsewhere))}
"""
        result = subprocess.run(["bash", "-c", command], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        assert not log.exists() or "publish-mcp-env" not in log.read_text(encoding="utf-8")
        assert elsewhere.read_text(encoding="utf-8") == EXPECTED
