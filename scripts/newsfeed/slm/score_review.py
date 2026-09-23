#!/usr/bin/env python3.13
"""Fail-closed evaluation gates for a blinded SLM human-review packet."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parents[2]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from newsfeed.slm.diagnostics import diagnose, index_by_id  # noqa: E402
from newsfeed.slm.eval import bootstrap_ci, evaluate_fixed, load_jsonl  # noqa: E402

ARMS = ("A", "B", "C")
ALIASES = ("Candidate 1", "Candidate 2", "Candidate 3")


def _aligned(rows: list[dict[str, Any]], expected: set[str], name: str) -> dict[str, dict[str, Any]]:
    indexed = index_by_id(rows, name)
    if indexed.keys() != expected:
        raise ValueError(f"{name} ID set differs: missing={len(expected - indexed.keys())}, unexpected={len(indexed.keys() - expected)}")
    return indexed


def _score_pair(gold_rows: list[dict[str, Any]], pred: dict[str, dict[str, Any]], taxonomy: list[str], rare: list[str]) -> dict[str, Any]:
    ordered = [pred[str(row.get("id") or row.get("post_id"))] for row in gold_rows]
    return evaluate_fixed(gold_rows, ordered, taxonomy=taxonomy, rare=rare)


def score_review(
    *,
    gold_test: list[dict[str, Any]],
    predictions: dict[str, list[dict[str, Any]]],
    packet: dict[str, Any],
    decisions: dict[str, Any],
    key: dict[str, Any],
    taxonomy: list[str],
    rare: list[str],
    g0: dict[str, Any] | None,
    b0_micro_f1: float | None,
) -> dict[str, Any]:
    test_index = index_by_id(gold_test, "gold test")
    test_ids = set(test_index)
    sample_items = packet.get("items")
    if packet.get("schema") != "radon.slm-review.v1" or not isinstance(sample_items, list) or len(sample_items) != 200:
        raise ValueError("Review packet must be a radon.slm-review.v1 packet of exactly 200 items")
    sample = _aligned(sample_items, {str(item.get("id")) for item in sample_items}, "review packet")
    sample_ids = set(sample)
    if not sample_ids <= test_ids:
        raise ValueError("Review sample IDs are not a subset of the pinned test set")
    if decisions.get("schema") != "radon.slm-review-decisions.v1" or decisions.get("runId") != packet.get("runId"):
        raise ValueError("Decision export schema/run ID does not match the packet")
    if key.get("schema") != "radon.slm-review-key.v1" or key.get("runId") != packet.get("runId"):
        raise ValueError("Private blinding key schema/run ID does not match the packet")
    decision_rows = decisions.get("decisions")
    mapping = key.get("aliasToArm")
    review_to_post = key.get("reviewIdToPostId")
    if not isinstance(decision_rows, list) or not isinstance(mapping, dict) or not isinstance(review_to_post, dict):
        raise ValueError("Decision export or private blinding key is malformed")
    decision_index = _aligned(decision_rows, sample_ids, "decisions")
    if set(mapping) != sample_ids or set(review_to_post) != sample_ids:
        raise ValueError("Private key IDs do not exactly match the reviewed sample")
    post_ids = list(review_to_post.values())
    if len(set(post_ids)) != len(sample_ids) or set(post_ids) - test_ids:
        raise ValueError("Private key post IDs are duplicated or outside the pinned holdout")
    human_gold: list[dict[str, Any]] = []
    by_arm_human: dict[str, list[dict[str, Any]]] = {arm: [] for arm in ARMS}
    acceptance: dict[str, list[float]] = {arm: [] for arm in ARMS}
    for review_id in sorted(sample_ids):
        post_id = review_to_post[review_id]
        row = decision_index[review_id]
        human_tags = row.get("humanTags")
        votes = row.get("acceptance")
        alias_map = mapping[review_id]
        if not isinstance(human_tags, list) or len(human_tags) != 3 or len({str(tag).casefold() for tag in human_tags}) != 3:
            raise ValueError(f"Missing or invalid human labels for {post_id}")
        if any(str(tag).casefold() not in {tag.casefold() for tag in taxonomy} for tag in human_tags):
            raise ValueError(f"Unknown human taxonomy label for {post_id}")
        if not isinstance(votes, dict) or set(votes) != set(ALIASES) or any(type(votes[a]) is not bool for a in ALIASES):
            raise ValueError(f"All three candidate votes are required for {post_id}")
        if not isinstance(alias_map, dict) or set(alias_map) != set(ALIASES) or set(alias_map.values()) != set(ARMS):
            raise ValueError(f"Invalid one-to-one blinding map for {post_id}")
        human_gold.append({"id": post_id, "tags": human_tags})
        item = sample[review_id]
        candidates = item.get("candidates")
        if not isinstance(candidates, dict) or set(candidates) != set(ALIASES):
            raise ValueError(f"Packet candidates are malformed for {post_id}")
        for alias, arm in alias_map.items():
            tags = candidates[alias]
            by_arm_human[arm].append({"id": post_id, "pred": {"tags": tags}})
            acceptance[arm].append(float(votes[alias]))

    per_arm: dict[str, dict[str, Any]] = {}
    label_diagnostics: dict[str, dict[str, Any]] = {}
    for arm in ARMS:
        test_predictions = _aligned(predictions[arm], test_ids, f"arm {arm} test predictions")
        ordered = [test_predictions[str(row.get("id") or row.get("post_id"))] for row in gold_test]
        per_arm[arm] = {
            "test": _score_pair(gold_test, test_predictions, taxonomy, rare),
            "human_review_labels": evaluate_fixed(human_gold, by_arm_human[arm], taxonomy=taxonomy),
        }
        label_diagnostics[arm] = diagnose(gold_test, ordered, taxonomy)["per_label"]

    ci_human = bootstrap_ci([c - a for c, a in zip(acceptance["C"], acceptance["A"], strict=True)])
    ci_micro_a = bootstrap_ci([
        c - a for c, a in zip(
            per_arm["C"]["human_review_labels"]["per_post_micro"],
            per_arm["A"]["human_review_labels"]["per_post_micro"],
            strict=True,
        )
    ])
    ci_micro_b = bootstrap_ci([
        c - b for c, b in zip(
            per_arm["C"]["test"]["per_post_micro"],
            per_arm["B"]["test"]["per_post_micro"],
            strict=True,
        )
    ])
    a_rare = per_arm["A"]["test"]["rare_label_recall"]
    c_rare = per_arm["C"]["test"]["rare_label_recall"]
    rare_target = max(a_rare - 0.05, 0.50)
    rare_labels = {
        tag: {
            **label_diagnostics["C"][tag],
            "minimum_support": 10,
            "target_recall": 0.30,
            "pass": label_diagnostics["C"].get(tag, {}).get("support", 0) < 10
            or (label_diagnostics["C"].get(tag, {}).get("recall") or 0) >= 0.30,
        }
        for tag in rare
    }
    a_accept = sum(acceptance["A"]) / len(acceptance["A"])
    c_accept = sum(acceptance["C"]) / len(acceptance["C"])
    a_test, b_test, c_test = (per_arm[arm]["test"] for arm in ARMS)
    g0_pass = False
    g0_detail = "unmeasured: parity file missing"
    if g0:
        delta = abs(float(g0["micro_f1_mlx"]) - float(g0["micro_f1_gguf"]))
        invalid_ok = float(g0["invalid_rate_gguf"]) <= float(g0["invalid_rate_mlx"]) + 0.002
        g0_pass = delta <= 0.01 and invalid_ok
        g0_detail = f"|mlx-gguf|={delta:.4f}; invalid parity={invalid_ok}"
    b0_slack = 1.0 - b0_micro_f1 if b0_micro_f1 is not None else None
    c_human_f1 = per_arm["C"]["human_review_labels"]["micro_f1"]
    a_human_f1 = per_arm["A"]["human_review_labels"]["micro_f1"]
    test_n_ok = len(gold_test) >= 300
    out_tax = c_test["invalid_breakdown"].get("abstain:out_of_taxonomy", 0) + c_test["invalid_breakdown"].get("out_of_taxonomy", 0)
    gates = {
        "G0_export_parity": {"pass": g0_pass, "detail": g0_detail},
        "G1_output_validity": {"pass": test_n_ok and c_test["invalid_rate"] <= 0.005 and out_tax / max(c_test["n"], 1) <= 0.003, "detail": f"n={c_test['n']} invalid={c_test['invalid_rate']:.4f}; out_of_taxonomy={out_tax/max(c_test['n'],1):.4f}"},
        "G2_human_label_quality": {"pass": c_human_f1 > a_human_f1 and ci_micro_a["lo"] > 0 and b0_slack is not None and c_test["micro_f1"] >= a_test["micro_f1"] - b0_slack, "detail": f"human F1 C={c_human_f1:.4f} A={a_human_f1:.4f}; paired CI lower={ci_micro_a['lo']:.4f}; B0={b0_micro_f1}"},
        "G3_macro_and_rare_recall": {"pass": c_test["macro_f1"] >= a_test["macro_f1"] and c_rare >= rare_target and all(label["pass"] for label in rare_labels.values()), "detail": f"macro C={c_test['macro_f1']:.4f} A={a_test['macro_f1']:.4f}; rare C={c_rare:.4f}, target={rare_target:.4f}; per-label support>=10 pass={all(label['pass'] for label in rare_labels.values())}"},
        "G4_invalid_no_regression": {"pass": c_test["invalid_rate"] <= a_test["invalid_rate"], "detail": f"invalid C={c_test['invalid_rate']:.4f} A={a_test['invalid_rate']:.4f}"},
        "G5_latency": {"pass": c_test.get("latency_p95_s") is not None and c_test["latency_p95_s"] <= 10, "detail": f"p95={c_test.get('latency_p95_s')}s (<=10s required)"},
        "G6_human_acceptance": {"pass": len(acceptance["C"]) == 200 and c_accept >= a_accept and ci_human["lo"] >= -0.05, "detail": f"C={c_accept:.3f} A={a_accept:.3f}; paired CI lower={ci_human['lo']:.3f}; sample=200"},
        "G7_cost_and_latency": {"pass": c_test.get("cost_per_1k_usd") is not None and a_test.get("cost_per_1k_usd") is not None and c_test.get("subscription_calls_per_1k") is not None and a_test.get("subscription_calls_per_1k") is not None and c_test["cost_per_1k_usd"] < a_test["cost_per_1k_usd"] and c_test["subscription_calls_per_1k"] < a_test["subscription_calls_per_1k"] and c_test.get("latency_p95_s") is not None and a_test.get("latency_p95_s") is not None and c_test["latency_p95_s"] <= a_test["latency_p95_s"], "detail": "missing cost/call/latency measurements fail closed"},
        "G8_beats_prompt_only": {"pass": c_test["micro_f1"] > b_test["micro_f1"] and ci_micro_b["lo"] > 0 and c_test["invalid_rate"] <= b_test["invalid_rate"], "detail": f"micro C={c_test['micro_f1']:.4f} B={b_test['micro_f1']:.4f}; paired CI lower={ci_micro_b['lo']:.4f}"},
    }
    failed = [name for name, value in gates.items() if not value["pass"]]
    return {
        "schema": "radon.slm-evaluation-gates.v1",
        "runId": packet["runId"],
        "holdout_n": len(gold_test),
        "human_review_n": len(acceptance["A"]),
        "arms": per_arm,
        "per_label": label_diagnostics,
        "rare_label_target": {"pooled": rare_target, "per_label_min_support": 10, "per_label_recall": 0.30, "labels": rare_labels},
        "human_acceptance": {"A": a_accept, "B": sum(acceptance["B"]) / 200, "C": c_accept, "C_minus_A_paired_95_ci": ci_human},
        "gates": gates,
        "decision": "ALL_GATES_PASS" if not failed else "SLM_STAYS_OFF",
        "failed_gates": failed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold-test", required=True)
    parser.add_argument("--pred-a", required=True)
    parser.add_argument("--pred-b", required=True)
    parser.add_argument("--pred-c", required=True)
    parser.add_argument("--packet", required=True)
    parser.add_argument("--decisions", required=True)
    parser.add_argument("--mapping", required=True)
    parser.add_argument("--taxonomy", required=True)
    parser.add_argument("--rare", required=True)
    parser.add_argument("--b0-micro-f1", type=float)
    parser.add_argument("--g0")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    def read(path: str) -> dict[str, Any]:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    report = score_review(
        gold_test=load_jsonl(Path(args.gold_test)),
        predictions={arm: load_jsonl(Path(path)) for arm, path in zip(ARMS, (args.pred_a, args.pred_b, args.pred_c), strict=True)},
        packet=read(args.packet),
        decisions=read(args.decisions),
        key=read(args.mapping),
        taxonomy=read(args.taxonomy),
        rare=read(args.rare),
        g0=read(args.g0) if args.g0 else None,
        b0_micro_f1=args.b0_micro_f1,
    )
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if stat.S_IMODE(output.parent.stat().st_mode) & 0o077:
        raise PermissionError(f"Private report directory must be mode 0700: {output.parent}")
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\\n")
    print(json.dumps({"run_id": report["runId"], "decision": report["decision"], "failed_gates": report["failed_gates"], "out": str(output)}))
    return 0 if report["decision"] == "ALL_GATES_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
