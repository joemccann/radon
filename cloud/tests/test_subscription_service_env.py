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
OPERATOR_TOKEN = "synthetic-operator-token"
KEEPER_TOKEN = "synthetic-keeper-token"
KEEPER_KEY = "TURSO_SUBSCRIPTION_KEEPER_AUTH_TOKEN"
MINT = (
    "turso db tokens create <DB> "
    "-p service_health:data_read,data_add,data_update "
    "-p service_health_events:data_add"
)
DERIVED = (
    "TURSO_DB_URL=libsql://synthetic.example.test\n"
    f"TURSO_AUTH_TOKEN={KEEPER_TOKEN}\n"
    "PUSHOVER_USER=synthetic-user\n"
    "PUSHOVER_TOKEN=synthetic-page-token\n"
    "CLAUDE_CONFIG_DIR=/home/radon/.claude\n"
    "CODEX_HOME=/home/radon/.codex\n"
)


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
        f"TURSO_AUTH_TOKEN={OPERATOR_TOKEN}\n"
        f"{KEEPER_KEY}={KEEPER_TOKEN}\n"
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
    assert target.read_text() == DERIVED
    assert OPERATOR_TOKEN not in target.read_text()
    assert KEEPER_KEY not in target.read_text()
    assert "CLERK_SECRET_KEY" not in target.read_text()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert target.stat().st_uid == os.getuid()
    assert not list(target.parent.glob(".env.*"))


def test_rotation_replaces_old_values_and_removes_omitted_options(publisher, paths):
    source, target = paths
    publish(publisher, paths)
    source.write_text(
        "TURSO_DB_URL=libsql://rotated.example.test\n"
        "TURSO_AUTH_TOKEN=rotated-operator\n"
        f"{KEEPER_KEY}=rotated-keeper\n"
        "PUSHOVER_USER=user\n"
        "PUSHOVER_TOKEN=rotated-page\n"
    )
    publish(publisher, paths)
    assert target.read_text() == (
        "TURSO_DB_URL=libsql://rotated.example.test\n"
        "TURSO_AUTH_TOKEN=rotated-keeper\n"
        "PUSHOVER_USER=user\n"
        "PUSHOVER_TOKEN=rotated-page\n"
    )
    assert "rotated-operator" not in target.read_text()
    assert "CLAUDE_CONFIG_DIR" not in target.read_text()


@pytest.mark.parametrize(
    "missing",
    ["TURSO_DB_URL", "TURSO_AUTH_TOKEN", KEEPER_KEY, "PUSHOVER_USER", "PUSHOVER_TOKEN"],
)
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
    assert f"TURSO_AUTH_TOKEN={KEEPER_TOKEN}" in target.read_text()
    assert OPERATOR_TOKEN not in target.read_text()


def test_keeper_quoting_is_preserved_and_operator_token_is_omitted(publisher, paths):
    source, target = paths
    source.write_text(source.read_text().replace(
        f"{KEEPER_KEY}={KEEPER_TOKEN}",
        f"{KEEPER_KEY}='{KEEPER_TOKEN}-$NOT_EXPANDED'",
    ))
    publish(publisher, paths)
    body = target.read_text()
    assert f"TURSO_AUTH_TOKEN='{KEEPER_TOKEN}-$NOT_EXPANDED'" in body
    assert OPERATOR_TOKEN not in body
    assert KEEPER_KEY not in body


def test_missing_keeper_token_does_not_create_output(publisher, paths):
    source, target = paths
    source.write_text("\n".join(
        line for line in source.read_text().splitlines() if not line.startswith(KEEPER_KEY + "=")
    ) + "\n")
    with pytest.raises(ValueError) as caught:
        publish(publisher, paths)
    assert KEEPER_KEY in str(caught.value)
    assert OPERATOR_TOKEN not in str(caught.value)
    assert KEEPER_TOKEN not in str(caught.value)
    assert not target.exists()


def test_identical_tokens_do_not_create_output(publisher, paths):
    source, target = paths
    source.write_text(source.read_text().replace(
        f"{KEEPER_KEY}={KEEPER_TOKEN}", f"{KEEPER_KEY}={OPERATOR_TOKEN}",
    ))
    with pytest.raises(ValueError) as caught:
        publish(publisher, paths)
    assert "matches" in str(caught.value)
    assert OPERATOR_TOKEN not in str(caught.value)
    assert not target.exists()


def test_equal_tokens_fail_closed_without_echoing_secrets(publisher, paths):
    source, target = paths
    publish(publisher, paths)
    before = target.read_bytes()
    source.write_text(source.read_text().replace(
        f"{KEEPER_KEY}={KEEPER_TOKEN}", f"{KEEPER_KEY}={OPERATOR_TOKEN}",
    ))
    with pytest.raises(ValueError) as caught:
        publish(publisher, paths)
    message = str(caught.value)
    assert "matches" in message
    assert "missing" not in message
    assert OPERATOR_TOKEN not in message
    assert KEEPER_TOKEN not in message
    assert target.read_bytes() == before


