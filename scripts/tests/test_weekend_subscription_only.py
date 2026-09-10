"""Nightly loops bill the captain's claude.ai subscription, never an API key.

Claude Code prefers ANTHROPIC_API_KEY (and every Bedrock / Vertex / Foundry /
gateway / auth-token reroute) over the machine's claude.ai login whenever one
is visible. On 2026-09-01 that moved a security scan onto metered API billing
with nothing in the run record.

Operator rule (2026-09-01): a reroute variable the launch shell happens to
carry is IGNORED, not fatal. The wrapper names it on stderr (never the value),
unsets it, and runs on the subscription. It still refuses what `unset` cannot
reach: a key file a scanner reloads itself, or a Claude Code settings file
carrying an apiKeyHelper / env reroute. The product's web/.env, provisioned
into the four Radon-credential clones, is scrubbed of reroute lines in place
so neither the dev server nor pytest's load_dotenv can hand the key onward.
"""

from __future__ import annotations

import json
import re
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
CREDENTIAL_LOOPS = sorted(loop for loop in LOOPS if loop != "security")

# Every var the installed CLI (2.1.258) honors as an off-subscription route:
# `strings` on the binary, filtered to key / token / base-url / USE_* names.
BILLING_REROUTE_KEYS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_API_KEY",
    "CLAUDE_API_KEY",
    "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR",
    "AWS_BEARER_TOKEN_BEDROCK",
    "ANTHROPIC_AWS_API_KEY",
    "ANTHROPIC_AWS_BASE_URL",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_BEDROCK_MANTLE_BASE_URL",
    "ANTHROPIC_VERTEX_BASE_URL",
    "ANTHROPIC_GOOGLE_CLOUD_BASE_URL",
    "ANTHROPIC_FOUNDRY_API_KEY",
    "ANTHROPIC_FOUNDRY_AUTH_TOKEN",
    "ANTHROPIC_FOUNDRY_BASE_URL",
    "ANTHROPIC_FOUNDRY_RESOURCE",
    "ANTHROPIC_IDENTITY_TOKEN",
    "ANTHROPIC_IDENTITY_TOKEN_FILE",
)
BILLING_REROUTE_FLAGS = (
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_GATEWAY",
    "CLAUDE_CODE_USE_MANTLE",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
    "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
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
    # The four fallback loops drive codex and grok from a rendered prompt file
    # under .claude/portable-prompts; a clone without them has no usable rung.
    prompts = repo / ".claude" / "portable-prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    for src in (REPO / ".claude" / "portable-prompts").glob("*.md"):
        shutil.copy2(src, prompts / src.name)
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
        "git": '#!/bin/sh\n# REL-188: the wrapper reads commit evidence from git before calling a phase\n# OK, so the stub reports a fresh HEAD and a current committer date.\ncase "$*" in\n  *"rev-parse HEAD"*) date +%s%N; exit 0 ;;\n  *"--format=%ct"*) date +%s; exit 0 ;;\nesac\nexit 0\n',
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
    *,
    env_extra=None,
    env_file=None,
    home_settings=None,
    repo_settings=None,
    mode="audit",
    claude_stub=None,
):
    env_dump = tmp_path / "agent-env.txt"
    gh_log = tmp_path / "gh.log"
    wrapper = LOOPS[loop]
    bin_dir = _stub_bin(tmp_path, env_dump, gh_log)
    if claude_stub is not None:
        (bin_dir / "claude").write_text(claude_stub, encoding="utf-8")
    repo = _clone(tmp_path, wrapper)
    home = tmp_path / "home"
    if env_file is not None:
        path, body = env_file
        dest = repo / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body, encoding="utf-8")
        dest.chmod(0o600)
    if home_settings is not None:
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "settings.json").write_text(
            json.dumps(home_settings), encoding="utf-8"
        )
    if repo_settings is not None:
        rel, body = repo_settings
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(json.dumps(body), encoding="utf-8")
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "RADON_WEEKEND_REPO": str(repo),
        # 2026-09-06: the billing rails in this file are about the ANTHROPIC
        # key path — an apiKeyHelper or ANTHROPIC_API_KEY must never move a
        # claude rung off the subscription. That rail only has meaning on a
        # claude rung, so pin one here. The four loops' real default ladder
        # (codex, grok, NVIDIA, Cerebras) is asserted in
        # test_provider_registry_parity.py, and its own rails in
        # test_provider_failover.py.
        "RADON_WEEKEND_PROVIDER_LADDER": "claude:claude-fable-5[1m]",
    }
    env.update(env_extra or {})
    proc = subprocess.run(
        [BASH, str(repo / "scripts" / wrapper.name), mode],
        cwd=repo, env=env, capture_output=True, text=True, timeout=120,
    )
    return proc, env_dump, repo


