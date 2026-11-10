"""Pin the scoped pre-push check (.githooks/pre-push -> scripts/ci/pre_push.py).

The hook runs, before a push, the tests that most often turned a PR red on its
first CI attempt: the docs ownership contract, the e2e curation ledger, the
tests of changed modules, and the CI pins when a workflow changed. Selection is
pure over a changed-file list; the git paths run against throwaway repos.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from ci import path_filter, pre_push

ROOT = Path(__file__).resolve().parents[2]
ZERO = "0" * 40


@pytest.fixture(autouse=True)
def _no_developer_git_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """pre_push drops every GIT_* variable before it runs git, so the suite's
    GIT_CONFIG_GLOBAL does not reach it. A private HOME does."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)


def _touch(root: Path, *paths: str) -> None:
    for rel in paths:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# fixture\n")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    _touch(repo, "README.md", "scripts/tests/test_docs_contract.py")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


def _commit(repo: Path, *paths: str) -> str:
    _touch(repo, *paths)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")
    return _git(repo, "rev-parse", "HEAD")


class FakeRun:
    """Records every command; `fail` names a token whose command exits 1."""

    def __init__(self, fail: str | None = None) -> None:
        self.calls: list[tuple[list[str], dict]] = []
        self.fail = fail

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), kwargs))
        code = 1 if self.fail and any(self.fail in part for part in cmd) else 0
        return subprocess.CompletedProcess(cmd, code, "", "")

    def commands(self) -> list[list[str]]:
        return [cmd for cmd, _ in self.calls if "-c" not in cmd]


def _main(repo: Path, stdin: str, run: FakeRun, env: dict | None = None, **kw) -> int:
    return pre_push.main(
        [],
        stdin=stdin,
        env=env if env is not None else {},
        root=repo,
        run=run,
        **kw,
    )


# ── reuse, not a copy ────────────────────────────────────────────────


def test_selection_reuses_the_ci_path_filter() -> None:
    assert pre_push.classify is path_filter.classify
    assert pre_push.select_gates is path_filter.select_gates
    assert pre_push.changed_paths is path_filter.changed_paths


# ── stdin parsing ────────────────────────────────────────────────────


def test_parse_push_lines_reads_each_ref_and_flags_deletions() -> None:
    text = (
        f"refs/heads/fix/a {'a' * 40} refs/heads/fix/a {ZERO}\n"
        "\n"
        f"(delete) {ZERO} refs/heads/old {'b' * 40}\n"
        "garbage line\n"
    )
    refs = pre_push.parse_push_lines(text)
    assert [(r.local_ref, r.remote_ref, r.deleted) for r in refs] == [
        ("refs/heads/fix/a", "refs/heads/fix/a", False),
        ("(delete)", "refs/heads/old", True),
    ]


def test_a_sha256_zero_id_is_a_deletion_too() -> None:
    (ref,) = pre_push.parse_push_lines(f"(delete) {'0' * 64} refs/heads/x {'c' * 64}\n")
    assert ref.deleted


# ── selection: pure over a file list ─────────────────────────────────


def test_a_changed_python_test_runs_itself(tmp_path: Path) -> None:
    _touch(tmp_path, "scripts/tests/test_widget.py", "cloud/tests/test_deploy_x.py", "tests/test_twr.py")
    sel = pre_push.select(
        ["scripts/tests/test_widget.py", "cloud/tests/test_deploy_x.py", "tests/test_twr.py"],
        root=tmp_path,
    )
    assert "scripts/tests/test_widget.py" in sel.python_tests
    assert "tests/test_twr.py" in sel.python_tests
    # cloud/tests collides with scripts/tests on `tests.conftest`: its own run.
    assert sel.cloud_tests == ("cloud/tests/test_deploy_x.py",)
    assert not any(t.startswith("cloud/") for t in sel.python_tests)


def test_a_deleted_test_file_is_not_selected(tmp_path: Path) -> None:
    sel = pre_push.select(["scripts/tests/test_gone.py"], root=tmp_path)
    assert "scripts/tests/test_gone.py" not in sel.python_tests


