#!/usr/bin/env python3
"""Select the CI gates and cross-tree contracts affected by a SHA range.

Used by .github/workflows/ci.yml to skip a test gate that cannot be affected
by the range. A gate is skipped only when NOTHING in the range is owned or
read by it: the trees each gate's own tests read across the tree boundary are
routed to that gate too (WEB_READS / PYTHON_READS below), so a change can
never skip the gate that asserts on it. Documentation-only ranges (.md, and
images under the skip prefixes) skip both gates.

The base of the range is what makes a skip safe. On a pull_request it is the
PR base. On a push to main it is the newest SHA whose own ci.yml push run
concluded `success` AND that HEAD descends from (`resolve_gate_base`), never
`github.event.before`: `before` is whatever main pointed at a moment ago, red
or green, so a docs-only push on top of a red main diffed as documentation,
skipped every gate, and `deploy` (which accepts `skipped`) shipped the red
runtime tree as a green release. When that base cannot be resolved (API error,
no green run, none on HEAD's ancestry) the range is empty and both gates run.
T-312.
"""

from __future__ import annotations

import fnmatch
import os
import posixpath
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

GATE_WORKFLOW = "ci.yml"
GATE_BRANCH = "main"
# Successful push runs to look back through for one on HEAD's ancestry. A
# force-push that rewrites more than this many releases resolves nothing and
# runs both gates, which is the fail-closed answer for a rewritten main.
GREEN_RUN_LOOKBACK = 30
# Bound on the `gh api` lookback: a stalled GitHub API socket must not park
# every downstream gate and the deploy for the default 360 min (R-509). A
# TimeoutExpired is caught by resolve_gate_base and runs both gates.
GREEN_RUN_LOOKUP_TIMEOUT_S = 60

# REL-179 (R-476): documentation a TEST asserts on is not documentation for
# gate purposes. Agent rails and repo contracts (every CLAUDE.md, AGENTS.md
# and skill SKILL.md) fail CLOSED to both gates — a push that removes a rail
# from an unattended loop must not skip the gate that pins it. Any other .md
# that a test names (by path or by basename) routes to the focused pytest
# modules that read it, or arms the web gate when a vitest suite reads it.
FAIL_CLOSED_DOC_BASENAMES = frozenset({"claude.md", "agents.md", "skill.md"})
TEST_TREES = ("tests", "scripts/tests", "cloud/tests", "web/tests")
TEST_SOURCE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs")