def _agent_env(env_dump: Path) -> dict:
    out = {}
    for line in env_dump.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        if sep:
            out[name] = value
    return out


@pytest.mark.parametrize("loop", sorted(LOOPS))
class TestTheWrapperNamesEveryBillingReroute:
    def test_the_wrapper_lists_every_var_the_cli_honors(self, loop):
        body = LOOPS[loop].read_text(encoding="utf-8")
        keys = re.search(r'^BILLING_REROUTE_KEYS="([^"]*)"$', body, re.M)
        flags = re.search(r'^BILLING_REROUTE_FLAGS="([^"]*)"$', body, re.M)
        assert keys and flags, f"{loop}: the reroute lists are not declared"
        listed = set(keys.group(1).split()) | set(flags.group(1).split())
        for var in BILLING_REROUTE_VARS:
            assert var in listed, (
                f"{loop}: {var} is not in the reroute list; Claude Code "
                "2.1.258 prefers it over the claude.ai login"
            )

    def test_the_wrapper_unsets_from_the_lists(self, loop):
        body = LOOPS[loop].read_text(encoding="utf-8")
        assert re.search(
            r"^unset \$BILLING_REROUTE_KEYS \$BILLING_REROUTE_FLAGS", body, re.M
        ), f"{loop}: the unset must cover the whole list, not a hand copy"


@pytest.mark.parametrize("loop", sorted(LOOPS))
class TestAnApiKeyInTheEnvironmentIsIgnored:
    @pytest.mark.parametrize("var", BILLING_REROUTE_KEYS)
    def test_a_set_billing_reroute_var_is_unset_and_the_run_proceeds(
        self, tmp_path, loop, var
    ):
        proc, env_dump, _ = _audit(tmp_path, loop, env_extra={var: KEY})
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, (
            f"{loop}: {var} in the launch shell must be ignored, not fatal: "
            f"{proc.returncode} {out}"
        )
        assert env_dump.exists(), f"{loop}: the agent never ran with {var} set"
        assert var not in _agent_env(env_dump), (
            f"{loop}: {var} reached the agent; it would bill metered API usage"
        )
        assert "IGNORING" in out and var in out, (
            f"{loop}: the wrapper must name {var} on stderr so the operator "
            f"knows the shell leaked it: {out!r}"
        )
        assert KEY not in out, f"{loop}: the wrapper echoed the secret: {out!r}"
        assert KEY not in env_dump.read_text(encoding="utf-8")

    def test_a_clean_environment_logs_nothing(self, tmp_path, loop):
        proc, env_dump, _ = _audit(tmp_path, loop)
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
        assert env_dump.exists()
        assert "IGNORING" not in proc.stdout + proc.stderr


