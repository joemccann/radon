"""nightly_issue_prune.py: decide whether to wipe a dead-man issue's old
comments, and the five wrappers' wiring into report().

Rule under test: an open PR for this loop means an operator still needs the
full run history, so nothing is pruned. No open PR (merged, closed, or never
opened) means there is nothing left to keep, so every existing comment is
deleted before the next one posts.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import nightly_issue_prune as prune

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
WRAPPERS = (
    SCRIPTS / "reliability_weekend.sh",
    SCRIPTS / "testing_weekend.sh",
    SCRIPTS / "ci_performance_nightly.sh",
    SCRIPTS / "documentation_nightly.sh",
    SCRIPTS / "security_nightly.sh",
)


class TestHasOpenPr:
    def test_true_when_a_head_ref_matches_the_prefix(self):
        assert prune.has_open_pr(
            ["documentation/2026-09-01", "reliability/2026-09-03"],
            branch_prefix="reliability/",
        )

    def test_false_when_no_head_ref_matches(self):
        assert not prune.has_open_pr(
            ["documentation/2026-09-01", "testing/2026-09-03"],
            branch_prefix="reliability/",
        )

    def test_false_when_no_open_prs_at_all(self):
        assert not prune.has_open_pr([], branch_prefix="reliability/")

    def test_does_not_match_a_different_loop_sharing_a_prefix_stem(self):
        # ci-performance/... must not satisfy a bare "ci/" prefix check.
        assert not prune.has_open_pr(["ci-performance/2026-09-03"], branch_prefix="ci/")


class TestCli:
    """Drives the real CLI against a fake `gh` script on disk so the
    subprocess wiring (argv shape, --jq parsing, DELETE calls) is covered,
    not just the pure decision function."""

    def _fake_gh(self, tmp_path: Path, *, open_refs: list[str], comment_ids: list[str]) -> Path:
        log = tmp_path / "delete-log.txt"
        script = tmp_path / "fake-gh"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import sys, json\n"
            f"OPEN_REFS = {open_refs!r}\n"
            f"COMMENT_IDS = {comment_ids!r}\n"
            f"LOG = {str(log)!r}\n"
            "args = sys.argv[1:]\n"
            "if args[:2] == ['pr', 'list']:\n"
            "    print(json.dumps([{'headRefName': r} for r in OPEN_REFS]))\n"
            "elif args[:1] == ['api'] and args[1:3] == ['-X', 'DELETE']:\n"
            "    comment_id = args[3].rsplit('/', 1)[-1]\n"
            "    with open(LOG, 'a') as f:\n"
            "        f.write(comment_id + '\\n')\n"
            "elif args[:1] == ['api']:\n"
            "    for cid in COMMENT_IDS:\n"
            "        print(cid)\n"
            "else:\n"
            "    sys.exit(1)\n",
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return script

    def _run(self, gh_bin: Path, *, issue: str = "42", branch_prefix: str = "reliability/"):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "nightly_issue_prune.py"),
                "--gh-bin", str(gh_bin),
                "--issue", issue,
                "--branch-prefix", branch_prefix,
                "--timeout", "10",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_open_pr_for_this_loop_deletes_nothing(self, tmp_path):
        gh = self._fake_gh(
            tmp_path,
            open_refs=["reliability/2026-09-03"],
            comment_ids=["1", "2", "3"],
        )
        proc = self._run(gh)
        assert proc.returncode == 0, proc.stderr
        assert not (tmp_path / "delete-log.txt").exists()

    def test_no_open_pr_deletes_every_comment(self, tmp_path):
        gh = self._fake_gh(
            tmp_path,
            open_refs=["documentation/2026-09-01"],
            comment_ids=["101", "102", "103"],
        )
        proc = self._run(gh)
        assert proc.returncode == 0, proc.stderr
        deleted = (tmp_path / "delete-log.txt").read_text().split()
        assert sorted(deleted) == ["101", "102", "103"]

    def test_no_open_prs_at_all_deletes_every_comment(self, tmp_path):
        gh = self._fake_gh(tmp_path, open_refs=[], comment_ids=["7"])
        proc = self._run(gh)
        assert proc.returncode == 0, proc.stderr
        assert (tmp_path / "delete-log.txt").read_text().split() == ["7"]

    def test_no_comments_to_delete_is_a_clean_no_op(self, tmp_path):
        gh = self._fake_gh(tmp_path, open_refs=[], comment_ids=[])
        proc = self._run(gh)
        assert proc.returncode == 0, proc.stderr
        assert not (tmp_path / "delete-log.txt").exists()

    def test_a_broken_gh_binary_still_exits_zero(self, tmp_path):
        gh = tmp_path / "broken-gh"
        gh.write_text("#!/usr/bin/env python3\nimport sys; sys.exit(1)\n", encoding="utf-8")
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        proc = self._run(gh)
        assert proc.returncode == 0, proc.stderr


class TestWrapperWiring:
    """report() is the single chokepoint every phase status flows through
    (see nightly_issue_format.py). The prune check belongs there, not
    scattered at call sites, so no code path can skip it by accident."""

    @pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
    def test_report_prunes_before_posting_the_new_comment(self, wrapper: Path):
        text = wrapper.read_text(encoding="utf-8")
        assert "prune_deadman_comments" in text, wrapper.name
        report_start = text.index("\nreport() {")
        report_end = text.index("\n}", report_start)
        body = text[report_start:report_end]
        assert "prune_deadman_comments" in body, wrapper.name
        prune_at = body.index("prune_deadman_comments")
        post_at = body.index('issue comment "$issue"')
        assert prune_at < post_at, wrapper.name

    @pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
    def test_prune_uses_the_isolated_origin_main_pipe(self, wrapper: Path):
        text = wrapper.read_text(encoding="utf-8")
        start = text.index("prune_deadman_comments() {")
        end = text.index("\n}", start)
        body = text[start:end]
        assert "origin/main:scripts/nightly_issue_prune.py" in body, wrapper.name
        assert "/usr/bin/python3 -I -" in body, wrapper.name
        assert "--branch-prefix" in body and "PR_BRANCH_PREFIX" in body, wrapper.name

    @pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
    def test_prune_is_bounded_and_never_fatal(self, wrapper: Path):
        text = wrapper.read_text(encoding="utf-8")
        start = text.index("prune_deadman_comments() {")
        end = text.index("\n}", start)
        body = text[start:end]
        assert "$TIMEOUT_BIN" in body, wrapper.name
        assert "|| true" in body, wrapper.name

    @pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
    def test_prune_is_skippable(self, wrapper: Path):
        text = wrapper.read_text(encoding="utf-8")
        start = text.index("prune_deadman_comments() {")
        end = text.index("\n}", start)
        body = text[start:end]
        assert "RADON_WEEKEND_SKIP_ISSUE_PRUNE" in body, wrapper.name
