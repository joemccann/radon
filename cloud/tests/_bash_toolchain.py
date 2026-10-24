"""T-484: pin which bash the control-plane suites execute.

The darwin cloud "baseline" swung 33 -> 5 failures purely on whether
/opt/homebrew/bin preceded /bin on PATH, because these suites shell out to
scripts using bash>=4 features (mapfile, exec {fd}<>) while /bin/bash on
macOS is 3.2. Resolve a bash >= 4 explicitly once; suites that need it skip
with a named reason when none exists, so the recorded baseline is identical
regardless of PATH order.

The same rule covers the other host tools the control-plane scripts assume:
each `requires_*` below skips with a named reason instead of failing, so a
macOS baseline records "skipped: no flock" rather than a red node that the
Linux cloud-tests shard (the authority for these contracts) passes.
"""

import os
import shutil
import subprocess

import pytest


def _resolve_modern_bash():
    seen = []
    candidates = [
        shutil.which("bash"),
        "/opt/homebrew/bin/bash",
        "/usr/local/bin/bash",
        "/bin/bash",
    ]
    for candidate in candidates:
        if not candidate or candidate in seen or not os.path.exists(candidate):
            continue
        seen.append(candidate)
        try:
            probe = subprocess.run(
                [candidate, "-c", 'echo "${BASH_VERSINFO[0]}"'],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        major = probe.stdout.strip()
        if probe.returncode == 0 and major.isdigit() and int(major) >= 4:
            return candidate
    return None


MODERN_BASH = _resolve_modern_bash()

requires_modern_bash = pytest.mark.skipif(
    MODERN_BASH is None,
    reason=(
        "no bash >= 4 on this host (scripts use mapfile / exec {fd}<>); "
        "install one (e.g. brew install bash) to run this suite"
    ),
)

requires_flock = pytest.mark.skipif(
    shutil.which("flock") is None,
    reason=(
        "no flock(1) on this host (deploy.sh takes the deploy lock with it; "
        "util-linux ships it on Linux, macOS has none); the Linux cloud-tests "
        "shard is the authority for this contract"
    ),
)

requires_linux_test_binary = pytest.mark.skipif(
    not os.path.exists("/usr/bin/test"),
    reason=(
        "no /usr/bin/test on this host (grok_responder_account.py probes the "
        "service account through that absolute Linux path; macOS ships "
        "/bin/test); the Linux cloud-tests shard is the authority for this "
        "contract"
    ),
)
