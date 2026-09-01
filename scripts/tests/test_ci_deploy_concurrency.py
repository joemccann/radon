"""Deployment workflow concurrency contracts.

The 2026-07-08 production outage was caused by workflow-level
``cancel-in-progress`` terminating the SSH deploy after it had stopped services.
Test jobs may still cancel superseded work, but a deploy that has started must
finish and every deploy must name the exact commit it intends to release.
"""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path

import yaml


WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
# Hand-maintained mirror of `main`'s branch-protection required contexts. Read
# from disk on purpose: this suite must run offline, and a test that calls the
# GitHub API would be unrunnable on a fork and flaky on a rate limit.
REQUIRED_STATUS_CHECKS = (
    Path(__file__).resolve().parents[2] / ".github" / "required-status-checks.json"
)
TEST_JOBS = (
    "secret-scan",
    "changes",
    "app-images",
    "contract-cloud-tests",
    "contract-script-tests",
    "contract-root-tests",
    "web-tests",
    "web-coverage",
    "py-tests",
    "py-coverage",
    "cloud-tests",
    "perimeter-smoke",
)


def _declared_required_status_checks() -> set[str]:
    """Contexts the operator has declared as required status checks on `main`.

    Empty today: `gh api repos/{owner}/{repo}/branches/main/protection` returns
    no `required_status_checks` key at all. Adding a context here is a claim
    about live GitHub state that this test cannot verify — apply it over the API
    first, then declare it.
    """
    if not REQUIRED_STATUS_CHECKS.exists():
        return set()
    declared = json.loads(REQUIRED_STATUS_CHECKS.read_text(encoding="utf-8"))
    return set(declared.get("contexts", []))


def _workflow() -> dict:
    # BaseLoader avoids YAML 1.1 coercing GitHub's top-level ``on`` key to bool.
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_workflow_level_cancellation_cannot_kill_a_running_deploy() -> None:
    workflow = _workflow()
    concurrency = workflow.get("concurrency")
    assert concurrency is None or concurrency.get("cancel-in-progress") != "true", (
        "workflow-level cancel-in-progress can terminate Deploy via SSH after "
        "production services have been stopped"
    )


def test_superseded_test_jobs_remain_cancelable() -> None:
    jobs = _workflow()["jobs"]
    for name in TEST_JOBS:
        concurrency = jobs[name].get("concurrency", {})
        assert concurrency.get("cancel-in-progress") == "true", (
            f"{name} must cancel superseded test work without canceling deploy"
        )
        assert "github.ref" in concurrency.get("group", "")


def test_production_deploy_is_serialized_and_never_canceled() -> None:
    deploy = _workflow()["jobs"]["deploy"]
    concurrency = deploy.get("concurrency", {})
    assert concurrency.get("group") == "deploy-production"
    assert concurrency.get("cancel-in-progress") == "false"


def test_deploy_time_budgets_cover_supervisor_and_recovery() -> None:
    deploy = _workflow()["jobs"]["deploy"]
    assert int(deploy["timeout-minutes"]) == 60
    ssh_step = next(step for step in deploy["steps"] if step.get("name") == "Deploy via SSH")
    command_timeout = ssh_step["with"].get("command_timeout", "")
    assert command_timeout == "55m"

    # A fresh process may first recover an abandoned journal, spend the full
    # inner deadline, then recover another interrupted transition. Each recovery
    # has two 180s mutation actions, 30s verify and commit actions, and may wait
    # 190s once for an orphan root action to release the lifecycle lock.
    root_recovery_seconds = 190 + (2 * 180) + 30 + 30
    worst_case_seconds = (2 * root_recovery_seconds) + 900 + 30
    ssh_seconds = int(command_timeout.removesuffix("m")) * 60
    job_seconds = int(deploy["timeout-minutes"]) * 60
    assert ssh_seconds >= worst_case_seconds + 600
    assert job_seconds >= ssh_seconds + 300


def test_deploy_passes_the_explicit_workflow_sha() -> None:
    deploy = _workflow()["jobs"]["deploy"]
    ssh_step = next(step for step in deploy["steps"] if step.get("name") == "Deploy via SSH")
    script = ssh_step["with"]["script"]
    assert "scripts/deploy.sh" in script
    assert "${{ github.sha }}" in script, (
        "deploy.sh must receive the tested commit, not fetch an implicit moving main"
    )
    # Monorepo path: materialize cloud/ from the release SHA; legacy dual-checkout
    # remains the fallback when the SHA predates the fold.
    assert "cloud/scripts/deploy.sh" in script
    assert "cat-file -e" in script
    assert "LEGACY_CLOUD" in script
    assert "RADON_DEPLOY_ENV_FILE" in script


