"""Pending migrations apply before teardown, while the current release serves.

2026-11-14: 0112 (ten ALTER TABLE ADD COLUMN on marketdesk_pdf_queue) did
not finish inside radon-api's 20s ExecStartPre boot deadline. The first
deploy killed it mid-file; the next lost its Hrana stream on replay. Both
deploys (runs 43637794090, 43639529262) failed the post-restart gate and
rolled back. The boot path is bounded on purpose, so the deploy owns the
slow apply: a minutes-long bound before any unit stops, aborting cleanly
(no teardown, no rollback) when it fails.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

CLOUD_ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO_ROOT = CLOUD_ROOT.parent
DEPLOY = CLOUD_ROOT / "scripts" / "deploy.sh"
SHA = "a" * 40
PREV = "b" * 40


def _function_body(script: str, name: str) -> str:
    start = script.index(f"\n{name}() {{\n")
    end = script.index("\n}\n", start)
    return script[start:end]


def _apply(tmp_path: pathlib.Path, exit_code: int):
    calls = tmp_path / "calls"
    stage = tmp_path / "stage"
    stage.mkdir()
    shell = f"""
set -euo pipefail
source {DEPLOY}
STAGED_RELEASE_DIR={stage}
run_with_cloud_env() {{ printf '%s\\n' "$*" >> {calls}; return {exit_code}; }}
apply_target_migrations
printf 'teardown\\n' >> {calls}
"""
    result = subprocess.run(
        ["bash", "-c", shell],
        env={**os.environ, "RADON_DIR": str(tmp_path / "live")},
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result, calls.read_text(encoding="utf-8").splitlines(), stage


def test_target_release_migrations_apply_unbounded_by_the_boot_deadline(tmp_path):
    result, calls, stage = _apply(tmp_path, 0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls[-1] == "teardown"
    command = calls[0].split()
    # The TARGET release's migrations, not the serving checkout's.
    assert f"{stage}/scripts/db/migrate.py" in command
    assert "--boot" not in command
    timeout_at = command.index("timeout")
    assert int(command[timeout_at + 1]) >= 120, "a minutes-long bound, not the 20s boot deadline"
    # The marker radon-api reads on boot is the live checkout's.
    assert f"RADON_SCHEMA_MARKER_DIR={tmp_path / 'live'}/data" in command


def test_a_failed_migration_refuses_teardown(tmp_path):
    result, calls, _ = _apply(tmp_path, 75)
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert "teardown" not in calls
    assert "refusing teardown" in combined


def test_main_migrates_after_the_database_probe_and_before_any_teardown():
    main = _function_body(DEPLOY.read_text(encoding="utf-8"), "main")
    assert (
        main.index("preflight_database")
        < main.index("apply_target_migrations")
        < main.index('restart_services "$requested_sha" "$prev_commit"')
    )


def test_migration_failure_in_main_stops_nothing_and_does_not_roll_back(tmp_path):
    calls = tmp_path / "calls"
    shell = f"""
set -euo pipefail
source {DEPLOY}
event() {{ printf '%s\\n' "$*" >> {calls}; }}
preflight_control_plane() {{ return 0; }}
preflight_env() {{ return 0; }}
write_mcp_env() {{ return 0; }}
write_subscription_env() {{ return 0; }}
current_commit() {{ printf '%s\\n' {PREV}; }}
git() {{
  case "$*" in
    *"rev-parse origin/main"*) printf '%s\\n' {SHA} ;;
    *) return 0 ;;
  esac
}}
verify_tracked_drift_matches_target() {{ return 0; }}
green_marker_matches() {{ return 1; }}
load_prestage() {{ return 1; }}
build_staged_release() {{ event build; }}
prepull_app_images() {{ event prepull; }}
stage_model_clients() {{ event stage-clients; }}
preflight_database() {{ event database-probe; }}
apply_target_migrations() {{ event migrate; return 1; }}
restart_services() {{ event restart; }}
stop_services_for_transition() {{ event stop; }}
rollback() {{ event rollback; }}
deploy_gate() {{ event gate; }}
run_deploy_internal {SHA}
"""
    result = subprocess.run(
        ["bash", "-c", shell],
        env={**os.environ, "RADON_DIR": str(tmp_path / "live"), "RADON_DEPLOY_STAGE": "0"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    events = calls.read_text(encoding="utf-8").splitlines()
    assert events == ["build", "prepull", "stage-clients", "database-probe", "migrate"]
