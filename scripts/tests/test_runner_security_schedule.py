"""The two Claude-only runner loops cannot share the subscription.

security and security-deepsec are the only loops with ALLOWED_AGENTS=claude.
Each phase is its own session, and the caps run in sequence, so one fire's
ceiling is the sum of those caps plus the hook and kill grace around them.
Two ceilings do not fit in one day. The calendars use different launchd
weekdays, and a live peer holding subscription-claude.lock skips the fire.
"""
from __future__ import annotations

import plistlib
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LOOPS = REPO / "scripts" / "runner" / "loops"
RUNNER = REPO / "scripts" / "runner" / "run_loop.sh"
INSTALL = REPO / "scripts" / "runner" / "install.sh"
BASH = "bash"

# fresh_clone retries three times and is not under timeout. Half an hour
# covers a slow clone without pretending the git call has a cap.
CLONE_SLACK_SECS = 30 * 60
HORIZON_DAYS = 14

# Clock times the other five loops already had. This change does not move them.
UNCHANGED = {
    "reliability": (0, 0),
    "testing": (0, 10),
    "ci-performance": (0, 20),
    "performance": (22, 0),
    "documentation": (3, 0),
}


def _assignments(loop: str) -> dict[str, str]:
    out = {}
    for line in (LOOPS / f"{loop}.env").read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key] = value.strip().strip('"').strip("'")
    return out


def _bounds() -> tuple[int, int, int, int]:
    text = RUNNER.read_text()
    hook = int(re.search(r'HOOK_SECS="\$\{RADON_RUNNER_HOOK_SECS:-(\d+)\}"', text).group(1))
    kill = int(re.search(r"\n  KILL_AFTER_SECS=(\d+)\n", text).group(1))
    hook_kill = int(re.search(r'TIMEOUT_BIN" -k (\d+) "\$HOOK_SECS"', text).group(1))
    resolver = int(re.search(r'TIMEOUT_BIN" (\d+) "\$RUNNER_PYTHON".*--rungs', text).group(1))
    return hook, kill, hook_kill, resolver


def ceiling_secs(env: dict[str, str]) -> int:
    """Wall-clock cap of one fire: every phase, both hooks, kill grace, one resolver."""
    hook, kill, hook_kill, resolver = _bounds()
    phases = env.get("PHASES", "").split()
    if phases:
        caps = [int(spec.split(":", 1)[1]) for spec in phases]
        per = kill + 2 * (hook + hook_kill)
        return sum(caps) + len(caps) * per + resolver + CLONE_SLACK_SECS
    return int(env["TIMEOUT_SECS"]) + kill + CLONE_SLACK_SECS


def _weekdays(env: dict[str, str]) -> list[int]:
    raw = env.get("SCHEDULE_WEEKDAYS", "")
    if not raw:
        return list(range(7))
    days = [int(part) for part in raw.split(",")]
    # launchd treats 0 and 7 as Sunday. Count that day once.
    return sorted({0 if day == 7 else day for day in days})


def _fires(env: dict[str, str], duration: int) -> list[tuple[int, int]]:
    hour = int(env["SCHEDULE_HOUR"])
    minute = int(env["SCHEDULE_MINUTE"])
    days = set(_weekdays(env))
    windows = []
    for index in range(HORIZON_DAYS):
        if index % 7 not in days:
            continue
        start = index * 86400 + hour * 3600 + minute * 60
        windows.append((start, start + duration))
    return windows


def _overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _gap(left: tuple[int, int], right: tuple[int, int]) -> int:
    if left[1] <= right[0]:
        return right[0] - left[1]
    if right[1] <= left[0]:
        return left[0] - right[1]
    return -1


def _plist(loop: str) -> dict:
    out = subprocess.run([BASH, str(INSTALL), "--print-plist", loop], check=True, capture_output=True)
    return plistlib.loads(out.stdout)


def _plist_fires(plist: dict) -> list[tuple[int, int, int]]:
    interval = plist["StartCalendarInterval"]
    rows = interval if isinstance(interval, list) else [interval]
    fires = []
    for row in rows:
        day = int(row["Weekday"]) if "Weekday" in row else -1
        fires.append((day, int(row["Hour"]), int(row["Minute"])))
    return fires


def test_security_ceilings_do_not_overlap_and_other_clocks_stay():
    envs = {path.stem: _assignments(path.stem) for path in sorted(LOOPS.glob("*.env"))}
    assert set(envs) == set(UNCHANGED) | {"security", "security-deepsec"}

    for loop, clock in UNCHANGED.items():
        assert (int(envs[loop]["SCHEDULE_HOUR"]), int(envs[loop]["SCHEDULE_MINUTE"])) == clock
        assert "SCHEDULE_WEEKDAYS" not in envs[loop]
        assert "SUBSCRIPTION_LOCK" not in envs[loop]
        assert not envs[loop].get("ALLOWED_AGENTS", "").split() or "claude" not in envs[loop]["ALLOWED_AGENTS"].split()
        assert _plist_fires(_plist(loop)) == [(-1, clock[0], clock[1])]

    security = envs["security"]
    deepsec = envs["security-deepsec"]
    assert security["ALLOWED_AGENTS"] == "claude"
    assert deepsec["ALLOWED_AGENTS"] == "claude"
    assert security["AGENTS"] == "claude:claude-opus-5 claude:claude-sonnet-5"
    assert deepsec["AGENTS"] == security["AGENTS"]
    assert (int(security["SCHEDULE_HOUR"]), int(security["SCHEDULE_MINUTE"])) == (0, 40)
    assert (int(deepsec["SCHEDULE_HOUR"]), int(deepsec["SCHEDULE_MINUTE"])) == (0, 50)
    # Sunday stays on security: the monthly full-repository refresh is that loop.
    assert _weekdays(security) == [0, 2, 4, 6]
    assert _weekdays(deepsec) == [1, 3, 5]
    assert security["SUBSCRIPTION_LOCK"] == deepsec["SUBSCRIPTION_LOCK"] == "claude"

    sec_cap = ceiling_secs(security)
    deep_cap = ceiling_secs(deepsec)
    sec_windows = _fires(security, sec_cap)
    deep_windows = _fires(deepsec, deep_cap)
    assert sec_windows and deep_windows

    gaps = [_gap(left, right) for left in sec_windows for right in deep_windows]
    assert min(gaps) >= 0, (sec_cap, deep_cap, min(gaps))
    for loop_windows in (sec_windows, deep_windows):
        for index, left in enumerate(loop_windows):
            for right in loop_windows[index + 1:]:
                assert not _overlaps(left, right), (left, right, sec_cap)

    assert _plist_fires(_plist("security")) == [(day, 0, 40) for day in (0, 2, 4, 6)]
    assert _plist_fires(_plist("security-deepsec")) == [(day, 0, 50) for day in (1, 3, 5)]


def test_ceiling_uses_every_phase_cap():
    env = _assignments("security")
    caps = [int(spec.split(":", 1)[1]) for spec in env["PHASES"].split()]
    assert caps == [28800, 21600, 10800]
    assert ceiling_secs(env) >= sum(caps)
    assert ceiling_secs(env) > 24 * 3600 / 2