def test_stage_release_is_gated_and_cancelable() -> None:
    """Was ``..._overlaps_gating_jobs_and_is_cancelable``.

    The overlap half asserted ``"needs" not in stage`` and
    ``continue-on-error: true`` — i.e. it pinned the R-201 hole: the job SSHes
    to the live trading host and runs the pushed commit's own deploy.sh, so
    overlapping the gate meant untested code executed on production, and
    ``continue-on-error`` kept that invisible. Both assertions are inverted
    here; the cancelability and separate-concurrency-group half, which is what
    keeps a superseded prestage from colliding with the real deploy, is
    unchanged. The gate membership itself is asserted in
    ``test_ci_gate_integrity.py``.
    """
    jobs = _workflow()["jobs"]
    stage = jobs["stage-release"]
    assert stage.get("needs"), "stage-release must wait on the test gate"
    assert "refs/heads/main" in stage["if"]
    assert "push" in stage["if"]
    assert "refs/heads/main" in jobs["deploy"]["if"]
    concurrency = stage.get("concurrency", {})
    assert concurrency.get("cancel-in-progress") == "true"
    assert "github.ref" in concurrency.get("group", "")
    assert concurrency.get("group") != "deploy-production"
    assert stage.get("continue-on-error") not in ("true", True), (
        "a failed prestage against production must be a red job; deploy still "
        "tolerates it via needs.stage-release.result == 'failure'"
    )
    ssh_step = next(step for step in stage["steps"] if "ssh-action" in step.get("uses", ""))
    script = ssh_step["with"]["script"]
    assert "RADON_DEPLOY_STAGE=1" in script
    assert "${{ github.sha }}" in script
    assert "cloud/scripts/deploy.sh" in script
    assert "stage-release" in jobs["deploy"]["needs"]
    assert jobs["deploy"]["concurrency"]["cancel-in-progress"] == "false"


def test_exact_images_are_gated_and_prepulled_parallel_to_prestage() -> None:
    jobs = _workflow()["jobs"]
    images = jobs["app-images"]
    prepull = jobs["prepull-images"]
    stage = jobs["stage-release"]
    deploy = jobs["deploy"]

    assert images["uses"] == "./.github/workflows/app-images.yml"
    assert images["permissions"]["packages"] == "write"
    assert "refs/heads/main" in images["if"] and "push" in images["if"]
    assert set(prepull["needs"]) == set(stage["needs"])
    assert "app-images" in prepull["needs"]
    assert prepull["concurrency"]["cancel-in-progress"] == "true"
    assert prepull["concurrency"]["group"] != "deploy-production"

    ssh_step = next(step for step in prepull["steps"] if "ssh-action" in step.get("uses", ""))
    script = ssh_step["with"]["script"]
    assert "/usr/local/sbin/radon-app-runtime pull \"$SHA\"" in script
    assert "${{ github.sha }}" in script
    assert "git " not in script and "deploy.sh" not in script

    assert {"app-images", "prepull-images", "stage-release"} <= set(deploy["needs"])
    assert "needs.app-images.result == 'success'" in deploy["if"]
    assert "needs.prepull-images.result == 'failure'" in deploy["if"]
    assert deploy["concurrency"]["cancel-in-progress"] == "false"


def test_exact_images_publish_only_after_the_secret_scan() -> None:
    """R-442: the callee pushes the release SHA tag and the moving `:latest`
    tag in one BuildKit operation, and the caller job had an `if:` but no
    `needs:`, so a push to main that failed gitleaks still overwrote the
    public GHCR `:latest` pointer with an image carrying the Next client
    bundle and `scripts/`. Deploy was never at risk (`resolve_image` refuses
    non-SHA tags); this is supply-chain hygiene for a public tag.
    """
    jobs = _workflow()["jobs"]
    assert "secret-scan" in jobs["app-images"].get("needs", []), (
        "app-images must wait for secret-scan before publishing :latest"
    )


def _job_commands(job: dict) -> str:
    return "\n".join(str(step.get("run", "")) for step in job.get("steps", []))


def test_ci_runs_cloud_infra_pytest() -> None:
    jobs = _workflow()["jobs"]
    cloud = jobs["cloud-tests"]
    assert "matrix.paths" in _job_commands(cloud)
    assert "cloud/tests" in str(cloud["strategy"]["matrix"]["include"])
    assert "pytest cloud/tests" not in _job_commands(jobs["py-tests"]), (
        "cloud infra tests must run as their own job so they leave the unit "
        "pytest critical path"
    )
    deploy_needs = jobs["deploy"]["needs"]
    assert "cloud-tests" in deploy_needs
    assert "py-tests" in deploy_needs


