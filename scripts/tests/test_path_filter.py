"""Path-filter classification for CI test gates."""

from __future__ import annotations

import ast
import re
import subprocess
from functools import lru_cache
from pathlib import Path

from ci import path_filter
from ci.path_filter import (
    CROSS_TREE_CONTRACTS,
    DOC_SUFFIXES,
    _is_ancestor,
    changed_paths,
    classify,
    green_main_push_shas,
    resolve_gate_base,
    select_gates,
    write_output,
)

_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Tree-derived gate routing (T-156 / T-157).
#
# path_filter.py's prefix lists are hand-maintained, so they rot the moment a
# test tree grows a cross-tree read. These helpers rebuild the routing
# expectation FROM THE CHECKOUT on every run:
#
#   * the web gate (vitest) owns every tree its `test.include` globs collect,
#     plus every top-level tree those collected test files read by path;
#   * the python gate (pytest) owns every top-level tree read by a module
#     under `tests/`, `scripts/tests/` or `cloud/tests/`.
#
# A new cross-tree read the filter does not route makes the assertions below
# fail, which is the whole point: the rule comes from the tree, not a list.
# ---------------------------------------------------------------------------

_PYTHON_TEST_ROOTS = ("tests", "scripts/tests", "cloud/tests")

# A changed file that is definitely not documentation, so the SKIP_PREFIXES
# doc-extension carve-out cannot mask a routing hole.
_SENTINEL = "__ci_routing_sentinel__.bin"


