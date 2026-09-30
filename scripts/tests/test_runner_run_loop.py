"""scripts/runner/run_loop.sh: fresh clone of main, one agent session, one notification.

Every test drives the real script against a local bare origin with each agent
CLI, gh and curl stubbed on PATH. Stubs record argv, cwd, env and stdin.
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
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
printf '%s\\n' "$*" >> "$CALLS/gh.argv"
case "$*" in "pr list"*) echo "{PR_URL}" ;; esac
"""
CURL_STUB = """#!/bin/bash
printf '%s\\n' "$@" >> "$CALLS/curl.argv"
echo --- >> "$CALLS/curl.argv"
cat >> "$CALLS/curl"
echo --- >> "$CALLS/curl"
"""


def _curl_config_value(raw):
    out, i = [], 0
    while i < len(raw):
        if raw[i] == "\\" and i + 1 < len(raw):
            out.append({"n": "\n", "r": "\r", "t": "\t"}.get(raw[i + 1], raw[i + 1]))
            i += 2
        else:
            out.append(raw[i])
            i += 1
    return "".join(out)


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

        def env(self, **stub_env):
            return {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(home), "CALLS": str(calls),
                    "RADON_RUNNER_CURL": str(bin_dir / "curl"),
                    "RADON_RUNNER_RETRY_PAUSE": "0", "RADON_RUNNER_PYTHON": sys.executable, **stub_env}

        def run(self, **stub_env):
            return subprocess.run([BASH, str(install / "run_loop.sh"), "doc"], env=self.env(**stub_env),
                                  capture_output=True, text=True, timeout=120)

        def start(self, **stub_env):
            return subprocess.Popen([BASH, str(install / "run_loop.sh"), "doc"], env=self.env(**stub_env),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        def called(self):
            path = calls / "argv"
            return [line.split()[0] for line in path.read_text().splitlines()] if path.exists() else []

        def notifications(self):
            path = calls / "curl"
            pages = path.read_text().split("---\n") if path.exists() else []
            parsed = []
            for page in pages:
                fields = {}
                for line in page.splitlines():
                    m = re.fullmatch(r'form-string = "(.*)"', line)
                    if m:
                        key, _, value = _curl_config_value(m.group(1)).partition("=")
                        fields[key] = value
                if fields:
                    parsed.append(fields)
            return parsed

        def curl_argv(self):
            path = calls / "curl.argv"
            return path.read_text() if path.exists() else ""

        def notification(self):
            return self.notifications()[-1]

        def hook(self, name, body):
            (install / "hooks").mkdir(exist_ok=True)
            (install / "hooks" / name).write_text("#!/bin/bash\n" + body)

        def resolver(self, body):
            (install / "lib").mkdir(exist_ok=True)
            (install / "lib" / "resolver.py").write_text(body)

        def guard(self, body="exec gh-real \"$@\"\n"):
            (install / "guard" / "doc").mkdir(parents=True, exist_ok=True)
            shim = install / "guard" / "doc" / "gh"
            shim.write_text("#!/bin/bash\n" + body)
            shim.chmod(0o755)
            return shim

        def log(self):
            return "".join(p.read_text() for p in (self.state / "logs" / "doc").glob("*.log"))

    rig = Rig()
    rig.calls = calls
    rig.install = install
    rig.work = home / "radon-runner" / "work" / "doc"
    rig.loop_state = home / "radon-runner" / "state" / "doc"
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


def test_pushover_credentials_stay_off_the_curl_command_line(rig):
    rig.configure()
    rig.run()
    page = rig.notification()
    assert page["token"] == "t-dummy" and page["user"] == "u-dummy"
    argv = rig.curl_argv()
    assert "t-dummy" not in argv and "u-dummy" not in argv
    assert "--config" in argv and "-q" in argv.splitlines()[0]


def test_a_quote_or_newline_in_a_page_cannot_break_the_curl_config(rig):
    rig.configure()
    rig.run(STUB_GROK='echo "RESULT: said \\"hi\\" \\\\ ok"; exit 0')
    assert rig.notification()["message"] == 'RESULT: said "hi" \\ ok'


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


def _lstart(pid):
    out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True).stdout
    return " ".join(out.split())


def test_a_live_lock_skips_the_night_and_pages_it(rig):
    lock = rig.state / "doc.lock"
    lock.mkdir(parents=True)
    (lock / "pid").write_text(str(os.getpid()))
    (lock / "start").write_text(_lstart(os.getpid()) + "\n")

    proc = rig.run()

    assert proc.returncode == 0
    assert rig.called() == []
    assert lock.exists(), "another run's lock is never removed"
    assert rig.notification()["title"] == "radon doc: skipped"