def test_caddy_wall_clock_tests_have_their_own_shard() -> None:
    cloud = _workflow()["jobs"]["cloud-tests"]
    matrix = cloud["strategy"]["matrix"]
    assert [str(shard) for shard in matrix["shard"]] == ["al", "edge", "mz"]
    rows = {str(row["shard"]): str(row["paths"]) for row in matrix["include"]}
    assert rows["edge"] == "cloud/tests/test_caddy_edge_timeouts.py"
    assert rows["al"] == "cloud/tests/test_[a-l]*.py"
    omits = {
        str(row["shard"]): str(row.get("omit", ""))
        for row in matrix["include"]
    }
    assert omits["al"] == "cloud/tests/test_caddy_edge_timeouts.py"
    assert "matrix.omit" in _job_commands(cloud)
    assert "selected" in _job_commands(cloud)
    caddy_step = next(step for step in cloud["steps"] if step.get("name") == "Install caddy (edge mechanism tests)")
    assert "edge" in caddy_step["if"]
    assert "--durations=25" in _job_commands(cloud)
    assert "--durations=25" in _job_commands(_workflow()["jobs"]["py-tests"])


def test_python_ci_jobs_cache_pip_and_pin_the_test_toolchain() -> None:
    jobs = _workflow()["jobs"]
    uv_pin = "803947b9bd8e9f986429fa0c5a41c367cd732b41"
    for name in ("py-tests", "cloud-tests"):
        setup_uv = next(
            step
            for step in jobs[name]["steps"]
            if "astral-sh/setup-uv@" in step.get("uses", "")
        )
        assert uv_pin in setup_uv["uses"]
        assert setup_uv["with"]["python-version"] == "3.13"
        assert str(setup_uv["with"].get("enable-cache", "")).lower() in ("true", "True")
        assert str(setup_uv["with"].get("activate-environment", "")).lower() in ("true", "True")
        cache_paths = str(setup_uv["with"].get("cache-dependency-glob", ""))
        assert "requirements-dev.txt" in cache_paths
        commands = _job_commands(jobs[name])
        assert "uv pip install" in commands
        assert "uv pip install --system" not in commands
        assert "requirements-dev.txt" in commands
        assert "pip install --upgrade pip" not in commands
        assert "pip install pytest pytest-asyncio pytest-cov" not in commands


def test_vitest_uses_all_ci_workers() -> None:
    config = (WORKFLOW.parents[2] / "vitest.config.ts").read_text(encoding="utf-8")
    assert re.search(r'maxWorkers:\s*["\']100%["\']', config)
    assert re.search(r"fileParallelism:\s*true", config)


def test_vitest_shards_then_merges_coverage_ratchet() -> None:
    jobs = _workflow()["jobs"]
    web = jobs["web-tests"]
    shards = web["strategy"]["matrix"]["shard"]
    assert [str(item) for item in shards] == ["1", "2", "3", "4", "5", "6", "7", "8"]
    assert "matrix.shard" in web["concurrency"]["group"]
    commands = _job_commands(web)
    assert "--shard=${{ matrix.shard }}/8" in commands
    assert "coverage.thresholds.lines=0" in commands
    assert "coverage.thresholds.functions=0" in commands
    assert "coverage.thresholds.branches=0" in commands
    coverage = jobs["web-coverage"]
    assert "web-tests" in coverage["needs"]
    assert "merge_vitest_coverage" in _job_commands(coverage)
    web_checkout = next(
        step for step in coverage["steps"] if "actions/checkout" in step.get("uses", "")
    )
    sparse = str(web_checkout.get("with", {}).get("sparse-checkout", ""))
    assert "merge_vitest_coverage.py" in sparse
    assert "vitest.config.ts" in sparse


def test_vitest_coverage_uses_text_reporter_only() -> None:
    config = (WORKFLOW.parents[2] / "vitest.config.ts").read_text(encoding="utf-8")
    assert re.search(r'reporter:\s*\[\s*["\']text["\']\s*\]', config), (
        "html/clover/json coverage reports inflate the 307s vitest gate; "
        "the ratchet is thresholds, not artifacts"
    )
    assert "--coverage" in _job_commands(_workflow()["jobs"]["web-tests"])


