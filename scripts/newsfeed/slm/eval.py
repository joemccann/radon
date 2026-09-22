#!/usr/bin/env python3.13
"""SLM tagger metrics (HR-6). Invalid outputs count as zero predictions."""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

_SCRIPTS = Path(__file__).resolve().parents[2]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from newsfeed.slm.contract import classify_slm_tags, normalise_tags  # noqa: E402


def _as_tags(value: Any) -> list[str] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if isinstance(value, dict):
        value = value.get("tags")
    if not isinstance(value, list):
        return None
    return [str(x) for x in value]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def gold_tags(row: dict[str, Any]) -> list[str]:
    if "gold" in row:
        tags = _as_tags(row["gold"])
        if tags is not None:
            return normalise_tags(tags)
    messages = row.get("messages") or []
    for msg in messages:
        if msg.get("role") == "assistant":
            tags = _as_tags(msg.get("content"))
            if tags is not None:
                return normalise_tags(tags)
    tags = _as_tags(row.get("tags_text") or row.get("tags"))
    return normalise_tags(tags or [])


def pred_payload(row: dict[str, Any]) -> Any:
    if "pred" in row:
        return row["pred"]
    if "prediction" in row:
        return row["prediction"]
    if "tags" in row and "gold" in row:
        return {"tags": row["tags"]}
    return row.get("output") or row.get("data")


def f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def evaluate(
    gold_rows: Sequence[Mapping[str, Any]],
    pred_rows: Sequence[Mapping[str, Any]],
    *,
    taxonomy: Sequence[str],
    rare: Sequence[str] | None = None,
) -> dict[str, Any]:
    by_id: dict[str, Mapping[str, Any]] = {}
    for row in pred_rows:
        key = str(row.get("id") or row.get("post_id") or len(by_id))
        by_id[key] = row

    breakdown = {
        "unparseable": 0,
        "count": 0,
        "out_of_taxonomy": 0,
        "abstained": 0,
        "empty": 0,
    }
    tp = fp = fn = 0
    per_tag_tp: dict[str, int] = defaultdict(int)
    per_tag_fp: dict[str, int] = defaultdict(int)
    per_tag_fn: dict[str, int] = defaultdict(int)
    exact3 = 0
    jaccards: list[float] = []
    human_ok = 0
    human_n = 0
    latencies: list[float] = []
    rare_set = set(rare or [])
    rare_tp = rare_fn = 0

    n = 0
    for i, gold_row in enumerate(gold_rows):
        gid = str(gold_row.get("id") or gold_row.get("post_id") or i)
        gold = set(gold_tags(dict(gold_row)))
        pred_row = by_id.get(gid)
        if pred_row is None and i < len(pred_rows):
            pred_row = pred_rows[i]
        n += 1
        payload = pred_payload(dict(pred_row or {}))
        status_hint = str((pred_row or {}).get("slm_status") or "")
        code, tags = classify_slm_tags(payload if isinstance(payload, dict) else {"tags": _as_tags(payload)}, taxonomy)
        if status_hint.startswith("abstain:") or status_hint == "unparseable":
            code = status_hint
            tags = None
        valid = code == "ok" and tags is not None
        if not valid:
            if code == "unparseable":
                breakdown["unparseable"] += 1
            elif code == "abstain:count":
                breakdown["count"] += 1
                breakdown["abstained"] += 1
            elif code == "abstain:out_of_taxonomy":
                breakdown["out_of_taxonomy"] += 1
                breakdown["abstained"] += 1
            elif code == "abstain:empty":
                breakdown["empty"] += 1
                breakdown["abstained"] += 1
            else:
                breakdown["unparseable"] += 1
            pred: set[str] = set()
        else:
            pred = set(tags or [])
        tp += len(gold & pred)
        fp += len(pred - gold)
        fn += len(gold - pred)
        for tag in gold | pred:
            if tag in gold and tag in pred:
                per_tag_tp[tag] += 1
            elif tag in pred:
                per_tag_fp[tag] += 1
            else:
                per_tag_fn[tag] += 1
        if pred == gold and len(pred) == 3:
            exact3 += 1
        union = gold | pred
        jaccards.append(len(gold & pred) / len(union) if union else 0.0)
        if gold_row.get("human") in {"y", "yes", True} or gold_row.get("reviewed") == "y":
            human_n += 1
            corrected = gold_row.get("corrected_tags")
            if gold_row.get("human") in {"y", "yes", True} and pred == gold:
                human_ok += 1
            elif corrected is not None and set(normalise_tags(_as_tags(corrected) or [])) == pred:
                human_ok += 1
        elif "human_accept" in gold_row:
            human_n += 1
            if pred == gold:
                human_ok += 1
        if rare_set:
            rare_gold = gold & rare_set
            rare_tp += len(rare_gold & pred)
            rare_fn += len(rare_gold - pred)
        lat = (pred_row or {}).get("latency_s")
        if lat is None:
            lat = (pred_row or {}).get("latency_ms")
            if lat is not None:
                lat = float(lat) / 1000.0
        if lat is not None:
            latencies.append(float(lat))

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    tag_f1s: list[float] = []
    for tag in sorted(set(per_tag_tp) | set(per_tag_fp) | set(per_tag_fn)):
        ttp, tfp, tfn = per_tag_tp[tag], per_tag_fp[tag], per_tag_fn[tag]
        if ttp + tfn == 0:
            continue
        p = ttp / (ttp + tfp) if (ttp + tfp) else 0.0
        r = ttp / (ttp + tfn) if (ttp + tfn) else 0.0
        tag_f1s.append(f1(p, r))
    rare_recall = rare_tp / (rare_tp + rare_fn) if (rare_tp + rare_fn) else 0.0
    latencies_sorted = sorted(latencies)
    p50 = _percentile(latencies_sorted, 50)
    p95 = _percentile(latencies_sorted, 95)
    throughput = (n / sum(latencies) * 60.0) if latencies and sum(latencies) > 0 else 0.0
    return {
        "n": n,
        "invalid_rate": (
            (
                breakdown["unparseable"]
                + breakdown["count"]
                + breakdown["out_of_taxonomy"]
                + breakdown["empty"]
            )
            / n
            if n
            else 0.0
        ),
        "invalid_breakdown": breakdown,
        "micro_precision": precision,
        "micro_recall": recall,
        "micro_f1": f1(precision, recall),
        "macro_f1": sum(tag_f1s) / len(tag_f1s) if tag_f1s else 0.0,
        "rare_label_recall": rare_recall,
        "exact3": exact3 / n if n else 0.0,
        "jaccard": sum(jaccards) / n if n else 0.0,
        "human_accept": human_ok / human_n if human_n else None,
        "latency_p50_s": p50,
        "latency_p95_s": p95,
        "throughput_ppm": throughput,
        "cost_per_1k_usd": next(
            (
                float(r.get("cost_per_1k_usd"))
                for r in pred_rows
                if r.get("cost_per_1k_usd") is not None
            ),
            None,
        ),
        "subscription_calls_per_1k": next(
            (
                float(r.get("subscription_calls_per_1k"))
                for r in pred_rows
                if r.get("subscription_calls_per_1k") is not None
            ),
            None,
        ),
        "per_post_jaccard": jaccards,
        "per_post_micro": _per_post_scores(gold_rows, pred_rows, taxonomy),
    }


