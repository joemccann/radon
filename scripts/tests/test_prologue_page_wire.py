"""T-352: the prologue page is wired end to end, not by string coincidence.

The 2026-08-30 fix made every wrapper's prologue deaths (marker refusal,
held-lock refusal, the ERR trap armed over the whole prologue) call
``report()`` with ``PHASE="prologue"``. Notify is in-main ``_notify_curl``
(never post-agent python). A ``PHASE="pre-flight"`` typo would still comment
and never page. This records the wrapper's real curl argv.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# loop -> wrapper filename. All five carry the same prologue.
LOOPS = {
    "reliability": "reliability_weekend.sh",
    "testing": "testing_weekend.sh",
    "ci-performance": "ci_performance_nightly.sh",
    "documentation": "documentation_nightly.sh",
    "security": "security_nightly.sh",
}
LOOP_IDS = sorted(LOOPS)


def _executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _build(tmp_path: Path, *, marker: bool, lock_held: bool, loop: str) -> dict:
    """Stage a runner clone whose prologue will refuse, plus recording stubs."""
    clone = tmp_path / "clone"
    (clone / "scripts").mkdir(parents=True)
    wrapper_src = REPO / "scripts" / LOOPS[loop]
    wrapper = clone / "scripts" / LOOPS[loop]
    shutil.copy2(wrapper_src, wrapper)
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    if marker:
        (clone / ".radon-weekend-runner").touch()
        # REL-180 (R-504): every wrapper requires its OWN loop marker too, so
        # this generic clone carries all five.
        for loop_name in LOOPS:
            (clone / f".radon-{loop_name}-runner").touch()
    if lock_held:
        lock = clone / ".weekend-runner.lock"
        lock.mkdir()
        # This test process is alive, so `kill -0` succeeds and the lock is
        # never reclaimed.
        (lock / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_dir = tmp_path / "notify-argv"
    argv_dir.mkdir()
    curl_stub = bin_dir / "curl"
    (tmp_path / ".env").write_text(
        "PUSHOVER_USER=test-user\nPUSHOVER_TOKEN=test-token\n", encoding="utf-8"
    )
    wrapper.write_text(
        wrapper.read_text(encoding="utf-8").replace("/usr/bin/curl", str(curl_stub)),
        encoding="utf-8",
    )
    (clone / "scripts" / "weekend_notify.py").write_text("# unused\n", encoding="utf-8")

    _executable(
        curl_stub,
        "#!/bin/bash\n" f'printf \'%s\\0\' "$@" > "{argv_dir}/$$"\n' "exit 0\n",
    )
    _executable(
        bin_dir / "python3",
        "#!/bin/bash\n" f'printf \'%s\\0\' "$@" > "{argv_dir}/$$"\n' "exit 0\n",
    )
    _executable(
        bin_dir / "gh",
        "#!/bin/bash\n"
        'case "$1 $2" in\n'
        '  "issue list") echo 42 ;;\n'
        '  "pr list") echo "" ;;\n'
        "esac\n"
        "exit 0\n",
    )
    # net_bounded runs every gh call under `timeout`; consume the duration.
    _executable(
        bin_dir / "timeout",
        "#!/bin/bash\n"
        'while [ $# -gt 0 ]; do\n'
        '  case "$1" in\n'
        '    -k|--kill-after) shift 2 ;;\n'
        '    --foreground|--preserve-status) shift ;;\n'
        '    *) shift; break ;;\n'
        '  esac\n'
        'done\n'
        'exec "$@"\n',
    )

    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "RADON_WEEKEND_REPO": str(clone),
    }
    return {"clone": clone, "env": env, "argv_dir": argv_dir, "wrapper": wrapper}


def _run(cfg: dict, loop: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(cfg["wrapper"]), "audit"],
        cwd=cfg["clone"],
        env=cfg["env"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _recorded_argv(cfg: dict) -> list[list[str]]:
    records = []
    for path in sorted(cfg["argv_dir"].iterdir()):
        raw = path.read_bytes().decode("utf-8")
        assert raw.endswith("\0"), raw
        records.append(raw[:-1].split("\0"))
    return records


def _assert_curl_page(argv: list[str], loop: str, status: str) -> None:
    joined = " ".join(argv)
    assert "api.pushover.net" in joined, argv
    assert f"title=radon {loop} prologue" in joined, argv
    assert status in joined, argv
    assert "priority=0" in joined, argv


@pytest.mark.parametrize("loop", LOOP_IDS)
def test_marker_refusal_pages_through_the_real_notifier(tmp_path: Path, loop: str) -> None:
    cfg = _build(tmp_path, marker=False, lock_held=False, loop=loop)
    result = _run(cfg, loop)
    assert result.returncode == 2, result.stderr
    assert "REFUSING" in result.stderr
    records = _recorded_argv(cfg)
    assert len(records) == 1, records
    _assert_curl_page(records[0], loop, "REFUSED")


@pytest.mark.parametrize("loop", LOOP_IDS)
def test_held_lock_refusal_pages_through_the_real_notifier(tmp_path: Path, loop: str) -> None:
    cfg = _build(tmp_path, marker=True, lock_held=True, loop=loop)
    result = _run(cfg, loop)
    assert result.returncode == 3, result.stderr
    assert "another weekend run owns" in result.stderr
    records = _recorded_argv(cfg)
    assert len(records) == 1, records
    _assert_curl_page(records[0], loop, "REFUSED (lock held)")
