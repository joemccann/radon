"""Path-filter classification for CI test gates."""

from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path

from ci.path_filter import DOC_SUFFIXES, classify, write_output

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


def test_web_only_change_still_runs_python_gate() -> None:
    # T-157: tests/test_no_tracked_account_figures.py and
    # scripts/tests/test_replica_safe_default.py read web/lib, so a web-only
    # push must not skip the gate that guards it.
    python, web = classify(["web/lib/foo.ts", "web/tests/bar.test.tsx"])
    assert (python, web) == (True, True)


def test_scripts_only_change_still_runs_web_gate() -> None:
    # T-156: vitest is the ONLY runner of scripts/lib/**/*.test.js, and
    # web/tests reads scripts/ and cloud/ source directly.
    python, web = classify(["scripts/ib_sync.py", "cloud/scripts/deploy.sh"])
    assert (python, web) == (True, True)


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
    write_output(False, True, target)
    assert target.read_text(encoding="utf-8") == "python=false\nweb=true\n"


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


def test_python_gate_fires_for_site_plates_and_web_lib() -> None:
    """The ⛔ PII guard and the account-figure guard live in pytest.

    tests/test_no_public_account_assets.py forbids `site/public/plates/dashboard-*`
    and tests/test_no_tracked_account_figures.py scans `web/lib/chat.ts`, so each
    guard was being skipped by exactly the change it exists to catch.
    """
    assert classify(["site/public/plates/dashboard-x.png"])[0] is True
    assert classify(["web/lib/chat.ts"])[0] is True


def test_every_tree_read_by_a_python_test_routes_to_the_python_gate() -> None:
    reads = _referenced_trees(
        list(_python_test_files()), [_slash_literal_re(), _pathlib_segment_re()]
    )
    assert "web/" in reads and "site/" in reads, (
        "expected the python test trees to read web/ and site/; derivation is broken"
    )
    unrouted = {
        prefix: proof
        for prefix, proof in reads.items()
        if not classify([f"{prefix}{_SENTINEL}"])[0]
    }
    assert not unrouted, (
        "trees read by python tests that do NOT turn on the python gate: "
        + ", ".join(f"{prefix} (read by {proof})" for prefix, proof in sorted(unrouted.items()))
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
