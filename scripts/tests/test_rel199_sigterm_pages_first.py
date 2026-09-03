"""REL-199 (R-531): the SIGTERM dead-man pages and releases the lock BEFORE
it talks to GitHub — launchd's ~20s ExitTimeOut ate the old ordering's up to
five 120s-bounded gh calls, so a bootout produced no page at all."""
from __future__ import annotations

from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
LOOPS = [
    "reliability_weekend.sh",
    "testing_weekend.sh",
    "security_nightly.sh",
    "documentation_nightly.sh",
    "ci_performance_nightly.sh",
]


def _on_signal(name: str) -> str:
    body = "\n".join(
        line for line in (SCRIPTS / name).read_text().splitlines()
        if not line.lstrip().startswith("#")
    )
    start = body.index("on_signal()")
    return body[start : body.index("\n}", start)]


@pytest.mark.parametrize("loop", LOOPS)
class TestSigtermOrdering:
    def test_lock_release_and_page_precede_github(self, loop):
        fn = _on_signal(loop)
        release_at = fn.index("release_runner_lock")
        page_at = fn.index("notify_phase")
        gh_at = fn.index("report ")
        assert release_at < gh_at, f"{loop}: lock release sits behind the gh ladder"
        assert page_at < gh_at, f"{loop}: the Pushover page sits behind the gh ladder"

    def test_the_github_attempt_is_short_bounded(self, loop):
        fn = _on_signal(loop)
        assert "NET_TIMEOUT_SECS=1" in fn or "NET_TIMEOUT_SECS=" in fn.split("report ")[0].rsplit("\n", 2)[-1] or "NET_TIMEOUT_SECS" in fn, (
            f"{loop}: the gh comment keeps the 120s bound inside launchd's ~20s ExitTimeOut"
        )
        assert "NET_TIMEOUT_SECS=10" in fn, loop