def test_a_changed_module_selects_its_stem_tests(tmp_path: Path) -> None:
    _touch(
        tmp_path,
        "scripts/subscription_vault.py",
        "scripts/tests/test_subscription_vault.py",
        "scripts/tests/test_subscription_vault_peer.py",
        "scripts/tests/test_monitor_daemon/test_subscription_vault_daemon.py",
        "scripts/api/tests/test_subscription_vault_api.py",
        "scripts/tests/test_unrelated.py",
    )
    sel = pre_push.select(["scripts/subscription_vault.py"], root=tmp_path)
    assert set(sel.python_tests) == {
        "scripts/tests/test_subscription_vault.py",
        "scripts/tests/test_subscription_vault_peer.py",
        "scripts/tests/test_monitor_daemon/test_subscription_vault_daemon.py",
        "scripts/api/tests/test_subscription_vault_api.py",
    }


def test_package_markers_select_no_stem_tests(tmp_path: Path) -> None:
    _touch(tmp_path, "scripts/tests/test___init__x.py", "scripts/tests/test_conftest_x.py")
    sel = pre_push.select(["scripts/ci/__init__.py", "scripts/tests/conftest.py"], root=tmp_path)
    assert "scripts/tests/test___init__x.py" not in sel.python_tests
    assert "scripts/tests/test_conftest_x.py" not in sel.python_tests


def test_a_workflow_change_runs_the_ci_pins(tmp_path: Path) -> None:
    _touch(
        tmp_path,
        "scripts/tests/test_ci_gate_integrity.py",
        "scripts/tests/test_ci_merge_group.py",
        "scripts/tests/test_path_filter.py",
        "cloud/tests/test_app_images.py",
    )
    for changed in (".github/workflows/ci.yml", "scripts/ci/path_filter.py"):
        sel = pre_push.select([changed], root=tmp_path)
        assert {
            "scripts/tests/test_ci_gate_integrity.py",
            "scripts/tests/test_ci_merge_group.py",
            "scripts/tests/test_path_filter.py",
        } <= set(sel.python_tests), changed
        assert "cloud/tests/test_app_images.py" in sel.cloud_tests, changed


def test_a_code_only_change_still_runs_the_docs_contract(tmp_path: Path) -> None:
    # 4 of the 8 first-attempt scripts-df reds sampled on 2026-11-01 were
    # TestOwnership: a mapped CODE path changed without its owner doc. A
    # .md-only trigger cannot catch that, so the contract runs on any change.
    _touch(tmp_path, *pre_push.ALWAYS_TESTS)
    sel = pre_push.select(["cloud/scripts/deploy.sh"], root=tmp_path)
    assert set(pre_push.ALWAYS_TESTS) <= set(sel.python_tests)
    assert "scripts/tests/test_docs_contract.py" in pre_push.ALWAYS_TESTS


def test_a_new_e2e_spec_runs_the_curation_ledger_contract(tmp_path: Path) -> None:
    # The other 4 of 8 scripts-df reds: a web/e2e spec in neither ci.yml nor the
    # ledger. path_filter's cross-tree contracts already route it.
    _touch(tmp_path, "web/e2e/new-flow.spec.ts")
    sel = pre_push.select(["web/e2e/new-flow.spec.ts"], root=tmp_path)
    assert "scripts/tests/test_e2e_ci_curation.py" in sel.python_tests


def test_a_node_id_collapses_into_its_selected_module(tmp_path: Path) -> None:
    _touch(tmp_path, *pre_push.ALWAYS_TESTS, "web/README.md")
    sel = pre_push.select(["web/README.md"], root=tmp_path)
    assert "scripts/tests/test_docs_contract.py" in sel.python_tests
    assert not any(t.startswith("scripts/tests/test_docs_contract.py::") for t in sel.python_tests)


def test_vitest_gets_existing_web_armed_sources_only(tmp_path: Path) -> None:
    _touch(tmp_path, "web/lib/chat.ts", "site/lib/theme.ts", "scripts/lib/relay.js", "scripts/ib_sync.py")
    sel = pre_push.select(
        ["web/lib/chat.ts", "site/lib/theme.ts", "scripts/lib/relay.js", "web/lib/deleted.ts",
         "scripts/ib_sync.py", "docs/x.md"],
        root=tmp_path,
    )
    assert sel.vitest_files == ("scripts/lib/relay.js", "site/lib/theme.ts", "web/lib/chat.ts")