@pytest.mark.parametrize("start", ["Mon Jan  1 00:00:00 2001"])
def test_a_lock_whose_pid_was_reused_is_reclaimed(rig, start):
    lock = rig.state / "doc.lock"
    lock.mkdir(parents=True)
    (lock / "pid").write_text(str(os.getpid()))
    if start:
        (lock / "start").write_text(start + "\n")

    proc = rig.run()

    assert proc.returncode == 0
    assert rig.called() == ["grok"]


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
    # Per-user CLI installs (agy and fx in ~/.local/bin, grok in ~/.grok/bin) go on the agent's PATH
    # only; run_loop.sh prepends them there. codex comes from Homebrew.
    assert plist["EnvironmentVariables"]["PATH"] == "/opt/homebrew/bin:/usr/bin:/bin"
    assert 'AGENT_PATH_PREFIX="$HOME/.local/bin:$HOME/.grok/bin:$HOME/.bun/bin"' in RUNNER.read_text()
    assert plist["StartCalendarInterval"] == {"Hour": 3, "Minute": 0}


def test_ci_performance_runs_on_the_runner_in_the_old_loops_slot():
    """ci-performance moved off scripts/ci_performance_nightly.sh: same branch
    prefix and 00:20 start, and a prompt from main that ends on a RESULT line."""
    import plistlib

    out = subprocess.run([BASH, str(REPO / "scripts" / "runner" / "install.sh"), "--print-plist", "ci-performance"],
                         capture_output=True, check=True)
    plist = plistlib.loads(out.stdout)
    assert plist["Label"] == "com.radon.runner.ci-performance"
    assert plist["UserName"] == "_radonbot"
    assert plist["ProgramArguments"] == ["/bin/bash", "/usr/local/radon-runner/run_loop.sh", "ci-performance"]
    assert plist["StartCalendarInterval"] == {"Hour": 0, "Minute": 20}

    env = (REPO / "scripts" / "runner" / "loops" / "ci-performance.env").read_text()
    assert "\nBRANCH_PREFIX=ci-performance\n" in env
    prompt = REPO / ".claude" / "runner-prompts" / "ci-performance.md"
    assert f"\nPROMPT={prompt.relative_to(REPO)}\n" in env
    body = prompt.read_text()
    assert "RESULT: <PR URL>" in body and "RESULT: no PR" in body
    assert not (REPO / "scripts" / "ci_performance_nightly.sh").exists()
    assert not (REPO / "config" / "com.radon.ci-performance-daily.plist").exists()


@pytest.mark.parametrize(
    "loop,hour,minute,retired",
    [
        ("testing", 0, 10, ("scripts/testing_weekend.sh", "scripts/setup_testing_weekend.sh",
                            "config/com.radon.testing-daily.plist", ".claude/skills/testing-weekend")),
        ("reliability", 0, 0, ("scripts/reliability_weekend.sh", "scripts/setup_reliability_weekend.sh",
                               "config/com.radon.reliability-daily.plist", ".claude/skills/reliability-weekend")),
    ],
)
def test_testing_and_reliability_run_on_the_runner_in_their_old_slots(loop, hour, minute, retired):
    """Cut over from the per-loop wrappers: same branch prefix (so PR history
    and the rolling issue stay continuous) and start time, a prompt read from
    main that ends on a RESULT line, and the old launcher gone."""
    import plistlib

    out = subprocess.run([BASH, str(REPO / "scripts" / "runner" / "install.sh"), "--print-plist", loop],
                         capture_output=True, check=True)
    plist = plistlib.loads(out.stdout)
    assert plist["Label"] == f"com.radon.runner.{loop}"
    assert plist["UserName"] == "_radonbot"
    assert plist["ProgramArguments"] == ["/bin/bash", "/usr/local/radon-runner/run_loop.sh", loop]
    assert plist["StartCalendarInterval"] == {"Hour": hour, "Minute": minute}

    env = (REPO / "scripts" / "runner" / "loops" / f"{loop}.env").read_text()
    assert f"\nBRANCH_PREFIX={loop}\n" in env
    prompt = REPO / ".claude" / "runner-prompts" / f"{loop}.md"
    assert f"\nPROMPT={prompt.relative_to(REPO)}\n" in env
    body = prompt.read_text()
    assert "RESULT: <PR URL>" in body and "RESULT: no PR" in body
    assert f"--label {loop}-nightly" in body
    assert "audited-through:" in body
    for path in retired:
        assert not (REPO / path).exists(), path


# --- claude rungs -----------------------------------------------------------


def _claude_argv(rig):
    return (rig.calls / "argv").read_text()


def test_a_claude_rung_pins_the_model_effort_and_the_background_ceiling(rig):
    rig.configure(AGENTS="claude:claude-opus-5")
    proc = rig.run()

    assert proc.returncode == 0, proc.stderr + rig.log()
    argv = _claude_argv(rig)
    assert "--model claude-opus-5 --effort medium" in argv
    assert "--disallowedTools ScheduleWakeup Monitor CronCreate" in argv
    env = (rig.calls / "claude.env").read_text().splitlines()
    assert "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0" in env
    # A nested claude (the security Stage 4 scan) reads the rung from here.
    assert "RADON_RUNNER_AGENT=claude" in env and "RADON_RUNNER_MODEL=claude-opus-5" in env


