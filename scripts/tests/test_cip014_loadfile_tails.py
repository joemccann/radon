"""CIP-014: the two pytest tails must be splittable by ``--dist loadfile``.

Run 36379054426 kept ``test_weekend_subscription_only.py`` (184.5s, 900
tests) on one worker of ``pytest (scripts-gh)`` and
``test_loop_lifecycle_adversarial.py`` (118.8s) on one worker of
``pytest (scripts-jm)``. ``loadfile`` will not divide a module, so the
heavy classes and the two lifecycle floors have to live in separate
modules the existing shard globs already collect. No new shard.

Lifecycle cases that start processes must take ``tmp_path`` before those
modules run side by side. Read-only source comparisons are the exception.
"""

from __future__ import annotations

import ast
import fnmatch
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
TESTS = ROOT / "scripts" / "tests"

SUBSCRIPTION_LIB = TESTS / "weekend_subscription_only_lib.py"
LIFECYCLE_LIB = TESTS / "loop_lifecycle_adversarial_lib.py"
SUBSCRIPTION_MONOLITH = TESTS / "test_weekend_subscription_only.py"
LIFECYCLE_MONOLITH = TESTS / "test_loop_lifecycle_adversarial.py"

# Junit on run 36379054426. These four classes are 53.0 + 51.3 + 38.7 + 29.9s
# of the 184.5s subscription file. The two functions are 50.8s and 33.0s of
# the 118.8s lifecycle file (the 33s case is a single-test floor).
HEAVY_SUBSCRIPTION = (
    "TestATruthyUseFlagIsIgnored",
    "TestAnApiKeyInTheEnvironmentIsIgnored",
    "TestABillingRerouteInAnIgnoredEnvFileRefusesTheRun",
    "TestAFalsyUseFlagStillRuns",
)
HEAVY_LIFECYCLE = (
    "test_a_session_detached_agent_child_does_not_outlive_its_round",
    "test_concurrent_reclaim_of_a_stale_lock_never_yields_two_owners",
)

# Source comparisons. They do not create a clone, a lock, or a process.
LIFECYCLE_READ_ONLY = {
    "test_lifecycle_helpers_are_identical_in_all_six_wrappers",
    "test_every_wrapper_clears_git_locks_records_rounds_and_reaps",
    "test_launchd_gives_the_wrapper_time_to_reap_and_page",
    "test_no_loop_skill_tells_the_agent_to_verify_the_runner_lock",
}

# Inventory on the same junit. A dropped or duplicated case moves the count.
SUBSCRIPTION_TESTS = 600
LIFECYCLE_TESTS = 134


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _py_rows() -> dict[str, str]:
    job = _workflow()["jobs"]["py-tests"]
    return {
        str(row["shard"]): str(row["paths"])
        for row in job["strategy"]["matrix"]["include"]
    }


def _shards_for(rel: str) -> set[str]:
    hits: set[str] = set()
    for shard, paths in _py_rows().items():
        for token in paths.split():
            if token.startswith("--ignore="):
                continue
            if fnmatch.fnmatch(rel, token):
                hits.add(shard)
    return hits