def test_unit_pytest_uses_xdist_loadfile() -> None:
    commands = _job_commands(_workflow()["jobs"]["py-tests"])
    assert "-n auto" in commands
    assert "--dist loadfile" in commands
    assert "--cov-fail-under=0" in commands
    assert "--cov-branch" not in commands
    assert "term-missing" not in commands


def test_pytest_shards_then_combines_coverage_ratchet() -> None:
    jobs = _workflow()["jobs"]
    py_tests = jobs["py-tests"]
    shards = [str(item) for item in py_tests["strategy"]["matrix"]["shard"]]
    assert shards == [
        "scripts-ac",
        "scripts-df",
        "scripts-i",
        "scripts-gh",
        "scripts-jm",
        "scripts-npsz",
        "scripts-rs",
        "scripts-daemons",
        "rest-api",
        "rest",
    ]
    assert "matrix.shard" in py_tests["concurrency"]["group"]
    commands = _job_commands(py_tests)
    assert "matrix.paths" in commands
    include = str(py_tests["strategy"]["matrix"]["include"])
    assert "test_[a-c]" in include
    assert "test_[d-f]" in include
    assert "test_i*" in include
    assert "--cov-fail-under=0" in commands
    checkout = next(
        step
        for step in py_tests["steps"]
        if "actions/checkout" in step.get("uses", "")
    )
    fetch_depth = str(checkout.get("with", {}).get("fetch-depth", ""))
    assert "scripts-df" in fetch_depth
    upload = next(
        step
        for step in py_tests["steps"]
        if "upload-artifact" in step.get("uses", "")
    )
    # CIP-005: the shard ships its junit XML beside the data file so per-module
    # work on Linux is readable from the artifact (`--durations=25` covers under
    # a third of a work-bound shard). py-coverage finds `.coverage` BY NAME, so
    # the sibling XML cannot reach the ratchet.
    upload_paths = str(upload["with"]["path"]).split()
    assert ".coverage" in upload_paths
    assert "pytest-junit.xml" in upload_paths
    assert "--junitxml=pytest-junit.xml" in commands
    # upload-artifact v4+ skips dotfiles unless this is set (PR 88 first green
    # pytest run: 1586 passed, then "No files were found ... path: .coverage").
    assert str(upload["with"].get("include-hidden-files", "")).lower() in ("true", "True")
    coverage = jobs["py-coverage"]
    assert "py-tests" in coverage["needs"]
    cov_commands = _job_commands(coverage)
    assert "find coverage-artifacts" in cov_commands
    assert "name '.coverage'" in cov_commands
    assert "coverage combine" in cov_commands
    assert "fail-under=56" in cov_commands
    cov_checkout = next(
        step for step in coverage["steps"] if "actions/checkout" in step.get("uses", "")
    )
    sparse = str(cov_checkout.get("with", {}).get("sparse-checkout", ""))
    assert "pyproject.toml" in sparse
    assert "scripts" in sparse


def _shard_tokens(job: str) -> list[str]:
    """Every whitespace-separated path token in a matrix job's `paths`."""
    include = _workflow()["jobs"][job]["strategy"]["matrix"]["include"]
    return [
        token.strip('"').rstrip("/")
        for row in include
        for token in str(row["paths"]).split()
    ]


def _shard_rows(job: str) -> list[list[str]]:
    """`paths` tokens grouped per matrix row (one list per shard)."""
    include = _workflow()["jobs"][job]["strategy"]["matrix"]["include"]
    return [
        [token.strip('"').rstrip("/") for token in str(row["paths"]).split()]
        for row in include
    ]


def _partition(
    tests_dir: Path, prefix: str, rows: list[list[str]]
) -> tuple[dict[str, list[str]], list[str], list[str]]:
    """(overlapped files, unsharded files, unsharded subdirs) for one tests tree.

    Directories count as much as files. The `test_[a-c]*.py` shard globs match
    FILES only, so a new subdirectory runs 0 tests while CI stays green — that
    was the T-122 defect (752 tests), and it can recur because the guard used to
    build its universe from a non-recursive `glob("test_*.py")`. A subdirectory
    is only covered when a shard names it verbatim. T-216.

    Overlap is counted per SHARD, not per token: a row may name a module
    explicitly ahead of the glob that also matches it (CIP-001 lead modules,
    de-duplicated by pytest), but two rows matching one module runs it twice.
    """
    tokens = [token for row in rows for token in row]
    owned = [token for token in tokens if token.startswith(prefix)]
    row_globs = [
        [Path(token).name for token in row if token.startswith(prefix)] for row in rows
    ]
    files = [path.name for path in tests_dir.glob("test_*.py")]
    assigned = {
        name: [
            glob
            for globs in row_globs
            for glob in globs[:1]
            if any(fnmatch.fnmatch(name, candidate) for candidate in globs)
        ]
        for name in files
    }
    subdirs = [
        path.name
        for path in tests_dir.iterdir()
        if path.is_dir()
        and (path.name.startswith("test_") or any(path.rglob("test_*.py")))
    ]
    return (
        {name: hits for name, hits in assigned.items() if len(hits) > 1},
        sorted(name for name, hits in assigned.items() if not hits),
        sorted(name for name in subdirs if f"{prefix}{name}" not in owned),
    )


