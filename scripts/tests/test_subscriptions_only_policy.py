"""Subscriptions only (operator policy 2026-10-26, absolute).

Claude/Anthropic, Codex/OpenAI, Grok/xAI and Antigravity run ONLY on consumer
subscriptions (OAuth grant files / CLI login) in tests, local dev and
production. Their prepaid console keys are never read or sent, and the old
``RADON_LADDER_ALLOW_PREPAID`` escape hatch is gone. A set variable is ignored
with one stderr warning that names it (never its value).

Guard: no non-test file in the source tree may name those variables, outside
an explicit allowlist of files that only redact, scrub or deny them, plus
AST-owned scrub literals and the DeepSec ephemeral loopback-token assignment.
"""
from __future__ import annotations

import ast
import io
import json
import re
import subprocess
from pathlib import Path

import pytest

from model_gateway import engine as model_ladder
from clients import prepaid_denylist

ROOT = Path(__file__).resolve().parents[2]

PREPAID_NAMES = (
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_API_KEY",
    "CLAUDE_API_KEY",
    "XAI_API_KEY",
    "GROK_API_KEY",
    "OPENAI_API_KEY",
    "RADON_LADDER_ALLOW_PREPAID",
)
# Word boundaries: CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR and ANTHROPIC_FOUNDRY_API_KEY
# are other variables (scrubbed elsewhere), not these.
NAME_RE = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(PREPAID_NAMES) + r")(?![A-Za-z0-9_])")

# Every tracked file is scanned except prose history: docs (checked for the
# hatch below), task notes, test logs and the generated codemap.
UNSCANNED_PREFIXES = ("docs/", "tasks/", "tools/codemap/", "TEST_AUDIT.md", "TEST_LOG.md")

# Every entry must say why naming a prepaid variable there is not reading it.
ALLOWLIST = {
    "cloud/services/radon-.service.d/common.conf": "UnsetEnvironment strips these names from consumer processes; never reads them",
    "scripts/credential_redaction.py": "redacts the values out of logs and reports",
    "scripts/clients/prepaid_denylist.json": "the deny list: names to ignore and warn about",
    "web/lib/llm/prepaidDenylist.ts": "the web copy of the deny list (parity-tested below)",
    "scripts/runner/loops/security.env": "AGENT_UNSET: scrubbed from the agent CLI env",
    "scripts/runner/loops/security-deepsec.env": "AGENT_UNSET: scrubbed from the agent CLI env",
    "cloud/scripts/radon-app-runtime.sh": "render_env_file deletes these lines from every container env",
    "scripts/claude_cli_env_reviewed.txt": "reviewed Claude CLI env names (drift pager), not values",
    ".claude/runner-prompts/security.md": "instructs the agent never to provision these",
    ".claude/runner-prompts/security-deepsec.md": "instructs the agent never to provision these",
    "scripts/codex_capability_probe.py": (
        "a literal fake key (sk-radon-probe-not-a-real-token) sent only to a "
        "127.0.0.1 fixture server; no real credential is read"
    ),
}


def _is_test_path(rel: str) -> bool:
    parts = rel.split("/")
    name = parts[-1]
    return (
        any(p in {"tests", "e2e", "__tests__", "fixtures"} for p in parts[:-1])
        or name.startswith("test_")
        or name == "conftest.py"
        or re.search(r"\.(test|spec)\.[cm]?[jt]sx?$", name) is not None
        or name.endswith("_test.py")
    )


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True,
    ).stdout.decode()
    return [p for p in out.split("\0") if p and not p.startswith(UNSCANNED_PREFIXES)]


def _gateway_owned_name_nodes(rel: str, text: str) -> list[ast.AST]:
    """Allow exact scrub literals/local-token writes, never a whole gateway file."""
    if rel not in {"scripts/model_gateway/client.py", "scripts/model_gateway/cli.py"}:
        return []
    tree = ast.parse(text)
    owned = []
    if rel.endswith("/client.py"):
        for statement in tree.body:
            if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
                continue
            target, value = statement.targets[0], statement.value
            if (not isinstance(target, ast.Name) or target.id != "MODEL_SECRET_ENV"
                    or not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name)
                    or value.func.id != "frozenset" or value.keywords or len(value.args) != 1
                    or not isinstance(value.args[0], ast.Set)):
                continue
            members = value.args[0].elts
            if all(isinstance(item, ast.Constant) and isinstance(item.value, str) for item in members):
                owned.extend(item for item in members if item.value in PREPAID_NAMES)
    else:
        expected_call = ast.dump(ast.parse(
            "child.update(OPENAI_BASE_URL=url + '/v1', OPENAI_API_KEY=token)"
        ).body[0].value, include_attributes=False)
        expected_branch = ast.dump(ast.parse("kind == 'deepsec'", mode="eval").body,
                                   include_attributes=False)
        for function in tree.body:
            if not isinstance(function, ast.FunctionDef) or function.name != "configured_cli":
                continue
            for branch in ast.walk(function):
                if not isinstance(branch, ast.If) or ast.dump(branch.test, include_attributes=False) != expected_branch:
                    continue
                for statement in branch.body:
                    if (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
                            and ast.dump(statement.value, include_attributes=False) == expected_call):
                        owned.extend(keyword for keyword in statement.value.keywords
                                     if keyword.arg == "OPENAI_API_KEY")
    return owned