WEB_PREFIXES = (
    "web/",
    "site/",
    "lib/",
    # vitest.config.ts includes scripts/lib/**/*.test.js (the WS relay's
    # stale-tick ladder, reconnect gate and backpressure suites). pytest
    # collects none of them, so this prefix must arm the web gate too — the
    # "scripts/" python match below is not enough. R-202.
    "scripts/lib/",
    "brand/",
    "vitest.config.ts",
    "package.json",
    "bun.lock",
)
PYTHON_PREFIXES = (
    "scripts/",
    "cloud/",
    "tests/",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
)
SHARED_PREFIXES = (".github/",)
SKIP_PREFIXES = (
    "docs/",
    "tasks/",
    ".claude/",
    ".agents/",
    "notebooks/",
)
# A skip prefix skips DOCUMENTATION, not the whole subtree. docs/ also holds
# runtime data (docs/options-structures.json is read by two vitest suites,
# docs/owners.json is the map the docs contract runs on) and .claude/ holds
# executable hooks/workflows. T-159.
DOC_SUFFIXES = (".md", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico")

# Cross-tree reads: trees whose files are consumed by the OTHER gate's tests.
# Both tuples are derived from the checkout and pinned by
# scripts/tests/test_path_filter.py, which re-derives them on every run from
# vitest.config.ts's test.include globs and from the path literals inside
# web/tests, lib/tools/__tests__, site/lib, scripts/lib, .pi/tests and
# tests/, scripts/tests/, cloud/tests/. Adding a new cross-tree read without
# updating these turns that test red. They only ever switch a gate ON.
#
# WEB: vitest is the sole runner of scripts/lib/**/*.test.js (pytest cannot
# collect .js) and web/tests reads scripts/ and cloud/ source directly
# (refresh-schedule reads cloud/services/*, market-state-holiday imports
# scripts/config/market_holidays.json). T-156.
WEB_READS = (
    ".pi/",
    "cloud/",
    "data/",
    "docs/",
    "lib/",
    "logs/",
    "scripts/",
    "site/",
    "tasks/",
    "tests/",
    "web/",
)
# PYTHON: pytest reads several non-python trees. Web reads are intentionally
# handled by CROSS_TREE_CONTRACTS below so a web-only change does not start the
# full python/cloud suite. T-157 / E3.
#
# The three root FILES are web-owned but python-asserted:
# scripts/tests/test_merge_vitest_coverage.py holds the only assertion pinning
# the vitest coverage thresholds, and test_ci_deploy_concurrency.py reads
# vitest.config.ts / bun.lock while cloud/tests/test_payload_case_patterns.py
# reads package.json. Without them a config-only push skipped the entire python
# gate, so a ratchet could be lowered with nothing running anywhere. T-192.
PYTHON_READS = (
    ".claude/",
    ".github/",
    ".pi/",
    "cloud/",
    "config/",
    "data/",
    "docker/",
    "docs/",
    "lib/",
    "logs/",
    "scripts/",
    "site/",
    "tasks/",
    "tests/",
    "bun.lock",
    "package.json",
    "vitest.config.ts",
)


@dataclass(frozen=True)
class CrossTreeContract:
    """A web path pattern and the focused pytest targets that assert on it."""

    patterns: tuple[str, ...]
    tests: tuple[str, ...]


# Static inventory of pytest contracts that read tracked web files. Keep the
# targets focused: cloud/tests/test_caddy_edge_timeouts.py contains live timing
# mechanism tests, so selecting the whole module would erase the speedup.
CROSS_TREE_CONTRACTS = (
    CrossTreeContract(
        patterns=(
            "web/lib/*.js",
            "web/lib/*.jsx",
            "web/lib/*.mjs",
            "web/lib/*.cjs",
            "web/lib/*.ts",
            "web/lib/*.tsx",
            "web/app/*.js",
            "web/app/*.jsx",
            "web/app/*.mjs",
            "web/app/*.cjs",
            "web/app/*.ts",
            "web/app/*.tsx",
            "web/components/*.js",
            "web/components/*.jsx",
            "web/components/*.mjs",
            "web/components/*.cjs",
            "web/components/*.ts",
            "web/components/*.tsx",
        ),
        tests=("scripts/tests/test_replica_safe_default.py",),
    ),
    CrossTreeContract(
        patterns=("web/lib/chat.ts",),
        tests=("tests/test_no_tracked_account_figures.py",),
    ),
    CrossTreeContract(
        patterns=("web/lib/wsTicket.ts",),
        tests=(
            "cloud/tests/test_caddy_edge_timeouts.py::TestRideOutMatchesItsNamedClient::test_the_ib_window_is_not_longer_than_getwsticket_waits",
        ),
    ),
    CrossTreeContract(
        patterns=("web/app/api/assistant/route.ts",),
        tests=(
            "cloud/tests/test_caddy_edge_timeouts.py::TestTheAssistantTurnOutlivesTheGenericGuard::test_the_assistant_bound_stays_inside_the_routes_own_budget",
            "cloud/tests/test_caddy_edge_timeouts.py::TestTheAssistantHandleStatesItStreams::test_the_route_writes_its_header_before_the_loop",
        ),
    ),
    CrossTreeContract(
        patterns=("web/middleware.ts",),
        tests=(
            "cloud/tests/test_nextjs_db_watchdog.py::test_service_health_is_bearer_gated_not_public",
        ),
    ),
    CrossTreeContract(
        patterns=("web/lib/serviceHealthWindows.ts",),
        tests=(
            "cloud/tests/test_nextjs_db_watchdog.py::TestHealthRowServiceIsInBothCatalogs::test_registered_in_the_typescript_catalog",
            "scripts/tests/test_cadence_and_growth_bounds.py::TestSignalsFreshnessMatchesItsCadence::test_the_web_windows_agree_with_the_watchdog",
            "scripts/tests/test_cadence_and_growth_bounds.py::TestSignalsFreshnessMatchesItsCadence::test_the_web_side_measures_from_the_open",
            "scripts/tests/test_exit_orders_unregistered.py::test_exit_orders_absent_from_both_watchdog_catalogs",
            "scripts/tests/test_indicator_storage_hygiene.py::TestTrinClosedWindowCoversTheWeekend::test_both_catalogs_use_a_multi_day_closed_window",
            "scripts/tests/test_monitor_daemon/test_menthorq_session_check.py::TestRegistrationContract::test_typescript_window_mirrors_python",
            "scripts/tests/test_rel141_catalog_exemptions_are_true.py::TestTheTwoGenuineGapsAreClosed::test_the_service_is_in_both_catalogs",
            "scripts/tests/test_rel148_operability_fixes.py::TestEveryTimerDrivenScanIsWatched::test_the_web_catalog_no_longer_calls_it_on_demand",
            "scripts/tests/test_service_registration_completeness.py",
            "scripts/tests/test_watchdog/test_services.py",
            "scripts/tests/test_watchdog_catalog_and_journal_bound.py::TestEveryScheduledServiceIsInABucket::test_perf_twr_is_in_both_catalogs",
            "scripts/tests/test_watchdog_catalog_parity.py",
        ),
    ),
    CrossTreeContract(
        patterns=("web/app/api/setup/complete/route.ts",),
        tests=("scripts/tests/test_setup_service_id_parity.py",),
    ),
    CrossTreeContract(
        patterns=("web/lib/ieiHyg.ts",),
        tests=(
            "scripts/tests/test_silent_degradation_bounds.py::TestIeiHygNeedsAFullWindow::test_the_web_type_knows_the_unknown_state",
        ),
    ),
    CrossTreeContract(
        patterns=("web/components/WorkspaceSections.tsx",),
        tests=(
            "scripts/tests/test_uw_budget.py::test_scheduled_scan_defaults_resolve_under_the_universe_brake",
        ),
    ),
    CrossTreeContract(
        patterns=("web/tests/integration.test.ts", "web/tests/helpers/python313.ts"),
        tests=("scripts/tests/test_vitest_python_gating.py",),
    ),
    CrossTreeContract(
        patterns=("web/e2e/*.spec.ts", "web/e2e/ci-curation-ledger.txt"),
        tests=("scripts/tests/test_e2e_ci_curation.py",),
    ),
    CrossTreeContract(
        patterns=("web/package-lock.json", "web/bun.lock"),
        tests=(
            "cloud/tests/test_beta_sunset.py::test_root_and_web_do_not_ship_npm_lockfiles",
        ),
    ),
    CrossTreeContract(
        patterns=("web/README.md",),
        tests=(
            "scripts/tests/test_docs_contract.py::TestThinIndex::test_web_readme_does_not_teach_npm",
        ),
    ),
)


@dataclass(frozen=True)
class GateSelection:
    python: bool
    web: bool
    contract_tests: tuple[str, ...] = ()

    @property
    def contracts(self) -> bool:
        return bool(self.contract_tests)

    def tests_under(self, prefix: str) -> tuple[str, ...]:
        return tuple(test for test in self.contract_tests if test.startswith(prefix))

    @property
    def contract_cloud_tests(self) -> tuple[str, ...]:
        return self.tests_under("cloud/tests/")

    @property
    def contract_script_tests(self) -> tuple[str, ...]:
        return self.tests_under("scripts/tests/")

    @property
    def contract_root_tests(self) -> tuple[str, ...]:
        return self.tests_under("tests/")


def _matches(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes)


def _is_documentation(path: str) -> bool:
    if path.endswith(".md"):
        return True
    return _matches(path, SKIP_PREFIXES) and path.endswith(DOC_SUFFIXES)


def classify(paths: list[str]) -> tuple[bool, bool]:
    """Return (python, web). Unknown runtime paths force both gates on."""
    if not paths:
        return True, True
    python = False
    web = False
    saw_runtime = False
    for path in paths:
        if _is_documentation(path):
            continue
        saw_runtime = True
        if _matches(path, SHARED_PREFIXES):
            python = True
            web = True
            continue
        if _matches(path, WEB_PREFIXES + WEB_READS):
            web = True
        if _matches(path, PYTHON_PREFIXES + PYTHON_READS):
            python = True
        # Unknown runtime trees still run both gates. Deliberately measured
        # against the OWNED prefixes only: a cross-tree read must never be the
        # thing that stops an unclassified path from running both gates.
        if not _matches(path, WEB_PREFIXES + PYTHON_PREFIXES + SHARED_PREFIXES):
            python = True
            web = True
    if not saw_runtime:
        return False, False
    return python, web


@lru_cache(maxsize=4)
def _test_sources(root: Path) -> tuple[tuple[str, str], ...]:
    """(repo-relative path, text) for every test source under TEST_TREES."""
    found: list[tuple[str, str]] = []
    for tree in TEST_TREES:
        base = root / tree
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix not in TEST_SOURCE_SUFFIXES or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            found.append((path.relative_to(root).as_posix(), text))
    return tuple(found)


@dataclass(frozen=True)
class DocRouting:
    fail_closed: bool = False
    web: bool = False
    tests: tuple[str, ...] = ()


def route_documentation(paths: Iterable[str], root: Path = REPO) -> DocRouting:
    """Where documentation paths must run, derived from the test trees (R-476)."""
    fail_closed = False
    web = False
    tests: set[str] = set()
    for path in paths:
        name = posixpath.basename(path)
        # REL-207 (R-571): the repo lives on a case-insensitive filesystem;
        # `web/Claude.md` must arm the gates like `web/CLAUDE.md`.
        if name.casefold() in FAIL_CLOSED_DOC_BASENAMES:
            fail_closed = True
            continue
        needles = {f'"{path}"', f"'{path}'", f'"{name}"', f"'{name}'"}
        for rel, text in _test_sources(root):
            if not any(needle in text for needle in needles):
                continue
            if rel.endswith(".py"):
                tests.add(rel)
            else:
                web = True
    return DocRouting(fail_closed=fail_closed, web=web, tests=tuple(sorted(tests)))


def select_gates(paths: list[str]) -> GateSelection:
    """Return full gates plus focused pytest contracts for web-only changes."""
    python, web = classify(paths)
    docs = route_documentation(path for path in paths if _is_documentation(path))
    if docs.fail_closed:
        python = web = True
    web = web or docs.web
    if python:
        return GateSelection(python=python, web=web)

    tests = {
        test
        for path in paths
        if not _is_documentation(path)
        for contract in CROSS_TREE_CONTRACTS
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in contract.patterns)
        for test in contract.tests
    }
    tests.update(docs.tests)
    return GateSelection(python=python, web=web, contract_tests=tuple(sorted(tests)))


