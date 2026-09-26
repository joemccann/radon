"""Native security-audit workflows must resolve the clone, not a laptop path.

2026-09-26 security report OPERATOR_REQUIRED / DeepSec N130: both
``.claude/workflows/security-audit*.mjs`` files pinned
``const REPO = '/Users/joemccann/dev/apps/finance/radon'``. That directory
does not exist on the nightly runner, so the native audit cannot start.

Resolution order, fail-closed: ``RADON_REPO_ROOT``, then the existing
wrapper env ``RADON_WEEKEND_REPO``, then ``git rev-parse --show-toplevel``
from the working directory, then ``process.cwd()``. An explicit env that
is not a radon checkout does not fall through. A planted ``/Users/`` home
path in a guarded surface must fail CI.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "scripts" / "security" / "repo_root.py"
RESOLVER_JS = REPO / ".claude" / "workflows" / "resolveRepoRoot.mjs"
WORKFLOWS = sorted((REPO / ".claude" / "workflows").glob("security-audit*.mjs"))
SECURITY_WRAPPERS = (
    REPO / "scripts" / "security_nightly.sh",
    REPO / "scripts" / "security_deepsec_nightly.sh",
)


def _load_module():
    spec = importlib.util.spec_from_file_location("security_repo_root", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["security_repo_root"] = module
    spec.loader.exec_module(module)
    return module


def _fake_radon(root: Path) -> Path:
    (root / "scripts").mkdir(parents=True)
    (root / ".claude" / "workflows").mkdir(parents=True)
    (root / "CLAUDE.md").write_text("# RADON — CLAUDE.md\n", encoding="utf-8")
    (root / "scripts" / "evaluate.py").write_text("# evaluate\n", encoding="utf-8")
    (root / ".claude" / "workflows" / "security-audit.mjs").write_text(
        "export const meta = {}\n", encoding="utf-8"
    )
    return root


def _init_git(root: Path) -> None:
    subprocess.run(
        ["git", "init", "-q"], cwd=root, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="module")
def repo_root():
    return _load_module()


def test_module_and_resolver_exist():
    assert MODULE_PATH.is_file()
    assert RESOLVER_JS.is_file()


def test_explicit_env_wins_over_weekend_git_and_cwd(repo_root, tmp_path):
    chosen = _fake_radon(tmp_path / "chosen")
    other = _fake_radon(tmp_path / "other")
    _init_git(other)
    resolved = repo_root.resolve_radon_repo_root(
        env={
            "RADON_REPO_ROOT": str(chosen),
            "RADON_WEEKEND_REPO": str(other),
        },
        cwd=other,
        git_toplevel=lambda _cwd: str(other),
    )
    assert Path(resolved) == chosen.resolve()


def test_weekend_env_is_the_existing_wrapper_alias(repo_root, tmp_path):
    weekend = _fake_radon(tmp_path / "weekend")
    cwd = _fake_radon(tmp_path / "cwd")
    resolved = repo_root.resolve_radon_repo_root(
        env={"RADON_WEEKEND_REPO": str(weekend)},
        cwd=cwd,
        git_toplevel=lambda _cwd: str(cwd),
    )
    assert Path(resolved) == weekend.resolve()


def test_explicit_env_that_is_not_radon_does_not_fall_through(repo_root, tmp_path):
    bogus = tmp_path / "not-radon"
    bogus.mkdir()
    fallback = _fake_radon(tmp_path / "real")
    with pytest.raises(repo_root.NotARadonCheckout) as exc:
        repo_root.resolve_radon_repo_root(
            env={"RADON_REPO_ROOT": str(bogus)},
            cwd=fallback,
            git_toplevel=lambda _cwd: str(fallback),
        )
    assert "RADON_REPO_ROOT" in str(exc.value)
    assert "not a radon checkout" in str(exc.value)


def test_git_toplevel_then_cwd(repo_root, tmp_path):
    git_root = _fake_radon(tmp_path / "git-radon")
    cwd_only = tmp_path / "just-cwd"
    cwd_only.mkdir()
    resolved = repo_root.resolve_radon_repo_root(
        env={},
        cwd=cwd_only,
        git_toplevel=lambda _cwd: str(git_root),
    )
    assert Path(resolved) == git_root.resolve()

    resolved_cwd = repo_root.resolve_radon_repo_root(
        env={},
        cwd=git_root,
        git_toplevel=lambda _cwd: "",
    )
    assert Path(resolved_cwd) == git_root.resolve()


def test_unresolvable_tree_fails_closed(repo_root, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(repo_root.NotARadonCheckout):
        repo_root.resolve_radon_repo_root(
            env={},
            cwd=empty,
            git_toplevel=lambda _cwd: "",
        )


def test_guard_flags_a_planted_users_path(repo_root, tmp_path):
    planted = tmp_path / ".claude" / "workflows" / "planted.mjs"
    planted.parent.mkdir(parents=True)
    planted.write_text(
        "const REPO = '/Users/joemccann/dev/apps/finance/radon'\n",
        encoding="utf-8",
    )
    hits = repo_root.find_hardcoded_user_homes(
        tmp_path, files=[planted], tracked_only=False
    )
    assert hits, "the guard must fail CI when a /Users/ home path is planted"
    assert any("/Users/joemccann/" in hit.text for hit in hits)


def test_guard_ignores_ellipsis_docs_shape(repo_root, tmp_path):
    note = tmp_path / "scripts" / "nightly_note.py"
    note.parent.mkdir(parents=True)
    note.write_text("# lock paths look like /Users/..., /tmp/..., /home/...\n")
    hits = repo_root.find_hardcoded_user_homes(
        tmp_path, files=[note], tracked_only=False
    )
    assert hits == []


def test_tracked_guard_surfaces_have_no_hardcoded_user_home(repo_root):
    hits = repo_root.find_hardcoded_user_homes(REPO)
    assert hits == [], (
        "hardcoded /Users/ path in a guarded nightly surface:\n"
        + "\n".join(f"{h.path}:{h.line}:{h.text}" for h in hits)
    )


def test_workflows_resolve_via_env_not_a_laptop_path():
    assert WORKFLOWS, "security-audit workflows missing"
    for path in WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        assert "/Users/" not in text, path.name
        assert "RADON_REPO_ROOT" in text, path.name
        assert "resolveRadonRepoRoot" in text, path.name


@pytest.mark.parametrize("wrapper", SECURITY_WRAPPERS, ids=lambda p: p.name)
def test_security_wrappers_export_radon_repo_root(wrapper):
    body = "\n".join(
        line
        for line in wrapper.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    assert 'export RADON_REPO_ROOT="$REPO"' in body, wrapper.name


def test_js_resolver_honors_the_same_order(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to execute the workflow resolver")
    chosen = _fake_radon(tmp_path / "chosen")
    other = _fake_radon(tmp_path / "other")
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"RADON_REPO_ROOT", "RADON_WEEKEND_REPO"}
    }
    env["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")
    env["RADON_REPO_ROOT"] = str(chosen)
    env["RADON_WEEKEND_REPO"] = str(other)
    proc = subprocess.run(
        [node, str(RESOLVER_JS), "--print"],
        cwd=other,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert Path(proc.stdout.strip()) == chosen.resolve()
