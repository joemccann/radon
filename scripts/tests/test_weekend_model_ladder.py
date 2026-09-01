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


def _stub_bin(tmp_path: Path, models_log: Path, exhausted: Path, gh_log: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
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
            f'if grep -qxF -- "$model" "{exhausted}"; then\n'
            f"  echo \"{QUOTA_LINE}\"\n"
            "  exit 1\n"
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
        "git": "#!/bin/sh\nexit 0\n",
        "python3": "#!/bin/sh\nexit 0\n",
    }
    for name, body in stubs.items():
        exe = bin_dir / name
        exe.write_text(body, encoding="utf-8")
        exe.chmod(0o755)
    return bin_dir


def _audit(tmp_path: Path, loop: str, exhausted_models, ladder: str | None = None):
    return _run(tmp_path, loop, "audit", exhausted_models, ladder)


def _run(
    tmp_path: Path,
    loop: str,
    phase: str,
    exhausted_models,
    ladder: str | None = None,
):
    wrapper = LOOPS[loop]
    models_log = tmp_path / "models.txt"
    gh_log = tmp_path / "gh.log"
    exhausted = tmp_path / "exhausted.txt"
    exhausted.write_text("".join(f"{m}\n" for m in exhausted_models), encoding="utf-8")
    bin_dir = _stub_bin(tmp_path, models_log, exhausted, gh_log)
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


@pytest.mark.parametrize("loop", sorted(LOOPS))
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


@pytest.mark.parametrize("loop", sorted(LOOPS))
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
        assert "all model quotas exhausted" in calls, (
            f"{loop}: the dead-man must name the cause, or the operator sees a "
            f"bare FAILED and re-fires into the same wall: {calls!r}"
        )
        assert "claude.ai/settings/usage" in calls, (
            f"{loop}: the dead-man must carry the one place this is fixed: {calls!r}"
        )


# DOC-044 (2026-09-01): every case above runs `audit`, where MAX_ROUNDS=1, so
# the continuation-round suppression each wrapper added for an exhausted
# ladder was never executed by a test. `remediate` is the mode with 8 rounds —
# the mode where losing the guard relaunches the whole ladder into the same
# wall eight times over.


@pytest.mark.parametrize("loop", sorted(LOOPS))
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
        assert "all model quotas exhausted" in calls, (
            f"{loop}: the remediate dead-man must name the cause too: {calls!r}"
        )
