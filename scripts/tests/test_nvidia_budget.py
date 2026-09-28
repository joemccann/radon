"""Shared NVIDIA rate budget: flock, AIMD, acquire, wrapper fallback."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "scripts" / "nvidia_budget.py"
BASH = "/bin/bash"

_H = Path(__file__).with_name("_loop_harness.py")
_spec = importlib.util.spec_from_file_location("_loop_harness_nvb", _H)
_h = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _h
_spec.loader.exec_module(_h)


def _env(tmp_path: Path, **extra) -> dict[str, str]:
    env = os.environ.copy()
    env["RADON_NVIDIA_BUDGET_PATH"] = str(tmp_path / "nvidia-budget.json")
    env["RADON_NVIDIA_BUDGET_JITTER"] = "1"
    env.update({k: str(v) for k, v in extra.items()})
    return env


def _run(tmp_path: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", str(HELPER), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=env or _env(tmp_path),
    )


def _status(tmp_path: Path, env: dict[str, str] | None = None) -> dict:
    proc = _run(tmp_path, "status", env=env)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _record_one(payload: tuple) -> int:
    path, loop, now = payload
    env = os.environ.copy()
    env["RADON_NVIDIA_BUDGET_PATH"] = path
    env["RADON_NVIDIA_BUDGET_NOW"] = str(now)
    env["RADON_NVIDIA_BUDGET_JITTER"] = "1"
    proc = subprocess.run(
        [sys.executable, "-I", str(HELPER), "record-request", "--loop", loop],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        check=False,
    )
    return proc.returncode


def test_concurrent_record_request_keeps_an_exact_count(tmp_path):
    path = str(tmp_path / "nvidia-budget.json")
    n = 12
    payloads = [(path, f"loop-{i % 4}", 1_700_000_000.0) for i in range(n)]
    with ProcessPoolExecutor(max_workers=n) as pool:
        codes = list(pool.map(_record_one, payloads))
    assert codes == [0] * n
    body = json.loads(Path(path).read_text(encoding="utf-8"))
    assert len(body["requests"]) == n
    json.loads(Path(path).read_text(encoding="utf-8"))


def test_window_expiry_prunes_old_entries(tmp_path):
    env = _env(tmp_path, RADON_NVIDIA_BUDGET_NOW="1000", RADON_NVIDIA_BUDGET_WINDOW_SECS="60")
    assert _run(tmp_path, "record-request", "--loop", "testing", env=env).returncode == 0
    later = _env(tmp_path, RADON_NVIDIA_BUDGET_NOW="1070", RADON_NVIDIA_BUDGET_WINDOW_SECS="60")
    body = _status(tmp_path, env=later)
    assert body["requests"] == 0


def test_retry_after_is_honored(tmp_path):
    env = _env(tmp_path, RADON_NVIDIA_BUDGET_NOW="2000")
    proc = _run(
        tmp_path, "record-429", "--loop", "testing", "--retry-after", "17", env=env
    )
    assert proc.returncode == 0, proc.stderr
    body = json.loads((tmp_path / "nvidia-budget.json").read_text(encoding="utf-8"))
    assert body["cooldown_until"] == 2017


def test_exponential_backoff_grows_and_is_capped(tmp_path):
    now = 3000.0
    waits = []
    for i in range(8):
        env = _env(
            tmp_path,
            RADON_NVIDIA_BUDGET_NOW=str(now + i),
            RADON_NVIDIA_BUDGET_JITTER="1",
            RADON_NVIDIA_BUDGET_BACKOFF_CAP_SECS="32",
            RADON_NVIDIA_BUDGET_WINDOW_SECS="0.5",
        )
        assert _run(tmp_path, "record-429", "--loop", "testing", env=env).returncode == 0
        body = json.loads((tmp_path / "nvidia-budget.json").read_text(encoding="utf-8"))
        waits.append(body["cooldown_until"] - (now + i))
    assert waits[0] == 2
    assert waits[1] == 4
    assert waits[-1] == 32
    assert max(waits) <= 32


def test_aimd_decrease_and_increase_are_persisted(tmp_path):
    env = _env(tmp_path, RADON_NVIDIA_BUDGET_NOW="4000", RADON_NVIDIA_BUDGET_INITIAL_RPM="40")
    assert _run(tmp_path, "record-429", "--loop", "testing", "--retry-after", "1", env=env).returncode == 0
    cut = json.loads((tmp_path / "nvidia-budget.json").read_text(encoding="utf-8"))
    assert cut["learned_rpm"] == 20.0
    grown = _env(tmp_path, RADON_NVIDIA_BUDGET_NOW=str(4000 + 180))
    body = _status(tmp_path, env=grown)
    assert body["learned_rpm"] == pytest.approx(26.0)
    persisted = json.loads((tmp_path / "nvidia-budget.json").read_text(encoding="utf-8"))
    assert persisted["learned_rpm"] == pytest.approx(26.0)


def test_acquire_go_wait_fallback_around_headroom_and_deadline(tmp_path):
    env = _env(
        tmp_path,
        RADON_NVIDIA_BUDGET_NOW="5000",
        RADON_NVIDIA_BUDGET_INITIAL_RPM="5",
        RADON_NVIDIA_BUDGET_WINDOW_SECS="60",
        RADON_NVIDIA_BUDGET_JITTER="0",
    )
    go = _run(tmp_path, "acquire", "--loop", "testing", "--deadline", "6000", env=env)
    assert go.stdout.strip() == "go", go.stdout
    for _ in range(5):
        assert _run(tmp_path, "record-request", "--loop", "testing", env=env).returncode == 0
    wait = _run(tmp_path, "acquire", "--loop", "testing", "--deadline", "6000", env=env)
    assert wait.stdout.strip().startswith("wait "), wait.stdout
    fallback = _run(tmp_path, "acquire", "--loop", "testing", "--deadline", "5001", env=env)
    assert fallback.stdout.strip() == "fallback", fallback.stdout


def test_corrupt_file_is_a_fresh_budget(tmp_path):
    path = tmp_path / "nvidia-budget.json"
    path.write_text("{not-json", encoding="utf-8")
    env = _env(tmp_path, RADON_NVIDIA_BUDGET_NOW="6000")
    assert _run(tmp_path, "record-request", "--loop", "reliability", env=env).returncode == 0
    body = json.loads(path.read_text(encoding="utf-8"))
    assert len(body["requests"]) == 1
    assert body["learned_rpm"] == 40.0


def test_wrapper_fallback_moves_the_ladder_to_the_next_rung(tmp_path):
    stub = tmp_path / "budget.py"
    stub.write_text(
        "import sys\n"
        "cmd = sys.argv[1]\n"
        "if cmd == 'acquire':\n"
        "    print('fallback')\n"
        "else:\n"
        "    print('ok')\n",
        encoding="utf-8",
    )
    proc, tried, _calls, _argv = _h._run_multi(
        tmp_path, "testing", "audit",
        provider_ladder="fx:nvidia grok",
        env_extra={"RADON_NVIDIA_BUDGET_PY": str(stub)},
    )
    names = [t.split(":", 1)[0] for t in tried]
    assert names[:1] == ["grok"] or names[:2] == ["grok"], (tried, proc.stdout, proc.stderr)
    assert "fx" not in names, tried
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)


def test_wrapper_429_notice_slice_calls_record_429(tmp_path):
    seen = tmp_path / "budget-calls.txt"
    stub = tmp_path / "budget.py"
    stub.write_text(
        "import sys\n"
        f"open({str(seen)!r}, 'a', encoding='utf-8').write(' '.join(sys.argv[1:]) + '\\n')\n"
        "cmd = sys.argv[1]\n"
        "if cmd == 'acquire':\n"
        "    print('go')\n"
        "else:\n"
        "    print('ok')\n",
        encoding="utf-8",
    )
    notice = (
        '[notice] ⚠ Rate limited · HTTP 429: {"status":429,"title":"Too Many Requests"}'
        " · retrying request in 16s"
    )
    proc, tried, _calls, _argv = _h._run_multi(
        tmp_path, "testing", "audit",
        provider_ladder="fx:nvidia grok",
        reject_providers=("fx",),
        reject_output=notice,
        env_extra={"RADON_NVIDIA_BUDGET_PY": str(stub)},
    )
    calls = seen.read_text(encoding="utf-8") if seen.exists() else ""
    assert "record-429" in calls, (calls, tried, proc.stdout, proc.stderr)
    assert "--loop" in calls and "testing" in calls, calls


def test_budget_helpers_are_identical_on_the_fx_loops():
    names = ("nvidia_budget", "nvidia_budget_acquire_or_skip", "nvidia_budget_record_round")
    bodies = {n: {} for n in names}
    for loop in ("reliability", "testing", "ci-performance"):
        src = _h.LOOPS[loop].read_text(encoding="utf-8")
        for name in names:
            start = src.index(f"{name}() {{")
            depth = 0
            for i, ch in enumerate(src[start:], start):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        bodies[name][loop] = src[start : i + 1]
                        break
    for name, per_loop in bodies.items():
        assert len(set(per_loop.values())) == 1, name


def test_security_wrappers_do_not_call_the_nvidia_budget():
    for loop in ("security", "security-deepsec"):
        src = _h.LOOPS[loop].read_text(encoding="utf-8")
        assert "nvidia_budget" not in src


def _fn_body(src: str, name: str) -> str:
    start = src.index(f"{name}() {{")
    depth = 0
    for i, ch in enumerate(src[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError(name)


@pytest.mark.parametrize("loop", ("reliability", "testing", "ci-performance"))
def test_nvidia_budget_runs_origin_mains_helper_not_the_worktree_copy(tmp_path, loop):
    """DS-2026-09-28-01: the helper must be executed from origin/main's blob.

    Running it as a FILE in the agent-writable clone defeats the
    `reset --hard origin/main` that makes each fire's executed code equal to
    reviewed main: a helper edited during one phase would be run by the next.
    """
    repo = tmp_path / "clone"
    (repo / "scripts").mkdir(parents=True)
    helper = repo / "scripts" / "nvidia_budget.py"
    helper.write_text("print('GOOD')\n", encoding="utf-8")
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(repo)]
    subprocess.run([*git, "init", "-q"], check=True, capture_output=True)
    subprocess.run([*git, "add", "scripts/nvidia_budget.py"], check=True, capture_output=True)
    subprocess.run([*git, "commit", "-qm", "helper"], check=True, capture_output=True)
    subprocess.run([*git, "update-ref", "refs/remotes/origin/main", "HEAD"], check=True, capture_output=True)
    helper.write_text("print('TAMPERED')\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "python3.13"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)

    body = _fn_body(_h.LOOPS[loop].read_text(encoding="utf-8"), "nvidia_budget")
    proc = subprocess.run(
        [BASH, "-c", "set -Eeuo pipefail\n" + body + "\nnvidia_budget acquire --loop x\n"],
        capture_output=True,
        text=True,
        timeout=60,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "REPO": str(repo),
            "HOST_GITDIR": str(repo / ".git"),
        },
    )
    assert proc.stdout.strip() == "GOOD", (proc.stdout, proc.stderr)