def test_a_bare_claude_rung_passes_no_model_but_still_medium_effort(rig):
    rig.configure(AGENTS="claude")
    rig.run()

    argv = _claude_argv(rig)
    assert "--model" not in argv
    assert "--effort medium" in argv


# --- loop-env knobs -----------------------------------------------------------


def test_agent_env_is_set_and_agent_unset_is_removed_and_named_without_its_value(rig):
    rig.configure(AGENT_ENV="RADON_LADDER_NO_AUTH_FILES=1 RADON_WEEKEND_BROWSER_HOST=unavailable:disabled",
                  AGENT_UNSET="ANTHROPIC_API_KEY CLAUDE_CODE_USE_BEDROCK")
    rig.run(ANTHROPIC_API_KEY="sk-ant-dummy-value", CLAUDE_CODE_USE_BEDROCK="1")

    env = (rig.calls / "grok.env").read_text().splitlines()
    assert "RADON_LADDER_NO_AUTH_FILES=1" in env
    assert "RADON_WEEKEND_BROWSER_HOST=unavailable:disabled" in env
    assert not any(line.startswith(("ANTHROPIC_API_KEY=", "CLAUDE_CODE_USE_BEDROCK=")) for line in env)
    log = rig.log()
    assert "IGNORING: ANTHROPIC_API_KEY" in log and "IGNORING: CLAUDE_CODE_USE_BEDROCK" in log
    assert "sk-ant-dummy-value" not in log


def test_a_rung_outside_allowed_agents_is_refused_before_cloning(rig):
    rig.configure(AGENTS="claude grok", ALLOWED_AGENTS="claude")
    proc = rig.run()

    assert proc.returncode == 2
    assert rig.called() == []
    assert not rig.work.exists()
    notes = rig.notifications()
    assert len(notes) == 1 and "REFUSED" in notes[0]["title"]
    assert "grok" in notes[0]["message"]


def test_the_resolver_replaces_agents(rig):
    rig.configure(AGENTS="claude:safety-model", ALLOWED_AGENTS="claude", AGENTS_RESOLVER="lib/resolver.py")
    rig.resolver("import sys\nassert sys.argv[1:] == ['--rungs'] and sys.flags.isolated\nprint('claude:resolved-model')\n")
    proc = rig.run()

    assert proc.returncode == 0, proc.stderr + rig.log()
    assert "--model resolved-model" in _claude_argv(rig)


def test_an_empty_or_failed_resolver_keeps_agents_as_the_safety_ladder(rig):
    rig.configure(AGENTS="claude:safety-model", AGENTS_RESOLVER="lib/resolver.py")
    rig.resolver("import sys\nsys.exit(1)\n")
    rig.run()

    assert "--model safety-model" in _claude_argv(rig)
    assert "safety ladder claude:safety-model" in rig.log()


def test_a_resolver_naming_a_disallowed_agent_is_refused(rig):
    rig.configure(AGENTS="claude", ALLOWED_AGENTS="claude", AGENTS_RESOLVER="lib/resolver.py")
    rig.resolver("print('codex')\n")
    proc = rig.run()

    assert proc.returncode == 2
    assert rig.called() == []


# --- persistent state and KEEP_PATHS -----------------------------------------


def test_loop_state_is_private_persistent_and_exported(rig):
    rig.run(STUB_GROK='echo night1 > "$RADON_RUNNER_LOOP_STATE/memo"')
    rig.run(STUB_GROK='test "$(cat "$RADON_RUNNER_LOOP_STATE/memo")" = night1 || exit 9')

    assert rig.notification()["title"].endswith("via grok")
    assert (rig.loop_state.stat().st_mode & 0o777) == 0o700
    env = (rig.calls / "grok.env").read_text().splitlines()
    assert f"RADON_RUNNER_LOOP_STATE={rig.loop_state}" in env
    assert f"RADON_RUNNER_PIDFILE={rig.loop_state}/pids" in env


def test_keep_paths_survive_the_nightly_reclone_and_nothing_else_does(rig):
    rig.configure(KEEP_PATHS=".deepsec data/radon")
    rig.run(STUB_GROK="mkdir -p .deepsec/node_modules data/radon && echo pin > .deepsec/pin "
                      "&& echo st > data/radon/state.json && echo x > planted.txt")
    first_work = rig.work.resolve()

    rig.run(STUB_GROK="test \"$(cat .deepsec/pin)\" = pin && test -f data/radon/state.json "
                      "&& test -d .deepsec/node_modules && test ! -e planted.txt || exit 9")

    assert rig.notification()["title"].endswith("via grok"), rig.log()
    assert rig.work.resolve() == first_work, "WORK's path is stable, so rootPath stays valid"
    assert not [p for p in (rig.loop_state / "keep").rglob("*") if p.is_file()], "kept paths are back in the clone"


