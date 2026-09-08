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
    # The four fallback loops drive codex and grok from a rendered prompt file
    # under .claude/portable-prompts; a clone without them has no usable rung.
    prompts = repo / ".claude" / "portable-prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    for src in (REPO / ".claude" / "portable-prompts").glob("*.md"):
        shutil.copy2(src, prompts / src.name)
    return repo


# A clone whose HEAD never moves: `git rev-parse HEAD` returns the same value
# the wrapper recorded at phase start, so phase_committed() is false.
_GIT_FROZEN = (
    "#!/bin/sh\n"
    'case "$*" in\n'
    '  *"rev-parse HEAD"*) echo frozen; exit 0 ;;\n'
    '  *"--format=%ct"*) echo 1; exit 0 ;;\n'
    "esac\n"
    "exit 0\n"
)


def _stub_bin(
    tmp_path: Path,
    models_log: Path,
    exhausted: Path,
    gh_log: Path,
    exhausted_line: str = QUOTA_LINE,
    exhausted_exit: int = 1,
    committed: bool = True,
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
        # `committed=False` is the phase that ran, printed a real report and
        # legitimately had nothing to commit: HEAD never moves. That is the
        # shape testing/audit and documentation/remediate had on 2026-09-08.
        "git": (
            '#!/bin/sh\n# REL-188: the wrapper calls a phase OK only on commit evidence, so the\n# stub reports a fresh HEAD and a current committer date.\ncase "$*" in\n  *"rev-parse HEAD"*) date +%s%N; exit 0 ;;\n  *"--format=%ct"*) date +%s; exit 0 ;;\nesac\nexit 0\n'
            if committed
            else _GIT_FROZEN
        ),
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


# --- provider ladder (2026-09-06) -------------------------------------------
# The four non-security loops left the claude.ai subscription: they run on
# codex, then grok, then NVIDIA and Cerebras (both hosted by the grok CLI, the
# only agent CLI here that speaks /chat/completions). The harness below stubs
# every provider binary and records the PROVIDER sequence attempted, not just
# the model — a ladder that silently stays on one provider is the bug these
# tests exist to catch.

# 2026-09-07: the codex and grok rungs name NO model. The vendor migrates its
# own account default forward (~/.codex/config.toml already rewrote gpt-5.4 ->
# gpt-5.6-terra), and the pinned id is what produced the 400 that killed three
# loops. nvidia and cerebras still name one because the grok CLI resolves them
# through a `[model."<key>"]` config block: the rung names that stable KEY and
# scripts/agent_cli_bootstrap.sh resolves the live id behind it.
FALLBACK_LADDER = [
    "codex",
    "grok",
    "nvidia:nvidia-latest",
    "cerebras:cerebras-latest",
]
CLAUDE_LADDER = ["claude:" + m for m in LADDER]

# Real cap lines, captured from the CLIs rather than invented.
CODEX_CAP_LINE = (
    "You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage "
    "to purchase more credits or try again at 7:52 PM."
)
CLAUDE_SESSION_CAP_LINE = (
    "You've hit your session limit \u00b7 resets 5am (America/Los_Angeles)"
)
GROK_CAP_LINE = "usage limit reached, resets in 2 hours"

PROVIDER_BINARY = {
    "claude": "claude",
    "codex": "codex",
    "grok": "grok",
    "nvidia": "grok",
    "cerebras": "grok",
}


# The real 400 that killed reliability, ci-performance and documentation on
# 2026-09-07. Not a cap and not a network blip: a permanent rejection of THIS
# rung, which must cost one rung and none of the transient-network attempts.
REJECTION_400_LINE = (
    'ERROR: {"type":"error","status":400,"error":{"type":'
    "\"invalid_request_error\",\"message\":\"The 'gpt-5.4' model is not "
    'supported when using Codex with a ChatGPT account."}}'
)
# The same text QUOTED by a crashing round rather than printed as the CLI's own
# final verdict. These loops audit their own wrappers and echo this string.
REJECTION_QUOTED_OUTPUT = (
    "Traceback (most recent call last):\n"
    '  File "audit.py", line 12, in <module>\n'
    "    " + REJECTION_400_LINE + "\n"
    '  File "audit.py", line 31, in check\n'
    "    raise SystemExit(1)\n"
    "SystemExit: 1"
)


def _provider_stub(
    attempts, capped, cap_line, cap_exit, rejected, reject_out, agent_output=None,
):
    """One stub body, shared by every provider binary.

    It derives its own provider from $0 plus GROK_HOME (the grok binary hosts
    nvidia and cerebras, and only the home directory tells them apart), so a
    wrapper that launches the right binary with the wrong home still fails.
    """
    return (
        "#!/bin/bash\n"
        'self="$(basename "$0")"\n'
        'prov="$self"\n'
        'case "${GROK_HOME:-}" in\n'
        "  *grok-home-nvidia) prov=nvidia ;;\n"
        "  *grok-home-cerebras) prov=cerebras ;;\n"
        "esac\n"
        'model=""\n'
        'args="$*"\n'
        "while [ $# -gt 0 ]; do\n"
        '  case "$1" in\n'
        '    --model|-m) model="$2"; shift 2; continue ;;\n'
        "  esac\n"
        "  shift\n"
        "done\n"
        'printf "%s\\t%s\\t%s\\n" "$prov" "$model" "$args" >> "' + str(attempts) + '"\n'
        'if grep -qxF -- "$prov" "' + str(rejected) + '"; then\n'
        '  cat "' + str(reject_out) + '"\n'
        "  exit 1\n"
        "fi\n"
        'if grep -qxF -- "$prov" "' + str(capped) + '"; then\n'
        '  case "$prov" in\n'
        '    claude) echo "' + (cap_line or CLAUDE_SESSION_CAP_LINE) + '" ;;\n'
        '    codex) echo "' + (cap_line or CODEX_CAP_LINE) + '" ;;\n'
        '    *) echo "' + (cap_line or GROK_CAP_LINE) + '" ;;\n'
        "  esac\n"
        "  exit " + str(cap_exit) + "\n"
        "fi\n"
        + "cat <<'RADON_AGENT_EOF'\n"
        + (COMPLETION if agent_output is None else agent_output)
        + "\nRADON_AGENT_EOF\n"
        + "exit 0\n"
    )


def _run_multi(
    tmp_path,
    loop,
    phase,
    capped_providers=(),
    provider_ladder=None,
    installed=("claude", "codex", "grok"),
    authed=("claude", "codex", "grok", "nvidia", "cerebras"),
    cap_line=None,
    cap_exit=1,
    reject_providers=(),
    reject_output=None,
    committed=True,
    agent_output=None,
):
    """Run one phase against stubbed provider CLIs.

    Returns (proc, attempts, calls) where `attempts` is the ordered list of
    "<provider>:<model>" strings the wrapper actually launched.
    """
    wrapper = LOOPS[loop]
    attempts = tmp_path / "attempts.tsv"
    gh_log = tmp_path / "gh.log"
    capped = tmp_path / "capped.txt"
    capped.write_text("".join(p + "\n" for p in capped_providers), encoding="utf-8")

    rejected = tmp_path / "rejected.txt"
    rejected.write_text("".join(p + "\n" for p in reject_providers), encoding="utf-8")
    reject_out = tmp_path / "reject_out.txt"
    reject_out.write_text(
        (REJECTION_400_LINE if reject_output is None else reject_output) + "\n",
        encoding="utf-8",
    )

    bin_dir = _stub_bin(
        tmp_path, tmp_path / "models.txt", capped, gh_log, committed=committed,
    )
    body = _provider_stub(
        attempts, capped, cap_line, cap_exit, rejected, reject_out,
        agent_output=agent_output,
    )
    for prov in ("claude", "codex", "grok"):
        exe = bin_dir / PROVIDER_BINARY[prov]
        if prov in installed:
            exe.write_text(body, encoding="utf-8")
            exe.chmod(0o755)
        elif exe.exists():
            exe.unlink()

    home = tmp_path / "home"
    for sub in (".claude", ".grok", ".codex"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    if "claude" in authed:
        (home / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")
    if "codex" in authed:
        (home / ".codex" / "auth.json").write_text("{}", encoding="utf-8")
    if "grok" in authed:
        (home / ".grok" / "auth.json").write_text("{}", encoding="utf-8")

    cli_root = tmp_path / "agent-cli"
    cli_root.mkdir(parents=True, exist_ok=True)
    lines = ["# stub"]
    for prov, key in (("nvidia", "NVIDIA_API_KEY"), ("cerebras", "CEREBRAS_API_KEY")):
        if prov in authed:
            (cli_root / ("grok-home-" + prov)).mkdir(parents=True, exist_ok=True)
            (cli_root / ("grok-home-" + prov) / "config.toml").write_text(
                '[model."stub"]\nbase_url = "http://127.0.0.1:1"\n', encoding="utf-8"
            )
            lines.append(key + "=stub-key")
    (cli_root / "env").write_text("\n".join(lines) + "\n", encoding="utf-8")

    prompts = tmp_path / "prompts"
    prompts.mkdir(exist_ok=True)
    for skill in ("reliability-weekend", "testing-weekend", "documentation-nightly",
                  "ci-performance", "security-nightly"):
        for ph in ("audit", "remediate", "deliver"):
            (prompts / (skill + "." + ph + ".md")).write_text("stub\n", encoding="utf-8")

    repo = _clone(tmp_path, wrapper)
    env = {
        "PATH": str(bin_dir) + ":/usr/bin:/bin",
        "HOME": str(home),
        "RADON_WEEKEND_REPO": str(repo),
        "RADON_AGENT_CLI_ROOT": str(cli_root),
        "RADON_PORTABLE_PROMPT_DIR": str(prompts),
        "RADON_WEEKEND_CODEX_BIN": str(bin_dir / "codex"),
        "RADON_WEEKEND_GROK_BIN": str(bin_dir / "grok"),
    }
    if provider_ladder is not None:
        env["RADON_WEEKEND_PROVIDER_LADDER"] = provider_ladder
    proc = subprocess.run(
        [BASH, str(repo / "scripts" / wrapper.name), phase],
        cwd=repo, env=env, capture_output=True, text=True, timeout=180,
    )
    tried = []
    argv = []
    if attempts.exists():
        for line in attempts.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                tried.append(parts[0] + ":" + parts[1])
            if len(parts) >= 3:
                argv.append(parts[2])
    calls = gh_log.read_text(encoding="utf-8") if gh_log.exists() else ""
    return proc, tried, calls, argv