def _prepaid_name_violations(rel: str, text: str) -> list[str]:
    owned = _gateway_owned_name_nodes(rel, text)
    offenders = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for match in NAME_RE.finditer(line):
            if any(node.lineno == lineno == node.end_lineno
                   and node.col_offset <= match.start() < match.end() <= node.end_col_offset
                   for node in owned):
                continue
            offenders.append(f"{rel}:{lineno}: {match.group(1)}")
    return offenders


def test_no_source_file_names_a_prepaid_key_or_the_hatch():
    offenders: list[str] = []
    for rel in _tracked_files():
        if rel in ALLOWLIST or _is_test_path(rel):
            continue
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        offenders.extend(_prepaid_name_violations(rel, text))
    assert not offenders, "prepaid key / hatch named outside the allowlist:\n" + "\n".join(offenders)


@pytest.mark.parametrize("relative,source", [
    ("scripts/model_gateway/client.py", "key = os.environ.get('OPENAI_API_KEY')"),
    ("scripts/model_gateway/client.py", "MODEL_SECRET_ENV = frozenset({os.environ.get('OPENAI_API_KEY')})"),
    ("scripts/model_gateway/cli.py", "key = env.get('OPENAI_API_KEY')"),
    ("scripts/model_gateway/new_backend.py", "key = env.get('OPENAI_API_KEY')"),
    ("scripts/model_gateway/cli.py", "def configured_cli():\n    if kind == 'deepsec':\n        child.update(OPENAI_BASE_URL='https://api.openai.com/v1', OPENAI_API_KEY=token)"),
    ("scripts/model_gateway/cli.py", "def configured_cli():\n    if kind == 'deepsec':\n        child.update(OPENAI_BASE_URL=url + '/v1', OPENAI_API_KEY=env.get('OPENAI_API_KEY'))"),
])
def test_gateway_policy_rejects_vendor_auth_reads_and_nonlocal_assignments(relative, source):
    assert _prepaid_name_violations(relative, source)


def test_gateway_scrubs_prepaid_env_and_deepsec_receives_only_loopback_token(tmp_path, monkeypatch):
    from model_gateway import client, cli

    hostile = {name: f"forbidden-{name}" for name in PREPAID_NAMES
               if name != "RADON_LADDER_ALLOW_PREPAID"}
    for name, value in hostile.items():
        monkeypatch.setenv(name, value)
    client.scrub_model_secrets()
    assert all(name not in client.os.environ for name in hostile if name in client.MODEL_SECRET_ENV)
    command = ["/trusted/deepsec", "process", "--agent", "codex"]
    with cli.configured_cli("deepsec", command, "http://127.0.0.1:12345", "local-app-token",
                            env={**hostile, "GH_TOKEN": "tool-token"}, state_dir=tmp_path) as (args, child):
        assert args == command
        assert child["OPENAI_API_KEY"] == "local-app-token"
        assert child["OPENAI_BASE_URL"] == "http://127.0.0.1:12345/v1"
        assert child["GH_TOKEN"] == "tool-token"
        assert not set(hostile.values()).intersection(child.values())
        assert all(name not in child for name in hostile if name != "OPENAI_API_KEY")
    with pytest.raises(ValueError, match="private bridge"):
        with cli.configured_cli("deepsec", command, "https://api.openai.com", "local-app-token",
                                env=hostile, state_dir=tmp_path):
            pytest.fail("A vendor endpoint cannot receive the app-scoped bridge token")