def test_an_operator_seeded_or_crash_stranded_keep_dir_is_restored(rig):
    rig.configure(KEEP_PATHS=".deepsec")
    seeded = rig.loop_state / "keep" / ".deepsec"
    seeded.mkdir(parents=True)
    (seeded / "deepsec.config.ts").write_text("seeded\n")

    rig.run(STUB_GROK="grep -q seeded .deepsec/deepsec.config.ts || exit 9")

    assert rig.notification()["title"].endswith("via grok"), rig.log()


# --- phases and hooks ---------------------------------------------------------


def test_phases_run_in_order_each_with_its_own_header_budget_and_page(rig):
    rig.configure(PHASES="audit:60 deliver:120")
    proc = rig.run()

    assert proc.returncode == 0, proc.stderr + rig.log()
    assert rig.called() == ["grok", "grok"]
    prompts = sorted((rig.state / "logs" / "doc").glob("*.prompt.md"))
    headers = [p.read_text().split("\n\n", 1)[0] for p in prompts]
    assert any("Phase: audit" in h and "Time budget: 1 minutes" in h for h in headers)
    assert any("Phase: deliver" in h and "Time budget: 2 minutes" in h for h in headers)
    assert all(f"State: {rig.loop_state}" in h for h in headers)
    titles = [n["title"] for n in rig.notifications()]
    assert titles == ["radon doc audit", "radon doc deliver"]


def test_every_phase_runs_whatever_the_earlier_rc_and_the_night_exits_75(rig):
    rig.configure(AGENTS="grok", PHASES="audit:60 remediate:60")
    proc = rig.run(STUB_GROK='test -e "$CALLS/grok.ran" && exit 0; touch "$CALLS/grok.ran"; exit 3')

    assert rig.called() == ["grok", "grok"]
    assert proc.returncode == 75
    messages = [n["message"] for n in rig.notifications()]
    assert messages[0].startswith("FAILED") and messages[1].startswith("OK")


def test_post_run_reads_only_its_phase_slice_and_drives_the_page(rig):
    rig.configure(PHASES="audit:60 deliver:60", POST_RUN="hooks/post.sh")
    rig.hook("post.sh", 'env > "$CALLS/post.$PHASE.env"\ncp "$PHASE_LOG" "$CALLS/slice.$PHASE"\n'
                        'echo "status=OK: $PHASE done"\necho "pr_url=https://x/pull/$PHASE"\n'
                        'echo "report_url=https://reports/$PHASE.md"\n')
    rig.run(STUB_GROK='grep "^Phase:" "$PROMPT_FILE"')

    assert "Phase: audit" in (rig.calls / "slice.audit").read_text()
    assert "deliver" not in (rig.calls / "slice.audit").read_text()
    assert "Phase: deliver" in (rig.calls / "slice.deliver").read_text()
    env = (rig.calls / "post.deliver.env").read_text().splitlines()
    for key in ("LOOP=doc", "PHASE=deliver", "PHASE_RC=0", f"WORK={rig.work}", f"LOOP_STATE={rig.loop_state}"):
        assert key in env, key
    assert any(line.startswith("BRANCH=doc/") for line in env)
    assert any(line.startswith("PHASE_START_MARK=") for line in env)
    note = rig.notifications()[1]
    assert note["title"] == "radon doc deliver"
    assert note["message"] == "OK: deliver done https://x/pull/deliver"
    assert note["url"] == "https://reports/deliver.md"
    assert note["url_title"] == "Open private report"


@pytest.mark.parametrize("post", ["sleep 30\n", "exit 3\n", None])
def test_a_post_run_that_gives_no_status_fails_the_phase(rig, post):
    rig.configure(PHASES="audit:60", POST_RUN="hooks/post.sh")
    if post is not None:
        rig.hook("post.sh", post)
    proc = rig.run(RADON_RUNNER_HOOK_SECS="2")

    assert proc.returncode == 75, rig.log()
    assert rig.notification()["message"].startswith("FAILED (post-run hook gave no status)")


def test_the_first_status_line_stands_when_post_run_is_killed_later(rig):
    rig.configure(PHASES="audit:60", POST_RUN="hooks/post.sh")
    rig.hook("post.sh", 'echo "status=INCOMPLETE (no marker)"\nsleep 30\n')
    proc = rig.run(RADON_RUNNER_HOOK_SECS="2")

    assert proc.returncode == 75
    assert rig.notification()["message"] == "INCOMPLETE (no marker)"


def test_a_pinned_base_tells_the_agent_to_branch_from_head(rig):
    rig.configure(PHASES="audit:60", PRE_RUN="hooks/pre.sh")
    rig.hook("pre.sh", "exit 0\n")
    rig.run()

    header = next((rig.state / "logs" / "doc").glob("*.audit.prompt.md")).read_text().split("\n\n", 1)[0]
    assert "create it from HEAD" in header and "origin/main" not in header