def test_quoted_duplicate_of_operator_token_is_rejected(publisher, paths):
    source, target = paths
    publish(publisher, paths)
    before = target.read_bytes()
    text = source.read_text()
    text = text.replace(f"TURSO_AUTH_TOKEN={OPERATOR_TOKEN}", f"TURSO_AUTH_TOKEN='{OPERATOR_TOKEN}'")
    text = text.replace(f"{KEEPER_KEY}={KEEPER_TOKEN}", f'{KEEPER_KEY}="{OPERATOR_TOKEN}"')
    source.write_text(text)
    with pytest.raises(ValueError) as caught:
        publish(publisher, paths)
    assert OPERATOR_TOKEN not in str(caught.value)
    assert target.read_bytes() == before


def test_later_duplicate_that_copies_the_operator_token_is_rejected(publisher, paths):
    source, target = paths
    publish(publisher, paths)
    before = target.read_bytes()
    source.write_text(source.read_text() + f"{KEEPER_KEY}={OPERATOR_TOKEN}\n")
    with pytest.raises(ValueError) as caught:
        publish(publisher, paths)
    assert OPERATOR_TOKEN not in str(caught.value)
    assert target.read_bytes() == before


def test_blank_keeper_token_preserves_previous_file(publisher, paths):
    source, target = paths
    publish(publisher, paths)
    before = target.read_bytes()
    source.write_text(source.read_text().replace(f"{KEEPER_KEY}={KEEPER_TOKEN}", f"{KEEPER_KEY}="))
    with pytest.raises(ValueError) as caught:
        publish(publisher, paths)
    assert OPERATOR_TOKEN not in str(caught.value)
    assert KEEPER_TOKEN not in str(caught.value)
    assert target.read_bytes() == before


def test_cli_reports_failure_without_token_text(publisher, paths, monkeypatch, capsys):
    source, target = paths
    publish(publisher, paths)
    before = target.read_bytes()

    def boom(*_args, **_kwargs):
        raise ValueError(f"leaked {OPERATOR_TOKEN}")

    monkeypatch.setattr(publisher, "publish", boom)
    monkeypatch.setattr(publisher.os, "geteuid", lambda: 0)
    code = publisher.main([str(source), str(target)])
    captured = capsys.readouterr()
    blob = captured.out + captured.err
    assert code == 78
    assert blob.strip() == "service environment publication failed: ValueError"
    assert OPERATOR_TOKEN not in blob
    assert target.read_bytes() == before


def test_operator_contract_names_the_scoped_token_and_rollout():
    required = (CLOUD / "config/required-env.txt").read_text().splitlines()
    keys = [line.strip() for line in required if line.strip() and not line.lstrip().startswith("#")]
    assert KEEPER_KEY in keys
    example = (CLOUD / ".env.example").read_text()
    assert f"{KEEPER_KEY}=" in example
    assert MINT in example
    doc = " ".join((CLOUD.parent / "docs/subscription-tokens.md").read_text().split())
    assert KEEPER_KEY in doc
    assert MINT in doc
    assert "before this change merges" not in doc
    assert "before the publisher change merges" not in doc
    assert "next deploy stops" in doc
    assert "never logs or prints token values" in doc


def test_service_and_publication_lifecycle_contracts():
    unit = (CLOUD / "services/radon-subscription-tokens.service").read_text()
    assert "EnvironmentFile=" not in unit
    assert "scripts.model_gateway.refresh_cli" in unit
    assert "RADON_MODEL_CONTROL_SOCKET=/run/radon-model-control/control.sock" in unit
    assert "RADON_SUBSCRIPTION_VAULT_SOCKET=" not in unit
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