def test_nothing_changed_selects_nothing(tmp_path: Path) -> None:
    _touch(tmp_path, *pre_push.ALWAYS_TESTS)
    assert pre_push.select([], root=tmp_path).empty


def test_python_only_change_skips_lint_and_typecheck(tmp_path: Path) -> None:
    _touch(tmp_path, "scripts/widget.py", "scripts/tests/test_widget.py")
    sel = pre_push.select(["scripts/widget.py", "docs/x.md"], root=tmp_path)
    assert sel.lint_files == ()
    assert sel.typecheck_files == ()


def test_perimeter_and_typecheck_files_are_scoped(tmp_path: Path) -> None:
    _touch(
        tmp_path,
        "web/middleware.ts",
        "web/lib/chat.ts",
        "web/tests/chat.test.ts",
        "web/e2e/new-flow.spec.ts",
        "lib/tools/api-client.ts",
        "lib/tools/__tests__/api-client.test.ts",
        "site/lib/theme.ts",
    )
    sel = pre_push.select(
        [
            "web/middleware.ts",
            "web/lib/chat.ts",
            "web/tests/chat.test.ts",
            "web/e2e/new-flow.spec.ts",
            "lib/tools/api-client.ts",
            "lib/tools/__tests__/api-client.test.ts",
            "site/lib/theme.ts",
            "web/lib/deleted.ts",
        ],
        root=tmp_path,
    )
    assert sel.lint_files == ("web/middleware.ts",)
    assert sel.typecheck_files == ("lib/tools/api-client.ts", "web/lib/chat.ts", "web/middleware.ts")


_ESLINT_BARE_BUILTINS = re.compile(
    r"ImportDeclaration\[source\.value=/\^\(([^)]+)\)\$/"
)


def test_oxlint_perimeter_matches_the_eslint_builtin_ban() -> None:
    eslint = (ROOT / "web/eslint.config.mjs").read_text()
    match = _ESLINT_BARE_BUILTINS.search(eslint)
    assert match, "eslint perimeter builtin alternation moved"
    assert "ImportDeclaration[source.value=/^node:/" in eslint
    ox = json.loads((ROOT / "web/oxlint.perimeter.json").read_text())
    override = ox["overrides"][0]
    assert override["files"] == [
        "middleware.ts",
        "lib/apiContracts.ts",
        "lib/probeAuth.ts",
        "lib/sameOrigin.ts",
    ]
    patterns = override["rules"]["no-restricted-imports"][1]["patterns"]
    groups = [pattern["group"] for pattern in patterns]
    assert ["node:*", "node:*/*"] in groups
    assert match.group(1).split("|") in groups


# ── main: env skips, refs, merge-base ────────────────────────────────


def test_skip_env_exits_before_touching_git(tmp_path: Path, capsys) -> None:
    run = FakeRun()
    code = _main(tmp_path / "not-a-repo", "garbage", run, env={"RADON_SKIP_PRE_PUSH": "1"})
    assert code == 0 and run.calls == []
    assert "RADON_SKIP_PRE_PUSH" in capsys.readouterr().out


def test_ci_never_runs_the_hook(tmp_path: Path) -> None:
    run = FakeRun()
    assert _main(tmp_path / "not-a-repo", "garbage", run, env={"CI": "true"}) == 0
    assert run.calls == []


def test_a_deletion_only_push_checks_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    run = FakeRun()
    assert _main(repo, f"(delete) {ZERO} refs/heads/old {'b' * 40}\n", run) == 0
    assert run.calls == []


def test_merge_base_is_taken_against_origin_main(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "switch", "-q", "-c", "fix/x")
    head = _commit(repo, "scripts/widget.py")
    # main moves on after the branch point; the range must not include it.
    _git(repo, "update-ref", "refs/remotes/origin/main", _commit_on_side(repo, base))
    assert pre_push.merge_base(head, repo) == base


def _commit_on_side(repo: Path, base: str) -> str:
    tree = _git(repo, "rev-parse", f"{base}^{{tree}}")
    return _git(repo, "commit-tree", tree, "-p", base, "-m", "main moved")


