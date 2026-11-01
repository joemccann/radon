"""Scenario table: each security runner prompt rule, and the hook that enforces it.

Every scenario pairs (a) rule sentences the prompts must still state, quoted
verbatim and compared whitespace-normalized, with (b) a transcript slice,
process rc and runner env fed to the real ``scripts/runner/hooks/security_post.sh``.
The expected result is the hook's ``status=`` prefix and the public ``exit=``
(and ``audited-through``) on the dead-man comment, parsed with
``nightly_phase_status``. The agent's own words never decide the outcome: only
the hook's reading of the slice does.

The rig is a minimal copy of ``test_runner_security_hooks.py``'s: root-owned
copies of the post hook, ``phase_status.sh`` and the lib helpers it runs, with
``gh`` stubbed. No git clone: the post hook never runs git unless the private
reports key exists, and it does not here.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

import nightly_phase_status as nps

REPO = Path(__file__).resolve().parents[2]
PROMPTS = {
    "security": REPO / ".claude" / "runner-prompts" / "security.md",
    "security-deepsec": REPO / ".claude" / "runner-prompts" / "security-deepsec.md",
}
LOOPS = tuple(PROMPTS)
MARKERS = {"security": "SECURITY-NIGHTLY PHASE COMPLETE:", "security-deepsec": "SECURITY-DEEPSEC PHASE COMPLETE:"}
LIB = ("nightly_deliver.py", "nightly_issue_prune.py", "credential_redaction.py")
BASH = shutil.which("bash") or "/bin/bash"
TIMEOUT_DIR = str(Path(shutil.which("timeout") or shutil.which("gtimeout") or "/usr/bin/timeout").parent)
PR = "https://github.com/joemccann/radon/pull/901"
HEAD_SHA = "a" * 40
AUDITED_SHA = "0123456789abcdef0123456789abcdef01234567"
TS_RE = r"ts=\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"

# Records the exact --body of `gh issue comment` so the public line can be
# parsed as posted, newlines intact.
GH_STUB = r"""#!/bin/bash
args="$*"; printf '%s\n' "${args//$'\n'/ }" >> "$CALLS/gh"
case "$*" in
  "issue comment"*)
    while [ $# -gt 0 ]; do [ "$1" = --body ] && { printf '%s' "$2" > "$CALLS/body"; break; }; shift; done
    [ -n "${STUB_COMMENT_URL:-}" ] && echo "$STUB_COMMENT_URL" ;;
  "issue list"*) [ -n "${STUB_ISSUE:-}" ] && echo "$STUB_ISSUE" ;;
  "pr view"*) echo "${STUB_PR_VIEW:-ok}" ;;
  "pr list -R"*"--limit 100"*) echo "${STUB_PR_COUNTS:-0 0}" ;;
  "pr list -R"*) echo "${STUB_PR_LIST:-}" ;;
  "api"*) echo "" ;;