@lru_cache(maxsize=1)
def _top_level_trees() -> tuple[str, ...]:
    """Every tracked top-level directory, e.g. ('brand', 'cloud', 'web', ...)."""
    listing = subprocess.run(
        ["git", "ls-files"],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    return tuple(sorted({line.split("/", 1)[0] for line in listing if "/" in line}))


@lru_cache(maxsize=1)
def _root_level_files() -> tuple[str, ...]:
    """Every tracked root-level FILE, e.g. ('package.json', 'vitest.config.ts', ...).

    `_top_level_trees()` derives directories only, so a root file a python test
    reads by name was invisible to the routing guards below. T-192.
    """
    listing = subprocess.run(
        ["git", "ls-files"],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    return tuple(sorted(line for line in listing if line and "/" not in line))


@lru_cache(maxsize=1)
def _slash_literal_re() -> re.Pattern[str]:
    """`scripts/foo.py`, `"../../cloud/services"` — a path literal in any language.

    The leading boundary keeps `https://host/data/x` and `scripts/config/` from
    registering as reads of top-level `data/` and `config/`.
    """
    tops = "|".join(re.escape(tree) for tree in _top_level_trees())
    return re.compile(rf"""(?:^|(?<=[\s"'`(,=\[])|(?<=\.\./))({tops})/""", re.MULTILINE)


@lru_cache(maxsize=1)
def _pathlib_segment_re() -> re.Pattern[str]:
    """`ROOT / "site" / "public"` — pathlib segment chains hung off a repo root.

    Anchoring on a *ROOT/*REPO/*DIR identifier is what separates a real
    repo-relative read from `parents[1] / "lib"` (which is `scripts/lib`).
    """
    tops = "|".join(re.escape(tree) for tree in _top_level_trees())
    return re.compile(rf"""[\w.]*(?:ROOT|REPO|DIR|root|repo|dir)\s*/\s*["']({tops})["']""")


@lru_cache(maxsize=1)
def _root_file_re() -> re.Pattern[str]:
    """`"vitest.config.ts"` — a root-level filename named in any language.

    The boundaries keep `web/package.json` and `pyproject.toml.bak` from
    registering as reads of the ROOT file of that name.
    """
    names = "|".join(re.escape(name) for name in _root_level_files())
    return re.compile(
        rf"""(?:^|(?<=[\s"'`(,=\[]))({names})(?=["'`\s,)\]:]|$)""", re.MULTILINE
    )


def _referenced_trees(
    files: list[Path], patterns: list[re.Pattern[str]], suffix: str = "/"
) -> dict[str, str]:
    """Map each referenced top-level tree -> the file that proves the read."""
    found: dict[str, str] = {}
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern in patterns:
            for match in pattern.finditer(text):
                found.setdefault(f"{match.group(1)}{suffix}", str(path.relative_to(_ROOT)))
    return found


@lru_cache(maxsize=1)
def _vitest_include_globs() -> tuple[str, ...]:
    """The `test.include` array from vitest.config.ts (not `coverage.include`)."""
    text = (_ROOT / "vitest.config.ts").read_text(encoding="utf-8")
    body = text.split("test: {", 1)[1].split("include: [", 1)[1].split("]", 1)[0]
    globs = tuple(re.findall(r'"([^"]+)"', body))
    assert globs, "could not parse test.include from vitest.config.ts"
    return globs


def _glob_root(glob: str) -> str:
    """`scripts/lib/**/*.test.js` -> `scripts/lib/`."""
    segments: list[str] = []
    for segment in glob.split("/"):
        if any(char in segment for char in "*?["):
            break
        segments.append(segment)
    return "/".join(segments) + "/"


@lru_cache(maxsize=1)
def _vitest_collected_files() -> tuple[Path, ...]:
    files: list[Path] = []
    for glob in _vitest_include_globs():
        root = _ROOT / _glob_root(glob)
        if root.is_dir():
            files.extend(sorted(root.rglob(glob.rsplit("/", 1)[-1])))
    assert files, "vitest include globs collected no files"
    return tuple(files)


@lru_cache(maxsize=1)
def _python_test_files() -> tuple[Path, ...]:
    files: list[Path] = []
    for root in _PYTHON_TEST_ROOTS:
        files.extend(sorted((_ROOT / root).rglob("*.py")))
    assert files, "no python test modules collected"
    return tuple(files)


_WEB_READ_METHODS = {"exists", "glob", "is_file", "iterdir", "read_bytes", "read_text", "rglob"}
_NON_REPOSITORY_WEB_FIXTURES = {
    # These modules inspect temporary deploy fixtures named web/, not the
    # checkout's web tree, so a real web change cannot affect their assertions.
    "cloud/tests/test_deploy_corrections.py",
    "cloud/tests/test_deploy_resilience.py",
    "scripts/tests/test_weekend_runner_env_provisioning.py",
}
_DYNAMIC_WEB_READER_MODULES = {
    # Their tracked web paths live in parameter tables and are joined to ROOT
    # through loop variables, which the deliberately small AST pass cannot
    # resolve without executing test code.
    "scripts/tests/test_replica_safe_default.py",
    "scripts/tests/test_uw_budget.py",
}


def _declared_cross_tree_modules() -> set[str]:
    return {
        target.split("::", 1)[0]
        for contract in CROSS_TREE_CONTRACTS
        for target in contract.tests
    }


def _direct_web_reader_modules() -> set[str]:
    """Find pytest modules that directly read a tracked path under web/."""
    readers: set[str] = set()
    for path in _python_test_files():
        relative = str(path.relative_to(_ROOT))
        if relative in _NON_REPOSITORY_WEB_FIXTURES or relative == "scripts/tests/test_path_filter.py":
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        if not any(marker in source for marker in ('"web"', "'web'", "web/")):
            continue
        tree = ast.parse(source)
        web_path_names: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            value = ast.get_source_segment(source, node.value) or ""
            if "web" not in value.lower():
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            web_path_names.update(
                target.id for target in targets if isinstance(target, ast.Name)
            )
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in _WEB_READ_METHODS:
                continue
            receiver = ast.get_source_segment(source, node.func.value) or ""
            if '"web"' in receiver or "'web'" in receiver or any(
                re.search(rf"\b{re.escape(name)}\b", receiver) for name in web_path_names
            ):
                readers.add(relative)
                break
    return readers


def test_web_only_change_runs_only_web_and_mapped_cross_tree_contracts() -> None:
    # T-157: tests/test_no_tracked_account_figures.py and
    # scripts/tests/test_replica_safe_default.py read web/lib, so a web-only
    # push must not skip the gate that guards it.
    selection = select_gates(["web/lib/agent/turnSteps.ts"])
    assert (selection.python, selection.web, selection.contracts) == (False, True, True)
    assert "scripts/tests/test_replica_safe_default.py" in selection.contract_tests


def test_scripts_only_change_still_runs_web_gate() -> None:
    # T-156: vitest is the ONLY runner of scripts/lib/**/*.test.js, and
    # web/tests reads scripts/ and cloud/ source directly.
    python, web = classify(["scripts/ib_sync.py", "cloud/scripts/deploy.sh"])
    assert (python, web) == (True, True)


def test_python_change_does_not_duplicate_cross_tree_contract_job() -> None:
    selection = select_gates(["scripts/ib_sync.py", "web/lib/agent/turnSteps.ts"])
    assert (selection.python, selection.web, selection.contracts) == (True, True, False)
    assert selection.contract_tests == ()


def test_unread_web_asset_does_not_start_an_empty_contract_job() -> None:
    selection = select_gates(["web/app/globals.css"])
    assert (selection.python, selection.web, selection.contracts) == (False, True, False)


def test_shared_ci_yaml_runs_both() -> None:
    python, web = classify([".github/workflows/ci.yml"])
    assert (python, web) == (True, True)


def test_docs_only_skips_both_gates() -> None:
    python, web = classify(["docs/cloud-services.md", "tasks/todo.md", "README.md"])
    assert (python, web) == (False, False)


def test_mixed_web_and_python_runs_both() -> None:
    python, web = classify(["web/app/page.tsx", "scripts/ib_sync.py"])
    assert (python, web) == (True, True)


def test_unknown_runtime_path_runs_both() -> None:
    python, web = classify(["config/sudoers.d/radon-deploy"])
    assert (python, web) == (True, True)


def test_empty_range_runs_both() -> None:
    python, web = classify([])
    assert (python, web) == (True, True)


def test_write_output_appends_github_output(tmp_path: Path) -> None:
    target = tmp_path / "github_output"
    selection = select_gates(["web/lib/agent/turnSteps.ts"])
    write_output(selection, target)
    output = target.read_text(encoding="utf-8")
    assert "python=false\n" in output
    assert "web=true\n" in output
    assert "contracts=true\n" in output
    assert "contract_cloud_tests=\n" in output
    assert "contract_script_tests=scripts/tests/test_replica_safe_default.py\n" in output
    assert "contract_root_tests=\n" in output


def test_cross_tree_targets_are_split_by_pytest_root() -> None:
    selection = select_gates(["web/lib/chat.ts", "web/lib/wsTicket.ts"])
    assert selection.contract_cloud_tests
    assert selection.contract_script_tests == (
        "scripts/tests/test_replica_safe_default.py",
    )
    assert selection.contract_root_tests == (
        "tests/test_no_tracked_account_figures.py",
    )


# --- T-156: the web gate must own everything vitest collects ----------------


def test_every_vitest_include_root_routes_to_the_web_gate() -> None:
    """A change under any `test.include` root must turn the vitest gate on.

    Vitest is the only runner of `scripts/lib/**/*.test.js` (11 files) and
    pytest cannot collect `.js` — so if this root does not route to `web`,
    changing a test file skips the gate that runs it.
    """
    unrouted = []
    for glob in _vitest_include_globs():
        root = _glob_root(glob)
        _, web = classify([f"{root}{_SENTINEL}"])
        if not web:
            unrouted.append((root, glob))
    assert not unrouted, (
        "vitest include roots that do NOT turn on the web gate: "
        + ", ".join(f"{root} (from {glob})" for root, glob in unrouted)
    )


def test_every_tree_read_by_a_vitest_test_routes_to_the_web_gate() -> None:
    """Cross-tree reads (`../../scripts/...`, `cloud/services/...`) count too."""
    reads = _referenced_trees(list(_vitest_collected_files()), [_slash_literal_re()])
    assert "scripts/" in reads and "cloud/" in reads, (
        "expected the vitest trees to read scripts/ and cloud/; derivation is broken"
    )
    unrouted = {
        prefix: proof
        for prefix, proof in reads.items()
        if not classify([f"{prefix}{_SENTINEL}"])[1]
    }
    assert not unrouted, (
        "trees read by vitest tests that do NOT turn on the web gate: "
        + ", ".join(f"{prefix} (read by {proof})" for prefix, proof in sorted(unrouted.items()))
    )


# --- T-157: the python gate must own everything pytest reads ---------------


def test_python_contracts_fire_for_site_plates_and_web_lib() -> None:
    """The ⛔ PII guard and the account-figure guard live in pytest.

    tests/test_no_public_account_assets.py forbids `site/public/plates/dashboard-*`
    and tests/test_no_tracked_account_figures.py scans `web/lib/chat.ts`, so each
    guard was being skipped by exactly the change it exists to catch.
    """
    assert classify(["site/public/plates/dashboard-x.png"])[0] is True
    selection = select_gates(["web/lib/chat.ts"])
    assert selection.python is False
    assert "tests/test_no_tracked_account_figures.py" in selection.contract_tests


def test_every_tree_read_by_a_python_test_routes_to_the_python_gate() -> None:
    reads = _referenced_trees(
        list(_python_test_files()), [_slash_literal_re(), _pathlib_segment_re()]
    )
    assert "web/" in reads and "site/" in reads, (
        "expected the python test trees to read web/ and site/; derivation is broken"
    )
    # web/ is the one deliberately extracted tree; its focused inventory is
    # guarded separately below. Every other read still arms full pytest.
    unrouted = {
        prefix: proof
        for prefix, proof in reads.items()
        if prefix != "web/"
        if not classify([f"{prefix}{_SENTINEL}"])[0]
    }
    assert not unrouted, (
        "trees read by python tests that do NOT turn on the python gate: "
        + ", ".join(f"{prefix} (read by {proof})" for prefix, proof in sorted(unrouted.items()))
    )


def test_every_python_test_that_reads_web_has_a_cross_tree_contract() -> None:
    discovered = _direct_web_reader_modules() | _DYNAMIC_WEB_READER_MODULES
    undeclared = discovered - _declared_cross_tree_modules()
    assert not undeclared, (
        "pytest modules read tracked web files but have no CROSS_TREE_CONTRACTS entry: "
        + ", ".join(sorted(undeclared))
    )


def test_cross_tree_targets_name_existing_test_modules() -> None:
    missing = {
        module
        for module in _declared_cross_tree_modules()
        if not (_ROOT / module).is_file()
    }
    assert not missing, "cross-tree pytest target modules do not exist: " + ", ".join(
        sorted(missing)
    )


# --- T-159: skip prefixes are about documentation, not whole subtrees -------


def test_runtime_data_under_docs_runs_both_gates() -> None:
    """`docs/` holds runtime data, not only prose.

    docs/options-structures.json is read by two vitest suites and docs/owners.json
    is the map scripts/tests/test_docs_contract.py runs on.
    """
    assert classify(["docs/options-structures.json"]) == (True, True)
    assert classify(["docs/owners.json"]) == (True, True)


def test_executable_code_under_skip_prefixes_runs_both_gates() -> None:
    assert classify([".claude/hooks/session_start.py"]) == (True, True)
    assert classify([".claude/workflows/factory.mjs"]) == (True, True)


def test_documentation_assets_under_skip_prefixes_still_skip() -> None:
    assert classify(["docs/brand-identity.md", "docs/images/flow.png"]) == (False, False)


# --- T-192: root-level FILES are read by python tests too -------------------


def test_root_web_config_change_still_runs_python_gate() -> None:
    """`vitest.config.ts` is pinned by a PYTHON test.

    scripts/tests/test_merge_vitest_coverage.py holds the ONLY assertion on the
    vitest coverage thresholds and scripts/tests/test_ci_deploy_concurrency.py
    reads the config itself, so a config-only push was skipping the whole python
    gate, i.e. the only gate that can catch a lowered ratchet.
    """
    assert classify(["vitest.config.ts"]) == (True, True)
    assert classify(["package.json"]) == (True, True)
    assert classify(["bun.lock"]) == (True, True)
    # PYTHON_READS only ever switches a gate ON: a python-owned root file must
    # still leave the web gate off.
    assert classify(["pyproject.toml"]) == (True, False)


def test_every_root_file_read_by_a_python_test_routes_to_the_python_gate() -> None:
    """Same derivation as the tree guard, one level up: root FILES. T-192.

    Documentation-suffixed root files are excluded on purpose: `_is_documentation`
    makes a `.md`-only range skip both gates by design (path_filter's module
    docstring), so asserting on them here would contradict that contract.
    """
    reads = _referenced_trees(list(_python_test_files()), [_root_file_re()], suffix="")
    assert "vitest.config.ts" in reads, (
        "expected the python test trees to read vitest.config.ts; derivation is broken"
    )
    unrouted = {
        name: proof
        for name, proof in reads.items()
        if not name.endswith(DOC_SUFFIXES) and not classify([name])[0]
    }
    assert not unrouted, (
        "root files read by python tests that do NOT turn on the python gate: "
        + ", ".join(f"{name} (read by {proof})" for name, proof in sorted(unrouted.items()))
    )


# --- T-312: a push's diff base is the last GREEN main SHA, not event.before --
#
# `github.event.before` is whatever `main` pointed at a moment ago, green or
# red. A docs-only push on top of a red `main` diffed as documentation, skipped
# every gate, and `deploy` (which accepts `skipped`) shipped the red runtime
# tree as a green release. The base must be the newest SHA whose ci.yml push
# run concluded `success` AND that HEAD descends from; anything less resolvable
# runs both gates.


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repo: Path, path: str, message: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"# {message}\n", encoding="utf-8")
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "--no-verify", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _red_then_docs_history(tmp_path: Path) -> tuple[Path, str, str, str]:
    """green (runtime, gate passed) -> red (runtime, gate failed) -> head (docs)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    green = _commit(repo, "scripts/ok.py", "green")
    red = _commit(repo, "scripts/broken.py", "red")
    head = _commit(repo, "tasks/todo.md", "docs only")
    return repo, green, red, head


def _is_ancestor_in(repo: Path):
    return lambda sha, head: _is_ancestor(sha, head, cwd=repo)


def test_docs_push_after_a_red_main_runs_both_gates_from_the_last_green_base(
    tmp_path: Path,
) -> None:
    repo, green, red, head = _red_then_docs_history(tmp_path)
    # The finding: event.before is the red SHA and before..head is documentation.
    assert classify(changed_paths(red, head, cwd=repo)) == (False, False)

    base = resolve_gate_base(head, lambda: [green], _is_ancestor_in(repo))
    assert base == green
    assert classify(changed_paths(base, head, cwd=repo)) == (True, True)


def test_green_base_equal_to_before_keeps_the_docs_only_skip(tmp_path: Path) -> None:
    repo, _green, before, head = _red_then_docs_history(tmp_path)
    base = resolve_gate_base(head, lambda: [before], _is_ancestor_in(repo))
    assert base == before
    assert classify(changed_paths(base, head, cwd=repo)) == (False, False)


def test_unresolvable_green_base_runs_both_gates(tmp_path: Path) -> None:
    repo, _green, _red, head = _red_then_docs_history(tmp_path)

    def api_down() -> list[str]:
        raise RuntimeError("gh api: 502")

    def git_down(_sha: str, _head: str) -> bool:
        raise OSError("git missing")

    for base in (
        resolve_gate_base(head, lambda: [], _is_ancestor_in(repo)),
        resolve_gate_base(head, lambda: ["", "not-a-sha"], _is_ancestor_in(repo)),
        resolve_gate_base(head, api_down, _is_ancestor_in(repo)),
        resolve_gate_base(head, lambda: ["deadbeef"], git_down),
    ):
        assert base is None
        assert changed_paths(base or "", head, cwd=repo) == []
        assert classify(changed_paths(base or "", head, cwd=repo)) == (True, True)


def test_a_green_sha_off_the_ancestry_is_skipped_for_an_older_ancestor(
    tmp_path: Path,
) -> None:
    repo, green, _red, head = _red_then_docs_history(tmp_path)
    _git(repo, "checkout", "-q", "-b", "other", green)
    off_ancestry = _commit(repo, "scripts/other.py", "sibling branch")
    _git(repo, "checkout", "-q", "main")
    assert _is_ancestor(green, head, cwd=repo)
    assert not _is_ancestor(off_ancestry, head, cwd=repo)

    base = resolve_gate_base(head, lambda: [off_ancestry, green], _is_ancestor_in(repo))
    assert base == green


def _run_main(monkeypatch, tmp_path: Path, repo: Path, **env: str) -> str:
    output = tmp_path / "github_output"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_REPOSITORY", "joemccann/radon")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert path_filter.main() == 0
    return output.read_text(encoding="utf-8")


def test_main_on_a_push_diffs_against_deploy_history_not_event_before(
    monkeypatch, tmp_path: Path
) -> None:
    repo, green, red, head = _red_then_docs_history(tmp_path)
    # The pre-T-312 wiring: BASE_SHA is event.before, the red SHA.
    monkeypatch.setattr(path_filter, "green_main_push_shas", lambda _repo: [green], raising=False)
    output = _run_main(
        monkeypatch, tmp_path, repo, GITHUB_EVENT_NAME="push", BASE_SHA=red, HEAD_SHA=head
    )
    assert "python=true\n" in output
    assert "web=true\n" in output


def test_main_on_a_push_with_unreadable_history_runs_both_gates(
    monkeypatch, tmp_path: Path
) -> None:
    repo, _green, red, head = _red_then_docs_history(tmp_path)

    def api_down(_repo: str) -> list[str]:
        raise subprocess.CalledProcessError(1, ["gh", "api"])

    monkeypatch.setattr(path_filter, "green_main_push_shas", api_down, raising=False)
    output = _run_main(
        monkeypatch, tmp_path, repo, GITHUB_EVENT_NAME="push", BASE_SHA=red, HEAD_SHA=head
    )
    assert "python=true\n" in output
    assert "web=true\n" in output


def test_main_on_a_pull_request_still_diffs_against_the_pr_base(
    monkeypatch, tmp_path: Path
) -> None:
    repo, _green, red, head = _red_then_docs_history(tmp_path)

    def must_not_be_called(_repo: str) -> list[str]:
        raise AssertionError("pull_request events diff against the PR base, not deploy history")

    monkeypatch.setattr(path_filter, "green_main_push_shas", must_not_be_called, raising=False)
    output = _run_main(
        monkeypatch, tmp_path, repo, GITHUB_EVENT_NAME="pull_request", BASE_SHA=red, HEAD_SHA=head
    )
    assert "python=false\n" in output
    assert "web=false\n" in output


def test_green_main_push_shas_asks_for_successful_push_runs_of_the_gate_workflow(
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="aaa\nbbb\n", stderr="")

    monkeypatch.setattr(path_filter.subprocess, "run", fake_run)
    assert green_main_push_shas("joemccann/radon") == ["aaa", "bbb"]
    (cmd,) = calls
    assert cmd[:2] == ["gh", "api"]
    query = cmd[2]
    assert query.startswith("repos/joemccann/radon/actions/workflows/ci.yml/runs?")
    for clause in ("branch=main", "event=push", "status=success"):
        assert clause in query
    assert ".workflow_runs[].head_sha" in cmd


def test_ci_changes_job_resolves_the_push_base_from_deploy_history() -> None:
    import yaml

    workflow = yaml.load(
        (_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    changes = workflow["jobs"]["changes"]
    (filter_step,) = [step for step in changes["steps"] if step.get("id") == "filter"]
    env = filter_step["env"]
    assert "github.event.before" not in env["BASE_SHA"], (
        "event.before is whatever main pointed at, red or green; the push base "
        "comes from deploy history (T-312)"
    )
    assert "github.event.pull_request.base.sha" in env["BASE_SHA"]
    assert env["GH_TOKEN"] == "${{ github.token }}"
    assert changes["permissions"]["actions"] == "read"
    assert changes["permissions"]["contents"] == "read"
