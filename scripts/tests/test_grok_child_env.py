"""Every grok child process runs with an allowlisted environment.

The responder and upgrader parents legitimately hold Turso and Pushover
credentials (heartbeats, ledger, alerts). The grok agent they launch runs
``--always-approve`` over untrusted page text and needs none of them.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import grok_page_responder as responder  # noqa: E402
import grok_runtime  # noqa: E402
import grok_upgrade as upgrade  # noqa: E402

SECRETS = {
    "TURSO_DB_URL": "libsql://example.invalid",
    "TURSO_AUTH_TOKEN": "turso-test-value",
    "PUSHOVER_USER": "pushover-user-test",
    "PUSHOVER_TOKEN": "pushover-token-test",
    "GH_TOKEN": "gh-test-value",
    "GITHUB_TOKEN": "github-test-value",
    "ANTHROPIC_API_KEY": "anthropic-test-value",
    "UW_TOKEN": "uw-test-value",
}
FORBIDDEN_PREFIXES = ("TURSO_", "PUSHOVER_", "GH_", "GITHUB_", "ANTHROPIC_", "UW_")


@pytest.fixture
def secret_env(monkeypatch):
    for key, value in SECRETS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PATH", "/home/radon/.local/bin:/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/home/radon")
    monkeypatch.setenv("LANG", "C.UTF-8")


@pytest.fixture
def captured_run(monkeypatch):
    calls: list[dict] = []

    def fake_run(argv, **kwargs):
        calls.append({"argv": argv, **kwargs})
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def _assert_clean(env: dict | None) -> None:
    assert env is not None, "grok child inherited the full parent environment"
    leaked = sorted(k for k in env if k.startswith(FORBIDDEN_PREFIXES))
    assert not leaked, leaked
    assert env["PATH"] == "/home/radon/.local/bin:/usr/bin:/bin"
    assert env["HOME"] == "/home/radon"
    assert env["LANG"] == "C.UTF-8"


def test_child_env_is_an_allowlist(secret_env):
    env = grok_runtime.grok_child_env()
    _assert_clean(env)


def test_child_env_applies_overrides(secret_env):
    env = grok_runtime.grok_child_env({"GROK_HOME": "/scratch", "HOME": "/scratch"})
    assert env["GROK_HOME"] == "/scratch"
    assert env["HOME"] == "/scratch"
    assert not any(k.startswith(FORBIDDEN_PREFIXES) for k in env)


def test_responder_grok_runner_strips_secrets(secret_env, captured_run, tmp_path):
    responder._default_grok_runner(["grok", "--version"], cwd=str(tmp_path))
    assert captured_run[0]["argv"] == ["grok", "--version"]
    _assert_clean(captured_run[0].get("env"))


def test_runtime_default_runner_strips_secrets(secret_env, captured_run):
    grok_runtime._default_runner(["grok", "models"])
    _assert_clean(captured_run[0].get("env"))


def test_upgrade_default_runner_strips_secrets(secret_env, captured_run):
    upgrade._default_runner(["grok", "update", "--check", "--json"])
    _assert_clean(captured_run[0].get("env"))


def test_candidate_install_env_strips_secrets(secret_env, tmp_path):
    seen: list[dict] = []

    def runner(argv, **kwargs):
        seen.append(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    upgrade.install_candidate_cli(tmp_path / "cand", grok_bin="grok", runner=runner)
    env = seen[0]["env"]
    assert env["GROK_HOME"] == str(tmp_path / "cand")
    assert env["HOME"] == str(tmp_path / "cand")
    assert not any(k.startswith(FORBIDDEN_PREFIXES) for k in env)
