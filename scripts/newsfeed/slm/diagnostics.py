#!/usr/bin/env python3.13
"""Strict ID-aligned per-label diagnostics for a pinned tagger evaluation."""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parents[2]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from newsfeed.slm.contract import classify_slm_tags, normalise_tags  # noqa: E402
from newsfeed.slm.eval import gold_tags, load_jsonl, pred_payload  # noqa: E402


def index_by_id(rows: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = row.get("id") or row.get("post_id")
        if value is None:
            raise ValueError(f"{label} has a row without an ID")
        key = str(value)
        if key in indexed:
            raise ValueError(f"{label} has duplicate ID {key}")
        indexed[key] = row
    return indexed


def diagnose(gold_rows: list[dict], prediction_rows: list[dict], taxonomy: list[str]) -> dict[str, Any]:
    gold = index_by_id(gold_rows, "gold")
    predictions = index_by_id(prediction_rows, "predictions")
    if gold.keys() != predictions.keys():
        missing = sorted(gold.keys() - predictions.keys())
        unexpected = sorted(predictions.keys() - gold.keys())
        raise ValueError(f"ID sets differ: missing={len(missing)} unexpected={len(unexpected)}")

    counts: dict[str, Counter[str]] = {tag: Counter() for tag in taxonomy}
    examples: list[dict[str, Any]] = []
    invalid = Counter()
    for post_id, gold_row in gold.items():
        gold_set = set(gold_tags(gold_row))
        output = predictions[post_id]
        payload = pred_payload(output)
        code, tags = classify_slm_tags(payload if isinstance(payload, dict) else {"tags": payload}, taxonomy)
        status = str(output.get("slm_status") or "")
        if status in {"unparseable", "unavailable"} or status.startswith("abstain:"):
            code = status
            tags = None
        predicted = set(normalise_tags(tags or [])) if code == "ok" else set()
        if code != "ok":
            invalid[code] += 1
        for tag in taxonomy:
            if tag in gold_set and tag in predicted:
                counts[tag]["tp"] += 1
            elif tag in predicted:
                counts[tag]["fp"] += 1
            elif tag in gold_set:
                counts[tag]["fn"] += 1
        examples.append({
            "id": post_id,
            "gold": sorted(gold_set),
            "predicted": sorted(predicted),
            "status": code,
            "valid": code == "ok",
            "latency_s": output.get("latency_s"),
        })

    labels = {}
    for tag, tally in counts.items():
        tp, fp, fn = tally["tp"], tally["fp"], tally["fn"]
        labels[tag] = {
            "support": tp + fn,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "recall": tp / (tp + fn) if tp + fn else None,
        }
    return {
        "n": len(gold),
        "invalid_rate": sum(invalid.values()) / max(1, len(gold)),
        "invalid_breakdown": dict(sorted(invalid.items())),
        "per_label": labels,
        "per_example": examples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", required=True)
    parser.add_argument("--pred", required=True)
    parser.add_argument("--taxonomy", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    gold = load_jsonl(Path(args.gold))
    pred = load_jsonl(Path(args.pred))
    taxonomy = json.loads(Path(args.taxonomy).read_text(encoding="utf-8"))
    if not isinstance(taxonomy, list) or not all(isinstance(tag, str) for tag in taxonomy):
        raise SystemExit("taxonomy must be a JSON string array")
    result = diagnose(gold, pred, taxonomy)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if stat.S_IMODE(target.parent.stat().st_mode) & 0o077:
        raise PermissionError(f"Private diagnostics directory must be mode 0700: {target.parent}")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({"n": result["n"], "invalid_rate": result["invalid_rate"], "per_label_count": len(result["per_label"]), "out": str(target)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
