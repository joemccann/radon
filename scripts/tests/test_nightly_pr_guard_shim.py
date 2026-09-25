"""The shell dispatch inside install_nightly_pr_guard's `gh` shim must route
every `pr create` and `api` invocation to nightly_pr_guard.py, even when
flags separate the tokens. A joined-string `case " $* " in *" pr create "*)`
match missed `gh pr -R owner/repo create` because "pr" and "create" were no
longer adjacent once `-R owner/repo` sat between them -- that invocation fell
through to the real, unguarded `gh` binary. These tests extract the actual
GUARD heredoc from each wrapper (never a re-typed copy) and execute it with
stub `git`/guard-python/real-gh binaries to prove the dispatch decision.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
WRAPPERS = [
    "ci_performance_nightly.sh",
    "documentation_nightly.sh",
    "reliability_weekend.sh",
    "security_nightly.sh",
    "security_deepsec_nightly.sh",
    "testing_weekend.sh",
]

GUARD_RE = re.compile(r"cat <<'GUARD'\n(.*?)\nGUARD\n", re.DOTALL)


def _extract_guard_body(wrapper: str) -> str:
    text = (SCRIPTS / wrapper).read_text(encoding="utf-8")
    match = GUARD_RE.search(text)
    assert match, f"GUARD heredoc not found in {wrapper}"
    return match.group(1)


def _write_exec_stub(path: Path, marker: Path) -> None:
    path.write_text(f'#!/bin/bash\nprintf \'%s\\n\' "$*" > {marker!s}\n', encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _run_shim(tmp_path: Path, wrapper: str, argv: list[str]) -> tuple[str | None, str | None]:
    """Returns (real_gh_marker_contents, guard_python_marker_contents)."""
    guard_body = _extract_guard_body(wrapper)
    real_gh_marker = tmp_path / f"real_gh_called.{wrapper}"
    guard_py_marker = tmp_path / f"guard_py_called.{wrapper}"
    real_gh = tmp_path / f"real_gh.{wrapper}"
    guard_python = tmp_path / f"guard_python.{wrapper}"
    fake_git = tmp_path / "bin" / "git"
    fake_git.parent.mkdir(exist_ok=True)
    fake_git.write_text("#!/bin/bash\nprintf 'stub\\n'\n", encoding="utf-8")
    fake_git.chmod(fake_git.stat().st_mode | stat.S_IEXEC)
    _write_exec_stub(real_gh, real_gh_marker)
    _write_exec_stub(guard_python, guard_py_marker)

    shim = tmp_path / f"shim.{wrapper}.sh"
    shim.write_text("#!/bin/bash\nset -euo pipefail\n" + guard_body + "\n", encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)

    env = dict(os.environ)
    env["PATH"] = f"{tmp_path / 'bin'}:{env.get('PATH', '')}"
    env["RADON_NIGHTLY_REAL_GH"] = str(real_gh)
    env["RADON_NIGHTLY_GUARD_REPO"] = str(ROOT)
    env["RADON_NIGHTLY_HOST_GITDIR"] = str(ROOT / ".git")
    env["RADON_NIGHTLY_GUARD_PYTHON"] = str(guard_python)
    subprocess.run([str(shim), *argv], env=env, check=True, capture_output=True, timeout=30)

    real_out = real_gh_marker.read_text(encoding="utf-8") if real_gh_marker.exists() else None
    guard_out = guard_py_marker.read_text(encoding="utf-8") if guard_py_marker.exists() else None
    return real_out, guard_out


@pytest.mark.parametrize("wrapper", WRAPPERS)
@pytest.mark.parametrize("argv", [
    ["pr", "create", "--title", "x", "--body", "y"],
    ["pr", "-R", "owner/repo", "create", "--title", "x"],
    ["pr", "--repo", "owner/repo", "create"],
    ["api", "repos/a/b/pulls", "--method", "POST"],
])
def test_creation_shaped_invocations_always_reach_the_python_guard(wrapper, tmp_path, argv):
    real_out, guard_out = _run_shim(tmp_path, wrapper, argv)
    assert guard_out is not None, (wrapper, argv, "guard python was never invoked; the shim bypassed it")
    assert real_out is None, (wrapper, argv, "real gh was exec'd directly, skipping the guard")


@pytest.mark.parametrize("wrapper", WRAPPERS)
@pytest.mark.parametrize("argv", [
    ["pr", "checks", "1"],
    ["pr", "list"],
    ["pr", "merge", "1"],
    ["issue", "comment", "1", "--body", "done"],
    ["alias", "set", "m", "pr merge"],
    ["alias", "import", "-"],
])
def test_non_creation_pr_invocations_still_route_through_the_python_guard(wrapper, tmp_path, argv):
    """The shell dispatch is a deliberately loose superset (any "pr"/"api"
    token routes to nightly_pr_guard.py); the precise pass-through-vs-refuse
    decision is the Python layer's job (covered by test_nightly_pr_guard.py),
    not the shell's.
    """
    real_out, guard_out = _run_shim(tmp_path, wrapper, argv)
    assert guard_out is not None, (wrapper, argv, "guard python was never invoked")
    assert real_out is None, (wrapper, argv, "real gh was exec'd directly, bypassing the guard")


@pytest.mark.parametrize("wrapper", WRAPPERS)
@pytest.mark.parametrize("argv", [
    ["release", "create", "v1.0.0"],
    ["run", "view", "1"],
])
def test_non_pr_non_api_invocations_pass_through_to_real_gh(wrapper, tmp_path, argv):
    real_out, guard_out = _run_shim(tmp_path, wrapper, argv)
    assert real_out is not None, (wrapper, argv, "real gh was never invoked")
    assert guard_out is None, (wrapper, argv, "python guard ran for a non pr/api command")


@pytest.mark.parametrize("wrapper", WRAPPERS)
def test_shim_exports_the_loop_name_for_per_loop_policy(wrapper):
    text = (SCRIPTS / wrapper).read_text(encoding="utf-8")
    assert "printf 'export RADON_NIGHTLY_LOOP=%q\\n' \"$LOOP_SLUG\"" in text
