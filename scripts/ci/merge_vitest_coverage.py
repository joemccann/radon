#!/usr/bin/env python3
"""Merge sharded Vitest v8/istanbul JSON and enforce vitest.config.ts thresholds.

Shard jobs cannot apply the ratchet: each shard only executes a subset of
tests, so per-shard line/function/branch percentages would fail the gate.
This combiner is the only coverage ratchet in CI.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VITEST_CONFIG = REPO / "vitest.config.ts"
THRESHOLD_KEYS = ("lines", "functions", "branches")


def load_thresholds(config_text: str) -> dict[str, int]:
    block = re.search(r"thresholds:\s*\{([^}]+)\}", config_text)
    if not block:
        raise SystemExit("vitest.config.ts is missing a thresholds block")
    body = block.group(1)
    out: dict[str, int] = {}
    for key in THRESHOLD_KEYS:
        match = re.search(rf"{key}:\s*(\d+)", body)
        if not match:
            raise SystemExit(f"vitest.config.ts thresholds missing {key}")
        out[key] = int(match.group(1))
    return out


def _add_counts(left: dict, right: dict) -> dict:
    keys = set(left) | set(right)
    merged = {}
    for key in keys:
        a = left.get(key, 0)
        b = right.get(key, 0)
        if isinstance(a, list) or isinstance(b, list):
            a = a if isinstance(a, list) else []
            b = b if isinstance(b, list) else []
            n = max(len(a), len(b))
            merged[key] = [
                (a[i] if i < len(a) else 0) + (b[i] if i < len(b) else 0) for i in range(n)
            ]
        else:
            merged[key] = int(a or 0) + int(b or 0)
    return merged


def merge_coverage_maps(maps: list[dict]) -> dict:
    merged: dict = {}
    for payload in maps:
        for path, file_cov in payload.items():
            if path not in merged:
                merged[path] = {
                    "path": file_cov.get("path", path),
                    "statementMap": dict(file_cov.get("statementMap") or {}),
                    "s": dict(file_cov.get("s") or {}),
                    "f": dict(file_cov.get("f") or {}),
                    "b": {k: list(v) for k, v in (file_cov.get("b") or {}).items()},
                }
                continue
            existing = merged[path]
            existing["statementMap"].update(file_cov.get("statementMap") or {})
            existing["s"] = _add_counts(existing["s"], file_cov.get("s") or {})
            existing["f"] = _add_counts(existing["f"], file_cov.get("f") or {})
            existing["b"] = _add_counts(existing["b"], file_cov.get("b") or {})
    return merged


def _pct(covered: int, total: int) -> float | None:
    """None when the dimension is ABSENT — not 100%.

    Returning 100 for `total == 0` meant a single `coverage-final.json` of
    `{}`, or files with no `b` branch maps, printed
    `lines: 100.00% (gate 75%)` and exited 0. This module's own docstring
    calls it "the only coverage ratchet in CI", so the branch dimension could
    go permanently green on a reporter-shape change with no code change at
    all. R-241.
    """
    if total <= 0:
        return None
    return 100.0 * covered / total


def summarize(merged: dict) -> dict[str, float | None]:
    line_total = line_hit = 0
    fn_total = fn_hit = 0
    br_total = br_hit = 0
    for file_cov in merged.values():
        statement_map = file_cov.get("statementMap") or {}
        hits = file_cov.get("s") or {}
        lines: dict[int, int] = {}
        for sid, stmt in statement_map.items():
            try:
                line = int(stmt["start"]["line"])
            except (KeyError, TypeError, ValueError):
                continue
            lines[line] = lines.get(line, 0) + int(hits.get(str(sid), hits.get(sid, 0)) or 0)
        line_total += len(lines)
        line_hit += sum(1 for n in lines.values() if n > 0)
        for count in (file_cov.get("f") or {}).values():
            fn_total += 1
            if int(count or 0) > 0:
                fn_hit += 1
        for counts in (file_cov.get("b") or {}).values():
            for count in counts:
                br_total += 1
                if int(count or 0) > 0:
                    br_hit += 1
    return {
        "lines": _pct(line_hit, line_total),
        "functions": _pct(fn_hit, fn_total),
        "branches": _pct(br_hit, br_total),
    }


def find_coverage_json(root: Path) -> list[Path]:
    return sorted(root.rglob("coverage-final.json"))


def evaluate(
    maps: list[dict], thresholds: dict[str, int]
) -> tuple[dict[str, float | None], list[str]]:
    summary = summarize(merge_coverage_maps(maps))
    failures = []
    for key in THRESHOLD_KEYS:
        value = summary[key]
        if value is None:
            failures.append(
                f"{key} has no measurable total — the merged map carries no "
                f"{key} data, which is a broken report, not full coverage"
            )
        elif value + 1e-9 < thresholds[key]:
            failures.append(f"{key} {value:.2f}% < {thresholds[key]}%")
    return summary, failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=VITEST_CONFIG)
    # ci.yml shards `--shard=N/8`, but the merge job downloads with a glob and
    # this module globbed whatever landed — so a partial artifact download was
    # merged and gated as if it were the whole suite. R-241.
    parser.add_argument("--expect-shards", type=int, default=0)
    args = parser.parse_args(argv)
    files = find_coverage_json(args.root)
    if not files:
        print(f"no coverage-final.json under {args.root}", file=sys.stderr)
        return 1
    if args.expect_shards and len(files) != args.expect_shards:
        print(
            f"expected {args.expect_shards} shard coverage artifacts, found "
            f"{len(files)} — refusing to gate on a partial merge",
            file=sys.stderr,
        )
        return 1
    maps = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    thresholds = load_thresholds(args.config.read_text(encoding="utf-8"))
    summary, failures = evaluate(maps, thresholds)
    for key in THRESHOLD_KEYS:
        value = summary[key]
        shown = "n/a" if value is None else f"{value:.2f}%"
        print(f"{key}: {shown} (gate {thresholds[key]}%)")
    if failures:
        print("coverage ratchet failed: " + "; ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
