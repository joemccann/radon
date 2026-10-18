"""Pin the green base and consume a separate helper's snapshot, without SSH.

Drives the real hook against a local bare origin with the read helper stubbed.
The real helper's output and credential boundaries have separate coverage.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / "scripts" / "runner" / "hooks" / "performance_pre.sh"
LOOP_ENV = REPO / "scripts" / "runner" / "loops" / "performance.env"
BASH = shutil.which("bash") or "/bin/bash"


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def rig(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "README").write_text("x\n")
    _git("init", "-q", "-b", "main", cwd=src)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "add", ".", cwd=src)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init", cwd=src)
    origin = tmp_path / "origin.git"
    _git("clone", "-q", "--bare", str(src), str(origin), cwd=tmp_path)
    work = tmp_path / "work"
    _git("clone", "-q", str(origin), str(work), cwd=tmp_path)

    runner = tmp_path / "runner"
    (runner / "loops").mkdir(parents=True)
    (runner / "lib").mkdir()
    (runner / "lib" / "nightly_green_base.py").write_text("print('')\n")
    shutil.copy2(LOOP_ENV, runner / "loops" / "performance.env")

    home = tmp_path / "home"
    home.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    calls = tmp_path / "calls"
    calls.mkdir()

    (runner / "perf_probe_helper.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['CALLS'], 'read.argv').write_text(' '.join(sys.argv[1:]))\n"
        "if os.environ.get('STUB_PROBE_FAILURE'): sys.exit(75)\n"
        "print(json.dumps({'schema': 1, 'host': {'loadavg': [0.1, 0.1, 0.1]}}))\n"
    )

    class Rig:
        pass

    r = Rig()
    r.work, r.runner, r.state, r.calls, r.home, r.origin = work, runner, state, calls, home, origin

    def run(**extra):
        timeout_dir = os.path.dirname(shutil.which("gtimeout") or shutil.which("timeout") or "/usr/bin/timeout")
        env = {
            "HOME": str(home), "CALLS": str(calls), "LOOP": "performance", "PHASE": "perf",
            "WORK": str(work), "LOOP_STATE": str(state), "RUNNER_DIR": str(runner),
            "REPO_URL": str(origin), "RUNNER_PYTHON": sys.executable,
            "RADON_RUNNER_PATH": f"{timeout_dir}:/usr/bin:/bin:{os.path.dirname(shutil.which('git'))}",
            "RADON_RUNNER_GH": "/usr/bin/false", **extra,
        }
        return subprocess.run([BASH, str(HOOK)], cwd=work, env=env, capture_output=True, text=True, timeout=60)

    r.run = run
    r.probe = lambda: json.loads((state / "prod-probe.json").read_text())
    r.read_argv = lambda: (calls / "read.argv").read_text() if (calls / "read.argv").exists() else None
    return r


def test_probe_is_copied_from_the_separate_helper_without_ssh(rig):
    out = rig.run()
    assert out.returncode == 0, out.stderr
    assert "probe=ok" in out.stdout
    assert rig.probe()["schema"] == 1
    assert rig.read_argv() == 'read'
    assert oct((rig.state / "prod-probe.json").stat().st_mode & 0o777) == "0o600"
    assert not (rig.home / '.ssh').exists()


def test_the_clone_is_pinned_to_a_detached_base(rig):
    out = rig.run()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=rig.work, capture_output=True, text=True).stdout.strip()
    assert f"base={head}" in out.stdout
    branch = subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=rig.work, capture_output=True)
    assert branch.returncode != 0, "HEAD must be detached at the green base"


def test_an_unavailable_snapshot_never_refuses_the_phase(rig):
    (rig.state / "prod-probe.json").write_text('{"schema": 1, "stale": true}')
    out = rig.run(STUB_PROBE_FAILURE='1')
    assert out.returncode == 0, out.stderr
    assert "probe=unavailable" in out.stdout
    probe = rig.probe()
    assert "error" in probe and "stale" not in probe


def test_a_missing_helper_never_falls_back_to_a_connection(rig):
    (rig.runner / 'perf_probe_helper.py').unlink()
    out = rig.run()
    assert out.returncode == 0
    assert rig.read_argv() is None
    assert "helper" in rig.probe()["error"]


def test_a_clone_of_another_repository_is_refused(rig, tmp_path):
    out = rig.run(REPO_URL=str(tmp_path / "elsewhere.git"))
    assert out.returncode == 2
    assert "refused=" in out.stdout
    assert rig.read_argv() is None


def test_loop_config_wires_the_hook_one_phase_and_the_overnight_slot():
    body = LOOP_ENV.read_text()
    assert "PRE_RUN=hooks/performance_pre.sh" in body
    assert 'PHASES="perf:' in body
    assert "SCHEDULE_HOUR=22" in body
    assert "PROMPT=.claude/runner-prompts/performance.md" in body


def test_prompt_keeps_the_agent_off_production_and_out_of_ci_performance_scope():
    prompt = (REPO / ".claude" / "runner-prompts" / "performance.md").read_text()
    assert "Never ssh" in prompt
    assert "prod-probe.json" in prompt
    assert "belongs to the `ci-performance` loop" in prompt
    assert "--label performance-nightly" in prompt
    assert "scripts/db/migrations/*" in prompt
    assert "nightly_issue_prune.py" in prompt


def test_prune_deadman_is_called_with_loop_issue_and_branch_prefix(rig):
    gh_script = rig.calls / "fake-gh"
    gh_script.write_text(
        "#!/bin/bash\n"
        "if [[ \"$*\" == *\"issue list\"* ]]; then\n"
        "  echo '1000'\n"
        "fi\n"
    )
    gh_script.chmod(0o755)

    pruner = rig.runner / "lib" / "nightly_issue_prune.py"
    prune_record = rig.calls / "prune-record.txt"
    pruner.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        f"Path({str(prune_record)!r}).write_text(' '.join(sys.argv[1:]))\n"
    )

    out = rig.run(RADON_RUNNER_GH=str(gh_script))
    assert out.returncode == 0, out.stderr
    assert prune_record.exists(), "pruner should have been invoked"
    argv = prune_record.read_text().split()
    assert "--issue" in argv and "1000" in argv
    assert "--branch-prefix" in argv and "performance/" in argv


def test_prune_deadman_is_skippable_via_env(rig):
    gh_script = rig.calls / "fake-gh"
    gh_script.write_text(
        "#!/bin/bash\n"
        "if [[ \"$*\" == *\"issue list\"* ]]; then\n"
        "  echo '1000'\n"
        "fi\n"
    )
    gh_script.chmod(0o755)

    pruner = rig.runner / "lib" / "nightly_issue_prune.py"
    prune_record = rig.calls / "prune-record.txt"
    pruner.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        f"Path({str(prune_record)!r}).write_text(' '.join(sys.argv[1:]))\n"
    )

    out = rig.run(RADON_RUNNER_GH=str(gh_script), RADON_WEEKEND_SKIP_ISSUE_PRUNE="1")
    assert out.returncode == 0, out.stderr
    assert not prune_record.exists(), "pruner should have been skipped"


def test_prune_deadman_failure_does_not_fail_pre_run_hook(rig):
    gh_script = rig.calls / "fake-gh"
    gh_script.write_text(
        "#!/bin/bash\n"
        "if [[ \"$*\" == *\"issue list\"* ]]; then\n"
        "  echo '1000'\n"
        "fi\n"
    )
    gh_script.chmod(0o755)

    pruner = rig.runner / "lib" / "nightly_issue_prune.py"
    pruner.write_text("import sys; sys.exit(1)\n")

    out = rig.run(RADON_RUNNER_GH=str(gh_script))
    assert out.returncode == 0, out.stderr