def test_pytest_filename_shards_partition_scripts_tests() -> None:
    tokens = _shard_tokens("py-tests")
    overlap, missing, unsharded_dirs = _partition(
        WORKFLOW.parents[2] / "scripts" / "tests", "scripts/tests/", _shard_rows("py-tests")
    )
    assert overlap == {}
    assert missing == []
    assert unsharded_dirs == [], (
        "subdirectories of scripts/tests/ that no py-tests shard names verbatim "
        f"(a filename glob cannot match a directory): {unsharded_dirs}"
    )
    other_paths = [token for token in tokens if not token.startswith("scripts/tests/")]
    assert "scripts/api/tests" in other_paths
    assert "scripts/trade_blotter" in other_paths
    assert "tests" in other_paths


def test_shard_partition_guard_reds_for_an_unlisted_subdirectory(tmp_path: Path) -> None:
    """The guard must fail for a directory no shard names. T-216.

    Run against a fixture tree rather than the checkout so it keeps proving the
    rule once the real shards are correct.
    """
    tests_dir = tmp_path / "scripts" / "tests"
    (tests_dir / "test_monitor_daemon").mkdir(parents=True)
    (tests_dir / "test_monitor_daemon" / "test_handler.py").write_text("", encoding="utf-8")
    (tests_dir / "test_newdaemon").mkdir()
    (tests_dir / "test_newdaemon" / "test_handler.py").write_text("", encoding="utf-8")
    (tests_dir / "fixtures").mkdir()
    (tests_dir / "test_alpha.py").write_text("", encoding="utf-8")
    rows = [["scripts/tests/test_[a-c]*.py", "scripts/tests/test_monitor_daemon"]]

    overlap, missing, unsharded_dirs = _partition(tests_dir, "scripts/tests/", rows)

    assert overlap == {}
    assert missing == []
    assert unsharded_dirs == ["test_newdaemon"]

    # A lead module named ahead of its own row's glob is not an overlap; the
    # same module reached from a SECOND row is (it would run twice).
    (tests_dir / "test_beta.py").write_text("", encoding="utf-8")
    lead_rows = [["scripts/tests/test_beta.py", "scripts/tests/test_[a-c]*.py", "scripts/tests/test_monitor_daemon"]]
    assert _partition(tests_dir, "scripts/tests/", lead_rows)[0] == {}
    two_rows = [["scripts/tests/test_beta.py"], ["scripts/tests/test_[a-c]*.py", "scripts/tests/test_monitor_daemon"]]
    assert list(_partition(tests_dir, "scripts/tests/", two_rows)[0]) == ["test_beta.py"]


def test_coverage_ratchets_gate_the_deploy() -> None:
    """Both ratchets must be able to fail a release (TEST_AUDIT T-160).

    Was ``test_coverage_ratchets_do_not_serialize_deploy``, which asserted the
    ratchets were ABSENT from ``deploy.needs`` so the deploy would not wait on
    the coverage-combine barrier VM. That traded a real gate for ~1min of
    latency: with the ratchets off ``needs``, a coverage regression published a
    green deploy. Latency and gating are irreconcilable here — you cannot block
    on a job without waiting for it — so the safety arm wins.

    A ratchet counts as gating if EITHER it is in ``deploy.needs`` with the same
    ``success || skipped`` shape the test jobs use, OR its job name is a
    required status check on ``main``. The second arm is read from a checked-in
    declaration, never over the API, so this suite stays offline in CI.
    """
    jobs = _workflow()["jobs"]
    needs = jobs["deploy"]["needs"]
    deploy_if = jobs["deploy"]["if"]
    declared = _declared_required_status_checks()
    for job in ("web-coverage", "py-coverage"):
        on_needs = (
            job in needs
            and f"needs.{job}.result == 'success'" in deploy_if
            and f"needs.{job}.result == 'skipped'" in deploy_if
        )
        required_check = {job, jobs[job]["name"]} & declared
        assert on_needs or required_check, (
            f"{job} can neither fail the deploy job nor block a push to main: "
            f"it is not in deploy.needs with a success||skipped clause, and "
            f"neither {job!r} nor {jobs[job]['name']!r} is declared in "
            f"{REQUIRED_STATUS_CHECKS.name}. Its threshold is advisory."
        )
    assert "web-tests" in needs
    assert "py-tests" in needs
    assert "cloud-tests" in needs
    assert "stage-release" in needs
    assert "merge_vitest_coverage" in _job_commands(jobs["web-coverage"])
    assert "fail-under=56" in _job_commands(jobs["py-coverage"])


