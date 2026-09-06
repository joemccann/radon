"""Shared harness for the nightly-loop wrapper tests (T-481).

LADDER/LOOPS/the stub sandbox live here so test_weekend_model_ladder.py
and test_loop_session_limit.py both import a non-test module instead of
one test file exec_module-ing the other.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

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
        "git": '#!/bin/sh\n# REL-188: the wrapper calls a phase OK only on commit evidence, so the\n# stub reports a fresh HEAD and a current committer date.\ncase "$*" in\n  *"rev-parse HEAD"*) date +%s%N; exit 0 ;;\n  *"--format=%ct"*) date +%s; exit 0 ;;\nesac\nexit 0\n',
        "python3": "#!/bin/sh\nexit 0\n",
    }
    for name, body in stubs.items():
        exe = bin_dir / name
        exe.write_text(body, encoding="utf-8")
        exe.chmod(0o755)
    return bin_dir


def _run(
    tmp_path: Path,
    loop: str,
    phase: str,
    exhausted_models,
    ladder: str | None = None,
    exhausted_line: str = QUOTA_LINE,
):
    wrapper = LOOPS[loop]
    models_log = tmp_path / "models.txt"
    gh_log = tmp_path / "gh.log"
    exhausted = tmp_path / "exhausted.txt"
    exhausted.write_text("".join(f"{m}\n" for m in exhausted_models), encoding="utf-8")
    bin_dir = _stub_bin(
        tmp_path, models_log, exhausted, gh_log, exhausted_line=exhausted_line
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
