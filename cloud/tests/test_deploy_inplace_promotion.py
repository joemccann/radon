"""A release with no runtime payload change must finalize without a restart.

2026-11-02, #1382 (docs only): restart_services took the payload-unchanged
branch, which never ran stop-clean (the only verb that cleared the previous
release's commit receipt) nor restart-managed (the only verb that exported
model clients for the new SHA). commit-transition then refused with
"committed release identity changed", and every later deploy refused to
recover the stranded journal until root repaired both by hand.
"""
import subprocess
from pathlib import Path

from test_deploy_corrections import (
    DEPLOY,
    ROOT_HELPER,
    TestRootHelper as _RootFixture,
)

CLOUD = Path(__file__).resolve().parents[1]
PREVIOUS = "1" * 40
TARGET = "2" * 40


def _helper(env, verb, sha):
    return subprocess.run(["bash", str(ROOT_HELPER), verb],
                          env={**env, "RADON_TEST_COMMIT_SHA": sha},
                          capture_output=True, text=True)


def _committed_previous_release(tmp_path):
    env, *_ = _RootFixture()._root_helper_fixture(tmp_path)
    calls = tmp_path / "model-client-calls"
    runtime = tmp_path / "model-client-runtime"
    runtime.write_text(f'#!/bin/bash\nprintf "%s\\n" "$*" >> {calls}\n')
    runtime.chmod(0o755)
    env["RADON_TEST_MODEL_CLIENT_RUNTIME"] = str(runtime)
    for verb in ("stop-clean", "recover", "commit-transition"):
        result = _helper(env, verb, PREVIOUS)
        assert result.returncode == 0, (verb, result.stderr)
    assert _helper(env, "verify-committed", PREVIOUS).stdout.strip() == PREVIOUS
    calls.write_text("")
    return env, calls


def test_stale_receipt_alone_refuses_finalization(tmp_path):
    """The failure #1382 hit: the previous receipt survives into the next commit."""
    env, _ = _committed_previous_release(tmp_path)
    result = _helper(env, "commit-transition", TARGET)
    assert result.returncode == 78
    assert "committed release identity changed" in result.stderr


def test_in_place_promotion_clears_receipt_and_exports_clients(tmp_path):
    env, calls = _committed_previous_release(tmp_path)
    promoted = _helper(env, "promote-in-place", TARGET)
    assert promoted.returncode == 0, promoted.stderr
    absent = _helper(env, "verify-committed", TARGET)
    assert absent.returncode == 3, "the previous release's receipt must be gone"
    committed = _helper(env, "commit-transition", TARGET)
    assert committed.returncode == 0, committed.stderr
    assert _helper(env, "verify-committed", TARGET).stdout.strip() == TARGET
    steps = calls.read_text().split()
    assert steps.index("install-model-clients") < steps.index("commit-model-clients")


def test_in_place_promotion_never_stops_a_service(tmp_path):
    env, _ = _committed_previous_release(tmp_path)
    systemctl_calls = tmp_path / "systemctl-calls"
    systemctl = tmp_path / "systemctl"
    systemctl.write_text(f'#!/bin/bash\nprintf "%s\\n" "$*" >> {systemctl_calls}\nexit 0\n')
    systemctl.chmod(0o755)
    result = _helper({**env, "RADON_TEST_SYSTEMCTL": str(systemctl)}, "promote-in-place", TARGET)
    assert result.returncode == 0, result.stderr
    issued = systemctl_calls.read_text() if systemctl_calls.exists() else ""
    assert not any(word in issued for word in ("stop", "restart", "kill"))


def test_payload_unchanged_deploy_promotes_in_place_after_checkout_moves(tmp_path):
    calls = tmp_path / "calls"
    shell = f"""set -euo pipefail
source {DEPLOY}
payload_paths_changed() {{ return 1; }}
prepare_release_transition() {{ printf 'prepare\\n' >> {calls}; }}
activate_staged_release() {{ printf 'activate\\n' >> {calls}; }}
refresh_control_plane() {{ printf 'refresh\\n' >> {calls}; }}
install_release_units() {{ printf 'units\\n' >> {calls}; }}
stop_services_for_transition() {{ printf 'STOP\\n' >> {calls}; }}
sudo() {{ printf 'sudo %s\\n' "$2" >> {calls}; }}
restart_services {TARGET} {PREVIOUS}
"""
    result = subprocess.run(["bash", "-c", shell], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    steps = calls.read_text().splitlines()
    assert "STOP" not in steps
    assert steps.index("prepare") < steps.index("activate") < steps.index("sudo promote-in-place")


def test_sudoers_grants_the_verb_exactly():
    sudoers = (CLOUD / "config" / "sudoers.d" / "radon-deploy").read_text()
    assert "/usr/local/sbin/radon-deploy-root promote-in-place," in sudoers or \
        sudoers.rstrip().endswith("/usr/local/sbin/radon-deploy-root promote-in-place")
