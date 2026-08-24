"""The weekend runner's ground-truth fetch survives a transient uplink blip.

2026-08-23 10:11 PT: the remediate fire died in `ground_truth` on
`ssh: connect to host github.com port 22: Undefined error: 0` (NordVPN
blackholing port 22 for one run) before the agent ever started, and the ERR
trap was not yet inherited so the cycle vanished with no dead-man comment.
One fetch attempt at 00:00 is a single point of failure for the whole day.
"""
import os
import plistlib
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "scripts" / "reliability_weekend.sh"
PLIST = REPO / "config" / "com.radon.reliability-daily.plist"


def _fake_git(bin_dir: Path, fail_first: int) -> Path:
    calls = bin_dir / "calls"
    calls.write_text("0")
    shim = bin_dir / "git"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        f'n=$(cat "{calls}"); n=$((n + 1)); echo "$n" > "{calls}"\n'
        f"[[ $n -le {fail_first} ]] && exit 128\n"
        "exit 0\n"
    )
    shim.chmod(0o755)
    return calls


def _run_fetch(bin_dir: Path, attempts: int) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "RADON_WEEKEND_FETCH_ATTEMPTS": str(attempts),
        "RADON_WEEKEND_FETCH_PAUSE_SECS": "0",
    }
    return subprocess.run(
        ["bash", "-c", f'source "{WRAPPER}" --lock-lib-only; fetch_origin_with_retry'],
        capture_output=True,
        text=True,
        env=env,
    )


class TestGroundTruthFetchRetry:
    def test_a_transient_fetch_failure_is_retried_to_success(self, tmp_path):
        calls = _fake_git(tmp_path, fail_first=2)
        rc = _run_fetch(tmp_path, attempts=3)
        assert rc.returncode == 0, rc.stderr
        assert calls.read_text().strip() == "3"

    def test_a_dead_network_still_surfaces_after_the_bound(self, tmp_path):
        calls = _fake_git(tmp_path, fail_first=99)
        rc = _run_fetch(tmp_path, attempts=3)
        assert rc.returncode != 0
        assert calls.read_text().strip() == "3"

    def test_ground_truth_fetches_through_the_retry(self):
        text = WRAPPER.read_text()
        ground_truth = text[text.index("ground_truth() {") :]
        ground_truth = ground_truth[: ground_truth.index("}")]
        assert "fetch_origin_with_retry" in ground_truth
        assert "git fetch origin" not in ground_truth


class TestDailyPlistPath:
    def test_bun_is_reachable_from_launchd(self):
        # launchd does not source the shell profile; bun installs only to
        # ~/.bun/bin on the runner, and the loop's gates run vitest via bun.
        path = plistlib.loads(PLIST.read_bytes())["EnvironmentVariables"]["PATH"]
        assert "__HOME__/.bun/bin" in path.split(":")
