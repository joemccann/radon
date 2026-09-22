"""REL-188 (R-520, R-534, R-535, R-537): a phase is OK only on affirmative
evidence, uniformly across the five loops.

A `claude -p` round can exit 0 having done nothing — no commits, no ledger
advance, no PR — and every dead-man channel then said OK. Testing and security
each grew their own guard; the other three had none, security's marker match
could be satisfied by the agent quoting the marker mid-sentence, testing's
INCOMPLETE left rc 0, and a failed `gh issue` call inside report() was
indistinguishable from a dead runner.

2026-09-13: last-non-empty-line anchoring rejected an honest deliver stamp
when trailing Done/Next prose followed it (cycle 20260913T000007, PR 420).
The wrapper now takes the last dedicated marker LINE (prefix at line start)
and, for deliver, requires that line after a verdict line in the same round.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
WRAPPER = SCRIPTS / "security_nightly.sh"
SKILL = REPO / ".claude" / "skills" / "security-nightly" / "SKILL.md"
BASH = shutil.which("bash") or "/bin/bash"
COMMIT_EVIDENCE_LOOPS = (
    SCRIPTS / "reliability_weekend.sh",
    SCRIPTS / "testing_weekend.sh",
    SCRIPTS / "ci_performance_nightly.sh",
    SCRIPTS / "documentation_nightly.sh",
)
ALL_LOOPS = COMMIT_EVIDENCE_LOOPS + (WRAPPER, SCRIPTS / "security_deepsec_nightly.sh")

SEP13_DELIVER_LOG = """\
NIGHTLY DELIVER READY: loop=security prs=1 https://github.com/joemccann/radon/pull/420
SECURITY-NIGHTLY PHASE COMPLETE: deliver run_id=20260913-deliver
**Done**
- delivered
**Next**
- merge
"""

RECITAL_WITHOUT_MARKER_LINE = (
    "remember to print SECURITY-NIGHTLY PHASE COMPLETE: deliver "
    "run_id=20260913-deliver because the skill names it\n"
)

MARKER_WITHOUT_VERDICT = """\
SECURITY-NIGHTLY PHASE COMPLETE: deliver run_id=20260913-deliver
**Done**
- shipped
"""

NO_EVIDENCE = (
    "Suite at ~35%; I'll pick up when the background run completes.\n"
)


def _uncommented(path: Path) -> str:
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def _extract_named_fn(src: str, name: str) -> str:
    needle = f"{name}() {{"
    start = src.index(needle)
    depth = 0
    for i, ch in enumerate(src[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError(f"unclosed {name}")


def _marker_constants(src: str) -> str:
    lines = []
    for name in (
        "PHASE_COMPLETE_MARKER",
        "DELIVER_READY_MARKER",
        "DELIVER_INCOMPLETE_MARKER",
    ):
        match = re.search(rf'^{name}="[^"]+"', src, re.M)
        assert match, f"the wrapper lost {name}"
        lines.append(match.group(0))
    return "\n".join(lines)


def _run_phase_marker(
    tmp_path: Path,
    log_text: str,
    *,
    phase: str,
    prior_round: str = "",
) -> subprocess.CompletedProcess:
    """Drive the wrapper's own phase_marker_present against a fixture log.

    The wrapper is the source of truth: this extracts its constants and
    functions and does not reimplement the match.
    """
    src = WRAPPER.read_text(encoding="utf-8")
    helpers = [_marker_constants(src)]
    if "phase_marker_in_slice() {" in src:
        helpers.append(_extract_named_fn(src, "phase_marker_in_slice"))
    helpers.append(_extract_named_fn(src, "phase_marker_present"))
    log = tmp_path / "round.log"
    log.write_bytes((prior_round + log_text).encode("utf-8"))
    mark = len(prior_round.encode("utf-8"))
    driver = tmp_path / "phase_marker_driver.sh"
    driver.write_text(
        "\n".join(
            [
                "set -euo pipefail",
                *helpers,
                f'RUN_LOG="{log}"',
                f"ROUND_LOG_MARK={mark}",
                f'PHASE="{phase}"',
                "phase_marker_present",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [BASH, str(driver)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def _present(tmp_path: Path, log_text: str, *, phase: str, prior_round: str = "") -> bool:
    return _run_phase_marker(
        tmp_path, log_text, phase=phase, prior_round=prior_round
    ).returncode == 0


@pytest.mark.parametrize("wrapper", COMMIT_EVIDENCE_LOOPS, ids=lambda p: p.name)
def test_an_exit_zero_phase_with_no_commit_is_incomplete(wrapper: Path):
    body = _uncommented(wrapper)
    assert "phase_committed" in body, wrapper.name
    assert "PHASE_HEAD_BEFORE" in body and "PHASE_START_EPOCH" in body, wrapper.name
    assert "! phase_committed" in body, wrapper.name


@pytest.mark.parametrize("wrapper", ALL_LOOPS, ids=lambda p: p.name)
def test_incomplete_exits_nonzero(wrapper: Path):
    """launchd and the cycle exit code must not read an unfinished phase as
    success. Security already used 75; every loop does now."""
    body = _uncommented(wrapper)
    assert "RC=75" in body, wrapper.name
    idx = body.index("INCOMPLETE")
    assert "RC=75" in body[idx:], wrapper.name


def test_the_security_marker_is_a_line_start_match_not_a_whole_slice_grep():
    """R-535: a `grep -qF` over the whole round slice is satisfied by the
    agent RECITING the marker mid-sentence. Keep the prefix-at-line-start
    rule; do not require the marker to be the transcript's last line."""
    body = _uncommented(WRAPPER)
    start = body.index("PHASE_COMPLETE_MARKER")
    slice_ = body[start:]
    assert "phase_marker_present" in slice_, "the marker check is not factored out"
    fn_start = body.index("phase_marker_present() {")
    present_fn = _extract_named_fn(body, "phase_marker_present")
    assert "grep -qF" not in present_fn, present_fn
    assert 'PHASE_COMPLETE_MARKER"*' in body[fn_start:] or (
        "phase_marker_in_slice" in present_fn
    ), present_fn
    # The Sep 13 hole: last-non-empty-line-only rejects honest trailing prose.
    assert "tail -n 1" not in present_fn, present_fn


