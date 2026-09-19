"""I.3 monitor signals and exit codes."""
from __future__ import annotations

import io

from newsfeed.slm.monitor import evaluate_signals, main

CLEAN = [
    {
        "slm_status": "ok",
        "tags_slm": ["GAMMA", "SPX", "VOL"],
        "slm_latency_ms": 800,
        "jaccard": 0.8,
        "ladder_sampled": 1,
    }
] * 20


class TestSignals:
    def test_clean_fixture_has_no_breaches(self):
        signals = evaluate_signals(
            CLEAN,
            CLEAN,
            train_distribution={"GAMMA": 10, "SPX": 10, "VOL": 10},
            shadow_jaccard_mean=0.8,
        )
        assert signals["breaches"] == []
        assert signals["invalid_or_empty"] == 0

    def test_invalid_breach(self):
        rows = [{"slm_status": "unparseable"}] * 5 + CLEAN[:5]
        signals = evaluate_signals(rows, rows, train_distribution={"GAMMA": 1})
        assert "invalid" in signals["breaches"]

    def test_vocabulary_drift_share(self):
        rows = [{"slm_status": "abstain:out_of_taxonomy", "tags_slm_raw": ["NEW"]}] * 4 + CLEAN[:6]
        signals = evaluate_signals(rows, rows, train_distribution={"GAMMA": 1})
        assert "vocabulary_drift" in signals["breaches"]

    def test_unknown_tag_hot(self):
        rows = (
            [{"slm_status": "abstain:out_of_taxonomy", "tags_slm_raw": ["WEIRD"]}] * 10
            + CLEAN
        )
        signals = evaluate_signals(rows, rows, train_distribution={"GAMMA": 1})
        assert "vocabulary_drift" in signals["breaches"]
        assert signals["unknown_hot"]["WEIRD"] >= 10

    def test_label_shift(self):
        rows = [{"slm_status": "ok", "tags_slm": ["PUTS", "PUTS", "PUTS"], "slm_latency_ms": 100}] * 20
        signals = evaluate_signals(
            rows, rows, train_distribution={"GAMMA": 100, "SPX": 100, "VOL": 100, "PUTS": 1}
        )
        assert "label_shift" in signals["breaches"]

    def test_agreement_drop(self):
        rows = [{**CLEAN[0], "jaccard": 0.2, "ladder_sampled": 1}] * 20
        signals = evaluate_signals(
            rows, rows, train_distribution={"GAMMA": 1}, shadow_jaccard_mean=0.9
        )
        assert "agreement" in signals["breaches"]

    def test_availability(self):
        rows = [{"slm_status": "unavailable"}] * 3 + CLEAN[:7]
        signals = evaluate_signals(rows, rows, train_distribution={"GAMMA": 1})
        assert "availability" in signals["breaches"]

    def test_latency(self):
        rows = [{**CLEAN[0], "slm_latency_ms": 12_000}] * 20
        signals = evaluate_signals(rows, rows, train_distribution={"GAMMA": 1})
        assert "latency" in signals["breaches"]


class TestExit:
    def test_off_is_noop_exit_0(self, monkeypatch, capsys):
        monkeypatch.setenv("RADON_SLM_TAGGER_MODE", "off")
        code = main([], rows_7d=CLEAN, rows_28d=CLEAN, heartbeat=False)
        assert code == 0
        assert '"noop": true' in capsys.readouterr().out

    def test_shadow_is_noop_exit_0(self, monkeypatch, capsys):
        monkeypatch.setenv("RADON_SLM_TAGGER_MODE", "shadow")
        code = main([], rows_7d=CLEAN, rows_28d=CLEAN, heartbeat=False)
        assert code == 0

    def test_prefer_breach_exits_3(self, monkeypatch, capsys):
        monkeypatch.setenv("RADON_SLM_TAGGER_MODE", "prefer")
        rows = [{"slm_status": "unparseable"}] * 10
        code = main([], rows_7d=rows, rows_28d=rows, heartbeat=False)
        assert code == 3

    def test_prefer_clean_exits_0(self, monkeypatch):
        monkeypatch.setenv("RADON_SLM_TAGGER_MODE", "prefer")
        code = main(
            ["--shadow-jaccard-mean", "0.8"],
            rows_7d=CLEAN,
            rows_28d=CLEAN,
            heartbeat=False,
        )
        assert code == 0