def green_main_push_shas(repo: str) -> list[str]:
    """head_sha of the newest successful push runs of the gate workflow, newest first.

    A push run of ci.yml concludes `success` only when every job that ran
    passed: the test gates, the coverage ratchets and `deploy`, which is part
    of the same run. Gates a run skipped were skipped because THEIR range
    resolved to a green base, so success is transitive down this list. The
    Deployments API would need a second call per record for its status and
    says nothing about a run whose deploy was skipped by its `if:`; this is one
    call and answers the question the filter asks: did the gate pass at that SHA.
    """
    query = (
        f"repos/{repo}/actions/workflows/{GATE_WORKFLOW}/runs"
        f"?branch={GATE_BRANCH}&event=push&status=success&per_page={GREEN_RUN_LOOKBACK}"
    )
    result = subprocess.run(
        ["gh", "api", query, "--jq", ".workflow_runs[].head_sha"],
        check=True,
        capture_output=True,
        text=True,
        timeout=GREEN_RUN_LOOKUP_TIMEOUT_S,
    )
    return result.stdout.split()


def _is_ancestor(sha: str, head: str, cwd: Path | None = None) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, head],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def resolve_gate_base(
    head: str,
    green_shas: Callable[[], Iterable[str]],
    is_ancestor: Callable[[str, str], bool],
) -> str | None:
    """The newest green SHA that HEAD descends from; None when nothing qualifies.

    None is the fail-closed answer: `changed_paths` turns it into an empty
    range and `classify` runs both gates on an empty range.
    """
    try:
        candidates = list(green_shas())
        for sha in candidates:
            if sha and is_ancestor(sha, head):
                return sha
    except Exception as error:  # noqa: BLE001 - unreadable history must run both gates
        print(f"gate base unresolved ({error!r}); running both gates", file=sys.stderr)
        return None
    return None


