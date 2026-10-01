"""setup-grok-page-responder.sh [2/5]: rerunning setup keeps operator flags.

Every rerun used to rebuild the stripped env from the production env alone,
dropping GROK_PAGE_RESPONDER / AUTOSHIP / AUTOPUSH. The responder treats an
unset flag as off (REL-030), so it silently reported "skipped": "disabled".
These tests run the real step [2/5] block with chown stubbed. Example data
only.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

CLOUD = Path(__file__).resolve().parents[1]
SETUP = CLOUD / "scripts" / "setup-grok-page-responder.sh"
RESPONDER = CLOUD.parent / "scripts" / "grok_page_responder.py"

PROD = """\
# example production env
TURSO_DB_URL=libsql://example.invalid
TURSO_AUTH_TOKEN=new-turso-token
PUSHOVER_USER=example-user
PUSHOVER_TOKEN=new-pushover-token
GH_TOKEN=new-gh-token
UW_TOKEN=not-for-the-responder
GROK_PAGE_RESPONDER=0
"""

EXISTING = """\
TURSO_DB_URL=libsql://old.invalid
TURSO_AUTH_TOKEN=old-turso-token
PUSHOVER_USER=example-user
PUSHOVER_TOKEN=old-pushover-token
GH_TOKEN=old-gh-token
GROK_PAGE_RESPONDER=1
GROK_PAGE_AUTOSHIP=1
GROK_PAGE_AUTOPUSH=0
GROK_PAGE_MAX_ACTIONS_PER_DAY=3
GROK_PAGE_NO_DOTENV=0
GROK_PAGE_SYNC_REMOTE=0
GROK_BIN=/tmp/elsewhere/grok
UW_TOKEN=stale-extra-secret
"""


def _step2() -> str:
    text = SETUP.read_text(encoding="utf-8")
    match = re.search(r'^echo "\[2/5\].*?(?=^echo "\[3/5\])', text, re.M | re.S)
    assert match, "step [2/5] moved; update this test"
    return match.group(0)


def _builder():
    sys.path.insert(0, str(CLOUD / "scripts"))
    try:
        import grok_responder_env as builder
    finally:
        sys.path.pop(0)
    return builder


def _run_result(
    tmp_path: Path, prod: str, existing: str | None
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    prod_env = tmp_path / "prod.env"
    prod_env.write_text(prod, encoding="utf-8")
    env_file = tmp_path / "radon-page-responder.env"
    if existing is not None:
        env_file.write_text(existing, encoding="utf-8")
    shim = tmp_path / "bin"
    shim.mkdir(exist_ok=True)
    python = shim / "python3.13"
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    python.chmod(0o755)
    script = (
        "set -euo pipefail\n"
        # Runs as the test user; the contract test below pins "-u radon".
        'sudo() { [[ "$1 $2" == "-u radon" ]] || exit 97; shift 2; "$@"; }\n'
        f'SCRIPT_DIR="{CLOUD / "scripts"}"\n'
        f'ENV_FILE="{env_file}"\nPROD_ENV="{prod_env}"\n'
        + _step2()
    )
    result = subprocess.run(
        ["bash", "-c", script],
        env={**os.environ, "PATH": f"{shim}:{os.environ.get('PATH', '')}"},
        capture_output=True,
        text=True,
    )
    parsed: dict[str, str] = {}
    if env_file.is_file():
        lines = env_file.read_text(encoding="utf-8").splitlines()
        keys = [line.partition("=")[0] for line in lines]
        assert len(keys) == len(set(keys)), f"duplicate keys: {keys}"
        parsed = dict(line.partition("=")[::2] for line in lines)
    return result, parsed


def _run(tmp_path: Path, prod: str, existing: str | None) -> dict[str, str]:
    result, parsed = _run_result(tmp_path, prod, existing)
    assert result.returncode == 0, result.stderr
    env_file = tmp_path / "radon-page-responder.env"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    return parsed


def test_rerun_preserves_operator_flags(tmp_path):
    env = _run(tmp_path, PROD, EXISTING)
    assert env["GROK_PAGE_RESPONDER"] == "1"
    assert env["GROK_PAGE_AUTOSHIP"] == "1"
    assert env["GROK_PAGE_AUTOPUSH"] == "0"
    assert env["GROK_PAGE_MAX_ACTIONS_PER_DAY"] == "3"


def test_secrets_come_only_from_the_production_env(tmp_path):
    env = _run(tmp_path, PROD, EXISTING)
    assert env["PUSHOVER_TOKEN"] == "new-pushover-token"
    assert env["GH_TOKEN"] == "new-gh-token"
    assert env["TURSO_DB_URL"] == "libsql://example.invalid"
    assert "UW_TOKEN" not in env
    # A GH_TOKEN removed from production does not survive from the old file.
    env = _run(tmp_path, PROD.replace("GH_TOKEN=new-gh-token\n", ""), EXISTING)
    assert "GH_TOKEN" not in env


def test_prod_turso_auth_token_is_never_copied(tmp_path):
    env = _run(tmp_path, PROD, EXISTING)
    assert env.get("TURSO_AUTH_TOKEN") != "new-turso-token"
    assert env.get("TURSO_AUTH_TOKEN") != "old-turso-token"
    assert "TURSO_AUTH_TOKEN" not in env
    dest = (tmp_path / "radon-page-responder.env").read_text(encoding="utf-8")
    assert "new-turso-token" not in dest
    assert "old-turso-token" not in dest


def test_scoped_responder_token_is_copied_as_consumer_name(tmp_path):
    prod = PROD + "TURSO_RESPONDER_AUTH_TOKEN=scoped-responder-token\n"
    env = _run(tmp_path, prod, EXISTING)
    assert env["TURSO_AUTH_TOKEN"] == "scoped-responder-token"
    assert "TURSO_RESPONDER_AUTH_TOKEN" not in env
    dest = (tmp_path / "radon-page-responder.env").read_text(encoding="utf-8")
    assert "new-turso-token" not in dest
    assert "old-turso-token" not in dest


def test_missing_scoped_token_omits_turso_token_and_warns(tmp_path):
    result, env = _run_result(tmp_path, PROD, EXISTING)
    assert result.returncode == 0, result.stderr
    assert "TURSO_AUTH_TOKEN" not in env
    combined = result.stdout + result.stderr
    assert "WARNING:" in combined
    assert "TURSO_RESPONDER_AUTH_TOKEN" in combined
    assert "new-turso-token" not in combined
    assert "old-turso-token" not in combined
    assert "new-pushover-token" not in combined


def test_empty_scoped_token_is_treated_as_missing(tmp_path):
    prod = PROD + "TURSO_RESPONDER_AUTH_TOKEN=\n"
    result, env = _run_result(tmp_path, prod, None)
    assert result.returncode == 0, result.stderr
    assert "TURSO_AUTH_TOKEN" not in env
    assert "TURSO_RESPONDER_AUTH_TOKEN" in result.stderr


def test_token_values_never_printed(tmp_path, capsys):
    builder = _builder()
    prod = PROD + "TURSO_RESPONDER_AUTH_TOKEN=scoped-secret-value\n"
    text = builder.build(prod, EXISTING)
    captured = capsys.readouterr()
    for secret in (
        "new-turso-token",
        "old-turso-token",
        "scoped-secret-value",
        "new-pushover-token",
    ):
        assert secret not in captured.out
        assert secret not in captured.err
    assert "new-turso-token" not in text
    assert "scoped-secret-value" in text
    result, _env = _run_result(tmp_path, prod, EXISTING)
    printed = result.stdout + result.stderr
    for secret in (
        "new-turso-token",
        "old-turso-token",
        "scoped-secret-value",
        "new-pushover-token",
    ):
        assert secret not in printed


def test_managed_keys_are_reset_not_preserved(tmp_path):
    env = _run(tmp_path, PROD, EXISTING)
    assert env["GROK_PAGE_NO_DOTENV"] == "1"
    assert env["GROK_PAGE_SYNC_REMOTE"] == "1"
    assert env["GROK_BIN"] == "/home/radon/.local/bin/grok"


def test_first_install_leaves_flags_unset_so_they_default_off(tmp_path):
    env = _run(tmp_path, PROD, None)
    assert "GROK_PAGE_RESPONDER" not in env
    assert "GROK_PAGE_AUTOSHIP" not in env
    assert "GROK_PAGE_AUTOPUSH" not in env


def test_rerun_is_byte_identical(tmp_path):
    _run(tmp_path, PROD, EXISTING)
    env_file = tmp_path / "radon-page-responder.env"
    first = env_file.read_bytes()
    _run(tmp_path, PROD, first.decode())
    assert env_file.read_bytes() == first


@pytest.mark.parametrize("value", ["1; rm -rf /", "$(id)", "a" * 64])
def test_malformed_flag_values_are_dropped(tmp_path, value):
    env = _run(tmp_path, PROD, f"GROK_PAGE_RESPONDER={value}\n")
    assert "GROK_PAGE_RESPONDER" not in env


def test_allowlist_covers_every_operator_knob_the_responder_reads():
    sys.path.insert(0, str(CLOUD / "scripts"))
    try:
        import grok_responder_env as builder
    finally:
        sys.path.pop(0)
    read = set(re.findall(r"\b(GROK_PAGE_[A-Z_]+)\b", RESPONDER.read_text(encoding="utf-8")))
    managed = {key for key, _ in builder.MANAGED}
    assert read - managed == set(builder.OPERATOR_FLAGS)


# Paths radon owns or whose parent radon owns. Root must not open, move,
# chown, chmod or create anything there by name: a radon-planted symlink
# (to a file or a directory) would redirect the root operation (CWE-59).
_RADON_PATH = re.compile(
    r'"?\$(?:ENV_FILE|PROD_ENV|CLONE|MARKER)\b|/home/radon\b|/var/lib/radon/'
)
_ROOT_OPS = {
    "mv", "cp", "chown", "chmod", "install", "mkdir", "ln", "rm", "touch",
    "tee", "cat", "python3.13", "git",
}


def test_root_never_operates_by_name_inside_radon_owned_trees():
    offenders = []
    text = SETUP.read_text(encoding="utf-8").replace("\\\n", " ")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "echo ")):
            continue
        words = line.split()
        if words[0] in _ROOT_OPS and _RADON_PATH.search(line):
            offenders.append(line)
        # Redirects are opened by the root shell even on a sudo line;
        # single-quoted text runs inside the radon shell.
        unquoted = re.sub(r"'[^']*'", "''", line)
        if ">" in unquoted and _RADON_PATH.search(unquoted.split(">", 1)[1]):
            offenders.append(line)
    assert offenders == []


def test_env_file_is_published_by_radon(tmp_path):
    text = _step2()
    assert re.search(r'^sudo -u radon .*mv -f .*"\$1"', text, re.M | re.S)
    _run(tmp_path, PROD, EXISTING)


def _run_whole_script_as_fake_root(tmp_path: Path, source: Path):
    """The real script from ``source``, with ``id -u`` reporting root."""
    shim = tmp_path / "rootshim"
    shim.mkdir()
    calls = tmp_path / "calls.log"
    (shim / "id").write_text("#!/bin/sh\necho 0\n", encoding="utf-8")
    for tool in ("sudo", "git", "python3.13", "install", "curl"):
        (shim / tool).write_text(
            f'#!/bin/sh\necho "{tool} $*" >> "{calls}"\nexit 0\n', encoding="utf-8"
        )
    for exe in shim.iterdir():
        exe.chmod(0o755)
    result = subprocess.run(
        ["bash", str(source)],
        env={**os.environ, "PATH": f"{shim}:{os.environ.get('PATH', '')}"},
        capture_output=True,
        text=True,
    )
    return result, calls


def test_refuses_to_run_from_a_tree_root_does_not_own(tmp_path):
    # Root executing a radon-writable checkout runs whatever radon put there.
    staged = tmp_path / "cloud" / "scripts"
    staged.mkdir(parents=True)
    copy = staged / SETUP.name
    copy.write_text(SETUP.read_text(encoding="utf-8"), encoding="utf-8")
    result, calls = _run_whole_script_as_fake_root(tmp_path, copy)
    assert result.returncode == 77, result.stderr
    assert "root-owned" in result.stderr
    assert not calls.exists(), calls.read_text()


def test_documented_recipe_stages_from_the_root_provision_store():
    doc = (CLOUD.parent / "docs" / "grok-page-responder.md").read_text(encoding="utf-8")
    assert "/opt/radon-provision/radon.git" in doc
    assert "bash cloud/scripts/setup-grok-page-responder.sh" not in doc
