#!/usr/bin/env python3.13
"""Three-arm bakeoff A/B/C and F.3 gates (HR-1, HR-6)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

_SCRIPTS = Path(__file__).resolve().parents[2]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from newsfeed.slm.eval import bootstrap_ci, evaluate_fixed, load_jsonl  # noqa: E402

GATES = ("G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8")


def _load_metrics(path: Path | None, gold, taxonomy, rare) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    pred = load_jsonl(path)
    return evaluate_fixed(gold, pred, taxonomy=taxonomy, rare=rare)


def decide_gates(
    *,
    a_test: Mapping[str, Any] | None,
    b_test: Mapping[str, Any] | None,
    c_test: Mapping[str, Any] | None,
    a_gold: Mapping[str, Any] | None,
    b_gold: Mapping[str, Any] | None,
    c_gold: Mapping[str, Any] | None,
    g0: Mapping[str, Any] | None = None,
    b0_micro_f1: float | None = None,
    c_minus_a_gold: list[float] | None = None,
    c_minus_b_test: list[float] | None = None,
) -> dict[str, Any]:
    gates: dict[str, dict[str, Any]] = {}
    if g0 is None:
        gates["G0"] = {"pass": False, "detail": "unmeasured: no MLX and GGUF eval pair"}
    else:
        d = abs(float(g0["micro_f1_mlx"]) - float(g0["micro_f1_gguf"]))
        inv_ok = float(g0["invalid_rate_gguf"]) <= float(g0["invalid_rate_mlx"]) + 0.002
        gates["G0"] = {
            "pass": d <= 0.01 and inv_ok,
            "detail": f"|mlx-gguf|={d:.4f} invalid_gguf={g0['invalid_rate_gguf']}",
        }

    if c_test is None:
        for name in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8"):
            gates[name] = {"pass": False, "detail": "arm C missing"}
        failed = [k for k, v in gates.items() if not v["pass"]]
        return {"gates": gates, "verdict": f"C FAILS {','.join(failed)}"}

    gates["G1"] = {
        "pass": c_test["invalid_rate"] <= 0.005
        and c_test["invalid_breakdown"]["out_of_taxonomy"] / max(c_test["n"], 1) <= 0.003,
        "detail": f"invalid_rate={c_test['invalid_rate']:.4f}",
    }
    p95 = c_test.get("latency_p95_s")
    gates["G5"] = {
        "pass": p95 is not None and p95 <= 10,
        "detail": f"latency_p95_s={p95}",
    }

    if a_gold and c_gold and c_minus_a_gold is not None:
        ci = bootstrap_ci(c_minus_a_gold)
        g2_human = c_gold["micro_f1"] > a_gold["micro_f1"] and ci["lo"] > 0
    else:
        ci = {"lo": 0.0, "hi": 0.0, "mean": 0.0}
        g2_human = False
    slack = (1 - b0_micro_f1) if b0_micro_f1 is not None else 1.0
    g2_machine = True
    if a_test and c_test:
        g2_machine = c_test["micro_f1"] >= a_test["micro_f1"] - slack
    gates["G2"] = {
        "pass": bool(g2_human and g2_machine),
        "detail": f"human C={None if not c_gold else c_gold['micro_f1']} A={None if not a_gold else a_gold['micro_f1']} ci_lo={ci['lo']}",
    }

    if a_test:
        gates["G3"] = {
            "pass": c_test["macro_f1"] >= a_test["macro_f1"]
            and c_test["rare_label_recall"] >= a_test["rare_label_recall"] - 0.05,
            "detail": f"macro C={c_test['macro_f1']:.4f} A={a_test['macro_f1']:.4f}",
        }
        gates["G4"] = {
            "pass": c_test["invalid_rate"] <= a_test["invalid_rate"],
            "detail": f"invalid C={c_test['invalid_rate']:.4f} A={a_test['invalid_rate']:.4f}",
        }
    else:
        gates["G3"] = {"pass": False, "detail": "arm A test missing"}
        gates["G4"] = {"pass": False, "detail": "arm A test missing"}

    if a_gold and c_gold and c_gold.get("human_accept") is not None and a_gold.get("human_accept") is not None:
        gates["G6"] = {
            "pass": c_gold["human_accept"] >= a_gold["human_accept"],
            "detail": f"human_accept C={c_gold['human_accept']} A={a_gold['human_accept']}",
        }
    else:
        gates["G6"] = {"pass": False, "detail": "gold_human human_accept unmeasured"}

    cost_c = (c_test or {}).get("cost_per_1k_usd")
    cost_a = (a_test or {}).get("cost_per_1k_usd")
    if cost_c is None:
        cost_c = 0.0
    if cost_a is None:
        cost_a = 1.0
    calls_c = (c_test or {}).get("subscription_calls_per_1k")
    calls_a = (a_test or {}).get("subscription_calls_per_1k")
    if calls_c is None:
        calls_c = 0.0
    if calls_a is None:
        calls_a = 1.0
    lat_c = (c_test or {}).get("latency_p95_s")
    lat_a = (a_test or {}).get("latency_p95_s")
    g7 = (
        cost_c < cost_a
        and calls_c < calls_a
        and lat_c is not None
        and lat_a is not None
        and lat_c <= lat_a
    )
    gates["G7"] = {"pass": bool(g7), "detail": f"cost C={cost_c} A={cost_a} p95 C={lat_c} A={lat_a}"}

    if b_test and c_minus_b_test is not None:
        ci_b = bootstrap_ci(c_minus_b_test)
        g8 = c_test["micro_f1"] > b_test["micro_f1"] and ci_b["lo"] > 0 and c_test["invalid_rate"] <= b_test["invalid_rate"]
        gates["G8"] = {"pass": bool(g8), "detail": f"micro C={c_test['micro_f1']} B={b_test['micro_f1']} ci_lo={ci_b['lo']}"}
    else:
        gates["G8"] = {"pass": False, "detail": "arm B missing or no paired diffs"}

    failed = [k for k in GATES if not gates[k]["pass"]]
    verdict = "C WINS" if not failed else f"C FAILS {','.join(failed)}"
    return {"gates": gates, "verdict": verdict, "g2_ci": ci}


def markdown_table(arms: Mapping[str, Mapping[str, Any] | None], split: str) -> str:
    keys = [
        "n",
        "micro_f1",
        "macro_f1",
        "rare_label_recall",
        "invalid_rate",
        "exact3",
        "jaccard",
        "human_accept",
        "latency_p50_s",
        "latency_p95_s",
        "throughput_ppm",
        "cost_per_1k_usd",
        "subscription_calls_per_1k",
    ]
    lines = [f"### {split}", "", "| metric | A | B | C |", "|---|---|---|---|"]
    for key in keys:
        cells = [key]
        for arm in ("A", "B", "C"):
            block = arms.get(arm)
            val = None if block is None else block.get(key)
            cells.append("n/a" if val is None else (f"{val:.4f}" if isinstance(val, float) else str(val)))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def run_bakeoff(
    *,
    gold_test: list,
    gold_human: list,
    pred_a_test: Path | None,
    pred_b_test: Path | None,
    pred_c_test: Path | None,
    pred_a_human: Path | None,
    pred_b_human: Path | None,
    pred_c_human: Path | None,
    taxonomy: list[str],
    rare: list[str],
    g0: dict[str, Any] | None = None,
    b0_micro_f1: float | None = None,
) -> dict[str, Any]:
    a_test = _load_metrics(pred_a_test, gold_test, taxonomy, rare)
    b_test = _load_metrics(pred_b_test, gold_test, taxonomy, rare)
    c_test = _load_metrics(pred_c_test, gold_test, taxonomy, rare)
    a_gold = _load_metrics(pred_a_human, gold_human, taxonomy, rare) if gold_human else None
    b_gold = _load_metrics(pred_b_human, gold_human, taxonomy, rare) if gold_human else None
    c_gold = _load_metrics(pred_c_human, gold_human, taxonomy, rare) if gold_human else None

    c_minus_a = None
    c_minus_b = None
    if a_gold and c_gold:
        c_minus_a = [c - a for c, a in zip(c_gold["per_post_micro"], a_gold["per_post_micro"])]
    if b_test and c_test:
        c_minus_b = [c - b for c, b in zip(c_test["per_post_micro"], b_test["per_post_micro"])]

    decision = decide_gates(
        a_test=a_test,
        b_test=b_test,
        c_test=c_test,
        a_gold=a_gold,
        b_gold=b_gold,
        c_gold=c_gold,
        g0=g0,
        b0_micro_f1=b0_micro_f1,
        c_minus_a_gold=c_minus_a,
        c_minus_b_test=c_minus_b,
    )
    payload = {
        "honesty": "Radon SLM tagger: internal specialist, not a SotA replacement for open research.",
        "arms": {
            "A": {"test": a_test, "gold_human": a_gold},
            "B": {"test": b_test, "gold_human": b_gold},
            "C": {"test": c_test, "gold_human": c_gold},
        },
        "b0_micro_f1": b0_micro_f1,
        **decision,
        "markdown": {
            "test": markdown_table({"A": a_test, "B": b_test, "C": c_test}, "test.jsonl"),
            "gold_human": markdown_table({"A": a_gold, "B": b_gold, "C": c_gold}, "gold_human.jsonl"),
        },
    }
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold-test", required=True)
    parser.add_argument("--gold-human", default="")
    parser.add_argument("--pred-a-test", default="")
    parser.add_argument("--pred-b-test", default="")
    parser.add_argument("--pred-c-test", default="")
    parser.add_argument("--pred-a-human", default="")
    parser.add_argument("--pred-b-human", default="")
    parser.add_argument("--pred-c-human", default="")
    parser.add_argument("--taxonomy", default="")
    parser.add_argument("--rare", default="")
    parser.add_argument("--g0", default="")
    parser.add_argument("--b0-micro-f1", type=float, default=None)
    args = parser.parse_args(argv)
    gold_test = load_jsonl(Path(args.gold_test))
    gold_human = load_jsonl(Path(args.gold_human)) if args.gold_human else []
    taxonomy = json.loads(Path(args.taxonomy).read_text(encoding="utf-8")) if args.taxonomy else []
    rare = json.loads(Path(args.rare).read_text(encoding="utf-8")) if args.rare else []
    g0 = json.loads(Path(args.g0).read_text(encoding="utf-8")) if args.g0 else None
    payload = run_bakeoff(
        gold_test=gold_test,
        gold_human=gold_human,
        pred_a_test=Path(args.pred_a_test) if args.pred_a_test else None,
        pred_b_test=Path(args.pred_b_test) if args.pred_b_test else None,
        pred_c_test=Path(args.pred_c_test) if args.pred_c_test else None,
        pred_a_human=Path(args.pred_a_human) if args.pred_a_human else None,
        pred_b_human=Path(args.pred_b_human) if args.pred_b_human else None,
        pred_c_human=Path(args.pred_c_human) if args.pred_c_human else None,
        taxonomy=taxonomy,
        rare=rare,
        g0=g0,
        b0_micro_f1=args.b0_micro_f1,
    )
    json.dump(payload, sys.stdout, indent=2, sort_keys=True, default=str)
    sys.stdout.write("\n")
    sys.stderr.write(payload["markdown"]["test"] + "\n")
    sys.stderr.write(payload["markdown"]["gold_human"] + "\n")
    sys.stderr.write(payload["verdict"] + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