def test_hooks_get_the_runner_path_and_the_agent_gets_its_cli_dirs(rig):
    rig.configure(PHASES="audit:60", PRE_RUN="hooks/pre.sh")
    rig.hook("pre.sh", 'echo "$PATH" > "$CALLS/pre.path"\n')
    runner_path = f"{Path(shutil.which('gtimeout') or shutil.which('timeout')).parent}:/usr/bin:/bin"
    rig.run(RADON_RUNNER_PATH=runner_path)

    assert (rig.calls / "pre.path").read_text().strip() == runner_path
    env = (rig.calls / "grok.env").read_text().splitlines()
    path = next(line for line in env if line.startswith("PATH=")).split("=", 1)[1].split(":")
    home = rig.state.parent
    assert path[:3] == [f"{home}/.local/bin", f"{home}/.grok/bin", f"{home}/.bun/bin"]
    assert "GIT_CONFIG_NOSYSTEM=1" in env
    assert f"GIT_CONFIG_GLOBAL={rig.install}/gitconfig" in env


def test_sigterm_during_a_slow_pre_run_pages_killed_at_once(rig, tmp_path):
    rig.configure(PHASES="audit:60", PRE_RUN="hooks/pre.sh", POST_RUN="hooks/post.sh")
    marker = tmp_path / "hook.pid"
    rig.hook("pre.sh", f"echo $$ > {marker}; sleep 300\n")
    rig.hook("post.sh", 'echo "status=[$PHASE_STATUS]" >> "$CALLS/post"\n')
    proc = rig.start()
    for _ in range(100):
        if marker.exists() and marker.read_text().strip():
            break
        time.sleep(0.1)
    started = time.monotonic()
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=30)

    assert time.monotonic() - started < 10
    assert proc.returncode == 143
    assert _gone(int(marker.read_text()))
    assert "KILLED" in rig.notifications()[0]["message"]
    assert "status=[KILLED" in (rig.calls / "post").read_text()
    assert rig.called() == []


def test_a_failing_pre_run_skips_the_agent_and_the_night_exits_2(rig):
    rig.configure(PHASES="audit:60 remediate:60", PRE_RUN="hooks/pre.sh", POST_RUN="hooks/post.sh")
    rig.hook("pre.sh", 'if [ "$PHASE" = audit ]; then echo "refused=credential file present"; exit 2; fi\n')
    rig.hook("post.sh", 'echo "$PHASE refused=[$PHASE_REFUSED]" >> "$CALLS/post"\n')
    proc = rig.run()

    assert proc.returncode == 2
    assert rig.called() == ["grok"], "the refused phase ran no agent; the next phase still ran"
    post = (rig.calls / "post").read_text()
    assert "audit refused=[credential file present]" in post
    assert "remediate refused=[]" in post
    assert rig.notifications()[0]["message"].startswith("REFUSED")


def test_gh_guard_puts_the_root_owned_shim_first_on_the_agents_path(rig):
    rig.configure(GH_GUARD="1")
    rig.guard()
    rig.run()

    path = next(line for line in (rig.calls / "grok.env").read_text().splitlines() if line.startswith("PATH="))
    assert path.split("=", 1)[1].split(":")[0] == str(rig.install / "guard" / "doc")


def test_gh_guard_without_its_shim_is_refused(rig):
    rig.configure(GH_GUARD="1")
    proc = rig.run()

    assert proc.returncode == 2
    assert rig.called() == []


def test_pr_lookup_ignores_fork_prs(rig):
    rig.run()

    lookup = next(line for line in (rig.calls / "gh.argv").read_text().splitlines() if line.startswith("pr list"))
    assert "isCrossRepository" in lookup
    assert "select(.isCrossRepository==false)" in lookup


# --- reaping ----------------------------------------------------------------


DETACH = ("python3 -c \"import subprocess,sys; p=subprocess.Popen(['sleep','300'], start_new_session=True, "
          "cwd=sys.argv[1]); open(sys.argv[2],'w').write(str(p.pid))\" {cwd} {out}")


def _gone(pid):
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


def test_a_detached_process_working_in_the_clone_is_reaped(rig, tmp_path):
    pidfile = tmp_path / "detached.pid"
    rig.run(STUB_GROK=DETACH.format(cwd=".", out=pidfile))

    assert _gone(int(pidfile.read_text()))


def test_a_declared_detached_pid_outside_the_clone_is_reaped_and_an_undeclared_one_is_not(rig, tmp_path):
    declared, stranger = tmp_path / "declared.pid", tmp_path / "stranger.pid"
    rig.run(STUB_GROK=DETACH.format(cwd=tmp_path, out=declared) + ' && cat ' + str(declared)
            + ' >> "$RADON_RUNNER_PIDFILE" && echo >> "$RADON_RUNNER_PIDFILE"; '
            + DETACH.format(cwd=tmp_path, out=stranger))

    assert _gone(int(declared.read_text()))
    survivor = int(stranger.read_text())
    try:
        os.kill(survivor, 0)
    finally:
        os.kill(survivor, signal.SIGKILL)


