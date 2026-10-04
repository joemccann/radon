"""HR-1 bakeoff verdict and fixture-limited A/B/C table."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from newsfeed.slm.bakeoff import decide_gates, run_bakeoff
from newsfeed.slm.eval import evaluate_fixed

TAXONOMY = ["GAMMA", "SPX", "VOL", "VIX", "PUTS"]
GOLD = [
    {"id": "1", "gold": ["GAMMA", "SPX", "VOL"], "human": "y"},
    {"id": "2", "gold": ["GAMMA", "SPX", "VIX"], "human": "y"},
    {"id": "3", "gold": ["PUTS", "SPX", "VOL"], "human": "n"},
]


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


class TestVerdict:
    def test_missing_c_fails_all_win_gates(self):
        decision = decide_gates(
            a_test=None, b_test=None, c_test=None, a_gold=None, b_gold=None, c_gold=None
        )
        assert decision["verdict"].startswith("C FAILS")
        assert "G0" in decision["verdict"]

    def test_fixture_run_is_c_fails(self, tmp_path: Path):
        gold = _write(tmp_path / "gold.jsonl", GOLD)
        a = _write(
            tmp_path / "a.jsonl",
            [
                {"id": "1", "pred": {"tags": ["GAMMA", "SPX", "VOL"]}, "latency_s": 1.2},
                {"id": "2", "pred": {"tags": ["GAMMA", "SPX", "VIX"]}, "latency_s": 1.1},
                {"id": "3", "pred": {"tags": ["PUTS", "SPX", "VOL"]}, "latency_s": 1.0},
            ],
        )
        b = _write(
            tmp_path / "b.jsonl",
            [
                {"id": "1", "pred": {"tags": ["GAMMA", "SPX", "PUTS"]}, "latency_s": 3.0},
                {"id": "2", "pred": "prose", "latency_s": 3.1},
                {"id": "3", "pred": {"tags": ["PUTS", "SPX", "VOL"]}, "latency_s": 2.8},
            ],
        )
        c = _write(
            tmp_path / "c.jsonl",
            [
                {"id": "1", "pred": {"tags": ["GAMMA", "SPX", "VOL"]}, "latency_s": 2.0},
                {"id": "2", "pred": {"tags": ["GAMMA", "SPX", "VIX"]}, "latency_s": 2.1},
                {"id": "3", "pred": {"tags": ["GAMMA", "SPX", "VOL"]}, "latency_s": 1.9},
            ],
        )
        payload = run_bakeoff(
            gold_test=GOLD,
            gold_human=GOLD,
            pred_a_test=a,
            pred_b_test=b,
            pred_c_test=c,
            pred_a_human=a,
            pred_b_human=b,
            pred_c_human=c,
            taxonomy=TAXONOMY,
            rare=["PUTS"],
            g0=None,
            b0_micro_f1=0.85,
        )
        assert payload["verdict"].startswith("C FAILS")
        assert "G0" in payload["verdict"]
        assert "micro_f1" in payload["markdown"]["test"]
        a_metrics = evaluate_fixed(GOLD, json.loads(a.read_text().splitlines()[0]) and [
            json.loads(line) for line in a.read_text().splitlines()
        ], taxonomy=TAXONOMY, rare=["PUTS"])
        assert a_metrics["n"] == 3


def _passing_gate_inputs():
    a = dict(n=1000, micro_f1=0.8, macro_f1=0.7, rare_label_recall=0.75,
             invalid_rate=0.002, invalid_breakdown={"out_of_taxonomy": 0},
             latency_p95_s=12, human_accept=0.8, cost_per_1k_usd=1,
             subscription_calls_per_1k=1000)
    c = {**a, "micro_f1": 0.9, "macro_f1": 0.8, "invalid_rate": 0.001,
         "latency_p95_s": 8, "human_accept": 0.9, "cost_per_1k_usd": 0,
         "subscription_calls_per_1k": 0}
    return dict(a_test=a, b_test=copy.deepcopy(a), c_test=c,
                a_gold=copy.deepcopy(a), b_gold=copy.deepcopy(a), c_gold=copy.deepcopy(c),
                g0=dict(micro_f1_mlx=0, micro_f1_gguf=0, invalid_rate_mlx=0, invalid_rate_gguf=0),
                b0_micro_f1=0.9, c_minus_a_gold=[0.1] * 5, c_minus_b_test=[0.1] * 5)


def test_all_measured_promotion_gates_pass():
    decision = decide_gates(**_passing_gate_inputs())
    assert set(decision["gates"]) == {f"G{i}" for i in range(9)}
    assert all(gate["pass"] for gate in decision["gates"].values())
    assert decision["verdict"] == "C WINS"


@pytest.mark.parametrize("gate,block,key,value", [
    ("G0", "g0", "micro_f1_gguf", 0.011),
    ("G0", "g0", "invalid_rate_gguf", 0.0021),
    ("G0", None, "g0", None),
    ("G1", "c_test", "invalid_rate", 0.0051),
    ("G1", "c_test", "invalid_breakdown", {"out_of_taxonomy": 4}),
    ("G2", "c_gold", "micro_f1", 0.8),
    ("G2", None, "c_minus_a_gold", [0] * 5),
    ("G2", None, "c_minus_a_gold", None),
    ("G2", "c_test", "micro_f1", 0.699),
    ("G3", "c_test", "macro_f1", 0.699),
    ("G3", "c_test", "rare_label_recall", 0.699),
    ("G3", None, "a_test", None),
    ("G4", "c_test", "invalid_rate", 0.003),
    ("G4", None, "a_test", None),
    ("G5", "c_test", "latency_p95_s", 10.001),
    ("G5", "c_test", "latency_p95_s", None),
    ("G6", "c_gold", "human_accept", 0.799),
    ("G6", "c_gold", "human_accept", None),
    ("G6", None, "a_gold", None),
    ("G7", "c_test", "cost_per_1k_usd", 1),
    ("G7", "c_test", "subscription_calls_per_1k", 1000),
    ("G7", "c_test", "latency_p95_s", 13),
    ("G8", "b_test", "micro_f1", 0.9),
    ("G8", None, "c_minus_b_test", [0] * 5),
    ("G8", None, "b_test", None),
    ("G8", "b_test", "invalid_rate", 0),
])
def test_each_promotion_gate_refuses_failed_or_missing_evidence(gate, block, key, value):
    inputs = _passing_gate_inputs()
    if block is None:
        inputs[key] = value
    else:
        inputs[block][key] = value
    decision = decide_gates(**inputs)
    assert decision["gates"][gate]["pass"] is False
    assert decision["verdict"].startswith("C FAILS ")
    assert gate in decision["verdict"].removeprefix("C FAILS ").split(",")


@pytest.mark.parametrize("gate,block,key,value", [
    ("G0", "g0", "micro_f1_gguf", 0.01),
    ("G0", "g0", "invalid_rate_gguf", 0.002),
    ("G1", "c_test", "invalid_rate", 0.005),
    ("G1", "c_test", "invalid_breakdown", {"out_of_taxonomy": 3}),
    ("G3", "c_test", "macro_f1", 0.7),
    ("G3", "c_test", "rare_label_recall", 0.7),
    ("G4", "c_test", "invalid_rate", 0.002),
    ("G5", "c_test", "latency_p95_s", 10),
    ("G6", "c_gold", "human_accept", 0.8),
])
def test_inclusive_promotion_boundaries(gate, block, key, value):
    inputs = _passing_gate_inputs()
    inputs[block][key] = value
    assert decide_gates(**inputs)["gates"][gate]["pass"] is True


def test_missing_c_refuses_every_win_gate():
    inputs = _passing_gate_inputs()
    inputs["c_test"] = None
    decision = decide_gates(**inputs)
    assert all(not decision["gates"][f"G{i}"]["pass"] for i in range(1, 9))
    assert decision["verdict"] == "C FAILS G1,G2,G3,G4,G5,G6,G7,G8"