def test_path_filter_skips_the_other_gate() -> None:
    jobs = _workflow()["jobs"]
    assert jobs["changes"]["outputs"]["python"]
    assert jobs["changes"]["outputs"]["web"]
    assert jobs["changes"]["outputs"]["contracts"]
    for group in ("cloud", "script", "root"):
        output = f"contract_{group}_tests"
        job = f"contract-{group}-tests"
        assert jobs["changes"]["outputs"][output]
        assert jobs[job]["needs"] == ["changes"]
        assert output in jobs[job]["if"]
    assert "path_filter.py" in _job_commands(jobs["changes"])
    assert jobs["web-tests"]["needs"] == ["changes"]
    assert "web" in jobs["web-tests"]["if"]
    assert jobs["py-tests"]["needs"] == ["changes"]
    assert "python" in jobs["py-tests"]["if"]
    assert jobs["cloud-tests"]["needs"] == ["changes"]
    assert jobs["perimeter-smoke"]["needs"] == ["changes"]


def test_deploy_accepts_skipped_test_jobs() -> None:
    deploy_if = _workflow()["jobs"]["deploy"]["if"]
    # web-coverage / py-coverage are `if: needs.<gate>-tests.result == 'success'`,
    # so a path-filtered push skips them too — they need the same clause.
    for job in (
        "contract-cloud-tests",
        "contract-script-tests",
        "contract-root-tests",
        "web-tests",
        "py-tests",
        "cloud-tests",
        "perimeter-smoke",
        "web-coverage",
        "py-coverage",
    ):
        assert f"needs.{job}.result == 'skipped'" in deploy_if
    assert "needs.changes.result == 'success'" in deploy_if


def test_cloud_infra_shards_partition_cloud_tests() -> None:
    root = WORKFLOW.parents[2]
    assignments: dict[str, list[str]] = {}
    matrix = _workflow()["jobs"]["cloud-tests"]["strategy"]["matrix"]
    for row in matrix["include"]:
        for path in _expand_shard_row(root, row):
            assignments.setdefault(path, []).append(str(row["shard"]))
    on_disk = _test_modules_under(root, root / "cloud" / "tests")
    missing = sorted(on_disk - assignments.keys())
    overlap = {path: shards for path, shards in assignments.items() if len(shards) > 1}
    assert not missing, f"unsharded cloud tests: {missing[:6]}"
    assert not overlap, f"cloud tests assigned to multiple shards: {overlap}"


def test_py_coverage_installs_coverage_only() -> None:
    job = _workflow()["jobs"]["py-coverage"]
    commands = _job_commands(job)
    assert "pytest-cov==7.1.0" in commands
    assert "pip install -r requirements-dev.txt" not in commands
    setup_uv = next(
        step for step in job["steps"] if "astral-sh/setup-uv@" in step.get("uses", "")
    )
    assert setup_uv["with"]["python-version"] == "3.13"
    assert str(setup_uv["with"].get("activate-environment", "")).lower() in ("true", "True")
    assert "python -m coverage combine" in commands


def test_bun_jobs_cache_the_install_store() -> None:
    jobs = _workflow()["jobs"]
    pin = "5a3ec84eff668545956fd18022155c47e93e2684"
    for name in ("web-tests", "perimeter-smoke"):
        cache = next(
            step
            for step in jobs[name]["steps"]
            if "actions/cache@" in step.get("uses", "")
        )
        assert pin in cache["uses"]
        assert "~/.bun/install/cache" in str(cache["with"]["path"])
        key = str(cache["with"]["key"])
        assert "bun.lock" in key
        assert "web/bun.lock" in key


def test_ci_uses_frozen_bun_lockfile_contract_for_both_workspaces() -> None:
    workflow = _workflow()
    for job_name in ("web-tests", "perimeter-smoke"):
        setup_bun = next(
            step for step in workflow["jobs"][job_name]["steps"]
            if "oven-sh/setup-bun" in step.get("uses", "")
        )
        assert setup_bun["with"]["bun-version"] == "1.3.14"
        steps = workflow["jobs"][job_name]["steps"]
        commands = "\n".join(str(step.get("run", "")) for step in steps)
        assert commands.count("bun install --frozen-lockfile") == 2
        assert "&" in commands
        assert "wait" in commands