def gate_base(head: str) -> str:
    """PR base on pull_request; last green main SHA on push; '' when unknown."""
    if os.environ.get("GITHUB_EVENT_NAME") != "push":
        return os.environ.get("BASE_SHA", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    return resolve_gate_base(head, lambda: green_main_push_shas(repo), _is_ancestor) or ""


def changed_paths(base: str, head: str, cwd: Path | None = None) -> list[str]:
    if not base or set(base) <= {"0"}:
        return []
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def write_output(selection: GateSelection, output_path: Path) -> None:
    def joined(tests: tuple[str, ...]) -> str:
        return " ".join(tests)

    line = (
        f"python={'true' if selection.python else 'false'}\n"
        f"web={'true' if selection.web else 'false'}\n"
        f"contracts={'true' if selection.contracts else 'false'}\n"
        f"contract_cloud_tests={joined(selection.contract_cloud_tests)}\n"
        f"contract_script_tests={joined(selection.contract_script_tests)}\n"
        f"contract_root_tests={joined(selection.contract_root_tests)}\n"
    )
    with output_path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def main(argv: list[str] | None = None) -> int:
    del argv
    head = os.environ.get("HEAD_SHA", "") or "HEAD"
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        print("GITHUB_OUTPUT is required", file=sys.stderr)
        return 2
    base = gate_base(head)
    paths = changed_paths(base, head)
    selection = select_gates(paths)
    write_output(selection, Path(output))
    print(
        f"python={selection.python} web={selection.web} "
        f"contracts={selection.contracts} files={len(paths)} "
        f"base={base or 'unresolved'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
