"""`scripts/nightly_deliver.py`: the deliver phase's shared helper.

Every nightly loop's third phase pushes its dated branch, opens ONE PR, waits
for CI, fixes red checks, and hands the operator a green PR to merge. The
helper owns the deterministic parts so the five skills do not each re-invent
them: the verdict line the wrapper greps, the bounded `gh pr checks` poll,
and the resume record (branch + PR number) the next fire reads.

Stdlib only. Nothing here posts an issue comment or a Pushover page.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import github_pr_output as pr
import nightly_deliver as nd

REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "scripts" / "nightly_deliver.py"
URL1 = "https://github.com/joemccann/radon/pull/301"
URL2 = "https://github.com/joemccann/radon/pull/302"


class TestVerdictLine:
    def test_ready_line_carries_loop_count_and_urls(self):
        line = nd.ready_line("testing", [URL1, URL2])
        assert line == f"NIGHTLY DELIVER READY: loop=testing prs=2 {URL1} {URL2}"
        assert line.startswith(nd.READY_PREFIX)

    def test_ready_line_with_nothing_to_merge(self):
        assert nd.ready_line("documentation", []) == (
            "NIGHTLY DELIVER READY: loop=documentation prs=0"
        )

    def test_incomplete_line_names_the_failing_check(self):
        line = nd.incomplete_line("security", "pytest-scripts-npsz", URL1)
        assert line == (
            f"NIGHTLY DELIVER INCOMPLETE: loop=security check=pytest-scripts-npsz pr={URL1}"
        )
        assert line.startswith(nd.INCOMPLETE_PREFIX)

    def test_check_names_with_spaces_are_collapsed_to_one_token(self):
        # The wrapper splits the line on whitespace; a multi-word check name
        # must survive as one token.
        line = nd.incomplete_line("testing", "Python tests (scripts)", URL1)
        assert "check=Python-tests-(scripts)" in line

    @pytest.mark.parametrize("loop", ["bogus", "", "reliability-weekend"])
    def test_unknown_loop_is_refused(self, loop):
        with pytest.raises(ValueError):
            nd.ready_line(loop, [])


class TestNotifyStatus:
    """The one string every dead-man channel carries for the deliver phase."""

    def test_ready_is_n_prs_green_ready_to_merge(self):
        status = nd.notify_status(nd.ready_line("testing", [URL1, URL2]))
        assert status == f"2 PR(s) green, ready to merge: {URL1} {URL2}"

    def test_zero_prs_is_nothing_to_merge(self):
        assert nd.notify_status(nd.ready_line("security", [])) == "0 PR(s), nothing to merge"

    def test_incomplete_names_the_check(self):
        status = nd.notify_status(nd.incomplete_line("testing", "vitest", URL1))
        assert status == "INCOMPLETE: vitest"

    def test_no_verdict_is_incomplete(self):
        assert nd.notify_status("") == nd.NO_VERDICT_STATUS
        assert nd.NO_VERDICT_STATUS.startswith("INCOMPLETE")

    def test_last_verdict_line_wins(self):
        log = "\n".join([
            "chatter",
            nd.incomplete_line("testing", "vitest", URL1),
            "fixed and pushed",
            nd.ready_line("testing", [URL1]),
            "trailing",
        ])
        assert nd.notify_status(nd.last_verdict(log)) == f"1 PR(s) green, ready to merge: {URL1}"


class TestClassifyChecks:
    """`gh pr checks <n> --json name,bucket,link` rows -> green / red / pending."""

    def test_all_pass_or_skipped_is_green(self):
        rows = [
            {"name": "pytest", "bucket": "pass", "link": ""},
            {"name": "docs", "bucket": "skipping", "link": ""},
        ]
        assert nd.classify_checks(rows) == ("green", [])

    def test_one_failure_is_red_and_named(self):
        rows = [
            {"name": "pytest", "bucket": "pass", "link": ""},
            {"name": "vitest", "bucket": "fail", "link": "https://x/1"},
            {"name": "gitleaks", "bucket": "cancel", "link": "https://x/2"},
        ]
        state, failing = nd.classify_checks(rows)
        assert state == "red"
        assert [row["name"] for row in failing] == ["vitest", "gitleaks"]

    def test_pending_beats_green_but_not_red(self):
        rows = [
            {"name": "pytest", "bucket": "pass", "link": ""},
            {"name": "deploy", "bucket": "pending", "link": ""},
        ]
        state, pending = nd.classify_checks(rows)
        assert state == "pending"
        assert [row["name"] for row in pending] == ["deploy"]

    def test_no_checks_reported_yet_is_pending(self):
        assert nd.classify_checks([]) == ("pending", [])


class TestWatch:
    """Bounded poll. The clock and gh are injected so nothing sleeps for real."""

    def _runner(self, sequence):
        calls = []

        def run(pr_number):
            calls.append(pr_number)
            return sequence.pop(0) if sequence else sequence_last[0]

        sequence_last = [sequence[-1]]
        return run, calls

    def test_green_after_pending(self):
        run, calls = self._runner([
            [{"name": "pytest", "bucket": "pending", "link": ""}],
            [{"name": "pytest", "bucket": "pass", "link": ""}],
        ])
        clock = iter([0, 0, 60, 60, 120])
        slept = []
        verdict = nd.watch(
            301, cap_secs=600, interval=60, run_checks=run,
            clock=lambda: next(clock), sleep=slept.append,
        )
        assert verdict["state"] == "green"
        assert calls == [301, 301]
        assert slept == [60]

    def test_red_returns_immediately_with_the_failing_checks(self):
        run, calls = self._runner([
            [{"name": "vitest", "bucket": "fail", "link": "https://x/1"}],
        ])
        verdict = nd.watch(
            301, cap_secs=600, interval=60, run_checks=run,
            clock=lambda: 0, sleep=lambda _s: None,
        )
        assert verdict["state"] == "red"
        assert verdict["failing"] == [{"name": "vitest", "bucket": "fail", "link": "https://x/1"}]
        assert verdict["check"] == "vitest"
        assert calls == [301]

    def test_cap_while_pending_is_timeout_naming_the_pending_check(self):
        run, _calls = self._runner([
            [{"name": "deploy", "bucket": "pending", "link": ""}],
        ])
        clock = iter([0, 0, 500, 500, 1000, 1000, 1500])
        verdict = nd.watch(
            301, cap_secs=900, interval=500, run_checks=run,
            clock=lambda: next(clock), sleep=lambda _s: None,
        )
        assert verdict["state"] == "timeout"
        assert verdict["check"] == "deploy"

    def test_exit_codes_follow_the_state(self):
        assert nd.exit_code_for("green") == 0
        assert nd.exit_code_for("red") == 1
        assert nd.exit_code_for("timeout") == 3


class TestRecord:
    """The resume record: branch + PR number the next fire picks up."""

    def test_round_trip_and_private_location(self, tmp_path):
        path = nd.write_record(
            "reliability", root=tmp_path, branch="reliability/2026-09-02",
            pr=301, url=URL1, status="incomplete", check="vitest", run_id="20260902T000007",
        )
        assert path == tmp_path / ".reliability-deliver" / "record.json"
        assert (path.parent.stat().st_mode & 0o777) == 0o700
        record = nd.read_record("reliability", root=tmp_path)
        assert record["branch"] == "reliability/2026-09-02"
        assert record["pr"] == 301
        assert record["url"] == URL1
        assert record["status"] == "incomplete"
        assert record["check"] == "vitest"
        assert record["run_id"] == "20260902T000007"
        assert record["updated_at"]

    def test_missing_record_is_none(self, tmp_path):
        assert nd.read_record("testing", root=tmp_path) is None

    def test_green_record_clears_the_check(self, tmp_path):
        nd.write_record("testing", root=tmp_path, branch="testing/2026-09-02", pr=5,
                        url=URL1, status="incomplete", check="vitest")
        nd.write_record("testing", root=tmp_path, branch="testing/2026-09-02", pr=5,
                        url=URL1, status="green")
        assert nd.read_record("testing", root=tmp_path)["check"] is None

    def test_a_record_is_resumable_only_while_incomplete(self, tmp_path):
        nd.write_record("testing", root=tmp_path, branch="b", pr=5, url=URL1, status="green")
        assert nd.resumable("testing", root=tmp_path) is None
        nd.write_record("testing", root=tmp_path, branch="b", pr=5, url=URL1,
                        status="incomplete", check="vitest")
        assert nd.resumable("testing", root=tmp_path)["pr"] == 5


class TestEveryLoopIsAccepted:
    def test_the_helper_and_the_pr_formatter_agree_on_loop_names(self):
        assert set(nd.LOOPS) == set(pr.LOOP_TITLES)

    @pytest.mark.parametrize("loop", sorted(nd.LOOPS))
    def test_the_wrapper_slug_is_a_loop_both_helpers_accept(self, loop):
        wrappers = {
            "reliability": "reliability_weekend.sh",
            "testing": "testing_weekend.sh",
            "ci-performance": "ci_performance_nightly.sh",
            "documentation": "documentation_nightly.sh",
            "security": "security_nightly.sh",
        }
        text = (REPO / "scripts" / wrappers[loop]).read_text(encoding="utf-8")
        assert f'LOOP_SLUG="{loop}"' in text
        pr.format_pr_title(loop=loop, date="2026-09-02", issue="x")
        nd.ready_line(loop, [])


class TestCli:
    def _run(self, *argv, env=None):
        return subprocess.run(
            [sys.executable, str(HELPER), *argv],
            capture_output=True, text=True, check=False, env=env,
        )

    def test_verdict_ready(self):
        proc = self._run("verdict", "--loop", "testing", "--ready", URL1, URL2)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == nd.ready_line("testing", [URL1, URL2])

    def test_verdict_ready_with_no_urls(self):
        proc = self._run("verdict", "--loop", "security", "--ready")
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == nd.ready_line("security", [])

    def test_verdict_incomplete(self):
        proc = self._run("verdict", "--loop", "testing", "--incomplete", "vitest", "--pr-url", URL1)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == nd.incomplete_line("testing", "vitest", URL1)

    def test_verdict_refuses_an_unknown_loop(self):
        proc = self._run("verdict", "--loop", "bogus", "--ready")
        assert proc.returncode == 2

    def test_status_renders_the_operator_line(self):
        proc = self._run("status", "--line", nd.ready_line("testing", [URL1]))
        assert proc.stdout.strip() == f"1 PR(s) green, ready to merge: {URL1}"

    def test_record_and_show(self, tmp_path):
        env = {"PATH": "/usr/bin:/bin", "RADON_WEEKEND_ROOT": str(tmp_path)}
        proc = self._run(
            "record", "--loop", "testing", "--branch", "testing/2026-09-02",
            "--pr", "5", "--url", URL1, "--status", "incomplete", "--check", "vitest",
            env=env,
        )
        assert proc.returncode == 0, proc.stderr
        shown = self._run("show", "--loop", "testing", env=env)
        assert shown.returncode == 0, shown.stderr
        assert json.loads(shown.stdout)["pr"] == 5
        assert json.loads(shown.stdout)["resumable"] is True

    def test_show_without_a_record_is_empty_json(self, tmp_path):
        env = {"PATH": "/usr/bin:/bin", "RADON_WEEKEND_ROOT": str(tmp_path)}
        shown = self._run("show", "--loop", "testing", env=env)
        assert shown.returncode == 0
        assert json.loads(shown.stdout) == {"resumable": False}

    def test_watch_uses_gh_pr_checks_json(self, tmp_path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        gh_log = tmp_path / "gh.log"
        gh = bin_dir / "gh"
        gh.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$*" >> "{gh_log}"\n'
            'echo \'[{"name":"pytest","bucket":"pass","link":"https://x/1"}]\'\n'
            "exit 0\n",
            encoding="utf-8",
        )
        gh.chmod(0o755)
        env = {"PATH": f"{bin_dir}:/usr/bin:/bin"}
        proc = self._run("watch", "--pr", "301", "--cap-secs", "5", "--interval", "1", env=env)
        assert proc.returncode == 0, (proc.stdout, proc.stderr)
        verdict = json.loads(proc.stdout)
        assert verdict["state"] == "green"
        assert "pr checks 301 --json name,bucket,link" in gh_log.read_text(encoding="utf-8")


class TestWatchAbortsOnABrokenGh:
    """R-599/R-600 (P1): `gh_pr_checks` returned `[]` for both "no checks
    reported yet" and "gh could not run at all", so a broken `gh` (auth
    expiry, network) polled every 60s for the full 3h cap and then reported
    `no-checks-reported` — the deliver phase's entire budget burned learning
    nothing. A query FAILURE is not a pending state."""

    def _clock(self):
        t = {"now": 0.0}

        def clock():
            return t["now"]

        def sleep(secs):
            t["now"] += secs

        return clock, sleep

    def test_repeated_query_failures_abort_well_before_the_cap(self):
        clock, sleep = self._clock()
        calls = {"n": 0}

        def failing(_pr):
            calls["n"] += 1
            return nd.CHECKS_UNAVAILABLE

        verdict = nd.watch(
            7, cap_secs=10800, interval=60, run_checks=failing, clock=clock, sleep=sleep
        )
        assert verdict["state"] != "green"
        assert calls["n"] <= nd.MAX_CONSECUTIVE_QUERY_FAILURES + 1, calls
        assert verdict["elapsed_secs"] < 10800
        assert "gh" in verdict["check"] or "unavailable" in verdict["check"]

    def test_a_transient_failure_between_pending_polls_does_not_abort(self):
        clock, sleep = self._clock()
        seq = [
            nd.CHECKS_UNAVAILABLE,
            [{"name": "ci", "bucket": "pending"}],
            nd.CHECKS_UNAVAILABLE,
            [{"name": "ci", "bucket": "pass"}],
        ]

        def flaky(_pr):
            return seq.pop(0)

        verdict = nd.watch(
            7, cap_secs=10800, interval=60, run_checks=flaky, clock=clock, sleep=sleep
        )
        assert verdict["state"] == "green", verdict

    def test_an_empty_row_list_is_still_pending_not_a_failure(self):
        clock, sleep = self._clock()
        verdict = nd.watch(
            7, cap_secs=300, interval=60, run_checks=lambda _p: [],
            clock=clock, sleep=sleep,
        )
        assert verdict["state"] == "timeout"
        assert verdict["check"] == "no-checks-reported"

    def test_a_gh_timeout_yields_a_verdict_not_a_traceback(self, monkeypatch):
        def _boom(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="gh", timeout=120)

        monkeypatch.setattr(nd.subprocess, "run", _boom)
        assert nd.gh_pr_checks(7) is nd.CHECKS_UNAVAILABLE

    def test_a_nonzero_gh_with_no_json_is_unavailable_not_empty(self, monkeypatch):
        class _Proc:
            stdout = "gh: could not authenticate"
            returncode = 4

        monkeypatch.setattr(nd.subprocess, "run", lambda *a, **kw: _Proc())
        assert nd.gh_pr_checks(7) is nd.CHECKS_UNAVAILABLE


class TestDeliverStatusFromTheRecord:
    """R-611/R-613 (P1): the wrapper read its own verdict by grepping the
    agent's transcript, so a marker line quoted inside agent prose could be
    mistaken for the verdict, and a cap kill left nothing to resume from.
    The record file is the durable, unforgeable source."""

    def test_status_from_record_renders_the_operator_string(self, tmp_path):
        nd.write_record(
            "reliability", branch="reliability/2026-09-04", pr=301, url=URL1,
            status="green", root=tmp_path,
        )
        assert nd.status_from_record("reliability", root=tmp_path) == (
            f"1 PR(s) green, ready to merge: {URL1}"
        )

    def test_an_incomplete_record_names_the_failing_check(self, tmp_path):
        nd.write_record(
            "reliability", branch="reliability/2026-09-04", pr=301, url=URL1,
            status="incomplete", check="ci / gate", root=tmp_path,
        )
        assert nd.status_from_record("reliability", root=tmp_path) == "INCOMPLETE: ci / gate"

    def test_a_branch_only_record_is_reported_as_incomplete_but_resumable(self, tmp_path):
        """The wrapper writes this BEFORE launching the agent, so a cap kill
        mid-phase is genuinely resumable instead of leaving no trace."""
        nd.write_record(
            "reliability", branch="reliability/2026-09-04", pr=None, url=None,
            status="launched", root=tmp_path,
        )
        status = nd.status_from_record("reliability", root=tmp_path)
        assert status.startswith("INCOMPLETE"), status
        assert nd.resumable("reliability", root=tmp_path) is not None

    def test_no_record_at_all_says_so(self, tmp_path):
        assert nd.status_from_record("reliability", root=tmp_path) == (
            "INCOMPLETE (no deliver record — not resumable)"
        )


WRAPPERS = tuple(
    REPO / "scripts" / n for n in (
        "reliability_weekend.sh", "testing_weekend.sh", "ci_performance_nightly.sh",
        "documentation_nightly.sh", "security_nightly.sh",
    )
)


def _uncommented(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


class TestWrapperDeliverRecordContract:
    """R-611/R-613 (P1), all five loops."""

    @pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
    def test_a_branch_only_record_is_written_before_the_agent_starts(self, wrapper):
        body = _uncommented(wrapper.read_text(encoding="utf-8"))
        assert "arm_deliver_record" in body, wrapper.name
        assert "--status launched" in body, wrapper.name
        # Armed before the agent is launched, not after it. The launch is the
        # `claude -p "/<loop> $PHASE"` invocation; the loops differ over
        # whether the round loop is a function or inline in run_phase, so
        # anchor on the invocation itself.
        lines = body.splitlines()
        armed_at = next(
            i for i, l in enumerate(lines) if l.strip() == "arm_deliver_record"
        )
        # The launch is whichever comes first AFTER the arm point: the inline
        # `claude -p` invocation, or the `run_round` call for the loops that
        # factored the round loop into a function. Both must follow it.
        launch_at = next(
            i for i, l in enumerate(lines)
            if i > armed_at and ('claude -p "/' in l or l.strip() == "run_round")
        )
        assert armed_at < launch_at, wrapper.name
        # ...and nothing launches the agent before run_phase reaches the arm.
        before = "\n".join(lines[:armed_at])
        assert "\nrun_round\n" not in before, wrapper.name

    @pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
    def test_the_verdict_comes_from_the_record_not_the_transcript(self, wrapper):
        body = _uncommented(wrapper.read_text(encoding="utf-8"))
        start = body.index("deliver_status() {")
        end = body.index("\n}", start)
        fn = body[start:end]
        assert "deliver-status" in fn, wrapper.name
        assert "no deliver record" in fn, wrapper.name

    @pytest.mark.parametrize("wrapper", WRAPPERS, ids=lambda p: p.name)
    def test_the_record_read_uses_the_isolated_origin_main_pipe(self, wrapper):
        body = _uncommented(wrapper.read_text(encoding="utf-8"))
        for fn_name in ("deliver_status", "arm_deliver_record"):
            start = body.index(f"{fn_name}() {{")
            end = body.index("\n}", start)
            fn = body[start:end]
            assert "origin/main:scripts/nightly_deliver.py" in fn, (wrapper.name, fn_name)
            assert "/usr/bin/python3 -I -" in fn, (wrapper.name, fn_name)
