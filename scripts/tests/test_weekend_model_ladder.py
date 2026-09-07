"""A model quota is per model, so an exhausted quota must drop a rung, not kill the night.

2026-09-01: the operator's global `~/.claude/settings.json` carried
`model: claude-fable-5[1m]`. The nightly wrappers passed no `--model`, so every
loop inherited it. That model's subscription quota was exhausted, and `claude
-p` printed one line and exited 1 in under a second:

    You're out of usage credits. Switch to another model, or manage usage
    credits at claude.ai/settings/usage?from=cc_cli_limit_message, to continue.

The security loop's audit AND remediate phases both died that way at 00:00,
paged FAILED twice, and audited nothing — while `claude --model opus` and
`--model sonnet` answered normally on the same Max login the whole time. A
global setting any interactive session can change was, in effect, a
single-point kill switch on all five unattended loops.

Two properties, asserted here for every loop:

- the wrapper PINS the model it asks for rather than inheriting settings.json;
- when a rung reports an exhausted quota it drops to the next rung and keeps
  going, walking the whole ladder before it gives up, and a drop does not
  consume one of the three transient-network attempts.

The stub agent records the `--model` it was handed and fails with the real
quota line for whichever rungs a test declares exhausted, so these assert the
sequence of models actually attempted, not the shape of the script.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash") or "/bin/bash"

# Every nightly loop wrapper. A loop missing here keeps the 2026-09-01
# failure mode.
LOOPS = {
    "reliability": REPO / "scripts" / "reliability_weekend.sh",
    "testing": REPO / "scripts" / "testing_weekend.sh",
    "ci-performance": REPO / "scripts" / "ci_performance_nightly.sh",
    "documentation": REPO / "scripts" / "documentation_nightly.sh",
    "security": REPO / "scripts" / "security_nightly.sh",
}

# Best first. The top rung is the operator's own default; the next is the same
# family one tier down, which is what 2026-09-01 needed and never got.
LADDER = [
    "claude-fable-5[1m]",
    "claude-opus-5[1m]",
    "claude-opus-5",
    "claude-sonnet-5",
]

QUOTA_LINE = (
    "You're out of usage credits. Switch to another model, or manage usage "
    "credits at claude.ai/settings/usage?from=cc_cli_limit_message, to continue."
)
RATE_LIMIT_LINE = "Request rejected (429)"
CAPACITY_LINE = "API Error: Repeated 529 Overloaded errors"
# Claude Code tool-skip categories. Official docs say retry the SAME model.
# Bare `overloaded` / `rate.limit` in is_quota_exhausted treated these as a
# dead rung and walked the ladder, including on timeout 124 whose log merely
# mentioned rate limits.
TOOL_SKIP_OVERLOADED = "Claude Code skipped a tool (overloaded)"
TOOL_SKIP_RATE_LIMITED = "Claude Code skipped a tool (rate-limited)"
CASUAL_RATE_LIMITS = "the 500 mentioned rate limits in a timeout log"
# The security wrapper refuses to call a phase OK without this; harmless noise
# for the other four.
COMPLETION = "SECURITY-NIGHTLY PHASE COMPLETE: audit"

MARKERS = (
    ".radon-weekend-runner",
    ".radon-security-runner",
    ".radon-reliability-runner",
    ".radon-testing-runner",
    ".radon-ci-performance-runner",
    ".radon-documentation-runner",
)


# 2026-09-06: the claude model ladder now exists in ONE loop. The other four
# left the claude.ai subscription entirely — it is reserved for the security
# loop — and run codex, then grok, then NVIDIA, then Cerebras. Their ladder
# behaviour is asserted in test_provider_failover.py; what stays here is the
# claude-rung behaviour, against the loop that still has claude rungs.
CLAUDE_LOOPS = ["security"]


def _clone(tmp_path: Path, wrapper: Path) -> Path:
    repo = tmp_path / "clone"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(wrapper, repo / "scripts" / wrapper.name)
    (repo / "scripts" / wrapper.name).chmod(0o755)
    for helper in ("weekend_notify.py", "weekend_redact.py"):
        (repo / "scripts" / helper).write_text("# stub\n", encoding="utf-8")
    for marker in MARKERS:
        (repo / marker).write_text("", encoding="utf-8")
    return repo


def _stub_bin(
    tmp_path: Path,
    models_log: Path,
    exhausted: Path,
    gh_log: Path,
    exhausted_line: str = QUOTA_LINE,
    exhausted_exit: int = 1,
) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # What the agent's OWN nested `claude` calls would inherit. The wrapper's
    # `--model` binds only the outer process; the security skill's Stage 4
    # Claude Security scan is a separate `claude` invocation and reads this.
    env_models = tmp_path / "env_models.txt"
    stubs = {
        "gh": (
            "#!/bin/sh\n"
            f'printf "%s\\n" "$*" >> "{gh_log}"\n'
            'if [ "$1 $2" = "issue list" ]; then echo 4242; fi\n'
            "exit 0\n"
        ),
        # Records the model it was handed, then behaves like a real agent whose
        # quota for that model is (or is not) gone.
        "claude": (
            "#!/bin/bash\n"
            'model=""\n'
            "while [ $# -gt 0 ]; do\n"
            '  if [ "$1" = "--model" ]; then model="$2"; shift 2; continue; fi\n'
            "  shift\n"
            "done\n"
            f'printf "%s\\n" "$model" >> "{models_log}"\n'
            f'printf "%s\\n" "${{RADON_WEEKEND_MODEL:-<unset>}}" >> "{env_models}"\n'
            f'if grep -qxF -- "$model" "{exhausted}"; then\n'
            f"  echo \"{exhausted_line}\"\n"
            f"  exit {exhausted_exit}\n"
            "fi\n"
            f'echo "{COMPLETION}"\n'
            "exit 0\n"
        ),
        "timeout": (
            "#!/bin/bash\n"
            "while [ $# -gt 0 ]; do\n"
            '  case "$1" in\n'
            "    -k|--kill-after) shift 2 ;;\n"
            "    *) shift; break ;;\n"
            "  esac\n"
            "done\n"
            'exec "$@"\n'
        ),
        "git": '#!/bin/sh\n# REL-188: the wrapper calls a phase OK only on commit evidence, so the\n# stub reports a fresh HEAD and a current committer date.\ncase "$*" in\n  *"rev-parse HEAD"*) date +%s%N; exit 0 ;;\n  *"--format=%ct"*) date +%s; exit 0 ;;\nesac\nexit 0\n',
        "python3": "#!/bin/sh\nexit 0\n",
    }
    for name, body in stubs.items():
        exe = bin_dir / name
        exe.write_text(body, encoding="utf-8")
        exe.chmod(0o755)
    return bin_dir


def _audit(
    tmp_path: Path,
    loop: str,
    exhausted_models,
    ladder: str | None = None,
    exhausted_line: str = QUOTA_LINE,
):
    return _run(
        tmp_path, loop, "audit", exhausted_models, ladder, exhausted_line=exhausted_line
    )


def _run(
    tmp_path: Path,
    loop: str,
    phase: str,
    exhausted_models,
    ladder: str | None = None,
    exhausted_line: str = QUOTA_LINE,
    exhausted_exit: int = 1,
):
    wrapper = LOOPS[loop]
    models_log = tmp_path / "models.txt"
    gh_log = tmp_path / "gh.log"
    exhausted = tmp_path / "exhausted.txt"
    exhausted.write_text("".join(f"{m}\n" for m in exhausted_models), encoding="utf-8")
    bin_dir = _stub_bin(
        tmp_path, models_log, exhausted, gh_log, exhausted_line=exhausted_line,
        exhausted_exit=exhausted_exit,
    )
    repo = _clone(tmp_path, wrapper)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "RADON_WEEKEND_REPO": str(repo),
    }
    if ladder is not None:
        env["RADON_WEEKEND_MODEL_LADDER"] = ladder
    proc = subprocess.run(
        [BASH, str(repo / "scripts" / wrapper.name), phase],
        cwd=repo, env=env, capture_output=True, text=True, timeout=180,
    )
    models = (
        models_log.read_text(encoding="utf-8").splitlines()
        if models_log.exists() else []
    )
    calls = gh_log.read_text(encoding="utf-8") if gh_log.exists() else ""
    return proc, models, calls


@pytest.mark.parametrize("loop", CLAUDE_LOOPS)
class TestTheLoopPinsItsModel:
    def test_the_wrapper_passes_an_explicit_model(self, loop):
        body = LOOPS[loop].read_text(encoding="utf-8")
        assert "--model" in body, (
            f"the {loop} loop inherits ~/.claude/settings.json for its model, "
            "so an interactive session changing the operator's default silently "
            "decides what the unattended night runs on, and an exhausted quota "
            "on that default kills the loop outright"
        )

    def test_the_default_ladder_is_the_agreed_order(self, loop):
        body = LOOPS[loop].read_text(encoding="utf-8")
        start = body.index("RADON_WEEKEND_MODEL_LADDER:-")
        default = body[start + len("RADON_WEEKEND_MODEL_LADDER:-"):body.index("}", start)]
        assert default.split() == LADDER, (default.split(), LADDER)


@pytest.mark.parametrize("loop", CLAUDE_LOOPS)
class TestAnExhaustedQuotaDropsARung:
    def test_it_drops_to_opus_1m_when_the_default_model_is_out(self, tmp_path, loop):
        proc, models, _calls = _audit(tmp_path, loop, [LADDER[0]])
        assert models[:2] == LADDER[:2], (
            f"{loop}: expected a drop to {LADDER[1]!r} after {LADDER[0]!r} "
            f"reported an exhausted quota; models attempted: {models!r}\n"
            f"{proc.stdout}{proc.stderr}"
        )
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)

    def test_it_walks_the_whole_ladder(self, tmp_path, loop):
        proc, models, _calls = _audit(tmp_path, loop, LADDER[:-1])
        assert models == LADDER, (models, proc.stdout, proc.stderr)
        # Four attempts is more than MAX_ATTEMPTS=3: a quota drop is not one of
        # the three transient-network retries and must not consume one.
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)

    def test_an_operator_ladder_overrides_the_default(self, tmp_path, loop):
        proc, models, _calls = _audit(
            tmp_path, loop, ["stub-a"], ladder="stub-a stub-b"
        )
        assert models == ["stub-a", "stub-b"], (models, proc.stdout, proc.stderr)
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)

    def test_every_rung_exhausted_stops_and_says_so(self, tmp_path, loop):
        proc, models, calls = _audit(tmp_path, loop, LADDER)
        assert models == LADDER, (
            f"{loop}: the ladder must be walked exactly once and then give up; "
            f"models attempted: {models!r}"
        )
        assert proc.returncode != 0, (proc.returncode, proc.stdout, proc.stderr)
        assert "all agent providers exhausted" in calls, (
            f"{loop}: the dead-man must name the cause, or the operator sees a "
            f"bare FAILED and re-fires into the same wall: {calls!r}"
        )
        assert "claude.ai/settings/usage" in calls, (
            f"{loop}: the dead-man must carry the one place this is fixed: {calls!r}"
        )

    def test_a_rate_limit_drops_a_rung_instead_of_exit_1(self, tmp_path, loop):
        proc, models, _calls = _audit(
            tmp_path, loop, [LADDER[0]], exhausted_line=RATE_LIMIT_LINE
        )
        assert models[:2] == LADDER[:2], (
            f"{loop}: a 429 rate limit on {LADDER[0]!r} must drop to "
            f"{LADDER[1]!r}, not exit 1; models attempted: {models!r}\n"
            f"{proc.stdout}{proc.stderr}"
        )
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)

    def test_a_capacity_overload_drops_a_rung_instead_of_exit_1(self, tmp_path, loop):
        proc, models, _calls = _audit(
            tmp_path, loop, [LADDER[0]], exhausted_line=CAPACITY_LINE
        )
        assert models[:2] == LADDER[:2], (
            f"{loop}: a 529 overload on {LADDER[0]!r} must drop to "
            f"{LADDER[1]!r}, not exit 1; models attempted: {models!r}\n"
            f"{proc.stdout}{proc.stderr}"
        )
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)

    def test_quota_matcher_is_the_cli_strings_not_bare_words(self, loop):
        body = LOOPS[loop].read_text(encoding="utf-8")
        # 2026-09-06: the patterns live in quota_regex(), one arm per
        # provider; the claude arm is the one this test has always been about.
        start = body.index("quota_regex()")
        fn = body[start:body.index("\n}\n", start)]
        assert "rate.limit" not in fn, (
            f"{loop}: bare rate.limit matches tool-skip (rate-limited) and "
            f"ordinary text: {fn}"
        )
        assert "rate_limit" not in fn, (
            f"{loop}: bare rate_limit is wider than Request rejected (429): {fn}"
        )
        assert re.search(r"(?<!529 )overloaded", fn, re.I) is None, (
            f"{loop}: bare overloaded matches tool-skip (overloaded); keep "
            f"'529 Overloaded' only: {fn}"
        )
        assert "529 Overloaded" in fn, fn
        assert "Request rejected \\(429\\)" in fn, fn
        assert "out of usage credits" in fn, fn
        assert "experiencing high load" in fn, fn

    @pytest.mark.parametrize(
        "line",
        (TOOL_SKIP_OVERLOADED, TOOL_SKIP_RATE_LIMITED, CASUAL_RATE_LIMITS),
    )
    def test_a_tool_skip_or_casual_mention_does_not_drop_a_rung(
        self, tmp_path, loop, line
    ):
        proc, models, calls = _audit(
            tmp_path, loop, [LADDER[0]], exhausted_line=line
        )
        assert models == [LADDER[0]], (
            f"{loop}: {line!r} on {LADDER[0]!r} must retry/fail that rung, "
            f"not walk the ladder; models attempted: {models!r}\n"
            f"{proc.stdout}{proc.stderr}"
        )
        assert proc.returncode != 0, (proc.returncode, proc.stdout, proc.stderr)
        assert "all model quotas exhausted" not in calls, (
            f"{loop}: a tool-skip or a log that merely mentions rate limits "
            f"was reported as every quota gone: {calls!r}"
        )


# DOC-044 (2026-09-01): every case above runs `audit`, where MAX_ROUNDS=1, so
# the continuation-round suppression each wrapper added for an exhausted
# ladder was never executed by a test. `remediate` is the mode with 8 rounds —
# the mode where losing the guard relaunches the whole ladder into the same
# wall eight times over.


@pytest.mark.parametrize("loop", CLAUDE_LOOPS)
class TestAnExhaustedLadderStopsTheRemediateRounds:
    def test_remediate_walks_the_ladder_once_not_once_per_round(
        self, tmp_path, loop
    ):
        proc, models, calls = _run(tmp_path, loop, "remediate", LADDER)
        assert models == LADDER, (
            f"{loop}: remediate must walk the ladder exactly once and stop. "
            f"Every rung is exhausted, so a continuation round can only "
            f"re-fire into the same wall; models attempted: {models!r}\n"
            f"{proc.stdout}{proc.stderr}"
        )
        assert proc.returncode != 0, (proc.returncode, proc.stdout, proc.stderr)
        assert "all agent providers exhausted" in calls, (
            f"{loop}: the remediate dead-man must name the cause too: {calls!r}"
        )


class TestTheSecurityScanInheritsTheWrappersRung:
    """DOC-042: #219 pinned the OUTER `claude -p` and stopped there.

    The security skill's Stage 4 spawns a SECOND, nested `claude` — the Claude
    Security agent scan, the longest and most expensive call of the night — and
    that one carried no `--model`, so it still resolved the operator's global
    `~/.claude/settings.json` default: the exact single-point kill switch that
    killed the 2026-09-01 night, surviving inside the loop that died to it.

    A `--model` flag binds one process, so the rung has to travel as
    environment. The wrapper exports it where the rung is chosen AND after
    every ladder drop, so a dropped or resumed round scans on the rung actually
    in force rather than the one the round started on.
    """

    SKILL = REPO / ".claude" / "skills" / "security-nightly" / "SKILL.md"
    SCAN_INVOCATION = "claude --agent claude-security:claude-security"

    def _stage4_block(self) -> str:
        skill = self.SKILL.read_text(encoding="utf-8")
        at = skill.index(self.SCAN_INVOCATION)
        return skill[skill.rindex("```sh", 0, at):skill.index("```", at)]

    def test_the_wrapper_exports_the_rung_it_is_running(self, tmp_path):
        proc, models, _calls = _audit(tmp_path, "security", [LADDER[0]])
        exported = (tmp_path / "env_models.txt").read_text(encoding="utf-8").splitlines()
        assert models == LADDER[:2], (models, proc.stdout, proc.stderr)
        assert exported == models, (
            "every round must export the rung it is about to run on, at the "
            "point the rung is chosen and again after each ladder drop — "
            f"exported {exported!r} for attempts {models!r}"
        )

    def test_the_skill_scan_command_passes_an_explicit_model(self):
        block = self._stage4_block()
        assert "--model" in block, (
            "the Stage 4 Claude Security scan inherits ~/.claude/settings.json "
            "for its model, so the night's longest call still hangs off the "
            "operator's global default that #219 removed from the wrapper"
        )
        assign = re.search(r'(\w+)="--model \$RADON_WEEKEND_MODEL"', block)
        assert assign, (
            "the scan's --model must be the wrapper's exported rung, not a "
            f"second model name that can drift from the ladder:\n{block}"
        )
        invocation = block[block.index(self.SCAN_INVOCATION):]
        assert f"${assign.group(1)}" in invocation, (
            "the model argument is built but never handed to the scan:\n"
            f"{invocation}"
        )


class TestAnExhaustedLadderIsAProviderSpendStop:
    """DOC-043: the wrapper reported a status its own skill rules out.

    `SKILL.md` classifies a provider budget/spend stop as INCOMPLETE — *not
    failed, and never OK* — carrying two facts the operator needs: the audited
    SHA was not advanced, and the next fire resumes the same private run. An
    exhausted model ladder is a provider spend stop, and it is resumable: the
    quota refills, nothing was audited, no state moved. Reporting it FAILED
    dropped it into the generic non-zero arm, the one arm that withholds the
    run log (rail 7, public repo) and states neither fact.

    Security only. The other four loops post a redacted log tail on their
    generic arm, so their operator already sees the quota line, none of their
    skills classify a spend stop, and `INCOMPLETE` is spoken for in the testing
    loop ("exited 0 without committing"). Repointing it there would overload a
    status that already means something else.
    """

    def test_it_reports_incomplete_and_exits_75(self, tmp_path):
        proc, models, calls = _audit(tmp_path, "security", LADDER)
        assert models == LADDER, (models, proc.stdout, proc.stderr)
        assert proc.returncode == 75, (
            "a spend stop is the skill's INCOMPLETE/resume case, which exits "
            f"75 like every other incomplete phase: {proc.returncode}"
        )
        assert "INCOMPLETE (all agent providers exhausted" in calls, calls
        assert "FAILED" not in calls, (
            "the skill says a provider budget/spend stop is never reported "
            f"failed: {calls!r}"
        )
        assert "**audit**" in calls and "**INCOMPLETE" in calls, calls
        assert "claude.ai/settings/usage" in calls, (
            "the dead-man must name the top-up URL: {calls!r}"
        )
        assert "Top up at claude.ai/settings/usage, then let the next fire resume." not in calls, (
            "wrapper comments are PHASE status, not the three-section Next: "
            f"{calls!r}"
        )
        assert "Do not read this as a finished run" not in calls, calls

    def test_the_dead_man_carries_the_resume_facts(self, tmp_path):
        _proc, _models, calls = _audit(tmp_path, "security", LADDER)
        assert "nothing was advanced" in calls, calls
        assert "retries" in calls, (
            "an exhausted quota refills, so the operator must be told the next "
            f"fire picks the same private run back up: {calls!r}"
        )
        assert "claude.ai/settings/usage" in calls, calls
