"""nightly_issue_prune.py: decide whether to wipe a dead-man issue's old
comments, and the five wrappers' wiring into report().

Rule under test: an open PR for this loop means an operator still needs the
full run history, so nothing is pruned. No open PR (merged, closed, or never
opened) keeps the latest audit checkpoint and detailed/no-op report, plus the
just-posted wrapper status. Only superseded comments are deleted.
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
# The security loops' dead-man is posted and pruned by the runner's post-run hook.
HOOK = SCRIPTS / "runner" / "hooks" / "security_post.sh"


def _report_body() -> str:
    text = HOOK.read_text(encoding="utf-8")
    start = text.index("\nreport() {")
    return "\n".join(line for line in text[start:text.index("\n}", start)].splitlines()
                     if not line.lstrip().startswith("#"))


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

    def _fake_gh(self, tmp_path: Path, *, open_refs: list[str], comment_ids: list[str], bodies: dict[str, str] | None = None,
                 assoc: dict[str, str] | None = None) -> Path:
        log = tmp_path / "delete-log.txt"
        script = tmp_path / "fake-gh"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import sys, json\n"
            f"OPEN_REFS = {open_refs!r}\n"
            f"COMMENT_IDS = {comment_ids!r}\n"
            f"BODIES = {bodies or {}!r}\n"
            f"ASSOC = {assoc or {}!r}\n"
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
            "        print(json.dumps({'id': cid, 'body': BODIES.get(cid, ''),\n"
            "                          'author_association': ASSOC.get(cid, 'OWNER')}))\n"
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

    def test_keeps_latest_checkpoint_and_noop_report_without_a_pr(self, tmp_path):
        gh = self._fake_gh(
            tmp_path, open_refs=[], comment_ids=["101", "102", "103", "104", "105"],
            bodies={
                "101": "audited-through: aaaaaaa\nNO_SAFE_CHANGE",
                "102": "audited-through: bbbbbbb\nNO_SAFE_CHANGE",
                "103": "**audit** completed",
                "104": "**Issue discovered**\nNo actionable drift.\n**What was done to fix it**\nNothing this run.\n**Next**\nNothing to merge.",
                "105": "old wrapper status",
            },
        )
        proc = self._run(gh)
        assert proc.returncode == 0, proc.stderr
        assert sorted((tmp_path / "delete-log.txt").read_text().split()) == ["101", "103", "105"]

    def test_an_outsider_checkpoint_neither_survives_nor_evicts_the_real_one(self, tmp_path):
        gh = self._fake_gh(
            tmp_path, open_refs=[], comment_ids=["101", "102", "103"],
            bodies={
                "101": "audited-through: aaaaaaa",
                "102": "audited-through: bbbbbbb\nNO_SAFE_CHANGE",
                "103": "audited-through: ccccccc\nNO_SAFE_CHANGE",
            },
            assoc={"102": "NONE", "103": "CONTRIBUTOR"},
        )
        proc = self._run(gh)
        assert proc.returncode == 0, proc.stderr
        assert sorted((tmp_path / "delete-log.txt").read_text().split()) == ["102", "103"]

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
    """report() is the single chokepoint every phase status flows through."""

    def test_report_is_the_only_place_the_prune_is_wired(self):
        text = HOOK.read_text(encoding="utf-8")
        calls = [line for line in text.splitlines()
                 if "nightly_issue_prune.py" in line and not line.lstrip().startswith("#")]
        assert len(calls) == 1
        assert "nightly_issue_prune.py" in _report_body()

    def test_prune_is_the_root_installed_helper_isolated_bounded_and_never_fatal(self):
        body = _report_body()
        call = body[body.index('"$TIMEOUT_BIN" 30'):]
        assert '"$PY" -I "$RUNNER_DIR/lib/nightly_issue_prune.py"' in call
        assert '--branch-prefix "$PR_BRANCH_PREFIX"' in call
        assert "|| true" in call

    def test_prune_is_skippable(self):
        assert "RADON_WEEKEND_SKIP_ISSUE_PRUNE" in _report_body()


class TestFailClosed:
    """R-596/R-597/R-598 (P0): the prune is a DESTRUCTIVE delete gated on a
    network answer. 'gh failed' and 'this loop has no open PR' were the same
    empty list, so any gh outage wiped the operator's only record of a
    pending run. Unknown must mean: prune nothing."""

    def _gh(self, tmp_path: Path, body: str) -> Path:
        script = tmp_path / "fake-gh"
        script.write_text("#!/usr/bin/env python3\nimport sys, json\n" + body, encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return script

    def _run(self, gh_bin: Path, *, keep: str | None = None, timeout: str = "10"):
        argv = [
            sys.executable,
            str(SCRIPTS / "nightly_issue_prune.py"),
            "--gh-bin", str(gh_bin),
            "--issue", "42",
            "--branch-prefix", "reliability/",
            "--timeout", timeout,
        ]
        if keep is not None:
            argv += ["--keep", keep]
        return subprocess.run(argv, capture_output=True, text=True, timeout=60)

    def _listing_fails_gh(self, tmp_path: Path, *, failure: str) -> Path:
        # T-453: the timeout arm blocks on signal.pause() — held until the
        # CLI's own subprocess timeout kills it — instead of a real
        # time.sleep(30) that burned 10s of wall clock per run and let the
        # outer 60s harness cap raise TimeoutExpired BEFORE the assertions
        # under contention.
        log = tmp_path / "delete-log.txt"
        return self._gh(
            tmp_path,
            f"LOG = {str(log)!r}\n"
            "args = sys.argv[1:]\n"
            "if args[:2] == ['pr', 'list']:\n"
            + (
                "    sys.exit(1)\n" if failure == "nonzero"
                else "    import signal; signal.pause()\n"
            )
            + "elif args[:1] == ['api'] and args[1:3] == ['-X', 'DELETE']:\n"
            "    open(LOG, 'a').write(args[3].rsplit('/', 1)[-1] + '\\n')\n"
            "elif args[:1] == ['api']:\n"
            "    print('1'); print('2'); print('3')\n",
        )

    @pytest.mark.parametrize("failure", ["nonzero", "timeout"])
    def test_a_failed_pr_listing_deletes_nothing(self, tmp_path, failure):
        # `--timeout 0` expires against the signal-blocked gh immediately, so
        # the timeout path exercises the same TimeoutExpired branch in under a
        # second of wall clock.
        cli_timeout = "0" if failure == "timeout" else "10"
        proc = self._run(
            self._listing_fails_gh(tmp_path, failure=failure), timeout=cli_timeout
        )
        assert proc.returncode == 0, proc.stderr
        assert not (tmp_path / "delete-log.txt").exists(), proc.stderr
        assert "unknown" in proc.stderr

    def test_a_truncated_listing_page_deletes_nothing(self, tmp_path):
        log = tmp_path / "delete-log.txt"
        gh = self._gh(
            tmp_path,
            f"LOG = {str(log)!r}\n"
            "args = sys.argv[1:]\n"
            "limit = int(args[args.index('--limit') + 1]) if '--limit' in args else 30\n"
            "if args[:2] == ['pr', 'list']:\n"
            "    print(json.dumps([{'headRefName': 'other/%d' % i} for i in range(limit)]))\n"
            "elif args[:1] == ['api'] and args[1:3] == ['-X', 'DELETE']:\n"
            "    open(LOG, 'a').write(args[3].rsplit('/', 1)[-1] + '\\n')\n"
            "elif args[:1] == ['api']:\n"
            "    print('1')\n",
        )
        proc = self._run(gh)
        assert proc.returncode == 0, proc.stderr
        assert not (tmp_path / "delete-log.txt").exists(), proc.stderr

    @pytest.mark.parametrize("payload", ['not-json', '{"id":"1","body":null}', '{"id":"../2","body":"audit"}'])
    def test_unknown_comment_content_never_deletes_checkpoint_history(self, tmp_path, payload):
        log = tmp_path / "delete-log.txt"
        gh = self._gh(
            tmp_path,
            f"LOG = {str(log)!r}\n"
            "args = sys.argv[1:]\n"
            "if args[:2] == ['pr', 'list']:\n"
            "    print(json.dumps([]))\n"
            "elif args[:1] == ['api'] and args[1:3] == ['-X', 'DELETE']:\n"
            "    open(LOG, 'a').write('unexpected delete')\n"
            "elif args[:1] == ['api']:\n"
            f"    print({payload!r})\n",
        )
        proc = self._run(gh)
        assert proc.returncode == 0, proc.stderr
        assert not log.exists()
        assert "comment listing unknown" in proc.stderr

    def test_the_just_posted_comment_is_kept(self, tmp_path):
        log = tmp_path / "delete-log.txt"
        gh = self._gh(
            tmp_path,
            f"LOG = {str(log)!r}\n"
            "args = sys.argv[1:]\n"
            "if args[:2] == ['pr', 'list']:\n"
            "    print(json.dumps([]))\n"
            "elif args[:1] == ['api'] and args[1:3] == ['-X', 'DELETE']:\n"
            "    open(LOG, 'a').write(args[3].rsplit('/', 1)[-1] + '\\n')\n"
            "elif args[:1] == ['api']:\n"
            "    for cid in ['1', '2', '99']: print(json.dumps({'id': cid, 'body': ''}))\n",
        )
        proc = self._run(gh, keep="99")
        assert proc.returncode == 0, proc.stderr
        assert sorted((tmp_path / "delete-log.txt").read_text().split()) == ["1", "2"]


class TestWrapperPostBeforePrune:
    """R-612 (P0): the prune ran BEFORE a post whose failure was swallowed by
    `|| true`, so a gh outage during the post deleted the history and added
    nothing. The post must be confirmed first, and the new comment kept.
    Driven end to end in test_runner_security_hooks.py."""

    def test_prune_runs_only_after_a_confirmed_post(self):
        body = _report_body()
        assert body.index('issue comment "$issue"') < body.index("nightly_issue_prune.py")
        assert body.index('if [[ -z "$posted" ]]; then') < body.index("nightly_issue_prune.py")

    def test_prune_forwards_the_new_comment_id_to_keep(self):
        body = _report_body()
        assert '--keep "$keep"' in body
        assert '[[ "$keep" =~ ^[0-9]+$ ]] || return 0' in body


class TestDurableState:
    def test_checkpoint_and_latest_report_can_share_one_comment(self):
        comments = [
            {"id": "1", "author_association": "OWNER", "body": "audited-through: aaaaaaa"},
            {"id": "2", "author_association": "OWNER", "body": "audited-through: bbbbbbb\nNO_ACTIONABLE_DRIFT"},
            {"id": "3", "author_association": "OWNER", "body": "**deliver** 0 PR(s), nothing to merge"},
        ]
        assert prune.state_comment_ids(comments) == {"2"}

    def test_creation_order_not_listing_order_controls_authoritative_checkpoint(self):
        comments = [
            {"id": "20", "author_association": "OWNER", "body": "audited-through: bbbbbbb"},
            {"id": "9", "author_association": "OWNER", "body": "audited-through: aaaaaaa"},
        ]
        assert prune.state_comment_ids(comments) == {"20"}

    def test_quoted_placeholder_is_not_a_verified_checkpoint(self):
        assert prune.state_comment_ids([
            {"id": "1", "author_association": "OWNER", "body": "audited-through: <verified-origin-main-sha>"},
        ]) == set()

    def test_only_repository_insiders_hold_state(self):
        assert prune.state_comment_ids([
            {"id": "1", "author_association": "MEMBER", "body": "audited-through: aaaaaaa"},
            {"id": "2", "author_association": "COLLABORATOR", "body": "NO_SAFE_CHANGE"},
            {"id": "3", "author_association": "NONE", "body": "audited-through: bbbbbbb\nNO_SAFE_CHANGE"},
            {"id": "4", "body": "audited-through: ccccccc"},
        ]) == {"1", "2"}

    def test_standalone_noop_report_survives_without_a_repository_log(self):
        assert prune.state_comment_ids([
            {"id": "1", "author_association": "OWNER", "body": "NO_SAFE_CHANGE"},
            {"id": "2", "author_association": "OWNER", "body": "NIGHTLY PHASE NO-OP: loop=testing phase=audit no findings"},
        ]) == {"2"}