@pytest.mark.parametrize("wrapper", ALL_LOOPS, ids=lambda p: p.name)
def test_a_failed_gh_issue_call_is_logged(wrapper: Path):
    """R-537: `gh issue ... || true` swallowed an auth expiry, so a gh that
    can no longer post looks exactly like a runner that never fired."""
    body = _uncommented(wrapper)
    start = body.index("\nreport() {")
    end = body.index("\n}", start)
    report = body[start:end]
    assert "gh issue call failed" in report, wrapper.name


def test_sep13_ready_then_stamp_then_done_next_is_present(tmp_path):
    """2026-09-13 deliver: READY, PHASE COMPLETE, then Done/Next. The stamp
    is real; trailing operator-facing prose must not flip it to INCOMPLETE."""
    assert _present(tmp_path, SEP13_DELIVER_LOG, phase="deliver")


def test_sep12_stamp_as_the_final_line_is_still_present(tmp_path):
    log = (
        "NIGHTLY DELIVER READY: loop=security prs=1 "
        "https://github.com/joemccann/radon/pull/420\n"
        "SECURITY-NIGHTLY PHASE COMPLETE: deliver run_id=20260912-deliver\n"
    )
    assert _present(tmp_path, log, phase="deliver")


def test_a_mid_prose_recital_without_a_marker_line_is_absent(tmp_path):
    """R-535 still: naming the prefix inside a sentence is not a stamp."""
    ready = (
        "NIGHTLY DELIVER READY: loop=security prs=1 "
        "https://github.com/joemccann/radon/pull/420\n"
    )
    assert not _present(tmp_path, ready + RECITAL_WITHOUT_MARKER_LINE, phase="deliver")
    parked = (
        "I am not printing SECURITY-NIGHTLY PHASE COMPLETE: because the "
        "scan is still running\n"
    )
    assert not _present(tmp_path, parked, phase="audit")


def test_a_deliver_stamp_with_no_prior_verdict_is_absent(tmp_path):
    assert not _present(tmp_path, MARKER_WITHOUT_VERDICT, phase="deliver")


def test_a_deliver_stamp_before_the_verdict_is_absent(tmp_path):
    log = (
        "SECURITY-NIGHTLY PHASE COMPLETE: deliver run_id=20260913-deliver\n"
        "NIGHTLY DELIVER READY: loop=security prs=0\n"
        "**Done**\n"
    )
    assert not _present(tmp_path, log, phase="deliver")


def test_an_audit_stamp_then_done_next_is_present(tmp_path):
    log = (
        "SECURITY-NIGHTLY PHASE COMPLETE: audit run_id=20260913-audit\n"
        "**Done**\n"
        "- recorded OPERATOR_REQUIRED\n"
        "**Next**\n"
    )
    assert _present(tmp_path, log, phase="audit")


def test_exit_zero_with_no_affirmative_evidence_is_absent(tmp_path):
    """REL-188: no marker line, no verdict. Still not complete."""
    assert not _present(tmp_path, NO_EVIDENCE, phase="audit")
    assert not _present(tmp_path, NO_EVIDENCE, phase="deliver")
    assert not _present(tmp_path, "", phase="audit")


def test_a_prior_round_stamp_does_not_count_for_this_round(tmp_path):
    """R-426: only this round's slice. A leftover stamp before ROUND_LOG_MARK
    plus trailing prose in this round is still incomplete."""
    prior = (
        "NIGHTLY DELIVER READY: loop=security prs=1 "
        "https://github.com/joemccann/radon/pull/419\n"
        "SECURITY-NIGHTLY PHASE COMPLETE: deliver run_id=prior\n"
    )
    assert not _present(
        tmp_path, "**Done**\n- leftover\n", phase="deliver", prior_round=prior
    )


def test_an_incomplete_verdict_then_a_stamp_then_prose_is_present(tmp_path):
    log = (
        "NIGHTLY DELIVER INCOMPLETE: loop=security check=ci "
        "pr=https://github.com/joemccann/radon/pull/420\n"
        "SECURITY-NIGHTLY PHASE COMPLETE: deliver run_id=20260913-deliver\n"
        "**Next**\n"
        "- resume CI\n"
    )
    assert _present(tmp_path, log, phase="deliver")


def test_the_skill_requires_the_stamp_but_not_as_the_very_last_line():
    text = SKILL.read_text(encoding="utf-8")
    assert "SECURITY-NIGHTLY PHASE COMPLETE:" in text
    assert "very last line" not in text
    lowered = text.lower()
    assert "done/next" in lowered or "trailing" in lowered
    assert "after the verdict" in lowered or "after the deliver verdict" in lowered