def test_a_declared_pid_of_another_loops_run_is_not_reaped(rig, tmp_path):
    other_pid = tmp_path / "other.pid"
    lock = rig.state / "other.lock"
    lock.mkdir(parents=True)
    rig.run(STUB_GROK=DETACH.format(cwd=tmp_path, out=other_pid) + f" && cp {other_pid} {lock}/pid"
            + f' && cat {other_pid} >> "$RADON_RUNNER_PIDFILE" && echo >> "$RADON_RUNNER_PIDFILE"')

    pid = int(other_pid.read_text())
    try:
        os.kill(pid, 0)
        assert f"not reaping declared pid {pid}" in rig.log()
    finally:
        os.kill(pid, signal.SIGKILL)


def test_sigterm_kills_the_agent_pages_killed_and_runs_post_run(rig, tmp_path):
    rig.configure(PHASES="audit:60", POST_RUN="hooks/post.sh")
    rig.hook("post.sh", 'echo "status=[$PHASE_STATUS]" >> "$CALLS/post"\n')
    marker = tmp_path / "agent.pid"
    proc = rig.start(STUB_GROK=f"echo $$ > {marker}; sleep 300")
    for _ in range(100):
        if marker.exists() and marker.read_text().strip():
            break
        time.sleep(0.1)
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=30)

    assert proc.returncode == 143
    assert _gone(int(marker.read_text()))
    assert "KILLED" in rig.notifications()[0]["message"]
    assert "status=[KILLED" in (rig.calls / "post").read_text()
    assert not (rig.state / "doc.lock").exists()


# --- install.sh ---------------------------------------------------------------


def _plist(loop):
    import plistlib

    out = subprocess.run([BASH, str(REPO / "scripts" / "runner" / "install.sh"), "--print-plist", loop],
                         capture_output=True, check=True)
    return plistlib.loads(out.stdout)


def test_every_daemon_disables_auto_update_and_has_a_minute_to_page_on_stop():
    plist = _plist("documentation")
    assert plist["EnvironmentVariables"]["DISABLE_AUTOUPDATER"] == "1"
    assert plist["ExitTimeOut"] == 60


def test_the_daemon_path_holds_no_bot_writable_directory():
    assert _plist("security")["EnvironmentVariables"]["PATH"] == "/opt/homebrew/bin:/usr/bin:/bin"


def test_every_runner_git_reads_the_root_owned_gitconfig():
    out = subprocess.run([BASH, str(REPO / "scripts" / "runner" / "install.sh"), "--print-gitconfig"],
                         capture_output=True, text=True, check=True).stdout
    assert "helper = !/opt/homebrew/bin/gh auth git-credential" in out
    src = (REPO / "scripts" / "runner" / "install.sh").read_text()
    assert 'print_gitconfig > "$PREFIX/gitconfig"' in src
    assert "git config --global" not in src


def test_install_copies_hooks_helpers_and_the_bot_state_dir():
    src = (REPO / "scripts" / "runner" / "install.sh").read_text()
    for helper in ("nightly_pr_guard.py", "nightly_publish.py", "nightly_issue_prune.py", "nightly_green_base.py",
                   "nightly_audit_context.py", "nightly_deliver.py", "security_claude_ladder.py",
                   "claude_cli_env_drift.py", "claude_cli_env_reviewed.txt"):
        assert helper in src, helper
        assert (REPO / "scripts" / helper).is_file(), helper
    assert '"$SRC/hooks/"*' in src
    assert 'for dir in "$BOT_HOME/radon-runner" "$BOT_HOME/radon-runner/state"; do' in src
    assert 'as_bot /bin/mkdir -p -m 700 "$dir"' in src
    # Root never writes, chmods or chowns a path inside the bot's home.
    configure = src.split("configure_user() {", 1)[1].split("\n}\n", 1)[0]
    for line in configure.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(("local", "for ", "done", "sh ")):
            assert stripped.startswith("as_bot "), stripped


def test_the_gh_guard_shim_routes_pr_api_issue_and_alias_to_the_guard(tmp_path):
    calls = tmp_path / "calls"
    env = {**os.environ, "RADON_RUNNER_PREFIX": str(tmp_path / "prefix"),
           "RADON_RUNNER_GH": str(tmp_path / "gh-real"), "RADON_RUNNER_PYTHON": str(tmp_path / "py")}
    shim = subprocess.run([BASH, str(REPO / "scripts" / "runner" / "install.sh"), "--print-guard", "security"],
                          env=env, capture_output=True, text=True, check=True).stdout
    for name in ("gh-real", "py"):
        (tmp_path / name).write_text(f'#!/bin/bash\necho "{name} $*" >> {calls}\n')
        (tmp_path / name).chmod(0o755)
    (tmp_path / "gh").write_text(shim)
    (tmp_path / "gh").chmod(0o755)

    for argv in (["pr", "merge", "5"], ["-R", "o/r", "issue", "comment", "1"], ["api", "x"], ["alias", "set"],
                 ["run", "view", "7"]):
        subprocess.run([str(tmp_path / "gh"), *argv], env=env, check=True)

    lines = calls.read_text().splitlines()
    guard = f"-I {tmp_path / 'prefix'}/lib/nightly_pr_guard.py"
    assert lines[:4] == [f"py {guard} pr merge 5", f"py {guard} -R o/r issue comment 1",
                         f"py {guard} api x", f"py {guard} alias set"]
    assert lines[4] == "gh-real run view 7"
    assert "export RADON_NIGHTLY_LOOP=security" in shim
    assert f"export RADON_NIGHTLY_REAL_GH={tmp_path / 'gh-real'}" in shim


