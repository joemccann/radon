"""The coverage ratchet must fail on an empty or partial map, not report 100%.

R-241: `_pct` returns `100.0` whenever `total <= 0`, and `summarize` derives
every total purely by iterating the merged map. A single `coverage-final.json`
containing `{}` — or files with no `b` branch maps — produces
`{"lines": 100.0, "functions": 100.0, "branches": 100.0}`, `evaluate` finds no
failures, and `main` prints `lines: 100.00% (gate 75%)` and returns 0. The only
emptiness guard checks that at least one FILE exists, never that it contains
statements. And there is no assertion that all eight shard artifacts arrived:
ci.yml shards `--shard=N/8` but the merge globs whatever landed, so a partial
download is silently accepted. The module's own docstring calls itself "the
only coverage ratchet in CI", and the branch dimension in particular can go
permanently green on any reporter-shape change.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from ci import merge_vitest_coverage as merger  # noqa: E402


def _covered_file(statements: int = 4, branches: int = 2) -> dict:
    return {
        "statementMap": {
            str(i): {"start": {"line": i + 1}} for i in range(statements)
        },
        "s": {str(i): 1 for i in range(statements)},
        "f": {"0": 1},
        "b": {str(i): [1, 1] for i in range(branches)},
    }


def _write(root: Path, name: str, payload: dict) -> Path:
    path = root / name / "coverage-final.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestEmptyMapsFail:
    def test_an_empty_object_is_not_a_pass(self, tmp_path, capsys):
        _write(tmp_path, "vitest-coverage-1", {})
        rc = merger.main(["--root", str(tmp_path)])
        out = capsys.readouterr()
        assert rc != 0, (
            f"an empty coverage map reported a pass:\n{out.out}{out.err}"
        )

    def test_a_map_with_no_branch_entries_fails_the_branch_dimension(self, tmp_path, capsys):
        payload = {"a.ts": _covered_file(branches=0)}
        for shard in range(1, 9):
            _write(tmp_path, f"vitest-coverage-{shard}", payload)
        rc = merger.main(["--root", str(tmp_path)])
        out = capsys.readouterr()
        assert rc != 0, (
            "a map with no `b` entries reported branches at 100%, which is how "
            f"a reporter-shape change goes permanently green:\n{out.out}{out.err}"
        )

    def test_a_real_map_still_passes(self, tmp_path):
        payload = {"a.ts": _covered_file()}
        for shard in range(1, 9):
            _write(tmp_path, f"vitest-coverage-{shard}", payload)
        assert merger.main(["--root", str(tmp_path)]) == 0


class TestPartialShardDownloadFails:
    def test_six_of_eight_shards_is_refused(self, tmp_path, capsys):
        payload = {"a.ts": _covered_file()}
        for shard in range(1, 7):
            _write(tmp_path, f"vitest-coverage-{shard}", payload)
        rc = merger.main(["--root", str(tmp_path), "--expect-shards", "8"])
        out = capsys.readouterr()
        assert rc != 0, (
            f"a partial artifact download merged silently:\n{out.out}{out.err}"
        )
        assert "6" in (out.out + out.err)

    def test_all_eight_shards_is_accepted(self, tmp_path):
        payload = {"a.ts": _covered_file()}
        for shard in range(1, 9):
            _write(tmp_path, f"vitest-coverage-{shard}", payload)
        assert merger.main(["--root", str(tmp_path), "--expect-shards", "8"]) == 0

    def test_the_workflow_passes_its_shard_count(self):
        workflow = (
            Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
        ).read_text(encoding="utf-8")
        assert "--expect-shards" in workflow, (
            "ci.yml shards N/8 but the merge accepts whatever landed"
        )


class TestPctIsHonest:
    def test_zero_total_is_not_a_hundred_percent(self):
        assert merger._pct(0, 0) is None, (
            "an absent dimension reported as fully covered"
        )

    def test_a_real_ratio_is_unchanged(self):
        assert merger._pct(3, 4) == pytest.approx(75.0)
