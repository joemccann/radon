"""scripts/runner/run_loop.sh: fresh clone of main, one agent session, one notification.

Every test drives the real script against a local bare origin with each agent
CLI, gh and curl stubbed on PATH. Stubs record argv, cwd, env and stdin.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "scripts" / "runner" / "run_loop.sh"
BASH = shutil.which("bash") or "/bin/bash"
PROMPT_BODY = "Audit the docs.\n"
PR_URL = "https://github.com/stub/radon/pull/7"

STUB = r"""#!/bin/bash
name="$(basename "$0")"
printf '%s\n' "$name $*" >> "$CALLS/argv"
pwd > "$CALLS/$name.cwd"
env > "$CALLS/$name.env"
case "$name" in codex|fx) cat > "$CALLS/$name.stdin" ;; esac
var="STUB_$(echo "$name" | tr a-z A-Z)"
eval "${!var:-exit 0}"
"""
GH_STUB = f"""#!/bin/bash
case "$*" in "pr list"*) echo "{PR_URL}" ;; esac
"""
CURL_STUB = """#!/bin/bash
printf '%s\\n' "$@" >> "$CALLS/curl"
"""


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def rig(tmp_path):
    origin_src = tmp_path / "origin-src"
    (origin_src / ".claude" / "runner-prompts").mkdir(parents=True)
    (origin_src / ".claude" / "runner-prompts" / "doc.md").write_text(PROMPT_BODY)
    _git("init", "-q", "-b", "main", cwd=origin_src)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "add", ".", cwd=origin_src)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init", cwd=origin_src)
    origin = tmp_path / "origin.git"
    _git("clone", "-q", "--bare", str(origin_src), str(origin), cwd=tmp_path)

    install = tmp_path / "install"
    (install / "loops").mkdir(parents=True)
    shutil.copy2(RUNNER, install / "run_loop.sh")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("claude", "codex", "grok", "agy", "fx"):
        (bin_dir / name).write_text(STUB)
    (bin_dir / "gh").write_text(GH_STUB)
    (bin_dir / "curl").write_text(CURL_STUB)
    for path in bin_dir.iterdir():
        path.chmod(0o755)

    calls = tmp_path / "calls"
    calls.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    (home / ".radon-runner.env").write_text("PUSHOVER_USER=u-dummy\nPUSHOVER_TOKEN='t-dummy'\nGH_TOKEN=gh-dummy\n")

    class Rig:
        state = home / "radon-runner"

        def configure(self, **values):
            body = {"REPO_URL": str(origin), "PROMPT": ".claude/runner-prompts/doc.md",
                    "AGENTS": "grok codex", "TIMEOUT_SECS": "60", "KILL_AFTER_SECS": "2", **values}
            (install / "loops" / "doc.env").write_text("".join(f'{k}="{v}"\n' for k, v in body.items()))

        def run(self, **stub_env):
            env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(home), "CALLS": str(calls),
                   "RADON_RUNNER_RETRY_PAUSE": "0", **stub_env}
            return subprocess.run([BASH, str(install / "run_loop.sh"), "doc"], env=env,
                                  capture_output=True, text=True, timeout=120)

        def called(self):
            path = calls / "argv"
            return [line.split()[0] for line in path.read_text().splitlines()] if path.exists() else []

        def notification(self):
            args = (calls / "curl").read_text().splitlines()
            return {a.split("=", 1)[0]: a.split("=", 1)[1] for a in args if "=" in a and not a.startswith("http")}

        def log(self):
            return "".join(p.read_text() for p in (self.state / "logs" / "doc").glob("*.log"))

    rig = Rig()
    rig.calls = calls
    rig.configure()
    return rig


def test_first_agent_succeeds_and_the_rest_never_run(rig):
    proc = rig.run(STUB_GROK="echo 'RESULT: https://x/pull/7 - fixed a stale runbook'; exit 0")

    assert proc.returncode == 0, proc.stderr + rig.log()
    assert rig.called() == ["grok"]
    note = rig.notification()
    assert note["title"] == "radon doc: done via grok"
    assert note["message"] == "RESULT: https://x/pull/7 - fixed a stale runbook"
    assert note["url"] == PR_URL
    assert note["token"] == "t-dummy" and note["user"] == "u-dummy"


def test_agent_runs_in_a_clone_of_main_with_the_header_and_prompt(rig):
    rig.run()

    work = rig.state / "work" / "doc"
    assert Path((rig.calls / "grok.cwd").read_text().strip()).resolve() == work.resolve()
    argv = (rig.calls / "argv").read_text().split()
    prompt = Path(argv[argv.index("--prompt-file") + 1]).read_text()
    header, body = prompt.split("\n\n", 1)
    assert header.startswith("Date: ") and "Branch: doc/" in header
    assert body == PROMPT_BODY
    assert "--always-approve" in argv


def test_agent_never_sees_the_notification_credentials(rig):
    rig.run()
    env = (rig.calls / "grok.env").read_text()
    assert "t-dummy" not in env and "u-dummy" not in env
    assert "GH_TOKEN=gh-dummy" in env


def test_a_failing_agent_falls_through_to_the_next(rig):
    proc = rig.run(STUB_GROK="exit 3")

    assert proc.returncode == 0
    assert rig.called() == ["grok", "codex"]
    assert (rig.calls / "codex.stdin").read_text().endswith(PROMPT_BODY)
    assert rig.notification()["title"] == "radon doc: done via codex"


def test_every_agent_failing_is_reported_as_failed(rig):
    proc = rig.run(STUB_GROK="exit 3", STUB_CODEX="exit 5")

    assert proc.returncode == 1
    note = rig.notification()
    assert note["title"] == "radon doc: FAILED"
    assert "grok=3" in note["message"] and "codex=5" in note["message"]


def test_a_timeout_stops_the_night_and_reaps_leftover_processes(rig, tmp_path):
    rig.configure(TIMEOUT_SECS="2")
    pidfile = tmp_path / "child.pid"
    proc = rig.run(STUB_GROK=f"sleep 300 & echo $! > {pidfile}; sleep 300")

    assert proc.returncode == 1
    assert rig.called() == ["grok"], "a timeout must not start another full budget"
    assert "grok=124" in rig.notification()["message"]
    child = int(pidfile.read_text())
    time.sleep(0.5)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


def test_leftover_processes_are_reaped_after_a_clean_exit(rig, tmp_path):
    pidfile = tmp_path / "server.pid"
    rig.run(STUB_GROK=f"(sleep 300 & echo $! > {pidfile}); exit 0")

    time.sleep(0.5)
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


def test_nothing_from_a_previous_night_survives(rig):
    work = rig.state / "work" / "doc"
    rig.run()
    (work / "planted.txt").write_text("x")
    hook = work / ".git" / "hooks" / "post-checkout"
    hook.write_text("#!/bin/sh\necho pwned\n")

    rig.run(STUB_GROK="test ! -e planted.txt && test ! -e .git/hooks/post-checkout")

    assert rig.notification()["title"].endswith("via grok")


def test_fx_rungs_pass_the_provider_and_the_prompt_on_stdin(rig):
    rig.configure(AGENTS="fx:nvidia")
    rig.run()

    assert "FX_PROVIDER=nvidia" in (rig.calls / "fx.env").read_text()
    assert (rig.calls / "fx.stdin").read_text().endswith(PROMPT_BODY)
    assert "fx ask --full-access --no-save" in (rig.calls / "argv").read_text()


def test_a_live_lock_skips_the_night(rig):
    lock = rig.state / "doc.lock"
    lock.mkdir(parents=True)
    (lock / "pid").write_text(str(os.getpid()))

    proc = rig.run()

    assert proc.returncode == 0
    assert rig.called() == []
    assert lock.exists(), "another run's lock is never removed"


def test_a_dead_lock_is_reclaimed(rig):
    lock = rig.state / "doc.lock"
    lock.mkdir(parents=True)
    (lock / "pid").write_text("999999")

    proc = rig.run()

    assert proc.returncode == 0
    assert rig.called() == ["grok"]
    assert not lock.exists()


def test_a_clone_failure_is_reported(rig, tmp_path):
    rig.configure(REPO_URL=str(tmp_path / "missing.git"))

    proc = rig.run()

    assert proc.returncode == 70
    assert rig.called() == []
    note = rig.notification()
    assert note["title"] == "radon doc: FAILED" and "could not clone" in note["message"]


def test_installed_daemon_runs_the_root_owned_runner_as_the_bot_user():
    import plistlib

    out = subprocess.run([BASH, str(REPO / "scripts" / "runner" / "install.sh"), "--print-plist", "documentation"],
                         capture_output=True, check=True)
    plist = plistlib.loads(out.stdout)
    assert plist["Label"] == "com.radon.runner.documentation"
    assert plist["UserName"] == "_radonbot"
    assert plist["ProgramArguments"] == ["/bin/bash", "/usr/local/radon-runner/run_loop.sh", "documentation"]
    assert plist["EnvironmentVariables"]["HOME"] == "/Users/_radonbot"
    assert plist["StartCalendarInterval"] == {"Hour": 3, "Minute": 0}