# --- the security loops -----------------------------------------------------


@pytest.mark.parametrize("loop,hour,minute,marker,audit_secs,keep", [
    ("security", 0, 40, "SECURITY-NIGHTLY PHASE COMPLETE:", 7200, None),
    ("security-deepsec", 0, 50, "SECURITY-DEEPSEC PHASE COMPLETE:", 28800, ".deepsec data/radon"),
])
def test_security_loops_run_on_the_runner_in_their_old_slots(loop, hour, minute, marker, audit_secs, keep):
    plist = _plist(loop)
    assert plist["Label"] == f"com.radon.runner.{loop}"
    assert plist["UserName"] == "_radonbot"
    assert plist["StartCalendarInterval"] == {"Hour": hour, "Minute": minute}

    text = (REPO / "scripts" / "runner" / "loops" / f"{loop}.env").read_text()
    env = {}
    for line in text.splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            env[key] = value.strip('"')
    assert env["BRANCH_PREFIX"] == loop
    assert env["PROMPT"] == f".claude/runner-prompts/{loop}.md"
    assert env["ALLOWED_AGENTS"] == "claude"
    assert env["AGENTS_RESOLVER"] == "lib/security_claude_ladder.py"
    assert env["AGENTS"] == "claude:claude-opus-5 claude:claude-sonnet-5"
    assert env["PHASES"] == f"audit:{audit_secs} remediate:21600 deliver:10800"
    assert env["GH_GUARD"] == "1"
    assert env["PRE_RUN"] == "hooks/security_pre.sh" and env["POST_RUN"] == "hooks/security_post.sh"
    assert "RADON_LADDER_NO_AUTH_FILES=1" in env["AGENT_ENV"].split()
    unset = env["AGENT_UNSET"].split()
    for name in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
                 "OPENAI_API_KEY", "CODEX_API_KEY", "PW_TEST_CONNECT_WS_ENDPOINT"):
        assert name in unset, name
    assert env.get("KEEP_PATHS") == keep
    for hook in ("security_pre.sh", "security_post.sh"):
        assert (REPO / "scripts" / "runner" / "hooks" / hook).is_file()

    body = (REPO / ".claude" / "runner-prompts" / f"{loop}.md").read_text()
    assert marker in body and "RESULT: " in body
    assert "$RADON_RUNNER_LOOP_STATE/scratch" in body
    assert '--model "$RADON_RUNNER_MODEL"' in body or loop == "security-deepsec"
    assert "held.git" in body
    assert "~/radon-weekend" not in body
    retired = {"security": ("scripts/security_nightly.sh", "config/com.radon.security-daily.plist",
                            ".claude/skills/security-nightly"),
               "security-deepsec": ("scripts/security_deepsec_nightly.sh", "config/com.radon.security-deepsec.plist",
                                    ".claude/skills/security-deepsec")}[loop]
    for path in retired:
        assert not (REPO / path).exists(), path


@pytest.mark.parametrize("metadata", ["empty", "live-without-start", "unreadable-start"])
def test_rel291_uncertain_lock_never_replaces_an_active_clone(rig, metadata):
    """REL-291 / R-710: incomplete ownership is not evidence of a dead run."""
    lock = rig.state / "doc.lock"
    lock.mkdir(parents=True)
    if metadata != "empty":
        (lock / "pid").write_text(str(os.getpid()))
    if metadata == "unreadable-start":
        (lock / "start").write_text(_lstart(os.getpid()) + "\n")
        script = rig.install / "run_loop.sh"
        text = script.read_text()
        text = text.replace('proc_start() { ps ', 'proc_start() { return 1; ps ')
        script.write_text(text)
    rig.work.mkdir(parents=True)
    sentinel = rig.work / "uncommitted-work"
    sentinel.write_text("active agent's work")

    proc = rig.run()

    assert rig.called() == [], "uncertain ownership must refuse before fresh_clone"
    assert sentinel.read_text() == "active agent's work"
    assert lock.exists()
    assert proc.returncode != 0


def test_rel291_only_one_contender_can_reclaim_a_stale_lock(rig):
    """Another claimant may be between ownership recheck and replacement."""
    lock = rig.state / "doc.lock"
    (lock / "reaping").mkdir(parents=True)
    (lock / "pid").write_text("999999")
    rig.work.mkdir(parents=True)
    sentinel = rig.work / "uncommitted-work"
    sentinel.write_text("preserve")

    proc = rig.run()

    assert proc.returncode != 0
    assert rig.called() == []
    assert sentinel.read_text() == "preserve"
    assert (lock / "reaping").is_dir()


# --- install.sh: source-tree trust -----------------------------------------------

