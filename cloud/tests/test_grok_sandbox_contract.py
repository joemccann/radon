"""Sandbox contract for the grok responder and upgrader units.

Both units run a third-party agent CLI. Their own env file is read by
systemd before the namespace is built, so hiding it from the process
costs nothing and keeps its credentials off disk for the agent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SERVICES = Path(__file__).resolve().parents[1] / "services"
RESPONDER = "radon-grok-page-responder.service"
UPGRADE = "radon-grok-upgrade.service"
ENV_FILE = "/home/radon/radon-page-responder.env"


def _paths(unit: str, directive: str) -> set[str]:
    text = (SERVICES / unit).read_text(encoding="utf-8")
    out: set[str] = set()
    for line in text.splitlines():
        if line.startswith(f"{directive}="):
            out.update(p.lstrip("-") for p in line.split("=", 1)[1].split())
    return out


@pytest.mark.parametrize("unit", [RESPONDER, UPGRADE])
def test_env_file_is_inaccessible_to_the_process(unit):
    assert f"EnvironmentFile={ENV_FILE}" in (SERVICES / unit).read_text()
    assert ENV_FILE in _paths(unit, "InaccessiblePaths")


# The responder runs grok --always-approve over untrusted page text. Its write
# set must not reach any directory a secret-bearing unit executes from, nor
# radon state beyond its own runtime lock dir.
TOKENS = "radon-subscription-tokens.service"
SECRET_DIRS = ("/var/lib/radon/flex-secrets", "/var/lib/radon/rh-mcp")
RUNTIME_DIR = "/var/lib/radon/grok-runtime"
UPGRADE_SCRATCH = "/var/lib/radon/grok-upgrade"


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _tokens_exec_dirs() -> set[str]:
    text = (SERVICES / TOKENS).read_text(encoding="utf-8")
    dirs = {"/home/radon/.local/bin", "/home/radon/.grok/bin"}  # LOCAL_BIN_DIRS
    for line in text.splitlines():
        if line.startswith("Environment=PATH="):
            dirs.update(line.split("=", 2)[2].split(":"))
    return {d for d in dirs if d.startswith(("/home/radon", "/var/lib/radon"))}


def _writable(unit: str, path: str) -> bool:
    """Most specific ReadWritePaths / ReadOnlyPaths / InaccessiblePaths wins."""
    rules = [(p, "rw") for p in _paths(unit, "ReadWritePaths")]
    rules += [(p, "ro") for p in _paths(unit, "ReadOnlyPaths")]
    rules += [(p, "no") for p in _paths(unit, "InaccessiblePaths")]
    hits = [(len(p), mode) for p, mode in rules if _under(path, p)]
    if not hits:
        return True  # no ProtectSystem: plain unix perms, radon owns it
    return max(hits)[1] == "rw"


def test_responder_does_not_write_blanket_local_or_state():
    rw = _paths(RESPONDER, "ReadWritePaths")
    assert "/home/radon/.local" not in rw
    assert "/var/lib/radon" not in rw


@pytest.mark.parametrize("path", sorted(_tokens_exec_dirs()) + [
    "/home/radon/.local/bin/grok",
    "/home/radon/.grok/bin/grok",
    "/home/radon/.grok/hooks",
    "/var/lib/radon/grok_lkg.json",
    UPGRADE_SCRATCH + "/candidate/bin/grok",
])
def test_responder_cannot_write_executables_or_radon_state(path):
    assert not _writable(RESPONDER, path), path


@pytest.mark.parametrize("path", [
    "/home/radon/radon-page-responder/scripts/x.py",
    "/home/radon/.grok/sessions/s.json",
    RUNTIME_DIR + "/grok-runtime.lock",
])
def test_responder_still_writes_what_it_needs(path):
    assert _writable(RESPONDER, path), path


@pytest.mark.parametrize("unit", [RESPONDER, UPGRADE])
def test_secret_state_dirs_are_inaccessible(unit):
    denied = _paths(unit, "InaccessiblePaths")
    for secret in SECRET_DIRS:
        assert secret in denied, (unit, secret)


def test_upgrade_candidate_lives_outside_the_responder_write_set():
    text = (SERVICES / UPGRADE).read_text(encoding="utf-8")
    assert f"--scratch {UPGRADE_SCRATCH}" in text
    assert f"--lock {RUNTIME_DIR}/grok-runtime.lock" in text
    assert _writable(UPGRADE, UPGRADE_SCRATCH + "/candidate")
    assert _writable(UPGRADE, "/home/radon/.local/bin/grok")
    assert not _writable(RESPONDER, UPGRADE_SCRATCH)


# Home credential stores: an SSH key registered on GitHub, agent CLI OAuth
# sessions, GPG keys and gh/git credential files. The grok child runs with
# HOME=/home/radon, so anything readable here is readable to it.
HOME_CREDENTIAL_STORES = (
    "/home/radon/.ssh",
    "/home/radon/.claude",
    "/home/radon/.claude.json",
    "/home/radon/.codex",
    "/home/radon/.gemini",
    "/home/radon/.gnupg",
    "/home/radon/.config/gh",
    "/home/radon/.git-credentials",
)


@pytest.mark.parametrize("unit", [RESPONDER, UPGRADE])
@pytest.mark.parametrize("path", HOME_CREDENTIAL_STORES)
def test_home_credential_stores_are_inaccessible(unit, path):
    assert path in _paths(unit, "InaccessiblePaths"), (unit, path)


# Every other agent CLI whose subscription grant the token keeper refreshes
# keeps it under HOME. grok is the agent these units run, so its own store
# stays reachable; a newly added provider must be denied here too.
SUBSCRIPTION_TOKENS = Path(__file__).resolve().parents[2] / "scripts" / "subscription_tokens.py"


def _other_provider_home_dirs() -> set[str]:
    subdirs = re.findall(r'default_subdir="([^"]+)"', SUBSCRIPTION_TOKENS.read_text(encoding="utf-8"))
    tops = {sub.split("/", 1)[0] for sub in subdirs}
    assert ".grok" in tops and len(tops) > 1
    return {f"/home/radon/{top}" for top in tops - {".grok"}}


@pytest.mark.parametrize("unit", [RESPONDER, UPGRADE])
def test_other_subscription_token_stores_are_inaccessible(unit):
    missing = _other_provider_home_dirs() - _paths(unit, "InaccessiblePaths")
    assert not missing, (unit, sorted(missing))


def test_responder_clone_origin_needs_no_credential():
    setup = SERVICES.parent / "scripts" / "setup-grok-page-responder.sh"
    text = setup.read_text(encoding="utf-8")
    assert 'ORIGIN_URL="${RADON_PAGE_RESPONDER_ORIGIN:-https://github.com/' in text
    assert 'git -C "$CLONE" remote set-url origin "$ORIGIN_URL"' in text


# radon-subscription-tokens.service runs three unpinned third-party agent CLIs
# (agy, grok, codex) out of radon-writable ~/.local/bin, as the same user, with
# /etc/radon/env loaded. subscription_tokens.py builds an allowlisted child
# environment, which stops accidental inheritance but is not a boundary against
# a compromised CLI distribution: the file itself was readable (root:radon
# 0640, and ProtectSystem=full restricts writes, not reads). systemd reads
# EnvironmentFile before the namespace is built, so hiding the file from the
# process costs the unit nothing, exactly as the two grok units already do.
TOKENS_ENV_FILE = "/etc/radon/subscription-tokens/env"


def test_token_keeper_cannot_read_its_own_env_file_at_runtime():
    text = (SERVICES / TOKENS).read_text(encoding="utf-8")
    assert f"EnvironmentFile={TOKENS_ENV_FILE}" in text
    assert "/etc/radon/env" in _paths(TOKENS, "InaccessiblePaths")
    assert TOKENS_ENV_FILE in _paths(TOKENS, "InaccessiblePaths"), (
        "the agent CLIs this unit launches could read the production env file "
        "directly; systemd has already loaded it by then"
    )


def test_token_keeper_restricts_procfs_to_its_own_user():
    text = (SERVICES / TOKENS).read_text(encoding="utf-8")
    assert re.search(r"(?m)^ProtectProc=(invisible|noaccess|ptraceable)$", text), (
        "the unit launches third-party CLIs and must not expose other users' "
        "process metadata to them"
    )


# The subscription vault broker answers {"op":"get"} on an AF_UNIX socket with
# no peer-identity check, so the socket's directory mode is the whole access
# control and both grok units already run as the uid that owns it. The keeper
# is the vault's only intended client; the agent-exposed units must be denied.
VAULT_RUNTIME_DIR = "/run/radon-subscription-vault"


@pytest.mark.parametrize("unit", [RESPONDER, UPGRADE])
def test_subscription_vault_socket_is_inaccessible(unit):
    assert VAULT_RUNTIME_DIR in _paths(unit, "InaccessiblePaths"), (
        unit,
        "the grok child runs as radon, which owns the vault socket directory; "
        "subscription_vault.py performs no client authorization",
    )


def test_token_keeper_keeps_the_vault_socket():
    assert VAULT_RUNTIME_DIR not in _paths(TOKENS, "InaccessiblePaths")
    assert f"{VAULT_RUNTIME_DIR}/vault.sock" in (SERVICES / TOKENS).read_text(encoding="utf-8")