def test_the_hatch_is_gone_from_docs_too():
    out = subprocess.run(
        ["git", "grep", "-l", "RADON_LADDER_ALLOW_PREPAID", "--", "docs", "*.md"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout.split()
    allowed = {"TEST_AUDIT.md", "TEST_LOG.md", "tasks/todo.md"}  # historical logs
    assert [p for p in out if p not in allowed and not p.startswith("docs/archive/")] == []


def test_allowlist_entries_exist_and_still_need_it():
    for rel in ALLOWLIST:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert NAME_RE.search(text), f"{rel} no longer names a prepaid key; drop it from ALLOWLIST"


def test_deny_lists_match_across_python_and_web():
    canonical = json.loads((ROOT / "scripts/clients/prepaid_denylist.json").read_text())
    assert tuple(canonical) == PREPAID_NAMES
    web = (ROOT / "web/lib/llm/prepaidDenylist.ts").read_text()
    assert tuple(re.findall(r'"([A-Z_]+)"', web.split("IGNORED_PREPAID_ENV")[1].split("]")[0])) == PREPAID_NAMES


# -- behavior: the ladder never returns prepaid auth --------------------------

def _hostile_env(home: Path) -> dict[str, str]:
    env = {name: f"value-of-{name.lower()}" for name in PREPAID_NAMES}
    env["RADON_LADDER_ALLOW_PREPAID"] = "1"
    env["HOME"] = str(home)
    env["CODEX_HOME"] = str(home / ".codex")
    return env


@pytest.fixture(autouse=True)
def _fresh_warning_state(monkeypatch):
    monkeypatch.setattr(prepaid_denylist, "_warned", set())


@pytest.mark.parametrize("provider", ["anthropic", "grok", "codex", "antigravity"])
def test_ladder_never_returns_prepaid_auth_with_flag_and_keys_set(tmp_path, provider):
    env = _hostile_env(tmp_path)
    assert model_ladder._auth_for(provider, env) is None
    assert model_ladder.subscription_auth(provider, env) is None
    assert provider not in model_ladder.wired_providers(env)


@pytest.mark.parametrize("provider,relative,payload", [
    ("codex", ".codex/auth.json", {"auth_mode": "apikey", "OPENAI_API_KEY": "fake-prepaid"}),
    ("codex", ".codex/auth.json", {"OPENAI_API_KEY": "fake-prepaid"}),
    ("grok", ".grok/auth.json", {"api_key": "fake-prepaid"}),
    ("grok", ".grok/auth.json", {"apiKey": "fake-prepaid"}),
    ("grok", ".grok/auth.json", {"credentials": {"api_key": "fake-prepaid"}}),
])
def test_auth_file_api_keys_are_never_read_even_with_the_flag(tmp_path, provider, relative, payload):
    path = tmp_path / relative
    path.parent.mkdir()
    path.write_text(json.dumps(payload))
    assert model_ladder._auth_for(provider, _hostile_env(tmp_path)) is None


@pytest.mark.parametrize("provider", ["anthropic", "grok", "codex"])
def test_subscription_wins_and_is_the_only_kind(tmp_path, provider):
    env = _hostile_env(tmp_path)
    if provider == "anthropic":
        env["CLAUDE_CODE_OAUTH_TOKEN"] = "oauth-grant"
    elif provider == "grok":
        (tmp_path / ".grok").mkdir()
        (tmp_path / ".grok/auth.json").write_text(json.dumps({"access_token": "oauth-grant"}))
    else:
        (tmp_path / ".codex").mkdir()
        (tmp_path / ".codex/auth.json").write_text(
            json.dumps({"auth_mode": "chatgpt", "tokens": {"access_token": "oauth-grant"}})
        )
    auth = model_ladder._auth_for(provider, env)
    assert auth is not None and auth.kind == "subscription" and auth.token == "oauth-grant"


def test_no_request_carries_a_prepaid_key(tmp_path):
    env = _hostile_env(tmp_path)
    sent: list[str] = []

    def post(url, **kwargs):
        sent.append(url + json.dumps(kwargs.get("headers") or {}) + json.dumps(kwargs.get("json") or {}))
        raise AssertionError("no provider is wired; nothing may be sent")

    with pytest.raises(model_ladder.ModelLadderExhausted):
        model_ladder.complete_text_json(
            "{}", system="s", env=env, post=post,
            providers=("anthropic", "grok", "codex"),
        )
    assert sent == []


def test_one_warning_per_variable_per_process_naming_it_not_its_value(tmp_path, monkeypatch):
    stderr = io.StringIO()
    monkeypatch.setattr("sys.stderr", stderr)
    env = _hostile_env(tmp_path)
    for _ in range(3):
        for provider in ("anthropic", "grok", "codex"):
            model_ladder._auth_for(provider, env)
    lines = [line for line in stderr.getvalue().splitlines() if line.strip()]
    assert len(lines) == len(PREPAID_NAMES)
    for name in PREPAID_NAMES:
        assert sum(NAME_RE.findall(line) == [name] for line in lines) == 1
        assert f"value-of-{name.lower()}" not in stderr.getvalue()


def test_no_warning_when_nothing_prepaid_is_set(tmp_path, monkeypatch):
    stderr = io.StringIO()
    monkeypatch.setattr("sys.stderr", stderr)
    model_ladder._auth_for("anthropic", {"HOME": str(tmp_path), "CLAUDE_CODE_OAUTH_TOKEN": "g"})
    assert stderr.getvalue() == ""