# TEST_AUDIT T-122: the sharded matrix must reach every test module the
# pre-shard recursive invocation collected. A `test_[a-c]*.py` glob cannot
# match a directory, so scripts/tests/test_monitor_daemon/ and
# scripts/tests/test_watchdog/ (752 tests over fills, exit orders, journal
# sync and the whole watchdog) silently left CI on 424e66da while the
# workflow stayed green. Set-equality, not shard names.
PY_COLLECTION_ROOTS = ("scripts/tests", "scripts/api/tests", "scripts/trade_blotter", "tests")


def _test_modules_under(root: Path, base: Path) -> set[str]:
    return {str(f.relative_to(root)) for f in base.rglob("test_*.py")}


def _expand_shard_paths(root: Path, paths: str) -> set[str]:
    out: set[str] = set()
    ignored: set[str] = set()
    for token in paths.split():
        if token.startswith("--ignore="):
            ignored_path = root / token.split("=", 1)[1]
            if ignored_path.is_dir():
                ignored |= _test_modules_under(root, ignored_path)
            elif ignored_path.is_file():
                ignored.add(str(ignored_path.relative_to(root)))
            continue
        target = root / token
        if target.is_dir():
            out |= _test_modules_under(root, target)
        else:
            out |= {str(f.relative_to(root)) for f in root.glob(token) if f.is_file()}
    return out - ignored


def _expand_shard_row(root: Path, row: dict) -> set[str]:
    selected = _expand_shard_paths(root, str(row["paths"]))
    omitted = str(row.get("omit", ""))
    return selected - (_expand_shard_paths(root, omitted) if omitted else set())


def test_pytest_shard_union_equals_recursive_collection() -> None:
    root = WORKFLOW.parents[2]
    py_tests = _workflow()["jobs"]["py-tests"]
    sharded: set[str] = set()
    for item in py_tests["strategy"]["matrix"]["include"]:
        sharded |= _expand_shard_paths(root, str(item["paths"]))
    on_disk: set[str] = set()
    for base in PY_COLLECTION_ROOTS:
        on_disk |= _test_modules_under(root, root / base)
    assert on_disk, "collection roots moved; update PY_COLLECTION_ROOTS"
    missing = sorted(on_disk - sharded)
    assert not missing, (
        f"{len(missing)} test modules are collected by NO pytest shard "
        f"(CI reads green without running them): {missing[:6]}"
    )
    stray = sorted(sharded - on_disk)
    assert not stray, f"shard globs reach outside the collection roots: {stray[:6]}"


def test_cloud_shard_union_equals_recursive_collection() -> None:
    """The cloud-tests matrix must reach every module under cloud/tests/.

    TEST_AUDIT T-173. ``test_cloud_infra_shards_partition_cloud_tests`` builds
    its universe from a NON-recursive ``glob("test_*.py")``, so it can only
    reason about a subdirectory through the separate name heuristic in
    ``_partition``; it never checks that the shard union equals what pytest
    actually collects. That is the T-122 shape (752 tests left CI while the
    workflow stayed green), and cloud/tests/ is one ``mkdir`` away from it.

    Swapping ``_partition`` itself to ``rglob`` is NOT the fix: it is shared
    with the py-tests guard, whose shards name ``scripts/tests/test_watchdog``
    and ``scripts/tests/test_monitor_daemon`` as DIRECTORY tokens, and a
    recursive file listing there reports every already-sharded module inside
    them as unsharded. Set-equality against recursive collection is the check
    that generalises, so cloud gets the same one py-tests has.
    """
    root = WORKFLOW.parents[2]
    cloud_tests = _workflow()["jobs"]["cloud-tests"]
    sharded: set[str] = set()
    for item in cloud_tests["strategy"]["matrix"]["include"]:
        sharded |= _expand_shard_row(root, item)
    on_disk = _test_modules_under(root, root / "cloud" / "tests")
    assert on_disk, "cloud/tests moved; this guard is measuring nothing"
    missing = sorted(on_disk - sharded)
    assert not missing, (
        f"{len(missing)} cloud test modules are collected by NO cloud-tests "
        f"shard (CI reads green without running them): {missing[:6]}"
    )
    stray = sorted(sharded - on_disk)
    assert not stray, f"cloud shard globs reach outside cloud/tests: {stray[:6]}"