def _invalid_count(breakdown: Mapping[str, int]) -> int:
    return (
        breakdown["unparseable"]
        + breakdown["count"]
        + breakdown["out_of_taxonomy"]
        + breakdown["empty"]
    )


def evaluate_fixed(
    gold_rows: Sequence[Mapping[str, Any]],
    pred_rows: Sequence[Mapping[str, Any]],
    *,
    taxonomy: Sequence[str],
    rare: Sequence[str] | None = None,
) -> dict[str, Any]:
    result = evaluate(gold_rows, pred_rows, taxonomy=taxonomy, rare=rare)
    n = result["n"]
    result["invalid_rate"] = _invalid_count(result["invalid_breakdown"]) / n if n else 0.0
    return result


def _per_post_scores(
    gold_rows: Sequence[Mapping[str, Any]],
    pred_rows: Sequence[Mapping[str, Any]],
    taxonomy: Sequence[str],
) -> list[float]:
    scores: list[float] = []
    for i, gold_row in enumerate(gold_rows):
        gold = set(gold_tags(dict(gold_row)))
        pred_row = pred_rows[i] if i < len(pred_rows) else {}
        payload = pred_payload(dict(pred_row))
        code, tags = classify_slm_tags(
            payload if isinstance(payload, dict) else {"tags": _as_tags(payload)},
            taxonomy,
        )
        pred = set(tags or []) if code == "ok" else set()
        tp = len(gold & pred)
        fp = len(pred - gold)
        fn = len(gold - pred)
        p = tp / (tp + fp) if (tp + fp) else 0.0
        r = tp / (tp + fn) if (tp + fn) else 0.0
        scores.append(f1(p, r))
    return scores


def _percentile(sorted_vals: Sequence[float], q: int) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = (q / 100.0) * (len(sorted_vals) - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return sorted_vals[lo]
    frac = idx - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def bootstrap_ci(
    diffs: Sequence[float],
    *,
    n_resamples: int = 1000,
    seed: int = 20260919,
) -> dict[str, float]:
    """Paired bootstrap CI of the mean difference."""
    import random

    if not diffs:
        return {"mean": 0.0, "lo": 0.0, "hi": 0.0, "n": 0}
    rng = random.Random(seed)
    means: list[float] = []
    n = len(diffs)
    for _ in range(n_resamples):
        sample = [diffs[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int(0.025 * (len(means) - 1))]
    hi = means[int(0.975 * (len(means) - 1))]
    return {"mean": sum(diffs) / n, "lo": lo, "hi": hi, "n": n}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", required=True)
    parser.add_argument("--pred", required=True)
    parser.add_argument("--taxonomy", default="")
    parser.add_argument("--rare", default="")
    args = parser.parse_args(argv)
    gold = load_jsonl(Path(args.gold))
    pred = load_jsonl(Path(args.pred))
    taxonomy = json.loads(Path(args.taxonomy).read_text(encoding="utf-8")) if args.taxonomy else []
    rare = json.loads(Path(args.rare).read_text(encoding="utf-8")) if args.rare else []
    result = evaluate_fixed(gold, pred, taxonomy=taxonomy, rare=rare)
    json.dump(result, sys.stdout, indent=2, sort_keys=True, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
