"""Scheduled providers never receive a shared host Playwright server."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from test_rel137_weekend_wrapper_survivability import (
    BASH,
    CLAUDE_RUNG_LADDER,
    TESTING,
    RELIABILITY,
    _cloned_wrapper,
    _runner_clone,
    _stub_bin,
)

REPO = Path(__file__).resolve().parents[2]
HOST_LOOPS = {"testing": TESTING, "reliability": RELIABILITY}
ENDPOINT = "ws://127.0.0.1:4711/tok"


def _plant_pkg(path: Path, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'{{"name":"@playwright/test","version":"{version}"}}\n', encoding="utf-8")


def _plant_host(home: Path, *, version: str = "1.58.2", body: str | None = None) -> Path:
    root = home / ".radon" / "agent-cli" / "browser-host"
    bin_path = root / "node_modules" / ".bin" / "playwright"
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    _plant_pkg(root / "node_modules" / "@playwright" / "test" / "package.json", version)
    (root / "node_modules" / "playwright").mkdir(parents=True, exist_ok=True)
    (root / "node_modules" / "playwright" / "index.js").write_text(
        "module.exports={chromium:{connect:async()=>({newPage:async()=>({setContent:async()=>{}}),close:async()=>{}})}};\n",
        encoding="utf-8",
    )
    if body is None:
        body = (
            "#!/bin/sh\n"
            f'echo "Listening on {ENDPOINT}"\n'
            "sleep 60\n"
        )
    bin_path.write_text(body, encoding="utf-8")
    bin_path.chmod(0o755)
    return bin_path


def _plant_node(bin_dir: Path, home: Path) -> None:
    """Fake node: the launcher runs the planted host body, the smoke passes."""
    host_bin = home / ".radon" / "agent-cli" / "browser-host" / "node_modules" / ".bin" / "playwright"
    node = bin_dir / "node"
    node.write_text(
        "#!/bin/sh\n"
        f'case "$*" in *launchServer*) exec "{host_bin}" ;; esac\n'
        "exit 0\n",
        encoding="utf-8",
    )
    node.chmod(0o755)


def _plant_client(repo: Path, version: str = "1.58.2") -> None:
    _plant_pkg(
        repo / "web" / "node_modules" / "@playwright" / "test" / "package.json",
        version,
    )


def _env(tmp_path: Path, repo: Path, bin_dir: Path, extra: dict | None = None) -> dict:
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
        "RADON_WEEKEND_REPO": str(repo),
        "RADON_WEEKEND_PROVIDER_LADDER": CLAUDE_RUNG_LADDER,
        "RADON_WEEKEND_SKIP_PRUNE": "1",
        # Each wait tick shells out to ps; under a loaded xdist run 3 ticks
        # can pass before a stub that prints at once is read.
        "RADON_WEEKEND_BROWSER_HOST_WAIT_SECS": "10",
        "RADON_WEEKEND_BROWSER_HOST_SMOKE_SECS": "3",
    }
    if extra:
        env.update(extra)
    return env


def _combined(proc: subprocess.CompletedProcess, repo: Path) -> str:
    text = proc.stdout + proc.stderr
    log_dir = repo / "logs"
    if log_dir.exists():
        for path in log_dir.rglob("*.log"):
            text += path.read_text(encoding="utf-8")
    return text


class TestScheduledBrowserDisabled:
    """R01-A: the workspace-write rung does not receive the host browser endpoint."""

    @pytest.mark.parametrize("name", sorted(HOST_LOOPS))
    def test_codex_has_no_host_browser_or_logged_endpoint(self, name, tmp_path):
        repo = _runner_clone(tmp_path, name)
        home = tmp_path / "home"
        home.mkdir()
        _plant_host(home)
        _plant_client(repo)
        prompts = repo / ".claude" / "portable-prompts"
        prompts.mkdir(parents=True)
        skill = "testing-weekend" if name == "testing" else "reliability-weekend"
        (prompts / f"{skill}.audit.md").write_text("audit\n", encoding="utf-8")
        (home / ".codex").mkdir()
        (home / ".codex" / "auth.json").write_text("{}\n", encoding="utf-8")
        env_dump = tmp_path / "agent.env"
        bin_dir, _gh, _py = _stub_bin(
            tmp_path,
            claude_body="#!/bin/sh\nexit 0\n",
        )
        _plant_node(bin_dir, home)
        codex = bin_dir / "codex"
        codex.write_text(
            f"#!/bin/sh\nenv > {env_dump}\nexit 0\n",
            encoding="utf-8",
        )
        codex.chmod(0o755)
        env = _env(tmp_path, repo, bin_dir)
        env.pop("RADON_WEEKEND_PROVIDER_LADDER", None)
        env["RADON_WEEKEND_PROVIDER_LADDER"] = "codex"
        env["RADON_WEEKEND_CODEX_BIN"] = str(codex)
        proc = subprocess.run(
            [BASH, str(_cloned_wrapper(repo, name)), "audit"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        combined = _combined(proc, repo)
        assert "browser-host=ready" not in combined, combined
        assert not list(repo.glob("logs/**/browser-host-*.log"))
        assert env_dump.exists(), (proc.stdout, proc.stderr, combined)
        dumped = env_dump.read_text(encoding="utf-8")
        assert "PW_TEST_CONNECT_WS_ENDPOINT=" not in dumped, dumped
        assert "RADON_WEEKEND_BROWSER_HOST=unavailable:disabled" in dumped, dumped



@pytest.mark.parametrize("name", sorted(HOST_LOOPS))
@pytest.mark.parametrize("first", ["claude", "codex"])
def test_browser_lifetime_follows_provider_transition(name, first, tmp_path):
    repo = _runner_clone(tmp_path, name)
    home = tmp_path / "home"
    home.mkdir()
    host_pid = tmp_path / "host.pid"
    _plant_host(home, body=f'#!/bin/sh\necho $$ > "{host_pid}"\necho "Listening on {ENDPOINT}"\nsleep 60\n')
    _plant_client(repo)
    prompts = repo / ".claude" / "portable-prompts"
    prompts.mkdir(parents=True)
    skill = "testing-weekend" if name == "testing" else "reliability-weekend"
    (prompts / f"{skill}.audit.md").write_text("audit\n")
    (home / ".codex").mkdir()
    (home / ".codex/auth.json").write_text("{}\n")
    claude_env = tmp_path / "claude.env"
    codex_env = tmp_path / "codex.env"
    alive = tmp_path / "host-alive-during-codex"
    claude_body = f'#!/bin/sh\nenv > "{claude_env}"\n'
    if first == "claude":
        claude_body += 'echo "You\x27ve hit your session limit resets tomorrow"\nexit 1\n'
    else:
        claude_body += 'exit 0\n'
    bin_dir, _gh, _py = _stub_bin(tmp_path, claude_body=claude_body)
    _plant_node(bin_dir, home)
    codex = bin_dir / "codex"
    codex_body = f'#!/bin/sh\nenv > "{codex_env}"\nif test -f "{host_pid}" && kill -0 "$(cat "{host_pid}")" 2>/dev/null; then touch "{alive}"; fi\n'
    if first == "codex":
        codex_body += 'echo "You\x27ve hit your usage limit. try again tomorrow"\nexit 1\n'
    else:
        codex_body += 'exit 0\n'
    codex.write_text(codex_body)
    codex.chmod(0o755)
    ladder = "claude codex" if first == "claude" else "codex claude"
    proc = subprocess.run([BASH, str(_cloned_wrapper(repo, name)), "audit"], env=_env(tmp_path, repo, bin_dir, {"RADON_WEEKEND_PROVIDER_LADDER": ladder, "RADON_WEEKEND_CODEX_BIN": str(codex)}), capture_output=True, text=True, timeout=90)
    combined = _combined(proc, repo)
    assert codex_env.exists() and claude_env.exists(), combined
    assert not alive.exists(), combined
    assert "PW_TEST_CONNECT_WS_ENDPOINT=" not in codex_env.read_text()
    assert "PW_TEST_CONNECT_WS_ENDPOINT=" not in claude_env.read_text()
    assert not host_pid.exists(), combined
    assert not list(repo.glob("logs/**/browser-host-*.log"))


@pytest.mark.parametrize("provider", ["claude", "codex", "grok", "nvidia", "cerebras"])
@pytest.mark.parametrize("name", ["testing_weekend", "reliability_weekend", "documentation_nightly", "ci_performance_nightly", "security_nightly", "security_deepsec_nightly"])
def test_every_provider_discards_inherited_host_endpoint(name, provider, tmp_path):
    source = (REPO / "scripts" / f"{name}.sh").read_text()
    start = source.index("launch_round() {")
    end = source.index("\n  # A bare rung", start)
    # Execute the actual environment prologue with a poisoned inherited endpoint.
    script = source[start:end] + '\n  env\n}\nlaunch_round 60\n'
    env = {**os.environ, "RUNG_PROVIDER": provider, "PW_TEST_CONNECT_WS_ENDPOINT": ENDPOINT,
           "NIGHTLY_PR_GUARD_DIR": str(tmp_path), "PORTABLE_PROMPT_DIR": str(tmp_path),
           "LOOP_SKILL": "unused", "PHASE": "audit"}
    proc = subprocess.run([BASH, "-c", script], env=env, capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    assert "PW_TEST_CONNECT_WS_ENDPOINT=" not in proc.stdout
    assert "RADON_WEEKEND_BROWSER_HOST=unavailable:disabled" in proc.stdout


@pytest.mark.parametrize("name", sorted(HOST_LOOPS))
def test_scheduled_wrapper_contains_no_host_server_launcher(name):
    source = HOST_LOOPS[name].read_text()
    assert "launchServer" not in source
    assert "run-server" not in source
    assert "export PW_TEST_CONNECT_WS_ENDPOINT=" not in source