def _run_deploy_main_with_publication_state(tmp_path, *, stage, publication_state, already_green=False, journal_present=False):
    """Run real main/publication paths with only isolated command substitutes."""
    import shlex
    import subprocess

    target_sha = "a" * 40
    previous_sha = target_sha if already_green else "b" * 40
    calls = tmp_path / "calls"
    calls.touch()
    journal = tmp_path / "transition.json"
    if journal_present:
        journal.write_text("synthetic unfinished transition\n")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    helper = tmp_path / "deploy-root"
    helper.write_text('''#!/bin/bash
set -euo pipefail
printf 'helper:%s\\n' "$*" >> "$CALLS"
case "$1" in
  publish-mcp-env) exit 0 ;;
  publish-subscription-env)
    [[ "$PUBLICATION_STATE" == ready ]] && exit 0
    printf 'unknown action\\n' >&2
    exit 2 ;;
  commit-transition) exit 0 ;;
  *) exit 99 ;;
esac
''')
    helper.chmod(0o755)
    sudo = fake_bin / "sudo"
    sudo.write_text('''#!/bin/bash
set -euo pipefail
printf 'sudo:%s\\n' "$*" >> "$CALLS"
[[ "${1:-}" == -n ]] && shift
if [[ "${1:-}" == -l ]]; then
  shift
  [[ "${1:-}" == -- ]] && shift
  [[ "$1" == "$HELPER" && "$2" == publish-mcp-env ]]
  exit "$?"
fi
[[ "$1" == "$HELPER" ]] || exit 99
if [[ "$2" == publish-subscription-env && "$PUBLICATION_STATE" == old-grant ]]; then
  printf 'sudo: a password is required\\n' >&2
  exit 1
fi
exec "$@"
''')
    sudo.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "CALLS": str(calls), "HELPER": str(helper),
        "PUBLICATION_STATE": publication_state,
        "TARGET_SHA": target_sha, "PREVIOUS_SHA": previous_sha,
        "ALREADY_GREEN": "1" if already_green else "0",
        "RADON_DIR": str(tmp_path / "checkout"),
        "RADON_CLOUD_DIR": str(CLOUD),
        "RADON_RUNTIME": "host",
        "RADON_DEPLOY_STAGE": "1" if stage else "0",
        "RADON_DEPLOY_ROOT_HELPER": str(helper),
        "RADON_DEPLOY_ENV_FILE": str(tmp_path / "canonical.env"),
        "RADON_MCP_ENV_FILE": str(tmp_path / "mcp.env"),
        "RADON_DEPLOY_TRANSITION_JOURNAL": str(journal),
        "RADON_DEPLOY_GREEN_MARKER": str(tmp_path / "green"),
        "RADON_DEPLOY_PRESTAGE": str(tmp_path / "prestage"),
    }
    # Every downstream external operation is a harmless event recorder. Real
    # main(), write_mcp_env() and write_subscription_env() remain under test.
    shell = f'''
source {shlex.quote(str(CLOUD / 'scripts/deploy.sh'))}
event() {{ printf '%s\\n' "$*" >> "$CALLS"; }}
preflight_control_plane() {{ event control-plane; }}
preflight_env() {{ event env-preflight; }}
current_commit() {{ printf '%s\\n' "$PREVIOUS_SHA"; }}
git() {{
  case "$3" in
    fetch) event git-fetch ;;
    rev-parse) printf '%s\\n' "$TARGET_SHA" ;;
    *) return 99 ;;
  esac
}}
verify_tracked_drift_matches_target() {{ event drift-check; }}
green_marker_matches() {{ [[ "$ALREADY_GREEN" == 1 ]]; }}
build_staged_release() {{ event build; }}
write_prestage() {{ event prestage; }}
load_prestage() {{ return 1; }}
prepull_app_images() {{ event image-pull; }}
preflight_database() {{ event database-preflight; }}
restart_services() {{ event restart; }}
deploy_gate() {{ event gate; }}
write_transition_phase() {{ event transition-phase; }}
record_deploy_marker() {{ event deploy-marker; }}
write_green_marker() {{ event green-marker; }}
sync_scheduled_units() {{ event scheduled-sync; }}
publish_edge_config() {{ event edge-publish; }}
finalize_release_artifacts() {{ event finalize; }}
short_log() {{ printf synthetic; }}
notify_release_live() {{ event notification; }}
main "$TARGET_SHA"
'''
    result = subprocess.run(["bash", "-c", shell], env=env, capture_output=True, text=True, timeout=10)
    return result, calls.read_text().splitlines(), journal, helper


@pytest.mark.parametrize("publication_state", ["old-grant", "old-helper", "ready"])
@pytest.mark.parametrize("already_green", [False, True])
def test_prestage_does_not_require_or_invoke_subscription_publication(tmp_path, publication_state, already_green):
    result, calls, journal, _ = _run_deploy_main_with_publication_state(
        tmp_path, stage=True, publication_state=publication_state, already_green=already_green,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "helper:publish-mcp-env" in calls
    assert not any("publish-subscription-env" in call for call in calls)
    assert ("build" in calls) is not already_green
    assert ("prestage" in calls) is not already_green
    assert "restart" not in calls
    assert "transition-phase" not in calls
    assert not journal.exists()


@pytest.mark.parametrize("publication_state", ["old-grant", "old-helper", "ready"])
@pytest.mark.parametrize("already_green", [False, True])
def test_normal_deploy_requires_subscription_publication_even_for_green_release(tmp_path, publication_state, already_green):
    result, calls, journal, helper = _run_deploy_main_with_publication_state(
        tmp_path, stage=False, publication_state=publication_state, already_green=already_green,
    )
    publication_call = f"sudo:-n {helper} publish-subscription-env"
    assert publication_call in calls
    assert calls.index("helper:publish-mcp-env") < calls.index(publication_call)
    if publication_state != "ready":
        assert result.returncode != 0
        assert "Subscription service environment publication failed" in result.stdout + result.stderr
        assert "git-fetch" not in calls
        assert "build" not in calls
        assert "restart" not in calls
        assert "gate" not in calls
        assert "transition-phase" not in calls
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert calls.index("helper:publish-subscription-env") < calls.index("git-fetch")
        assert "gate" in calls
        assert ("restart" in calls) is not already_green
    assert not journal.exists()


def test_prestage_preserves_pending_journal_on_old_control_plane(tmp_path):
    result, calls, journal, _ = _run_deploy_main_with_publication_state(
        tmp_path, stage=True, publication_state="old-grant", journal_present=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert journal.read_text() == "synthetic unfinished transition\n"
    assert not any("publish-subscription-env" in call for call in calls)
    assert not {"build", "prestage", "restart", "transition-phase"}.intersection(calls)
