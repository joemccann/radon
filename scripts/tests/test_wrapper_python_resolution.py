"""Every ``scripts/run_*.sh`` wrapper must resolve the project virtualenv.

Each wrapper is a two-tier scheduler entry point: POST to the local FastAPI
endpoint first, then fall back to invoking the Python scanner directly when
FastAPI is unreachable. The fallback exists for exactly one situation — the
API is down — and on the VPS that is precisely when it was broken.

``resolve_python()`` never listed the project virtualenv among its
candidates, and ``RADON_PYTHON_BIN`` is unset in every deployed ``.env``.
So the first match was a bare ``python3.13`` on PATH (``/usr/bin/python3.13``
on the VPS), which carries none of the project dependencies. The fallback
died instantly on ``ModuleNotFoundError`` — ``dotenv`` for breadth, ``numpy``
for vcg — and the next timer tick five minutes later masked it as a flake.

The pre-existing wrapper tests all pinned ``RADON_PYTHON_BIN`` explicitly,
which short-circuits resolution at the first candidate and is why none of
them caught this. These tests deliberately leave it unset so the candidate
*ordering* is what is under test.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]

# Every wrapper that resolves an interpreter for a direct-invocation fallback.
WRAPPERS = [
    "run_breadth_scan.sh",
    "run_catalysts.sh",
    "run_cri_scan.sh",
    "run_cta_sync.sh",
    "run_data_refresh.sh",
    "run_event_odds.sh",
    "run_garch_refresh.sh",
    "run_informed_flow.sh",
    "run_leap_refresh.sh",
    "run_margin_debt_refresh.sh",
    "run_oi_changes_refresh.sh",
    "run_portfolio_refresh.sh",
    "run_vcg_refresh.sh",
]


def _executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _stub(identity: str, log: Path, *, has_project_deps: bool) -> str:
    """A fake interpreter that records that it was chosen.

    ``has_project_deps`` models the real split on the VPS: the virtualenv
    satisfies every ``importlib.util.find_spec`` probe, the system
    interpreter satisfies none of them — but still answers a bare
    ``-c "import sys"`` successfully, which is the whole reason the weak
    probe selected it.
    """
    dep_exit = "0" if has_project_deps else "1"
    return textwrap.dedent(
        f"""\
        #!/bin/bash
        echo "{identity}" >> "{log}"
        if [ "$1" = "-c" ]; then
            # A stdlib-only probe (`import sys`) succeeds on ANY interpreter.
            # A project-dependency probe must reflect this stub's identity.
            case "$2" in
                *"import sys"*) exit 0 ;;
                *) exit {dep_exit} ;;
            esac
        fi
        if [ "$1" = "-" ]; then
            # Heredoc mode serves two callers: the trading-day gate (reads
            # stdout) and find_spec dependency probes (read exit status).
            cat >/dev/null
            echo "yes"
            exit {dep_exit}
        fi
        exit {dep_exit}
        """
    )


def _stage(tmp_path: Path, wrapper: str) -> tuple[Path, Path, dict[str, str]]:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPTS_DIR / wrapper, repo / "scripts" / wrapper)
    # The wrappers source their interpreter resolution from a shared helper.
    (repo / "scripts" / "lib").mkdir()
    shutil.copy2(SCRIPTS_DIR / "lib" / "python_bin.sh",
                 repo / "scripts" / "lib" / "python_bin.sh")

    log = tmp_path / "which-python.log"

    # The project virtualenv: has every dependency.
    _executable(repo / ".venv" / "bin" / "python3.13",
                _stub("venv", log, has_project_deps=True))
    _executable(repo / ".venv" / "bin" / "python",
                _stub("venv", log, has_project_deps=True))

    # A bare system interpreter earlier on PATH: no project dependencies.
    # This is /usr/bin/python3.13 on the VPS.
    fake_bin = tmp_path / "fakebin"
    for name in ("python3.13", "python3.11", "python3.9", "python3"):
        _executable(fake_bin / name, _stub("system", log, has_project_deps=False))

    env = {
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        # Deliberately NOT setting RADON_PYTHON_BIN — that is the deployed
        # reality and the condition under which resolution actually matters.
    }
    return repo, log, env


@pytest.mark.parametrize("wrapper", WRAPPERS)
def test_wrapper_prefers_venv_over_bare_path_python(tmp_path: Path, wrapper: str) -> None:
    """The virtualenv must win over a dependency-less PATH interpreter."""
    repo, log, env = _stage(tmp_path, wrapper)

    subprocess.run(
        ["bash", str(repo / "scripts" / wrapper)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert log.exists(), (
        f"{wrapper} resolved no interpreter at all — it should have found "
        f"the virtualenv at .venv/bin/python3.13"
    )
    chosen = log.read_text(encoding="utf-8").split()
    assert chosen[0] == "venv", (
        f"{wrapper} chose the bare system interpreter over the project "
        f"virtualenv. On the VPS that interpreter has no project "
        f"dependencies, so the direct-invocation fallback dies on "
        f"ModuleNotFoundError. Resolution order was: {chosen}"
    )


def _resolve(tmp_path: Path, args: str, extra_env: dict[str, str] | None = None):
    """Source the shared helper and call radon_resolve_python directly."""
    script = tmp_path / "probe.sh"
    script.write_text(
        f'. "{SCRIPTS_DIR / "lib" / "python_bin.sh"}"\n'
        f"radon_resolve_python {args}\n",
        encoding="utf-8",
    )
    return subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env={**os.environ, **(extra_env or {})},
    )


def test_version_floor_rejects_every_interpreter_below_it(tmp_path: Path) -> None:
    """RADON_PYTHON_MIN_VERSION is what keeps run_data_refresh.sh off 3.9.

    The shared candidate list includes older interpreters, so the floor —
    not omission from the list — is the mechanism. An unsatisfiable floor
    must reject everything rather than quietly returning something older.
    """
    result = _resolve(tmp_path, "", {"RADON_PYTHON_MIN_VERSION": "99.0"})

    assert result.returncode != 0, (
        f"an unsatisfiable version floor still resolved {result.stdout.strip()!r}"
    )
    assert result.stdout.strip() == ""


def test_missing_dependency_is_rejected_rather_than_deferred(tmp_path: Path) -> None:
    """Resolution must fail loudly, not hand back a broken interpreter.

    Returning one anyway is the original bug: the caller proceeds and dies
    on ModuleNotFoundError deep inside the scanner, five minutes before the
    next timer tick papers over it.
    """
    result = _resolve(tmp_path, "a_module_that_does_not_exist_anywhere")

    assert result.returncode != 0, (
        f"resolution returned {result.stdout.strip()!r} despite the required "
        f"module being absent"
    )
    assert result.stdout.strip() == ""


def test_resolves_a_real_interpreter_for_a_stdlib_module(tmp_path: Path) -> None:
    """Sanity check that the probe is not simply rejecting everything."""
    result = _resolve(tmp_path, "json")

    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).exists()


@pytest.mark.parametrize("wrapper", WRAPPERS)
def test_explicit_python_bin_still_wins(tmp_path: Path, wrapper: str) -> None:
    """An operator-pinned RADON_PYTHON_BIN keeps priority over the venv."""
    repo, log, env = _stage(tmp_path, wrapper)

    pinned = tmp_path / "pinned" / "python3.13"
    _executable(pinned, _stub("pinned", log, has_project_deps=True))
    env["RADON_PYTHON_BIN"] = str(pinned)

    subprocess.run(
        ["bash", str(repo / "scripts" / wrapper)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert log.exists(), f"{wrapper} resolved no interpreter at all"
    chosen = log.read_text(encoding="utf-8").split()
    assert chosen[0] == "pinned", (
        f"{wrapper} ignored an explicitly pinned RADON_PYTHON_BIN. "
        f"Resolution order was: {chosen}"
    )