@pytest.mark.parametrize("loop", sorted(LOOPS))
class TestATruthyUseFlagIsIgnored:
    @pytest.mark.parametrize("var", BILLING_REROUTE_FLAGS)
    @pytest.mark.parametrize("value", TRUTHY_FLAG_VALUES)
    def test_a_truthy_use_flag_is_unset_and_the_run_proceeds(
        self, tmp_path, loop, var, value
    ):
        proc, env_dump, _ = _audit(tmp_path, loop, env_extra={var: value})
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, (proc.returncode, out)
        assert env_dump.exists(), f"{loop}: the agent never ran with {var}={value}"
        assert var not in _agent_env(env_dump), (
            f"{loop}: {var}={value} reached the agent and rerouted billing"
        )
        assert "IGNORING" in out and var in out, out


@pytest.mark.parametrize("loop", sorted(LOOPS))
class TestAFalsyUseFlagStillRuns:
    @pytest.mark.parametrize("var", BILLING_REROUTE_FLAGS)
    @pytest.mark.parametrize("value", FALSY_FLAG_VALUES)
    def test_a_falsy_use_flag_is_subscription_only(self, tmp_path, loop, var, value):
        proc, env_dump, _ = _audit(tmp_path, loop, env_extra={var: value})
        assert proc.returncode == 0, (
            f"{loop}: {var}={value} locks the reroute OFF, which is "
            f"subscription-only, so the run must proceed: "
            f"{proc.returncode} {proc.stdout}{proc.stderr}"
        )
        assert env_dump.exists(), (
            f"{loop}: {var}={value} refused the agent; a shell profile that "
            "sets the flag to 0/false is not a billing reroute"
        )
        assert "IGNORING" not in proc.stdout + proc.stderr, (
            f"{loop}: a falsy flag is not worth an operator line"
        )


