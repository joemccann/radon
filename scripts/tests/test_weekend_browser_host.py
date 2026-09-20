"""Weekend wrapper-owned Playwright run-server (plan browser cases 1-6)."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from pathlib import Path

import pytest

from test_rel137_weekend_wrapper_survivability import (
    BASH,
    CLAUDE_RUNG_LADDER,
    TESTING,
    RELIABILITY,
    _cloned_wrapper,
    _pid_exists,
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
        "RADON_WEEKEND_BROWSER_HOST_WAIT_SECS": "3",
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


class TestWeekendBrowserHost:
    @pytest.mark.parametrize("name", sorted(HOST_LOOPS))
    def test_case1_ready_exports_exact_endpoint(self, name, tmp_path):
        repo = _runner_clone(tmp_path, name)
        home = tmp_path / "home"
        home.mkdir()
        _plant_host(home)
        _plant_client(repo)
        env_dump = tmp_path / "agent.env"
        bin_dir, _gh, _py = _stub_bin(
            tmp_path,
            claude_body=f"#!/bin/sh\nenv > {env_dump}\nexit 0\n",
        )
        node = bin_dir / "node"
        node.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        node.chmod(0o755)
        proc = subprocess.run(
            [BASH, str(_cloned_wrapper(repo, name)), "audit"],
            env=_env(tmp_path, repo, bin_dir),
            capture_output=True,
            text=True,
            timeout=60,
        )
        combined = _combined(proc, repo)
        assert "browser-host=ready" in combined, combined
        assert env_dump.exists(), (proc.stdout, proc.stderr, combined)
        dumped = env_dump.read_text(encoding="utf-8")
        assert f"PW_TEST_CONNECT_WS_ENDPOINT={ENDPOINT}" in dumped, dumped
        assert "RADON_WEEKEND_BROWSER_HOST=ready" in dumped

    @pytest.mark.parametrize("name", sorted(HOST_LOOPS))
    def test_case2_exited_or_silent_is_unavailable_and_reaped(self, name, tmp_path):
        repo = _runner_clone(tmp_path, name)
        home = tmp_path / "home"
        home.mkdir()
        pid_file = tmp_path / "pw.pid"
        _plant_host(
            home,
            body=(
                "#!/bin/sh\n"
                f'echo $$ > "{pid_file}"\n'
                "exit 133\n"
            ),
        )
        _plant_client(repo)
        env_dump = tmp_path / "agent.env"
        bin_dir, _gh, _py = _stub_bin(
            tmp_path,
            claude_body=f"#!/bin/sh\nenv > {env_dump}\nexit 0\n",
        )
        (bin_dir / "node").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (bin_dir / "node").chmod(0o755)
        proc = subprocess.run(
            [BASH, str(_cloned_wrapper(repo, name)), "audit"],
            env=_env(tmp_path, repo, bin_dir),
            capture_output=True,
            text=True,
            timeout=60,
        )
        combined = _combined(proc, repo)
        assert "browser-host=unavailable:" in combined, combined
        assert env_dump.exists(), (proc.stdout, proc.stderr, combined)
        dumped = env_dump.read_text(encoding="utf-8")
        assert "PW_TEST_CONNECT_WS_ENDPOINT=" not in dumped
        assert "RADON_WEEKEND_BROWSER_HOST=unavailable:" in dumped
        if pid_file.exists():
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and _pid_exists(pid):
                time.sleep(0.05)
            assert not _pid_exists(pid), pid

        _plant_host(home, body="#!/bin/sh\nsleep 30\n")
        proc2 = subprocess.run(
            [BASH, str(_cloned_wrapper(repo, name)), "audit"],
            env=_env(tmp_path, repo, bin_dir),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert "unavailable:no-endpoint" in _combined(proc2, repo)

    @pytest.mark.parametrize("name", sorted(HOST_LOOPS))
    def test_case3_missing_or_mismatched_install(self, name, tmp_path):
        repo = _runner_clone(tmp_path, name)
        home = tmp_path / "home"
        home.mkdir()
        _plant_client(repo, "1.58.2")
        env_dump = tmp_path / "agent.env"
        bin_dir, _gh, _py = _stub_bin(
            tmp_path,
            claude_body=f"#!/bin/sh\nenv > {env_dump}\nexit 0\n",
        )
        (bin_dir / "node").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (bin_dir / "node").chmod(0o755)
        proc = subprocess.run(
            [BASH, str(_cloned_wrapper(repo, name)), "audit"],
            env=_env(tmp_path, repo, bin_dir),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert "unavailable:not-installed" in _combined(proc, repo)
        assert "PW_TEST_CONNECT_WS_ENDPOINT=" not in env_dump.read_text(encoding="utf-8")

        _plant_host(home, version="1.57.0")
        proc2 = subprocess.run(
            [BASH, str(_cloned_wrapper(repo, name)), "audit"],
            env=_env(tmp_path, repo, bin_dir),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert "unavailable:version-mismatch" in _combined(proc2, repo)

    @pytest.mark.parametrize("name", sorted(HOST_LOOPS))
    def test_case4_sigterm_reaps_the_server(self, name, tmp_path):
        repo = _runner_clone(tmp_path, name)
        home = tmp_path / "home"
        home.mkdir()
        pid_file = tmp_path / "pw.pid"
        started = tmp_path / "claude-started"
        _plant_host(
            home,
            body=(
                "#!/bin/sh\n"
                f'echo $$ > "{pid_file}"\n'
                f'echo "Listening on {ENDPOINT}"\n'
                "sleep 60\n"
            ),
        )
        _plant_client(repo)
        bin_dir, _gh, _py = _stub_bin(
            tmp_path,
            claude_body=f"#!/bin/sh\ntouch {started}\nsleep 60\n",
        )
        (bin_dir / "node").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (bin_dir / "node").chmod(0o755)
        proc = subprocess.Popen(
            [BASH, str(_cloned_wrapper(repo, name)), "audit"],
            env=_env(tmp_path, repo, bin_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 30
        while not started.exists() and time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.1)
        assert started.exists(), (proc.poll(), proc.communicate(timeout=10))
        assert pid_file.exists()
        server_pid = int(pid_file.read_text(encoding="utf-8").strip())
        proc.send_signal(signal.SIGTERM)
        try:
            proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail("wrapper did not exit after SIGTERM")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _pid_exists(server_pid):
            time.sleep(0.05)
        assert not _pid_exists(server_pid), server_pid

    @pytest.mark.parametrize("name", sorted(HOST_LOOPS))
    def test_case5_wrapper_never_execs_clone_playwright(self, name):
        text = HOST_LOOPS[name].read_text(encoding="utf-8")
        assert not re.search(
            r"web/node_modules/(?:\.bin/playwright|playwright)",
            text,
        )

    def test_case6_skill_names_endpoint_and_mach_refusal(self):
        check = subprocess.run(
            ["python3", str(REPO / "scripts" / "render_loop_prompt.py"), "--check"],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        assert check.returncode == 0, check.stdout + check.stderr
        skill = (REPO / ".claude" / "skills" / "testing-weekend" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        assert "PW_TEST_CONNECT_WS_ENDPOINT" in skill
        assert "Permission denied (1100)" in skill
        assert "RADON_WEEKEND_BROWSER_HOST" in skill
        assert "bash scripts/setup_testing_weekend.sh" in skill

    @pytest.mark.parametrize("name", sorted(HOST_LOOPS))
    def test_start_browser_host_pins_absolute_tools(self, name):
        text = HOST_LOOPS[name].read_text(encoding="utf-8")
        start = text[text.index("start_browser_host() {") : text.index("\non_signal()")]
        assert "openssl rand" not in start.replace("/usr/bin/openssl rand", "")
        assert "/usr/bin/openssl" in start
        assert "/usr/bin/grep" in start
        assert "/usr/bin/tail" in start
        assert "refuse_symlink" in start
        assert "host smoke only" in start
        exited_at = start.index("unavailable:exited")
        assert "stop_browser_host" in start[exited_at - 80 : exited_at + 40]

    @pytest.mark.parametrize("name", sorted(HOST_LOOPS))
    def test_path_grep_cannot_poison_endpoint(self, name, tmp_path):
        repo = _runner_clone(tmp_path, name)
        home = tmp_path / "home"
        home.mkdir()
        _plant_host(home, body="#!/bin/sh\nsleep 30\n")
        _plant_client(repo)
        env_dump = tmp_path / "agent.env"
        bin_dir, _gh, _py = _stub_bin(
            tmp_path,
            claude_body=f"#!/bin/sh\nenv > {env_dump}\nexit 0\n",
        )
        (bin_dir / "node").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (bin_dir / "node").chmod(0o755)
        for tool, body in (
            ("grep", f'#!/bin/sh\necho "Listening on ws://127.0.0.1:9/evil"\n'),
            ("tail", f'#!/bin/sh\necho "Listening on ws://127.0.0.1:9/evil"\n'),
            ("openssl", "#!/bin/sh\necho planted\n"),
        ):
            path = bin_dir / tool
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)
        proc = subprocess.run(
            [BASH, str(_cloned_wrapper(repo, name)), "audit"],
            env=_env(tmp_path, repo, bin_dir),
            capture_output=True,
            text=True,
            timeout=60,
        )
        combined = _combined(proc, repo)
        assert "unavailable:no-endpoint" in combined, combined
        dumped = env_dump.read_text(encoding="utf-8") if env_dump.exists() else ""
        assert "PW_TEST_CONNECT_WS_ENDPOINT=ws://127.0.0.1:9/evil" not in dumped

    @pytest.mark.parametrize("name", sorted(HOST_LOOPS))
    def test_exited_reaps_orphaned_session_child(self, name, tmp_path):
        repo = _runner_clone(tmp_path, name)
        home = tmp_path / "home"
        home.mkdir()
        child_pid = tmp_path / "child.pid"
        setsid = "/usr/bin/setsid" if os.path.exists("/usr/bin/setsid") else ""
        spawn = (
            f'{setsid} /bin/sleep 120 &\n'
            if setsid
            else "/bin/sleep 120 &\n"
        )
        _plant_host(
            home,
            body=(
                "#!/bin/sh\n"
                f"{spawn}"
                f'echo $! > "{child_pid}"\n'
                "exit 133\n"
            ),
        )
        _plant_client(repo)
        env_dump = tmp_path / "agent.env"
        bin_dir, _gh, _py = _stub_bin(
            tmp_path,
            claude_body=f"#!/bin/sh\nenv > {env_dump}\nexit 0\n",
        )
        (bin_dir / "node").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (bin_dir / "node").chmod(0o755)
        proc = subprocess.run(
            [BASH, str(_cloned_wrapper(repo, name)), "audit"],
            env=_env(tmp_path, repo, bin_dir),
            capture_output=True,
            text=True,
            timeout=60,
        )
        combined = _combined(proc, repo)
        assert "unavailable:exited" in combined, combined
        assert child_pid.exists(), combined
        pid = int(child_pid.read_text(encoding="utf-8").strip())
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline and _pid_exists(pid):
            time.sleep(0.05)
        assert not _pid_exists(pid), pid

    @pytest.mark.parametrize("name", sorted(HOST_LOOPS))
    def test_symlink_playwright_outside_host_is_refused(self, name, tmp_path):
        repo = _runner_clone(tmp_path, name)
        home = tmp_path / "home"
        home.mkdir()
        _plant_host(home)
        _plant_client(repo)
        real = tmp_path / "evil-playwright"
        real.write_text("#!/bin/sh\necho planted\nexit 0\n", encoding="utf-8")
        real.chmod(0o755)
        bin_path = home / ".radon" / "agent-cli" / "browser-host" / "node_modules" / ".bin" / "playwright"
        bin_path.unlink()
        bin_path.symlink_to(real)
        env_dump = tmp_path / "agent.env"
        bin_dir, _gh, _py = _stub_bin(
            tmp_path,
            claude_body=f"#!/bin/sh\nenv > {env_dump}\nexit 0\n",
        )
        (bin_dir / "node").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (bin_dir / "node").chmod(0o755)
        proc = subprocess.run(
            [BASH, str(_cloned_wrapper(repo, name)), "audit"],
            env=_env(tmp_path, repo, bin_dir),
            capture_output=True,
            text=True,
            timeout=60,
        )
        combined = _combined(proc, repo)
        assert "unavailable:" in combined, combined
        dumped = env_dump.read_text(encoding="utf-8") if env_dump.exists() else ""
        assert "PW_TEST_CONNECT_WS_ENDPOINT=" not in dumped
