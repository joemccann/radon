"""Merge sharded Vitest coverage and keep the vitest.config.ts ratchet."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ci.merge_vitest_coverage import (
    evaluate,
    find_coverage_json,
    load_thresholds,
    main,
    merge_coverage_maps,
    summarize,
)

REPO = Path(__file__).resolve().parents[2]


def _file_cov(*, statements: dict, functions: dict, branches: dict) -> dict:
    statement_map = {
        sid: {"start": {"line": int(sid) + 1, "column": 0}, "end": {"line": int(sid) + 1, "column": 1}}
        for sid in statements
    }
    return {
        "path": "web/lib/example.ts",
        "statementMap": statement_map,
        "s": statements,
        "f": functions,
        "b": branches,
    }


def test_load_thresholds_matches_vitest_config() -> None:
    text = (REPO / "vitest.config.ts").read_text(encoding="utf-8")
    assert load_thresholds(text) == {"lines": 75, "functions": 71, "branches": 65}


def test_complementary_shards_meet_the_ratchet() -> None:
    shard_a = {
        "web/lib/example.ts": _file_cov(
            statements={"0": 1, "1": 0},
            functions={"0": 1, "1": 0},
            branches={"0": [1, 0]},
        )
    }
    shard_b = {
        "web/lib/example.ts": _file_cov(
            statements={"0": 0, "1": 1},
            functions={"0": 0, "1": 1},
            branches={"0": [0, 1]},
        )
    }
    merged = merge_coverage_maps([shard_a, shard_b])
    summary = summarize(merged)
    assert summary["lines"] == 100.0
    assert summary["functions"] == 100.0
    assert summary["branches"] == 100.0
    _, failures = evaluate([shard_a, shard_b], {"lines": 75, "functions": 71, "branches": 65})
    assert failures == []


def test_a_single_shard_fails_the_ratchet() -> None:
    shard = {
        "web/lib/example.ts": _file_cov(
            statements={"0": 1, "1": 0, "2": 0, "3": 0},
            functions={"0": 1, "1": 0, "2": 0, "3": 0},
            branches={"0": [1, 0, 0, 0]},
        )
    }
    _, failures = evaluate([shard], {"lines": 75, "functions": 71, "branches": 65})
    assert failures
    assert any(item.startswith("lines ") for item in failures)


def test_main_merges_shard_json_and_exits_nonzero_when_short(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "vitest.config.ts"
    config.write_text(
        "export default { test: { coverage: { thresholds: { lines: 75, functions: 71, branches: 65 } } } }\n",
        encoding="utf-8",
    )
    root = tmp_path / "artifacts"
    shard = root / "vitest-coverage-1"
    shard.mkdir(parents=True)
    (shard / "coverage-final.json").write_text(
        json.dumps(
            {
                "web/lib/example.ts": _file_cov(
                    statements={"0": 1, "1": 0, "2": 0, "3": 0},
                    functions={"0": 1, "1": 0},
                    branches={"0": [1, 0]},
                )
            }
        ),
        encoding="utf-8",
    )
    assert find_coverage_json(root) == [shard / "coverage-final.json"]
    assert main(["--root", str(root), "--config", str(config)]) == 1
