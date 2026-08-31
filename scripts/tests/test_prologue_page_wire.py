"""T-352: the prologue page is wired end to end, not by string coincidence.

The 2026-08-30 fix made every wrapper's prologue deaths (marker refusal,
held-lock refusal, the ERR trap armed over the whole prologue) call
``report()`` with ``PHASE="prologue"``, and taught ``weekend_notify.py`` to
accept ``--phase prologue``. Nothing executed crossed that seam: every
wrapper test stubs ``python3`` and counts calls, and the notifier test
hardcodes ``"prologue"`` on its own side. ``PHASE="pre-flight"`` in one
wrapper, or a ``choices`` edit, makes argparse exit 2 behind ``|| true`` and
every prologue death posts a comment and never pages, all tests green.

Here the ``python3`` stub RECORDS the wrapper's real argv, and that exact argv
(minus the script path) is fed to the real ``weekend_notify.main()``
in-process with the transport patched.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import weekend_notify

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


def _build(tmp_path: Path, *, marker: bool, lock_held: bool) -> dict:
    """Stage a runner clone whose prologue will refuse, plus recording stubs."""
    clone = tmp_path / "clone"
    (clone / "scripts").mkdir(parents=True)
    if marker:
        (clone / ".radon-weekend-runner").touch()
        # REL-180 (R-504): every wrapper requires its OWN loop marker too, so
        # this generic clone carries all five.
        for loop in LOOPS:
            (clone / f".radon-{loop}-runner").touch()
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

    # One NUL-separated argv record per invocation, keyed by the stub's pid:
    # the record count is the page count, and the bytes are the wire.
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
    return {"clone": clone, "env": env, "argv_dir": argv_dir}


def _run(cfg: dict, loop: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(REPO / "scripts" / LOOPS[loop]), "audit"],
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


def _replay(argv: list[str], loop: str, status: str, monkeypatch) -> None:
    """Feed the wrapper's argv to the real notifier and assert the page."""
    script, notify_argv = argv[0], argv[1:]
    assert script.endswith("scripts/weekend_notify.py"), argv
    monkeypatch.setenv("PUSHOVER_USER", "u")
    monkeypatch.setenv("PUSHOVER_TOKEN", "t")
    with patch("weekend_notify._http_post", return_value=(200, b"")) as post:
        assert weekend_notify.main(notify_argv) == 0
    post.assert_called_once()
    url, payload = post.call_args[0]
    assert url == weekend_notify.PUSHOVER_API_URL
    assert payload["title"] == f"radon {loop} prologue"
    assert payload["message"].startswith(status), payload["message"]
    assert payload["priority"] == 0


@pytest.mark.parametrize("loop", LOOP_IDS)
def test_marker_refusal_pages_through_the_real_notifier(tmp_path: Path, loop: str, monkeypatch) -> None:
    cfg = _build(tmp_path, marker=False, lock_held=False)
    result = _run(cfg, loop)
    assert result.returncode == 2, result.stderr
    assert "REFUSING" in result.stderr
    records = _recorded_argv(cfg)
    assert len(records) == 1, records
    _replay(records[0], loop, "REFUSED", monkeypatch)


@pytest.mark.parametrize("loop", LOOP_IDS)
def test_held_lock_refusal_pages_through_the_real_notifier(tmp_path: Path, loop: str, monkeypatch) -> None:
    cfg = _build(tmp_path, marker=True, lock_held=True)
    result = _run(cfg, loop)
    assert result.returncode == 3, result.stderr
    assert "another weekend run owns" in result.stderr
    records = _recorded_argv(cfg)
    assert len(records) == 1, records
    _replay(records[0], loop, "REFUSED (lock held)", monkeypatch)