def _defines(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            found.add(node.name)
        elif isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            found.add(node.name)
    return found


def _imported_from(path: Path, lib_module: str) -> set[str]:
    if not path.is_file():
        return set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or node.module != lib_module:
            continue
        for alias in node.names:
            found.add(alias.name)
    return found


def _homes(symbols: tuple[str, ...], lib: Path, monolith: Path, collectors: list[Path], lib_module: str) -> dict[str, Path]:
    homes: dict[str, Path] = {}
    defined = _defines(lib) | _defines(monolith)
    for symbol in symbols:
        assert symbol in defined, f"{symbol} is no longer defined"
        owners = [monolith] if symbol in _defines(monolith) else []
        owners += [path for path in collectors if symbol in _imported_from(path, lib_module)]
        assert len(owners) == 1, f"{symbol} is collected from {owners or 'nowhere'}"
        homes[symbol] = owners[0]
    return homes


def test_py_tests_stay_on_loadfile_without_a_new_shard() -> None:
    job = _workflow()["jobs"]["py-tests"]
    commands = "\n".join(str(step.get("run", "")) for step in job["steps"])
    assert "--dist loadfile" in commands
    shards = job["strategy"]["matrix"]["shard"]
    assert shards == [
        "scripts-ac",
        "scripts-df",
        "scripts-i",
        "scripts-gh",
        "scripts-jm",
        "scripts-npsz",
        "scripts-rs",
        "scripts-daemons",
        "rest",
    ]


def test_subscription_heavy_classes_are_separate_scripts_gh_modules() -> None:
    """One worker held all four classes for 184.5s. Each needs its own file."""
    assert not SUBSCRIPTION_MONOLITH.exists(), (
        "test_weekend_subscription_only.py is one loadfile unit; "
        "split it into test_weekend_subscription_only_*.py"
    )
    assert SUBSCRIPTION_LIB.is_file(), "shared subscription helpers must stay uncollected"
    collectors = sorted(TESTS.glob("test_weekend_subscription_only_*.py"))
    assert len(collectors) >= len(HEAVY_SUBSCRIPTION)
    homes = _homes(
        HEAVY_SUBSCRIPTION,
        SUBSCRIPTION_LIB,
        SUBSCRIPTION_MONOLITH,
        collectors,
        "scripts.tests.weekend_subscription_only_lib",
    )
    assert len(set(homes.values())) == len(HEAVY_SUBSCRIPTION), homes
    for path in homes.values():
        rel = str(path.relative_to(ROOT))
        assert _shards_for(rel) == {"scripts-gh"}, f"{rel} -> {_shards_for(rel)}"


def test_lifecycle_floors_are_separate_scripts_jm_modules() -> None:
    """The 50.8s detach case and the 33s reclaim case must not share a worker."""
    assert not LIFECYCLE_MONOLITH.exists(), (
        "test_loop_lifecycle_adversarial.py is one loadfile unit; "
        "split it into test_loop_lifecycle_adversarial_*.py"
    )
    assert LIFECYCLE_LIB.is_file()
    collectors = sorted(TESTS.glob("test_loop_lifecycle_adversarial_*.py"))
    assert len(collectors) >= len(HEAVY_LIFECYCLE)
    homes = _homes(
        HEAVY_LIFECYCLE,
        LIFECYCLE_LIB,
        LIFECYCLE_MONOLITH,
        collectors,
        "scripts.tests.loop_lifecycle_adversarial_lib",
    )
    assert len(set(homes.values())) == len(HEAVY_LIFECYCLE), homes
    for path in homes.values():
        rel = str(path.relative_to(ROOT))
        assert _shards_for(rel) == {"scripts-jm"}, f"{rel} -> {_shards_for(rel)}"


def test_every_lifecycle_symbol_is_imported_once() -> None:
    source = LIFECYCLE_LIB if LIFECYCLE_LIB.is_file() else LIFECYCLE_MONOLITH
    defined = _defines(source)
    assert defined
    if LIFECYCLE_MONOLITH.exists():
        # Still one module: the split contract above is the failure. This
        # check only binds once the helpers live outside the collected file.
        return
    collectors = sorted(TESTS.glob("test_loop_lifecycle_adversarial_*.py"))
    imported: list[str] = []
    for path in collectors:
        imported.extend(sorted(_imported_from(path, "scripts.tests.loop_lifecycle_adversarial_lib")))
    assert sorted(imported) == sorted(defined)
    assert len(imported) == len(set(imported))


def test_every_subscription_symbol_is_imported_once() -> None:
    source = SUBSCRIPTION_LIB if SUBSCRIPTION_LIB.is_file() else SUBSCRIPTION_MONOLITH
    defined = _defines(source)
    assert defined
    if SUBSCRIPTION_MONOLITH.exists():
        return
    collectors = sorted(TESTS.glob("test_weekend_subscription_only_*.py"))
    imported: list[str] = []
    for path in collectors:
        imported.extend(sorted(_imported_from(path, "scripts.tests.weekend_subscription_only_lib")))
    assert sorted(imported) == sorted(defined)
    assert len(imported) == len(set(imported))


def test_lifecycle_process_tests_isolate_on_tmp_path() -> None:
    """Side-by-side workers are safe only when each case owns its clone."""
    source = LIFECYCLE_LIB if LIFECYCLE_LIB.is_file() else LIFECYCLE_MONOLITH
    tree = ast.parse(source.read_text(encoding="utf-8"))
    checked = 0
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue
        args = {arg.arg for arg in node.args.args}
        if node.name in LIFECYCLE_READ_ONLY:
            continue
        assert "tmp_path" in args, (
            f"{node.name} starts processes or writes a clone without tmp_path"
        )
        checked += 1
    assert checked >= 20


def test_split_modules_keep_the_measured_inventory() -> None:
    """Collect the pieces. A missing import drops cases; a leftover monolith duplicates them."""
    import subprocess
    import sys

    sub = sorted(TESTS.glob("test_weekend_subscription_only*.py"))
    life = sorted(TESTS.glob("test_loop_lifecycle_adversarial*.py"))
    assert sub and life
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *map(str, sub), *map(str, life)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-2000:] + proc.stdout[-2000:]
    summary = proc.stdout.strip().splitlines()[-1]
    assert f"{SUBSCRIPTION_TESTS + LIFECYCLE_TESTS} tests collected" in summary, summary