def test_pytest_coverage_ratchet_measures_branches() -> None:
    """TEST_AUDIT T-123: the 56 ratchet was rebased (T-050) on combined
    statement+branch coverage. The shard rewrite dropped ``--cov-branch`` and
    nothing re-enabled branch measurement, so the combined report scored
    statement-only (~2pt easier) under the same threshold. Branch must be on
    either via the invocation or ``[tool.coverage.run] branch = true``."""
    import tomllib

    pyproject = tomllib.loads((WORKFLOW.parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
    run_cfg = pyproject.get("tool", {}).get("coverage", {}).get("run", {})
    commands = _job_commands(_workflow()["jobs"]["py-tests"])
    assert run_cfg.get("branch") is True or "--cov-branch" in commands, (
        "pytest coverage ratchet is scoring statement-only; the 56 threshold "
        "was set against statement+branch (T-050)"
    )


# CIP-001 (CI_PERFORMANCE_LOG.md): the slowest pytest shard sets the python
# deploy gate. xdist ``--dist loadfile`` hands modules to workers in COLLECTION
# order, so a 20-45s module collected near the end of its shard runs alone at
# the tail while the other workers idle (run 33359053839: test_vixcor.py and
# test_weekend_wrapper_self_rewrite.py were modules #59 and #71 of 78 on
# scripts-npsz). Every module below is a measured >= 10s wall floor: real
# subprocess timeout / SIGKILL drills and wall-clock wrapper tests that cannot
# be made faster without weakening what they prove. Naming it FIRST in its
# shard's ``paths`` starts it at t=0. pytest de-duplicates a module named both
# explicitly and by the letter glob (``--keep-duplicates`` is off by default),
# so shard membership stays glob-derived and the union contract holds.
PYTEST_SHARD_LEAD_MODULES = {
    "scripts-jm": ["scripts/tests/test_leap_garch_no_duplicate_scan.py"],
    "scripts-npsz": [
        "scripts/tests/test_vixcor.py",
        "scripts/tests/test_weekend_wrapper_self_rewrite.py",
    ],
    "scripts-rs": [
        "scripts/tests/test_rel137_weekend_wrapper_survivability.py",
        "scripts/tests/test_run_flow_refresh_wrapper.py",
        "scripts/tests/test_run_portfolio_refresh_retry.py",
        "scripts/tests/test_run_signals_refresh_wrapper.py",
    ],
}


def test_heavy_pytest_modules_lead_their_shard() -> None:
    root = WORKFLOW.parents[2]
    rows = {
        str(row["shard"]): str(row["paths"]).split()
        for row in _workflow()["jobs"]["py-tests"]["strategy"]["matrix"]["include"]
    }
    for shard, leads in PYTEST_SHARD_LEAD_MODULES.items():
        tokens = rows[shard]
        assert tokens[: len(leads)] == leads, (
            f"{shard}: the wall-floor modules must be the first collection "
            f"targets so loadfile schedules them at t=0; got {tokens[:len(leads)]}"
        )
        globs = tokens[len(leads):]
        assert globs, f"{shard}: lead modules must be followed by the shard's letter globs"
        for lead in leads:
            # A renamed module makes pytest error on the explicit path (fail
            # closed) rather than silently dropping it; update the list.
            assert (root / lead).is_file(), f"{shard}: lead module {lead} is gone"
            assert any(fnmatch.fnmatch(lead, glob) for glob in globs), (
                f"{shard}: {lead} is outside this shard's globs; leads only "
                "reorder a shard, they never move a module between shards"
            )


def test_cloud_shards_parallelise_except_the_wall_clock_edge_shard() -> None:
    """CIP-001: ``pytest (cloud al)`` ran 765 tests serially in 91s (three
    control-plane modules are 15-18s each of real subprocess drills) while
    pytest-xdist was already installed from requirements-dev.txt. ``edge`` is
    the Caddy wall-clock mechanism shard (real R-219 / R-220 timeouts, given
    its own job for exactly that reason) and stays serial; ``loadfile`` keeps
    test_caddyfile.py's restart-window mechanism, the only other caddy spawner
    (``admin off``, ephemeral ports), on one worker."""
    cloud = _workflow()["jobs"]["cloud-tests"]
    rows = {str(row["shard"]): row for row in cloud["strategy"]["matrix"]["include"]}
    assert rows["al"]["xdist"] == "-n auto --dist loadfile"
    assert rows["mz"]["xdist"] == "-n auto --dist loadfile"
    assert rows["edge"]["xdist"] == "", "the edge shard is wall-clock; keep it serial"
    assert "matrix.xdist" in _job_commands(cloud)
