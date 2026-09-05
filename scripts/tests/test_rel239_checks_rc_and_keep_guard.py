"""REL-239 (R-644, R-657): the loop machinery's two executed residuals.

R-644: `gh pr checks` exiting non-zero with EMPTY stdout parsed as a fresh
empty check list (`json.loads("[]")` is not the CHECKS_UNAVAILABLE sentinel),
reset the consecutive-failure counter, and burned the full 3h deliver cap
(executed: expired-auth stub rc 1 / empty stdout -> `state: timeout
elapsed: 10800 polls: 181`). A non-zero rc is a failed query regardless of
what landed on stdout.

R-657: `keep="${posted##*issuecomment-}"` passes the whole posted URL through
when the `issuecomment-` marker is absent, so `--keep <url>` matches no
comment id and the prune deletes EVERYTHING, including the comment just
posted. An unparseable keep-id must mean: prune nothing. All five wrappers.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

import nightly_deliver as nd

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
WRAPPERS = (
    SCRIPTS / "reliability_weekend.sh",
    SCRIPTS / "testing_weekend.sh",
    SCRIPTS / "ci_performance_nightly.sh",
    SCRIPTS / "documentation_nightly.sh",
    SCRIPTS / "security_nightly.sh",
)


class _Proc:
    def __init__(self, returncode: int, stdout: str):
        self.returncode = returncode
        self.stdout = stdout


class TestNonZeroRcIsUnavailable:
    """R-644: the rc-path around the JSON-shape check."""

    def test_nonzero_rc_with_empty_stdout_is_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            nd.subprocess, "run", lambda *a, **kw: _Proc(1, "")
        )
        assert nd.gh_pr_checks(7) is nd.CHECKS_UNAVAILABLE

    def test_nonzero_rc_is_unavailable_regardless_of_stdout(self, monkeypatch):
        # Even syntactically valid JSON on stdout cannot rescue a failed
        # query: gh writes partial/handled output before dying too.
        monkeypatch.setattr(
            nd.subprocess,
            "run",
            lambda *a, **kw: _Proc(1, '[{"name": "ci", "bucket": "pass"}]'),
        )
        assert nd.gh_pr_checks(7) is nd.CHECKS_UNAVAILABLE

    def test_rc_zero_with_rows_still_parses(self, monkeypatch):
        monkeypatch.setattr(
            nd.subprocess,
            "run",
            lambda *a, **kw: _Proc(0, '[{"name": "ci", "bucket": "pass"}]'),
        )
        assert nd.gh_pr_checks(7) == [{"name": "ci", "bucket": "pass"}]

    def test_watch_over_real_gh_pr_checks_aborts_at_the_failure_bound(
        self, monkeypatch
    ):
        """The executed failure, end to end: every poll rc 1 / empty stdout.
        watch() must stop at the consecutive-failure bound, not cap_secs."""
        polls = {"n": 0}

        def _expired_auth(*a, **kw):
            polls["n"] += 1
            return _Proc(1, "")

        monkeypatch.setattr(nd.subprocess, "run", _expired_auth)
        t = {"now": 0.0}

        def clock():
            return t["now"]

        def sleep(secs):
            t["now"] += secs

        verdict = nd.watch(
            7, cap_secs=10800, interval=60,
            run_checks=nd.gh_pr_checks, clock=clock, sleep=sleep,
        )
        assert verdict["state"] == "timeout"
        assert polls["n"] == nd.MAX_CONSECUTIVE_QUERY_FAILURES, polls
        assert verdict["elapsed_secs"] < 10800, verdict
        assert "unavailable" in verdict["check"], verdict


def _prune_fn_body(wrapper: Path) -> str:
    text = wrapper.read_text(encoding="utf-8")
    start = text.index("prune_deadman_comments() {")
    return text[start : text.index("\n}", start) + 2]


def _run_prune(tmp_path: Path, wrapper: Path, posted: str) -> Path:
    """Run the wrapper's real prune fn with a logging TIMEOUT_BIN stub.

    The stub stands in for the whole delete pipeline: if it is never
    invoked, zero comments could have been deleted.
    """
    log = tmp_path / f"{wrapper.stem}-invoked.txt"
    stub = tmp_path / f"{wrapper.stem}-timeout-stub"
    stub.write_text(
        "#!/bin/sh\n" f'echo "$@" >> "{log}"\n' "exit 0\n", encoding="utf-8"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    script = (
        "set -u\n"
        f'REPO="{REPO}"\n'
        f'TIMEOUT_BIN="{stub}"\n'
        'GH_BIN="gh"\n'
        'PR_BRANCH_PREFIX="reliability/"\n'
        + _prune_fn_body(wrapper)
        + f'\nprune_deadman_comments "issue-url" "{posted}"\n'
    )
    proc = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True,
        env={**os.environ, "RADON_WEEKEND_SKIP_ISSUE_PRUNE": "0"},
    )
    assert proc.returncode == 0, (wrapper.name, proc.stderr)
    return log


@pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
class TestUnparseableKeepPrunesNothing:
    """R-657 contract across all five loop wrappers."""

    def test_marker_less_posted_url_deletes_zero_comments(
        self, tmp_path, wrapper: Path
    ):
        log = _run_prune(
            tmp_path, wrapper,
            "https://github.com/joemccann/radon/issues/9",
        )
        assert not log.exists(), (wrapper.name, "prune ran without a keep id")

    def test_empty_posted_deletes_zero_comments(self, tmp_path, wrapper: Path):
        log = _run_prune(tmp_path, wrapper, "")
        assert not log.exists(), wrapper.name

    def test_non_numeric_keep_suffix_deletes_zero_comments(
        self, tmp_path, wrapper: Path
    ):
        log = _run_prune(
            tmp_path, wrapper,
            "https://github.com/joemccann/radon/issues/9#issuecomment-",
        )
        assert not log.exists(), wrapper.name

    def test_parseable_keep_id_still_prunes(self, tmp_path, wrapper: Path):
        log = _run_prune(
            tmp_path, wrapper,
            "https://github.com/joemccann/radon/issues/9#issuecomment-12345",
        )
        assert log.exists(), (wrapper.name, "positive control: prune skipped")
        assert "--keep 12345" in log.read_text(), wrapper.name