@pytest.mark.parametrize("loop", sorted(LOOPS))
class TestABillingRerouteInAnIgnoredEnvFileRefusesTheRun:
    @pytest.mark.parametrize(
        "path,line",
        (
            (".env.local", f"ANTHROPIC_API_KEY={KEY}\n"),
            (".deepsec/.env.local", "CLAUDE_CODE_USE_BEDROCK=1\n"),
            (".env.local", "CLAUDE_CODE_API_KEY=sk-ant-alias\n"),
            (".deepsec/.env.local", f"ANTHROPIC_FOUNDRY_API_KEY={KEY}\n"),
            (".env.local", "CLAUDE_CODE_USE_FOUNDRY=1\n"),
        ),
    )
    def test_a_key_file_the_agent_would_reload_refuses(self, tmp_path, loop, path, line):
        proc, env_dump, _ = _audit(tmp_path, loop, env_file=(path, line))
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
        proc, env_dump, _ = _audit(
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
        proc, env_dump, _ = _audit(
            tmp_path,
            loop,
            env_file=(".env.local", f"{var}={value}\n"),
        )
        assert proc.returncode == 0, (
            f"{loop}: {var}={value} in .env.local is subscription-only: "
            f"{proc.returncode} {proc.stdout}{proc.stderr}"
        )
        assert env_dump.exists(), (
            f"{loop}: a file that locks the reroute off refused the run"
        )

    @pytest.mark.parametrize(
        "path,line",
        (
            (".env.local", 'CLAUDE_CODE_USE_BEDROCK="true"\n'),
            (".deepsec/.env.local", "CLAUDE_CODE_USE_VERTEX='1'\n"),
            (".env.local", 'CLAUDE_CODE_USE_BEDROCK="1"\n'),
            (".deepsec/.env.local", "CLAUDE_CODE_USE_BEDROCK='yes'\n"),
        ),
    )
    def test_a_quoted_truthy_use_flag_in_an_env_file_refuses(
        self, tmp_path, loop, path, line
    ):
        proc, env_dump, _ = _audit(tmp_path, loop, env_file=(path, line))
        out = proc.stdout + proc.stderr
        assert proc.returncode == 2, (
            f"{loop}: dotenv-quoted {line!r} in {path} is truthy after "
            f"quote-strip and must refuse: {proc.returncode} {out}"
        )
        assert "REFUSING" in out, out
        assert path in out, out
        assert not env_dump.exists(), (
            f"{loop}: the agent ran after a quoted billing-reroute flag"
        )

    @pytest.mark.parametrize(
        "path,line",
        (
            (".env.local", 'CLAUDE_CODE_USE_BEDROCK="0"\n'),
            (".deepsec/.env.local", "CLAUDE_CODE_USE_VERTEX='false'\n"),
            (".env.local", "CLAUDE_CODE_USE_BEDROCK='no'\n"),
        ),
    )
    def test_a_quoted_falsy_use_flag_in_an_env_file_still_runs(
        self, tmp_path, loop, path, line
    ):
        proc, env_dump, _ = _audit(tmp_path, loop, env_file=(path, line))
        assert proc.returncode == 0, (
            f"{loop}: quoted {line!r} still locks the reroute off: "
            f"{proc.returncode} {proc.stdout}{proc.stderr}"
        )
        assert env_dump.exists(), (
            f"{loop}: a quoted 0/false/no in {path} refused the run"
        )


WEB_ENV = (
    "UW_TOKEN=uw-dummy\n"
    f"ANTHROPIC_API_KEY={KEY}\n"
    "export CLAUDE_CODE_USE_BEDROCK=1\n"
    "CLAUDE_CODE_USE_VERTEX=0\n"
    "TURSO_DB_URL=libsql://dummy\n"
)


@pytest.mark.parametrize("loop", CREDENTIAL_LOOPS)
class TestTheProvisionedWebEnvIsScrubbedOfRerouteLines:
    def test_reroute_lines_are_removed_in_place_and_the_run_proceeds(
        self, tmp_path, loop
    ):
        proc, env_dump, repo = _audit(tmp_path, loop, env_file=("web/.env", WEB_ENV))
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, (proc.returncode, out)
        assert env_dump.exists(), f"{loop}: the agent never ran"
        web_env = repo / "web" / ".env"
        body = web_env.read_text(encoding="utf-8")
        assert KEY not in body, (
            f"{loop}: web/.env still carries ANTHROPIC_API_KEY; the dev server "
            "and pytest's load_dotenv would hand it to every child"
        )
        assert "CLAUDE_CODE_USE_BEDROCK=1" not in body, body
        assert "UW_TOKEN=uw-dummy\n" in body and "TURSO_DB_URL=libsql://dummy\n" in body, (
            f"{loop}: the scrub dropped a non-reroute line: {body!r}"
        )
        assert "CLAUDE_CODE_USE_VERTEX=0\n" in body, (
            f"{loop}: a falsy flag locks the reroute OFF and stays: {body!r}"
        )
        assert web_env.stat().st_mode & 0o777 == 0o600, oct(web_env.stat().st_mode)
        assert "IGNORING" in out and "web/.env" in out, out
        assert KEY not in out, out
        assert "ANTHROPIC_API_KEY" not in _agent_env(env_dump)

    def test_a_clean_web_env_is_left_untouched(self, tmp_path, loop):
        clean = "UW_TOKEN=uw-dummy\nTURSO_DB_URL=libsql://dummy\n"
        proc, env_dump, repo = _audit(tmp_path, loop, env_file=("web/.env", clean))
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
        assert env_dump.exists()
        assert (repo / "web" / ".env").read_text(encoding="utf-8") == clean
        assert "IGNORING" not in proc.stdout + proc.stderr


def test_the_security_clone_still_refuses_any_web_env(tmp_path):
    proc, env_dump, _ = _audit(tmp_path, "security", env_file=("web/.env", WEB_ENV))
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, (proc.returncode, out)
    assert "REFUSING" in out and "web/.env" in out, out
    assert KEY not in out, out
    assert not env_dump.exists()


@pytest.mark.parametrize("loop", sorted(LOOPS))
class TestASettingsLevelRerouteRefusesTheRun:
    @pytest.mark.parametrize(
        "settings",
        (
            {"apiKeyHelper": "/usr/local/bin/print-anthropic-key"},
            {"env": {"ANTHROPIC_API_KEY": KEY}},
            {"env": {"ANTHROPIC_FOUNDRY_API_KEY": KEY}},
            {"env": {"CLAUDE_CODE_USE_BEDROCK": "1"}},
            {"env": {"CLAUDE_CODE_USE_VERTEX": "true"}},
        ),
    )
    def test_a_home_settings_reroute_refuses_before_the_agent(
        self, tmp_path, loop, settings
    ):
        proc, env_dump, _ = _audit(tmp_path, loop, home_settings=settings)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 2, (
            f"{loop}: a settings-level apiKeyHelper / env reroute reaches the "
            f"agent past any unset and must refuse: {proc.returncode} {out}"
        )
        assert "REFUSING" in out and "settings.json" in out, out
        assert KEY not in out, out
        assert not env_dump.exists(), f"{loop}: the agent ran on a rerouted settings file"

    @pytest.mark.parametrize(
        "rel", (".claude/settings.json", ".claude/settings.local.json")
    )
    def test_a_repo_settings_reroute_refuses_before_the_agent(self, tmp_path, loop, rel):
        proc, env_dump, _ = _audit(
            tmp_path, loop, repo_settings=(rel, {"apiKeyHelper": "/bin/echo"})
        )
        out = proc.stdout + proc.stderr
        assert proc.returncode == 2, (proc.returncode, out)
        assert "REFUSING" in out and rel in out, out
        assert not env_dump.exists()

    @pytest.mark.parametrize(
        "settings",
        (
            {"model": "claude-fable-5-1[1m]", "env": {}},
            {"env": {"CLAUDE_CODE_USE_BEDROCK": "0"}},
            {"env": {"CLAUDE_CODE_USE_VERTEX": "false"}},
            {"permissions": {"allow": ["Bash(git status)"]}},
        ),
    )
    def test_ordinary_settings_still_run(self, tmp_path, loop, settings):
        proc, env_dump, _ = _audit(tmp_path, loop, home_settings=settings)
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
        assert env_dump.exists(), f"{loop}: ordinary settings refused the run"


@pytest.mark.parametrize("loop", sorted(LOOPS))
class TestTheRailsAreReCheckedBeforeEveryPhase:
    """A reroute planted by the in-phase agent must not reach the next phase.

    The prologue check runs once; `cycle` then runs audit and remediate with a
    reset in between, and the gitignored .deepsec/ and the settings files
    survive that reset. The remediate phase must re-check and refuse."""

    @pytest.mark.parametrize(
        "plant",
        (
            f"mkdir -p .deepsec && printf 'ANTHROPIC_API_KEY={KEY}\\n' > .deepsec/.env.local",
            "mkdir -p .claude && printf '{\"apiKeyHelper\": \"/bin/echo\"}' > .claude/settings.local.json",
        ),
    )
    def test_a_reroute_planted_during_audit_refuses_remediate(self, tmp_path, loop, plant):
        calls = tmp_path / "claude-calls.txt"
        stub = (
            "#!/bin/sh\n"
            f'printf "%s\\n" "$*" >> "{calls}"\n'
            f"{plant}\n"
            f'echo "{COMPLETION}"\n'
            "exit 0\n"
        )
        proc, _, _ = _audit(tmp_path, loop, mode="cycle", claude_stub=stub)
        out = proc.stdout + proc.stderr
        launched = calls.read_text(encoding="utf-8") if calls.exists() else ""
        assert "audit" in launched, (proc.returncode, out)
        assert "remediate" not in launched, (
            f"{loop}: the remediate phase launched the agent over a reroute "
            f"the audit phase planted: {out!r}"
        )
        assert proc.returncode == 2, (proc.returncode, out)
        assert "REFUSING" in out, out
        assert KEY not in out, out