_LIB = ("nightly_pr_guard.py", "nightly_publish.py", "nightly_issue_prune.py", "nightly_green_base.py",
        "nightly_audit_context.py", "nightly_deliver.py", "security_claude_ladder.py",
        "claude_cli_env_drift.py", "claude_cli_env_reviewed.txt")


def _source_tree(tmp_path):
    """A private copy of the tree install.sh copies from: scripts/runner plus the lib helpers."""
    root = tmp_path / "clone"
    shutil.copytree(REPO / "scripts" / "runner", root / "scripts" / "runner")
    for name in _LIB:
        shutil.copy2(REPO / "scripts" / name, root / "scripts" / name)
    for path in [root, *root.rglob("*")]:
        path.chmod(0o755 if path.is_dir() or path.suffix == ".sh" else 0o644)
    return root


def _install(root, *args, **env):
    env = {**{k: v for k, v in os.environ.items() if k != "RADON_RUNNER_INSTALL_TRUSTED_UID"}, **env}
    return subprocess.run([BASH, str(root / "scripts" / "runner" / "install.sh"), *args],
                          env=env, capture_output=True, text=True)


@pytest.mark.skipif(os.geteuid() == 0, reason="the refusal path is exercised as an unprivileged user")
def test_install_proceeds_past_the_trust_check_on_a_private_operator_owned_tree(tmp_path):
    out = _install(_source_tree(tmp_path), "documentation")
    assert out.returncode == 1
    assert "untrusted" not in out.stderr
    assert "run with sudo" in out.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="the refusal path is exercised as an unprivileged user")
@pytest.mark.parametrize("rel", ["", "scripts", "scripts/runner", "scripts/runner/hooks",
                                 "scripts/runner/loops/documentation.env", "scripts/runner/run_loop.sh",
                                 "scripts/nightly_pr_guard.py"])
@pytest.mark.parametrize("mode", [0o777, 0o775, 0o1777])
def test_install_refuses_a_group_or_world_writable_source_tree(tmp_path, rel, mode):
    root = _source_tree(tmp_path)
    target = root / rel
    target.chmod(mode if target.is_dir() else mode & 0o777)
    out = _install(root, "documentation")
    assert out.returncode != 0
    assert "untrusted" in out.stderr, out.stderr
    assert "run with sudo" not in out.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="the refusal path is exercised as an unprivileged user")
def test_install_refuses_a_group_writable_ancestor_of_the_source_tree(tmp_path):
    root = _source_tree(tmp_path / "parent")
    (tmp_path / "parent").chmod(0o777)
    out = _install(root, "documentation")
    assert out.returncode != 0
    assert "untrusted" in out.stderr and str((tmp_path / "parent").resolve()) in out.stderr, out.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="the refusal path is exercised as an unprivileged user")
def test_install_accepts_a_sticky_world_writable_ancestor(tmp_path):
    root = _source_tree(tmp_path / "shared")
    (tmp_path / "shared").chmod(0o1777)
    out = _install(root, "documentation")
    assert "untrusted" not in out.stderr, out.stderr
    assert "run with sudo" in out.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="the refusal path is exercised as an unprivileged user")
def test_install_refuses_a_source_tree_owned_by_another_user(tmp_path):
    # Files cannot be chowned without root, so narrow the trusted uid instead.
    out = _install(_source_tree(tmp_path), "documentation", RADON_RUNNER_INSTALL_TRUSTED_UID="424242")
    assert out.returncode != 0
    assert "untrusted" in out.stderr, out.stderr
    assert "run with sudo" not in out.stderr


def test_install_checks_the_source_before_any_install_action():
    src = (REPO / "scripts" / "runner" / "install.sh").read_text()
    main = src.split("main() {", 1)[1]
    assert main.index("check_source") < main.index("ensure_user")
    assert main.index("check_source") < main.index("install_runner")
    # The narrowing seam is ignored when running as root.
    check = src.split("check_source() {", 1)[1].split("\n}\n", 1)[0]
    assert "EUID" in check and "SUDO_UID" in check


def test_runner_docs_never_install_from_a_fixed_tmp_path():
    doc = (REPO / "docs" / "runner.md").read_text()
    assert "/tmp/radon-runner-install" not in doc
    assert "mktemp -d" in doc
    # Nothing root installs may pass through a shared fixed /tmp path either.
    for line in doc.splitlines():
        assert not ("sudo" in line and "/tmp/" in line), line


@pytest.mark.skipif(os.geteuid() == 0, reason="the refusal path is exercised as an unprivileged user")
def test_install_refuses_a_symlink_among_the_copied_files(tmp_path):
    root = _source_tree(tmp_path)
    elsewhere = tmp_path / "elsewhere.sh"
    elsewhere.write_text("#!/bin/bash\n")
    (root / "scripts" / "runner" / "hooks" / "security_pre.sh").unlink()
    (root / "scripts" / "runner" / "hooks" / "security_pre.sh").symlink_to(elsewhere)
    out = _install(root, "documentation")
    assert out.returncode != 0
    assert "untrusted" in out.stderr, out.stderr