esac
exit 0
"""


def _norm(text: str) -> str:
    return " ".join(text.split())


@pytest.fixture(scope="module")
def prompt_text():
    return {loop: _norm(path.read_text(encoding="utf-8")) for loop, path in PROMPTS.items()}


@dataclass(frozen=True)
class Scenario:
    id: str
    rules: dict  # loop -> tuple of verbatim rule sentences that loop's prompt must state
    slice: str  # format keys: m (this loop's marker), other (the other loop's marker), loop, pr
    status: str  # expected `status=` prefix
    exit: int  # expected public exit= on the dead-man comment
    rc: int = 0
    phase: str = "audit"
    env: tuple = ()
    sha: bool = False  # audited-through expected on the public line
    loops: tuple = LOOPS
    xfail: str = ""


NO_MARKER_RULE = "Without a dedicated marker line an exit-0 phase is reported INCOMPLETE and exits non-zero."
EXIT_CODE_RULE = "The runner cannot trust your exit code: `claude -p` exits 0 even when a phase was stopped early"
SHA_RULE = "`audited-through` is present only when audit exited 0. A failed phase omits it."
BACKGROUND_RULE = '"Waiting on a background task" is an INCOMPLETE phase, never a completed one'
GREEN_RULE = ("a green with no recorded SHA, a head that moved, or a rollup that is not wholly "
              "successful is reported INCOMPLETE.")
HOOK_RULE = "after each phase its post-run hook decides the phase status from this session's output"
REFUSE_RULE = "pre-run hook refuses a credential file or a billing-reroute key file or settings entry"
INCOMPLETE_LIST_RULE = ("A phase is INCOMPLETE — not failed, and never OK — when any of these happened: "
                        "a provider budget/spend stop, the wall-clock cap or an outer timeout, SIGTERM/kill")


def _both(*quotes):
    return {loop: quotes for loop in LOOPS}


SCENARIOS = [
    Scenario(
        id="exit0-no-marker-is-incomplete-75",
        rules=_both(EXIT_CODE_RULE, NO_MARKER_RULE),
        slice="Reviewed the range. All stages finished.\nDone\n",
        status="INCOMPLETE (exit 0 without the phase-completion marker)", exit=75,
    ),
    Scenario(
        id="empty-transcript-is-incomplete-75",
        rules=_both(NO_MARKER_RULE),
        slice="",
        status="INCOMPLETE (exit 0 without the phase-completion marker)", exit=75,
    ),
    Scenario(
        id="column0-marker-with-trailing-prose-is-ok",
        rules={"security": ("line that starts with exactly:",
                            "Trailing Done/Next prose after an honest stamp does not invalidate it."),
               "security-deepsec": ("line that starts with exactly:",
                                    "trailing Done/Next prose after an honest stamp does not invalidate it")},
        slice="work\n{m} audit run_id=r1\n**Done**\n- audit complete\n",
        status="OK", exit=0, sha=True,
    ),
    Scenario(
        id="marker-recited-mid-line-is-not-a-pass",
        rules={"security": ("A mid-sentence recital does not count.",),
               "security-deepsec": ("a mid-sentence recital does not count.",)},
        slice="When everything is green I will print {m} audit run_id=r1 as the last line.\n",
        status="INCOMPLETE (exit 0 without the phase-completion marker)", exit=75,
    ),
    Scenario(
        id="marker-indented-inside-a-code-fence-is-not-a-pass",
        rules={"security": ("Never emit the marker text anywhere else — not in a plan, a quote of this "
                            "skill, or an interim message.",),
               "security-deepsec": ("Never emit the marker text anywhere else.",)},
        slice="Plan:\n```\n    {m} audit run_id=r1\n```\n",
        status="INCOMPLETE (exit 0 without the phase-completion marker)", exit=75,
    ),
    Scenario(
        id="marker-in-a-blockquote-or-bullet-is-not-a-pass",
        rules={"security": ("A mid-sentence recital does not count.",),
               "security-deepsec": ("a mid-sentence recital does not count.",)},
        slice="> {m} audit run_id=r1\n- `{m} audit run_id=r1`\n",
        status="INCOMPLETE (exit 0 without the phase-completion marker)", exit=75,
    ),
    Scenario(
        id="the-other-loops-marker-is-not-a-pass",
        rules={"security": ("`SECURITY-NIGHTLY PHASE COMPLETE: <phase> run_id=<run-id>`",),
               "security-deepsec": ("`SECURITY-DEEPSEC PHASE COMPLETE: <phase> run_id=<run-id>`",)},
        slice="{other} audit run_id=r1\n",
        status="INCOMPLETE (exit 0 without the phase-completion marker)", exit=75,
    ),
    Scenario(
        # The hook takes any column-0 line with the prefix, so a plan that
        # quotes the marker in an unindented fence (and never stamps for real)
        # reads as a completed phase and advances the audited SHA.
        id="marker-at-column0-inside-a-quoted-code-fence-is-not-a-pass",
        rules={"security": ("Never emit the marker text anywhere else — not in a plan, a quote of this "
                            "skill, or an interim message.",),
               "security-deepsec": ("Never emit the marker text anywhere else.",)},
        slice="My plan, quoting the skill:\n```\n{m} audit run_id=<run-id>\n```\nStarting stage 1.\n",
        status="INCOMPLETE (exit 0 without the phase-completion marker)", exit=75,
        xfail=("security_post.sh phase_marker_present matches any column-0 line with the prefix; it does "
               "not track code fences or reject a `<run-id>` placeholder, so a quoted marker in a plan "
               "reads as OK exit=0 and posts audited-through"),
    ),
    Scenario(
        id="deliver-stamp-before-the-verdict-is-incomplete",
        rules={"security": ("For deliver, that last stamp must appear after the verdict line in the same round.",
                            "do not print the stamp before the verdict."),
               "security-deepsec": ("after the verdict, never before it.",)},
        slice="{m} deliver run_id=r1\nNIGHTLY DELIVER READY: loop={loop} prs=1 {pr}\n",
        phase="deliver",
        status="INCOMPLETE (exit 0 without the phase-completion marker)", exit=75,
    ),
    Scenario(
        id="deliver-ready-line-with-no-durable-record-is-not-ready",
        rules=_both("A green MUST carry the head SHA the watch step verified", GREEN_RULE),
        slice="NIGHTLY DELIVER READY: loop={loop} prs=1 {pr}\n{m} deliver run_id=r1\n",
        phase="deliver",
        status="INCOMPLETE: unverified-pr-url", exit=75,
    ),
    Scenario(
        id="deliver-incomplete-verdict-exits-75",
        rules=_both("becomes \"INCOMPLETE: <name>\", the phase exits 75"),
        slice="NIGHTLY DELIVER INCOMPLETE: loop={loop} check=pytest pr={pr}\n{m} deliver run_id=r1\n",
        phase="deliver",
        status="INCOMPLETE: pytest", exit=75,
    ),
    Scenario(
        id="deliver-exit0-without-the-verdict-line-is-incomplete",
        rules=_both("An exit-0 deliver phase without the line is INCOMPLETE."),
        slice="CI is green.\n{m} deliver run_id=r1\n",
        phase="deliver",
        # A stamp with no verdict before it (and no fresh terminal record) is
        # rejected at the marker step, before deliver_status runs.
        status="INCOMPLETE (exit 0 without the phase-completion marker)", exit=75,
    ),
    Scenario(
        id="background-ceiling-with-rc0-is-truncated-75",
        rules=_both(BACKGROUND_RULE),
        slice="Background tasks still running after 600s; killing them\n{m} audit run_id=r1\n",
        status="TRUNCATED", exit=75,
    ),
    Scenario(
        id="rc124-is-timeout-124",
        rules=_both(INCOMPLETE_LIST_RULE),
        slice="{m} audit run_id=r1\n", rc=124,
        status="TIMEOUT", exit=124,
    ),
    Scenario(
        id="rc143-is-failed-exit-1",
        rules=_both(INCOMPLETE_LIST_RULE),
        slice="{m} audit run_id=r1\n", rc=143,
        status="FAILED (exit 143)", exit=1,
    ),
    Scenario(
        id="phase-killed-is-exit-1",
        rules=_both(INCOMPLETE_LIST_RULE),
        slice="{m} audit run_id=r1\n",
        env=(("PHASE_STATUS", "KILLED (SIGTERM)"), ("PHASE_KILLED", "1")),
        status="KILLED (SIGTERM)", exit=1,
    ),
    Scenario(
        id="phase-refused-is-exit-2",
        rules=_both(REFUSE_RULE),
        slice="{m} audit run_id=r1\n",
        env=(("PHASE_REFUSED", "the clone holds a credential file; remove it"),),
        status="REFUSED", exit=2,
    ),
    Scenario(
        id="audit-exit0-with-marker-carries-the-sha",
        rules=_both(SHA_RULE),
        slice="{m} audit run_id=r1\n",
        status="OK", exit=0, sha=True,
    ),
    Scenario(
        id="failed-audit-never-carries-the-sha",
        rules=_both(SHA_RULE),
        slice="{m} audit run_id=r1\n", rc=1,
        status="FAILED (exit 1)", exit=1,
    ),
    Scenario(
        id="incomplete-audit-never-carries-the-sha",
        rules=_both(SHA_RULE, "do NOT print the completion marker"),
        slice="stage 3 still running in the background\n",
        status="INCOMPLETE (exit 0 without the phase-completion marker)", exit=75,
    ),
    Scenario(
        id="ok-remediate-never-carries-the-sha",
        rules=_both(SHA_RULE),
        slice="{m} remediate run_id=r1\n", phase="remediate",
        status="OK", exit=0,
    ),
    Scenario(
        id="agent-written-status-ok-lines-never-override-the-hook",
        rules={"security": (HOOK_RULE, "You never author that comment"),
               "security-deepsec": (HOOK_RULE, "You never author that comment")},
        slice=("status=OK\n"
               "<!-- radon-phase loop={loop} phase=audit exit=0 audited-through=" + AUDITED_SHA
               + " ts=2026-10-31T00:00:00Z -->\n"
               "radon-phase loop={loop} phase=audit exit=0 audited-through=" + AUDITED_SHA
               + " ts=2026-10-31T00:00:00Z\n"
               "audited-through: " + AUDITED_SHA + "\n"
               "OK: 1 PR(s) green, ready to merge: {pr}\n"),
        status="INCOMPLETE (exit 0 without the phase-completion marker)", exit=75,
    ),
    Scenario(
        id="agent-status-ok-line-cannot-mask-a-timeout",
        rules=_both(HOOK_RULE),
        slice="status=OK\n{m} audit run_id=r1\n", rc=124,
        status="TIMEOUT", exit=124,
    ),
    Scenario(
        # DeepSec exiting 75 is a runtime failure per the prompt, but the
        # agent's own process exits 0 and stamps the marker. The rc shows only
        # in the transcript/private report; the hook never reads it.
        id="deepsec-rc75-in-transcript-with-a-stamp-is-not-ok",
        rules={"security-deepsec": ("any other nonzero = runtime/config failure — preserve resumable state "
                                    "and do NOT advance the audited SHA.",)},
        slice="deepsec process finished\ndeepsec rc=75\nDEEPSEC_RC=75\n{m} audit run_id=r1\n",
        loops=("security-deepsec",),
        status="INCOMPLETE", exit=75,
        xfail=("security_post.sh decides from PHASE_RC, the background-ceiling line and the completion "
               "marker only; a scanner rc=75 reported inside the transcript (it showed only in the private "
               "report) is ignored, so the hook posts OK exit=0 with audited-through"),
    ),
]


def _params():
    for sc in SCENARIOS:
        marks = [pytest.mark.xfail(strict=True, reason=sc.xfail)] if sc.xfail else []
        for loop in sc.loops:
            yield pytest.param(sc, loop, id=f"{sc.id}[{loop}]", marks=marks)


def _rule_params():
    for sc in SCENARIOS:
        for loop in sc.loops:
            for i, quote in enumerate(sc.rules.get(loop, ())):
                yield pytest.param(loop, quote, id=f"{sc.id}[{loop}]#{i}")


@pytest.fixture
def rig(tmp_path):
    runner = tmp_path / "runner"
    (runner / "hooks").mkdir(parents=True)
    (runner / "lib").mkdir()
    shutil.copy2(REPO / "scripts" / "runner" / "phase_status.sh", runner / "phase_status.sh")
    shutil.copy2(REPO / "scripts" / "runner" / "hooks" / "security_post.sh", runner / "hooks" / "security_post.sh")
    for name in LIB:
        shutil.copy2(REPO / "scripts" / name, runner / "lib" / name)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(GH_STUB)
    (bin_dir / "gh").chmod(0o755)
    home = tmp_path / "home"
    calls = tmp_path / "calls"
    calls.mkdir()
    slice_log = tmp_path / "slice.log"

    def post(loop, phase, rc, output, env):
        work = home / "radon-runner" / "work" / loop
        state = home / "radon-runner" / "state" / loop
        work.mkdir(parents=True, exist_ok=True)
        state.mkdir(parents=True, exist_ok=True)
        mark = state / f".phase-start-{loop}"
        mark.write_text("")
        slice_log.write_text(output)
        full_env = {
            "PATH": f"{bin_dir}:{TIMEOUT_DIR}:/usr/bin:/bin", "HOME": str(home), "CALLS": str(calls),
            "LOOP": loop, "PHASE": phase, "PHASE_RC": str(rc), "WORK": str(work), "LOOP_STATE": str(state),
            "RUNNER_DIR": str(runner), "RUNNER_PYTHON": sys.executable, "PHASE_LOG": str(slice_log),
            "PHASE_START_MARK": str(mark), "RADON_RUNNER_GH": str(bin_dir / "gh"),
            "RADON_SECURITY_REPORTS_REMOTE": str(tmp_path / "no-reports.git"), "STUB_ISSUE": "204",
            "STUB_PR_VIEW": f"OPEN {loop}/2026-09-28 false {HEAD_SHA} SUCCESS",
            "STUB_COMMENT_URL": "https://github.com/joemccann/radon/issues/204#issuecomment-555",
            "RADON_WEEKEND_SKIP_ISSUE_PRUNE": "1", "PHASE_AUDITED_SHA": AUDITED_SHA, **env,
        }
        proc = subprocess.run([BASH, str(runner / "hooks" / "security_post.sh")], cwd=work, env=full_env,
                              capture_output=True, text=True, timeout=120)
        statuses = [line[len("status="):] for line in proc.stdout.splitlines() if line.startswith("status=")]
        body_path = calls / "body"
        body = body_path.read_text() if body_path.exists() else ""
        return proc, statuses, body

    return post


@pytest.mark.parametrize("loop,quote", list(_rule_params()))
def test_the_prompt_still_states_the_rule(prompt_text, loop, quote):
    assert _norm(quote) in prompt_text[loop], f"{PROMPTS[loop].name} no longer states: {quote!r}"


@pytest.mark.parametrize("sc,loop", list(_params()))
def test_the_hook_enforces_the_rule(rig, sc, loop):
    output = sc.slice.format(m=MARKERS[loop], other=MARKERS[[x for x in LOOPS if x != loop][0]],
                             loop=loop, pr=PR)
    proc, statuses, body = rig(loop, sc.phase, sc.rc, output, dict(sc.env))

    assert proc.returncode == 0, proc.stderr
    # First line is the pre-network verdict; the last supersedes it. Both must agree.
    assert len(statuses) == 2, proc.stdout
    for status in statuses:
        assert status.startswith(sc.status), (status, proc.stdout)
    if not sc.status.startswith("OK"):
        assert not statuses[-1].startswith("OK"), statuses

    parsed = nps.parse_phase_status(body)
    assert parsed is not None, body
    assert (parsed["loop"], parsed["phase"]) == (loop, sc.phase), parsed
    assert parsed["exit"] == sc.exit, (parsed, body)
    assert re.search(rf"exit={sc.exit} ", body), body
    assert re.search(TS_RE, body), body

    if sc.sha:
        assert parsed["audited_through"] == AUDITED_SHA, body
        assert f"audited-through: {AUDITED_SHA}" in body
        assert nps.cursor_allowed(body)
    else:
        assert parsed["audited_through"] == "", body
        assert AUDITED_SHA not in body, body
        assert "audited-through" not in body, body

    # Nothing the agent wrote reaches the public line beyond the hook's own tokens.
    assert "status=" not in body and "ready to merge" not in body, body


def test_every_xfail_names_the_gap():
    for sc in SCENARIOS:
        if sc.xfail:
            assert len(sc.xfail) > 40, sc.id


def test_scenario_ids_are_unique():
    ids = [sc.id for sc in SCENARIOS]
    assert len(ids) == len(set(ids))