def test_main_runs_the_selection_with_the_docs_base(tmp_path: Path, capsys) -> None:
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "switch", "-q", "-c", "fix/x")
    head = _commit(repo, "scripts/widget.py", "scripts/tests/test_widget.py")
    run = FakeRun()
    code = _main(repo, f"refs/heads/fix/x {head} refs/heads/fix/x {ZERO}\n", run)
    assert code == 0
    pytest_calls = [(cmd, kw) for cmd, kw in run.calls if "pytest" in cmd and "-c" not in cmd]
    assert len(pytest_calls) == 1
    cmd, kw = pytest_calls[0]
    assert "scripts/tests/test_widget.py" in cmd
    assert "scripts/tests/test_docs_contract.py" in cmd
    assert kw["env"]["DOCS_CONTRACT_BASE"] == base
    assert kw["cwd"] == repo
    out = capsys.readouterr().out
    assert "scripts/tests/test_widget.py" in out
    assert "pre-push: passed in" in out


def test_tests_never_inherit_the_hooks_git_dir(tmp_path: Path) -> None:
    # A push from a worktree exports GIT_DIR to the hook (git 2.56, measured
    # 2026-11-01). Inherited, it points every temp-repo `git init` / `git
    # commit` in the selected tests at the pushing worktree.
    repo = _repo(tmp_path)
    _git(repo, "switch", "-q", "-c", "fix/x")
    head = _commit(repo, "scripts/tests/test_widget.py")
    run = FakeRun()
    env = {"GIT_DIR": str(repo / ".git"), "GIT_WORK_TREE": str(repo), "GIT_PREFIX": "", "KEEP": "1"}
    assert _main(repo, f"refs/heads/fix/x {head} refs/heads/fix/x {ZERO}\n", run, env=env) == 0
    assert run.calls
    for _, kw in run.calls:
        assert not [key for key in kw["env"] if key.startswith("GIT_")]
        assert kw["env"]["KEEP"] == "1"


def test_a_failing_selection_blocks_the_push_with_the_bypass_hint(tmp_path: Path, capsys) -> None:
    repo = _repo(tmp_path)
    _git(repo, "switch", "-q", "-c", "fix/x")
    head = _commit(repo, "scripts/tests/test_widget.py")
    run = FakeRun(fail="scripts/tests/test_widget.py")
    code = _main(repo, f"refs/heads/fix/x {head} refs/heads/fix/x {ZERO}\n", run)
    assert code == 1
    out = capsys.readouterr()
    hint = out.out + out.err
    assert "git push --no-verify" in hint and "RADON_SKIP_PRE_PUSH=1" in hint


def test_a_ref_that_is_not_checked_out_is_skipped(tmp_path: Path, capsys) -> None:
    # Tests run against the working tree; a ref pushed from elsewhere
    # (`git push origin <sha>:refs/heads/x`) cannot be checked by them.
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "switch", "-q", "-c", "fix/x")
    _commit(repo, "scripts/tests/test_widget.py")
    run = FakeRun()
    assert _main(repo, f"refs/heads/main {base} refs/heads/other {ZERO}\n", run) == 0
    assert run.calls == []
    assert "not checked out" in capsys.readouterr().out


def test_no_changed_files_exits_fast(tmp_path: Path, capsys) -> None:
    repo = _repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    run = FakeRun()
    assert _main(repo, f"refs/heads/main {head} refs/heads/main {head}\n", run) == 0
    assert run.calls == []
    assert "nothing selected" in capsys.readouterr().out


def test_pytest_then_vitest_never_concurrently(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    _git(repo, "switch", "-q", "-c", "fix/x")
    head = _commit(repo, "web/lib/chat.ts")
    _touch(repo, "node_modules/.bin/vitest")
    monkeypatch.setattr(pre_push.shutil, "which", lambda name: f"/usr/bin/{name}")
    run = FakeRun()
    assert _main(repo, f"refs/heads/fix/x {head} refs/heads/fix/x {ZERO}\n", run) == 0
    cmds = run.commands()
    kinds = ["pytest" if "pytest" in cmd else "vitest" if "vitest" in cmd else "?" for cmd in cmds]
    assert kinds == ["pytest", "vitest"]
    vitest = cmds[-1]
    assert vitest[:6] == ["bunx", "vitest", "related", "--run", "--config", "vitest.config.ts"]
    assert vitest[-1] == "web/lib/chat.ts"


def _executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)


