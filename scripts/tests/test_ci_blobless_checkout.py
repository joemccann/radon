"""Blobless (`filter: blob:none`) checkouts in ci.yml.

A `fetch-depth: 0` checkout took 9s against 3s at depth 1, and `Path filter`
(the `changes` job) sits in front of every gating job. Its history consumers
read commits and trees, never old file contents: path_filter.py runs
`git merge-base --is-ancestor` and `git diff --no-renames --name-only`, and the
docs contract on scripts-df diffs names and reads commit messages. A blobless
clone keeps every commit and tree and downloads only the blobs the checkout
writes, so the history those scripts walk is still complete. Local timing
(2026-11-01, 3 rounds, actions/checkout's fetch and checkout): full history
11.4-12.3s, blobless 9.0-11.2s.

Where the filter stays off, and why:

* depth-1 py-tests shards: with no history to skip, the filter only splits one
  fetch into a tree fetch plus a lazy batch of every checked-out blob. Local
  timing, 3 rounds: 5.3-6.1s unfiltered, 9.0-9.5s filtered.
* secret-scan: gitleaks reads every blob in its `--log-opts` range. A blobless
  clone would lazily fetch them one object at a time, slower than the packed
  full fetch it replaces.
* release-tag: `release_version.py` folds the version with `git show -s` per
  commit, and `git show` still runs rename detection under `-s`. In a blobless
  clone of this repo the fold over 782 commits lazily fetched 49 blobs and took
  20s against 9s in a full clone (2026-11-01), and a failed lazy fetch raises
  CalledProcessError, which `build_identity` turns into the package.json
  fallback label without an error. The job runs after deploy, so nothing waits
  on its checkout.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from ci import path_filter

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

FULL_BLOB_JOBS = ("secret-scan", "release-tag")
# actions/checkout treats an empty `filter` input as no filter.
PY_TESTS_FILTER = "${{ matrix.shard == 'scripts-df' && 'blob:none' || '' }}"
PY_TESTS_DEPTH = "${{ matrix.shard == 'scripts-df' && '0' || '1' }}"


def _jobs() -> dict:
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)["jobs"]


def _checkout(job: dict) -> dict:
    (step,) = [s for s in job["steps"] if "actions/checkout@" in s.get("uses", "")]
    return step.get("with", {})


def test_path_filter_clones_full_history_without_blobs() -> None:
    checkout = _checkout(_jobs()["changes"])
    assert checkout.get("filter") == "blob:none", (
        "changes: a full-history checkout downloads every blob ever committed; "
        "path_filter.py only needs commits and trees"
    )
    assert checkout.get("fetch-depth") == "0"
    # path_filter.route_documentation reads every test source under
    # TEST_TREES from the working tree. A sparse checkout would route a doc
    # change to no test at all, so the whole tree stays checked out.
    assert "sparse-checkout" not in checkout


def test_only_the_full_history_py_tests_shard_is_blobless() -> None:
    checkout = _checkout(_jobs()["py-tests"])
    assert checkout.get("fetch-depth") == PY_TESTS_DEPTH
    assert checkout.get("filter") == PY_TESTS_FILTER, (
        "the filter belongs on the one depth-0 shard (scripts-df); on a depth-1 "
        "shard it only splits the fetch in two (see the module docstring)"
    )
    assert "sparse-checkout" not in checkout


@pytest.mark.parametrize("name", FULL_BLOB_JOBS)
def test_blob_reading_jobs_keep_every_blob(name: str) -> None:
    checkout = _checkout(_jobs()[name])
    assert "filter" not in checkout, (
        f"{name} reads historical blob contents; a blobless clone turns that "
        "into one lazy fetch per object (see the module docstring)"
    )
    assert checkout.get("fetch-depth") == "0", name


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=ci", "-c", "user.email=ci@example.invalid", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_path_filter_diff_needs_no_blob_in_a_blobless_clone(tmp_path: Path, monkeypatch) -> None:
    """changed_paths() answers from trees alone; GIT_NO_LAZY_FETCH proves it."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "uploadpack.allowFilter", "true")
    (origin / "scripts").mkdir()
    (origin / "scripts" / "old.py").write_text("x = 1\n" * 50, encoding="utf-8")
    (origin / "README.md").write_text("readme\n", encoding="utf-8")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "base")
    base = _git(origin, "rev-parse", "HEAD").strip()
    # A move (rename detection would need both blobs), an edit and an add.
    (origin / "web").mkdir()
    _git(origin, "mv", "scripts/old.py", "web/moved.py")
    (origin / "README.md").write_text("readme v2\n", encoding="utf-8")
    (origin / "docs").mkdir()
    (origin / "docs" / "new.md").write_text("new\n", encoding="utf-8")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "head")
    head = _git(origin, "rev-parse", "HEAD").strip()

    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", "--no-checkout", "--filter=blob:none", origin.as_uri(), str(clone))
    monkeypatch.setenv("GIT_NO_LAZY_FETCH", "1")

    # Teeth: the clone really lacks the blobs, so a content diff cannot run.
    content = subprocess.run(
        ["git", "diff", f"{base}...{head}"], cwd=clone, capture_output=True, text=True
    )
    assert content.returncode != 0, "fixture clone is not blobless; the test proves nothing"

    assert sorted(path_filter.changed_paths(base, head, cwd=clone)) == [
        "README.md",
        "docs/new.md",
        "scripts/old.py",
        "web/moved.py",
    ]
    assert path_filter._is_ancestor(base, head, cwd=clone)
