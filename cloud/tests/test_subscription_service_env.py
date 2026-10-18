"""Synthetic subscription-service environment publication contracts."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import stat

import pytest

CLOUD = Path(__file__).resolve().parents[1]
PUBLISHER = CLOUD / "scripts/subscription_service_env.py"
TARGET = "/etc/radon/subscription-tokens/env"


@pytest.fixture
def publisher():
    spec = importlib.util.spec_from_file_location("subscription_service_env", PUBLISHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def paths(tmp_path):
    source = tmp_path / "canonical.env"
    source.write_text(
        "TURSO_DB_URL=libsql://synthetic.example.test\n"
        "TURSO_AUTH_TOKEN=synthetic-heartbeat-token\n"
        "PUSHOVER_USER=synthetic-user\n"
        "PUSHOVER_TOKEN=synthetic-page-token\n"
        "CLAUDE_CONFIG_DIR=/home/radon/.claude\n"
        "CODEX_HOME=/home/radon/.codex\n"
        "CLERK_SECRET_KEY=synthetic-unrelated-value\n"
        "OPENAI_API_KEY=synthetic-unrelated-value\n"
    )
    source.chmod(0o600)
    return source, tmp_path / "subscription-tokens/env"


def publish(publisher, paths):
    publisher.publish(*paths, owner_uid=os.getuid())


def test_publishes_only_needed_keys_with_private_metadata(publisher, paths):
    source, target = paths
    publish(publisher, paths)
    assert target.read_text() == "\n".join(source.read_text().splitlines()[:6]) + "\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert target.stat().st_uid == os.getuid()
    assert not list(target.parent.glob(".env.*"))


def test_rotation_replaces_old_values_and_removes_omitted_options(publisher, paths):
    source, target = paths
    publish(publisher, paths)
    source.write_text("TURSO_DB_URL=libsql://rotated.example.test\nTURSO_AUTH_TOKEN=rotated\nPUSHOVER_USER=user\nPUSHOVER_TOKEN=rotated-page\n")
    publish(publisher, paths)
    assert target.read_text() == source.read_text()
    assert "CLAUDE_CONFIG_DIR" not in target.read_text()


@pytest.mark.parametrize("missing", ["TURSO_DB_URL", "TURSO_AUTH_TOKEN", "PUSHOVER_USER", "PUSHOVER_TOKEN"])
def test_incomplete_inputs_preserve_existing_output(publisher, paths, missing):
    source, target = paths
    publish(publisher, paths)
    before = target.read_bytes()
    source.write_text("\n".join(line for line in source.read_text().splitlines() if not line.startswith(missing + "=")) + "\n")
    with pytest.raises(ValueError):
        publish(publisher, paths)
    assert target.read_bytes() == before


@pytest.mark.parametrize("replacement", ["source-link", "target-link", "directory-link", "directory-writable", "source-writable"])
def test_refuses_noncanonical_inputs_without_modifying_target(publisher, paths, tmp_path, replacement):
    source, target = paths
    publish(publisher, paths)
    before = target.read_bytes()
    victim = tmp_path / "other"
    if replacement == "source-link":
        source.rename(victim)
        source.symlink_to(victim)
    elif replacement == "target-link":
        target.rename(victim)
        target.symlink_to(victim)
    elif replacement == "directory-link":
        target.parent.rename(victim)
        target.parent.symlink_to(victim, target_is_directory=True)
    elif replacement == "directory-writable":
        target.parent.chmod(0o770)
    elif replacement == "source-writable":
        source.chmod(0o620)
    with pytest.raises((ValueError, OSError)):
        publish(publisher, paths)
    assert target.read_bytes() == before


def test_foreign_owner_is_refused(publisher, paths):
    with pytest.raises(ValueError):
        publisher.publish(*paths, owner_uid=os.getuid() + 10000)


def test_empty_and_duplicate_required_assignments_are_rejected(publisher, paths):
    source, _ = paths
    source.write_text(source.read_text() + "PUSHOVER_TOKEN=\n")
    with pytest.raises(ValueError):
        publish(publisher, paths)



def test_unset_optional_directory_inputs_use_native_defaults(publisher, paths):
    source, target = paths
    source.write_text(source.read_text() + 'CLAUDE_CONFIG_DIR=""\nCODEX_HOME=\n')
    publish(publisher, paths)
    assert "CLAUDE_CONFIG_DIR=" not in target.read_text()
    assert "CODEX_HOME=" not in target.read_text()


def test_literal_quoted_values_are_preserved_without_expansion(publisher, paths):
    source, target = paths
    source.write_text(source.read_text().replace("PUSHOVER_TOKEN=synthetic-page-token", "PUSHOVER_TOKEN='synthetic-$NOT_EXPANDED'"))
    publish(publisher, paths)
    assert "PUSHOVER_TOKEN='synthetic-$NOT_EXPANDED'" in target.read_text()

def test_service_and_publication_lifecycle_contracts():
    unit = (CLOUD / "services/radon-subscription-tokens.service").read_text()
    assert f"EnvironmentFile={TARGET}" in unit
    assert "EnvironmentFile=/etc/radon/env\n" not in unit
    assert TARGET in next(line for line in unit.splitlines() if line.startswith("InaccessiblePaths="))
    assert "RADON_SUBSCRIPTION_VAULT_SOCKET=/run/radon-subscription-vault/vault.sock" in unit
    assert "Environment=RADON_DB_NO_REPLICA=1" in unit
    helper = (CLOUD / "scripts/deploy-root-helper.sh").read_text()
    assert "publish-subscription-env)" in helper
    assert "/usr/local/lib/radon/subscription_service_env.py" in helper
    assert f" {TARGET}" in helper
    assert "publish-subscription-env" in (CLOUD / "config/sudoers.d/radon-deploy").read_text()
    setup = (CLOUD / "scripts/setup-vps.sh").read_text().split("main() {", 1)[1]
    assert setup.index("write_subscription_env") < setup.index("copy_systemd_services")
    deploy = (CLOUD / "scripts/deploy.sh").read_text()
    assert "write_subscription_env" in deploy
    assert "publish-subscription-env" in deploy


def test_control_plane_manifest_uses_canonical_mode_strings():
    import re

    helper = (CLOUD / "scripts/deploy-root-helper.sh").read_text()
    modes = re.search(r"readonly -a CONTROL_PLANE_MODES=\((.*?)\)", helper, re.S).group(1).split()
    assert all(mode in {"440", "644", "755"} for mode in modes)


@pytest.mark.parametrize("delivery_status", [0, 1])
def test_deploy_publisher_uses_exact_root_action_and_propagates_failure(tmp_path, delivery_status):
    import subprocess

    calls = tmp_path / "calls"
    shell = f"""
source {str(CLOUD / 'scripts/deploy.sh')!r}
sudo() {{ printf '%s\\n' "$*" > {str(calls)!r}; return {delivery_status}; }}
write_subscription_env
"""
    result = subprocess.run(["bash", "-c", shell], capture_output=True, text=True)
    assert result.returncode == delivery_status
    assert calls.read_text().strip() == "-n /usr/local/sbin/radon-deploy-root publish-subscription-env"