class _ReadingRun(FakeRun):
    def __init__(self) -> None:
        super().__init__()
        self.projects: list[str] = []

    def __call__(self, cmd, **kwargs):
        if cmd and cmd[0].endswith("tsc") and "-p" in cmd:
            self.projects.append(Path(cmd[cmd.index("-p") + 1]).read_text())
        return super().__call__(cmd, **kwargs)


def test_static_checks_run_before_pytest_on_changed_files(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _git(repo, "switch", "-q", "-c", "fix/x")
    head = _commit(repo, "web/middleware.ts", "lib/tools/api-client.ts")
    _touch(repo, "web/tsconfig.json", "lib/tsconfig.json", "web/oxlint.perimeter.json")
    _executable(repo / "web/node_modules/.bin/oxlint")
    _executable(repo / "web/node_modules/.bin/tsc")
    run = _ReadingRun()
    assert _main(repo, f"refs/heads/fix/x {head} refs/heads/fix/x {ZERO}\n", run) == 0
    kinds = []
    for cmd, _kw in run.calls:
        if "oxlint" in cmd[0]:
            kinds.append("oxlint")
        elif cmd[0].endswith("tsc"):
            kinds.append("tsc")
        elif "pytest" in cmd and "-c" not in cmd:
            kinds.append("pytest")
    assert kinds[:3] == ["oxlint", "tsc", "tsc"]
    assert "pytest" in kinds[3:]
    ox, ox_kw = next((cmd, kw) for cmd, kw in run.calls if "oxlint" in cmd[0])
    assert ox[1:3] == ["-c", "oxlint.perimeter.json"]
    assert ox[-1] == "middleware.ts"
    assert ox_kw["cwd"] == repo / "web"
    projects = [json.loads(text) for text in run.projects]
    files = sorted(path for project in projects for path in project["files"])
    assert any(path.endswith("middleware.ts") for path in files)
    assert any(path.endswith("api-client.ts") for path in files)
    assert all(project["include"] == [] for project in projects)
    assert all(project["compilerOptions"]["noEmit"] is True for project in projects)
    # The generated project is removed after tsc so it cannot leak into a commit.
    assert not (repo / "web/node_modules/.cache/radon-pre-push").exists() or not any(
        (repo / "web/node_modules/.cache/radon-pre-push").glob("tsconfig.*.json")
    )


def test_oxlint_and_tsc_failures_block_the_push(tmp_path: Path, capsys) -> None:
    repo = _repo(tmp_path)
    _git(repo, "switch", "-q", "-c", "fix/x")
    head = _commit(repo, "web/lib/chat.ts")
    _executable(repo / "web/node_modules/.bin/tsc")
    run = FakeRun(fail="tsc")
    assert _main(repo, f"refs/heads/fix/x {head} refs/heads/fix/x {ZERO}\n", run) == 1
    assert "tsc" in capsys.readouterr().err
    assert not any("pytest" in cmd for cmd in run.commands())


def test_missing_oxlint_and_tsc_are_skipped(tmp_path: Path, capsys) -> None:
    repo = _repo(tmp_path)
    _git(repo, "switch", "-q", "-c", "fix/x")
    head = _commit(repo, "web/middleware.ts")
    run = FakeRun()
    assert _main(repo, f"refs/heads/fix/x {head} refs/heads/fix/x {ZERO}\n", run) == 0
    assert not any("oxlint" in cmd[0] or "tsc" in cmd[0] for cmd in run.commands())
    out = capsys.readouterr().out
    assert "oxlint skipped" in out
    assert "tsc skipped" in out


def test_vitest_is_skipped_without_node_modules(tmp_path: Path, capsys) -> None:
    repo = _repo(tmp_path)
    _git(repo, "switch", "-q", "-c", "fix/x")
    head = _commit(repo, "web/lib/chat.ts")
    run = FakeRun()
    assert _main(repo, f"refs/heads/fix/x {head} refs/heads/fix/x {ZERO}\n", run) == 0
    assert not any("vitest" in cmd for cmd in run.commands())
    assert "vitest skipped" in capsys.readouterr().out


def test_python_without_pytest_is_skipped_not_failed(tmp_path: Path, capsys) -> None:
    repo = _repo(tmp_path)
    _git(repo, "switch", "-q", "-c", "fix/x")
    head = _commit(repo, "scripts/tests/test_widget.py")
    run = FakeRun(fail="import pytest")
    assert _main(repo, f"refs/heads/fix/x {head} refs/heads/fix/x {ZERO}\n", run) == 0
    assert not any("pytest" in cmd for cmd in run.commands())
    assert "pytest skipped" in capsys.readouterr().out


# ── interpreter: the main checkout's venv, from any worktree ────────


def test_python_prefers_the_main_checkout_venv_from_a_worktree(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert pre_push.python_for(repo) == "python3"
    venv = repo / ".venv" / "bin" / "python"
    _touch(repo, ".venv/bin/python")
    venv.chmod(0o755)
    worktree = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "wt", str(worktree))
    assert Path(pre_push.python_for(repo)).resolve() == venv.resolve()
    assert Path(pre_push.python_for(worktree)).resolve() == venv.resolve()


# ── install: a shim in the hooks dir, never core.hooksPath ──────────


def _hooks_dir(repo: Path) -> Path:
    return Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-path", "hooks"))


def test_install_writes_an_executable_shim(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert pre_push.install(repo) == 0
    hook = _hooks_dir(repo) / "pre-push"
    text = hook.read_text()
    assert text.startswith("#!/bin/sh\n") and pre_push.SHIM_MARKER in text
    assert ".githooks/pre-push" in text
    assert hook.stat().st_mode & stat.S_IXUSR
    # core.hooksPath would move `git rev-parse --git-path hooks` onto the
    # tracked .githooks/ dir, where grok_page_responder.install_push_guard
    # would then write its fix/*-only guard over a tracked file.
    assert subprocess.run(
        ["git", "-C", str(repo), "config", "--get", "core.hooksPath"], capture_output=True
    ).returncode == 1
    assert pre_push.install(repo) == 0  # idempotent over its own shim


def test_install_never_replaces_a_foreign_hook(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    hook = _hooks_dir(repo) / "pre-push"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\n# radon push guard\nexit 1\n")
    assert pre_push.install(repo) == 0
    assert hook.read_text() == "#!/bin/sh\n# radon push guard\nexit 1\n"


def test_install_respects_a_configured_hooks_path(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _git(repo, "config", "core.hooksPath", "/dev/null")
    assert pre_push.install(repo) == 0
    assert not (repo / ".git" / "hooks" / "pre-push").exists()


def test_install_hooks_a_dispatcher_directory_and_runs_once(tmp_path: Path) -> None:
    # Cloud agent images set core.hooksPath to a dispatcher dir. Git then
    # ignores .git/hooks, so the shim has to be a pre-push.cursor* script
    # behind a pre-push -> .dispatcher link.
    repo = _repo(tmp_path)
    hooks = tmp_path / "agent-hooks"
    hooks.mkdir()
    (hooks / ".dispatcher").write_text(
        "#!/bin/sh\n"
        "dir=$(CDPATH= cd -- \"$(dirname \"$0\")\" && pwd)\n"
        "for hook in \"$dir\"/pre-push.cursor*; do\n"
        "  [ -x \"$hook\" ] && \"$hook\" \"$@\"\n"
        "done\n"
    )
    (hooks / ".dispatcher").chmod(0o755)
    legacy = repo / ".git" / "hooks" / "pre-push"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(pre_push.SHIM)
    _git(repo, "config", "core.hooksPath", str(hooks))
    assert pre_push.install(repo) == 0
    assert (hooks / "pre-push").is_symlink()
    assert os.readlink(hooks / "pre-push") == ".dispatcher"
    companion = hooks / "pre-push.cursor.radon"
    assert pre_push.SHIM_MARKER in companion.read_text()
    assert companion.stat().st_mode & stat.S_IXUSR
    assert not legacy.exists()  # dispatcher would otherwise run the check twice
    assert pre_push.install(repo) == 0
    (repo / ".githooks").mkdir()
    (repo / ".githooks" / "pre-push").write_text("#!/bin/sh\necho radon-hook\nexit 0\n")
    done = subprocess.run(
        [str(hooks / "pre-push"), "origin", "url"],
        cwd=repo,
        input="",
        text=True,
        capture_output=True,
    )
    assert (done.returncode, done.stdout) == (0, "radon-hook\n"), done.stderr


def test_install_does_not_replace_a_foreign_dispatcher_pre_push(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    hooks = tmp_path / "agent-hooks"
    hooks.mkdir()
    (hooks / ".dispatcher").write_text("#!/bin/sh\nexit 0\n")
    (hooks / "pre-push").write_text("#!/bin/sh\n# foreign\nexit 3\n")
    _git(repo, "config", "core.hooksPath", str(hooks))
    assert pre_push.install(repo) == 0
    assert (hooks / "pre-push").read_text() == "#!/bin/sh\n# foreign\nexit 3\n"
    assert not (hooks / "pre-push.cursor.radon").exists()


def test_install_outside_a_git_repo_is_a_no_op(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert pre_push.install(plain) == 0
    assert list(plain.iterdir()) == []


def test_the_shim_runs_the_checkouts_hook_and_tolerates_its_absence(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    pre_push.install(repo)
    hook = _hooks_dir(repo) / "pre-push"
    # A branch without .githooks/pre-push has no check.
    assert subprocess.run(["sh", str(hook)], cwd=repo, input="", text=True).returncode == 0
    (repo / ".githooks").mkdir()
    (repo / ".githooks" / "pre-push").write_text('#!/bin/sh\necho "ran $#"\nexit 7\n')
    done = subprocess.run(["sh", str(hook), "origin", "url"], cwd=repo, input="", text=True, capture_output=True)
    assert (done.returncode, done.stdout) == (7, "ran 2\n")


# ── the tracked hook and the install trigger ─────────────────────────


def test_the_hook_is_posix_sh_and_execs_the_checker() -> None:
    hook = ROOT / ".githooks" / "pre-push"
    text = hook.read_text()
    assert text.startswith("#!/bin/sh\n")
    assert "exec python3 scripts/ci/pre_push.py" in text
    assert os.access(hook, os.X_OK)
    assert chr(0x2014) not in text  # no em dashes in new copy


def test_the_hook_honours_the_skip_env_end_to_end() -> None:
    done = subprocess.run(
        ["sh", ".githooks/pre-push", "origin", "url"],
        cwd=ROOT,
        input=f"refs/heads/x {'a' * 40} refs/heads/x {ZERO}\n",
        text=True,
        capture_output=True,
        env={**{k: v for k, v in os.environ.items() if k != "CI"}, "RADON_SKIP_PRE_PUSH": "1"},
    )
    assert done.returncode == 0, done.stderr
    assert "RADON_SKIP_PRE_PUSH" in done.stdout


def test_prepare_installs_the_shim_and_never_fails() -> None:
    prepare = json.loads((ROOT / "package.json").read_text())["scripts"]["prepare"]
    assert "scripts/ci/pre_push.py --install" in prepare
    assert "hooksPath" not in prepare
    assert prepare.rstrip().endswith("|| true")


def test_prepare_installs_the_shim_in_a_git_checkout(tmp_path: Path) -> None:
    # Cloud agent images clone the repo, then `bun install` runs this prepare
    # script. The script's own --install writes the shim into that checkout.
    repo = _repo(tmp_path)
    dest = repo / "scripts" / "ci"
    dest.mkdir(parents=True)
    shutil.copy(ROOT / "scripts/ci/pre_push.py", dest / "pre_push.py")
    shutil.copy(ROOT / "scripts/ci/path_filter.py", dest / "path_filter.py")
    prepare = json.loads((ROOT / "package.json").read_text())["scripts"]["prepare"]
    done = subprocess.run(["sh", "-c", prepare], cwd=repo, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    hook = _hooks_dir(repo) / "pre-push"
    assert pre_push.SHIM_MARKER in hook.read_text()
    assert hook.stat().st_mode & stat.S_IXUSR


def test_prepare_is_silent_and_green_in_a_docker_style_tree(tmp_path: Path) -> None:
    # docker/app/Dockerfile.node copies only package.json + bun.lock before
    # `bun install`, into an image with no .git (and no git binary).
    prepare = json.loads((ROOT / "package.json").read_text())["scripts"]["prepare"]
    done = subprocess.run(["sh", "-c", prepare], cwd=tmp_path, capture_output=True, text=True)
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")
