"""HR-1 bakeoff verdict and fixture-limited A/B/C table."""
from __future__ import annotations

import json
from pathlib import Path

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
