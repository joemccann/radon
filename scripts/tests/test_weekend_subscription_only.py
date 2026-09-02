"""Nightly loops bill the captain's claude.ai subscription, never an API key.

Claude Code prefers ANTHROPIC_API_KEY (and Bedrock/Vertex/auth-token reroutes)
over the machine's claude.ai login whenever one is visible. On 2026-09-01 that
moved a security scan onto metered API billing with nothing in the run record.

The security wrapper already unsets some of those variables, but it only
refuses two of them if they reappear in a gitignored env file, and the other
four loops do neither. Close the hole for every nightly wrapper: unset AND
refuse every billing-reroute / off-subscription variable, in the environment
or in a file the agent/scanners would reload themselves.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash") or "/bin/bash"

LOOPS = {
    "reliability": REPO / "scripts" / "reliability_weekend.sh",
    "testing": REPO / "scripts" / "testing_weekend.sh",
    "ci-performance": REPO / "scripts" / "ci_performance_nightly.sh",
    "documentation": REPO / "scripts" / "documentation_nightly.sh",
    "security": REPO / "scripts" / "security_nightly.sh",
}

# Every var that reroutes Claude Code off the claude.ai subscription. The
# security wrapper used to unset six of these and refuse only two in files.
BILLING_REROUTE_KEYS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_API_KEY",
    "CLAUDE_API_KEY",
    "AWS_BEARER_TOKEN_BEDROCK",
)
BILLING_REROUTE_FLAGS = (
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
)
BILLING_REROUTE_VARS = BILLING_REROUTE_KEYS + BILLING_REROUTE_FLAGS
TRUTHY_FLAG_VALUES = ("1", "true", "yes", "TRUE", "Yes")
FALSY_FLAG_VALUES = ("0", "false", "no")

KEY = "sk-ant-api03-CONTRACT-TEST-NOT-A-REAL-KEY"
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


def _stub_bin(tmp_path: Path, env_dump: Path, gh_log: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stubs = {
        "gh": (
            "#!/bin/sh\n"
            f'printf "%s\\n" "$*" >> "{gh_log}"\n'
            'if [ "$1 $2" = "issue list" ]; then echo 4242; fi\n'
            "exit 0\n"
        ),
        "claude": (
            "#!/bin/sh\n"
            f"env > '{env_dump}'\n"
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


def _audit(tmp_path: Path, loop: str, *, env_extra=None, env_file=None):
    env_dump = tmp_path / "agent-env.txt"
    gh_log = tmp_path / "gh.log"
    wrapper = LOOPS[loop]
    bin_dir = _stub_bin(tmp_path, env_dump, gh_log)
    repo = _clone(tmp_path, wrapper)
    if env_file is not None:
        path, body = env_file
        dest = repo / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body, encoding="utf-8")
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "RADON_WEEKEND_REPO": str(repo),
    }
    env.update(env_extra or {})
    proc = subprocess.run(
        [BASH, str(repo / "scripts" / wrapper.name), "audit"],
        cwd=repo, env=env, capture_output=True, text=True, timeout=120,
    )
    return proc, env_dump


@pytest.mark.parametrize("loop", sorted(LOOPS))
class TestTheWrapperUnsetsAndRefusesBillingReroutes:
    def test_the_wrapper_unsets_every_billing_reroute(self, loop):
        body = LOOPS[loop].read_text(encoding="utf-8")
        unset_idx = body.find("unset ANTHROPIC_API_KEY")
        assert unset_idx != -1, (
            f"{loop}: never unsets ANTHROPIC_API_KEY, so a hand-run from an "
            "operator shell that inherited a key bills metered API usage"
        )
        unset_block = body[unset_idx:body.find("\n\n", unset_idx)]
        for var in BILLING_REROUTE_VARS:
            assert var in unset_block, (
                f"{loop}: unsets some billing-reroute vars but not {var}; "
                "Claude Code prefers it over the claude.ai login"
            )

    def test_the_file_refuse_covers_every_billing_reroute(self, loop):
        body = LOOPS[loop].read_text(encoding="utf-8")
        for var in BILLING_REROUTE_VARS:
            assert var in body, (
                f"{loop}: {var} is never named, so a gitignored env file that "
                "holds it would silently reroute billing off the subscription"
            )


@pytest.mark.parametrize("loop", sorted(LOOPS))
class TestAnApiKeyInTheEnvironmentRefusesTheRun:
    @pytest.mark.parametrize("var", BILLING_REROUTE_KEYS)
    def test_a_set_billing_reroute_var_refuses_before_the_agent(self, tmp_path, loop, var):
        proc, env_dump = _audit(tmp_path, loop, env_extra={var: KEY})
        out = proc.stdout + proc.stderr
        assert proc.returncode == 2, (proc.returncode, out)
        assert "REFUSING" in out, out
        assert var in out, (
            f"{loop}: the refusal must name {var} so the operator knows "
            f"what to unset: {out!r}"
        )
        assert KEY not in out, f"{loop}: the refusal echoed the secret: {out!r}"
        assert not env_dump.exists(), (
            f"{loop}: the agent ran after a billing-reroute var was set"
        )


@pytest.mark.parametrize("loop", sorted(LOOPS))
class TestATruthyUseFlagRefusesTheRun:
    @pytest.mark.parametrize("var", BILLING_REROUTE_FLAGS)
    @pytest.mark.parametrize("value", TRUTHY_FLAG_VALUES)
    def test_a_truthy_use_flag_refuses_before_the_agent(
        self, tmp_path, loop, var, value
    ):
        proc, env_dump = _audit(tmp_path, loop, env_extra={var: value})
        out = proc.stdout + proc.stderr
        assert proc.returncode == 2, (proc.returncode, out)
        assert "REFUSING" in out, out
        assert var in out, (
            f"{loop}: the refusal must name {var} so the operator knows "
            f"what to unset: {out!r}"
        )
        assert not env_dump.exists(), (
            f"{loop}: the agent ran after {var}={value} rerouted billing"
        )


@pytest.mark.parametrize("loop", sorted(LOOPS))
class TestAFalsyUseFlagStillRuns:
    @pytest.mark.parametrize("var", BILLING_REROUTE_FLAGS)
    @pytest.mark.parametrize("value", FALSY_FLAG_VALUES)
    def test_a_falsy_use_flag_is_subscription_only(self, tmp_path, loop, var, value):
        proc, env_dump = _audit(tmp_path, loop, env_extra={var: value})
        assert proc.returncode == 0, (
            f"{loop}: {var}={value} locks Bedrock/Vertex OFF, which is "
            f"subscription-only, so the run must proceed: "
            f"{proc.returncode} {proc.stdout}{proc.stderr}"
        )
        assert env_dump.exists(), (
            f"{loop}: {var}={value} refused the agent; a shell profile that "
            "sets the flag to 0/false is not a billing reroute"
        )


@pytest.mark.parametrize("loop", sorted(LOOPS))
class TestABillingRerouteInAnIgnoredEnvFileRefusesTheRun:
    @pytest.mark.parametrize(
        "path,line",
        (
            (".env.local", f"ANTHROPIC_API_KEY={KEY}\n"),
            (".deepsec/.env.local", "CLAUDE_CODE_USE_BEDROCK=1\n"),
            (".env.local", "CLAUDE_CODE_API_KEY=sk-ant-alias\n"),
        ),
    )
    def test_a_key_file_the_agent_would_reload_refuses(self, tmp_path, loop, path, line):
        proc, env_dump = _audit(tmp_path, loop, env_file=(path, line))
        out = proc.stdout + proc.stderr
        assert proc.returncode == 2, (proc.returncode, out)
        assert "REFUSING" in out, out
        assert path in out, (
            f"{loop}: the refusal must name {path} so the operator knows "
            f"which file to clear: {out!r}"
        )
        assert KEY not in out, f"{loop}: the refusal echoed the secret: {out!r}"
        assert not env_dump.exists(), (
            f"{loop}: the agent ran after a billing-reroute file was present"
        )

    def test_a_commented_out_key_line_still_runs(self, tmp_path, loop):
        proc, env_dump = _audit(
            tmp_path,
            loop,
            env_file=(
                ".env.local",
                "# ANTHROPIC_API_KEY= (removed)\nVERCEL_TOKEN=dummy\n",
            ),
        )
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
        assert env_dump.exists(), "the agent never ran on a clean env file"

    @pytest.mark.parametrize("var", BILLING_REROUTE_FLAGS)
    @pytest.mark.parametrize("value", FALSY_FLAG_VALUES)
    def test_a_falsy_use_flag_in_an_env_file_still_runs(
        self, tmp_path, loop, var, value
    ):
        proc, env_dump = _audit(
            tmp_path,
            loop,
            env_file=(".env.local", f"{var}={value}\n"),
        )
        assert proc.returncode == 0, (
            f"{loop}: {var}={value} in .env.local is subscription-only: "
            f"{proc.returncode} {proc.stdout}{proc.stderr}"
        )
        assert env_dump.exists(), (
            f"{loop}: a file that locks Bedrock/Vertex off refused the run"
        )
