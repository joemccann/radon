from __future__ import annotations

import pytest

from newsfeed.slm.diagnostics import diagnose


def test_diagnostics_align_by_id_and_count_out_of_taxonomy_as_invalid() -> None:
    gold = [
        {"id": "one", "tags": ["A", "B", "C"]},
        {"id": "two", "tags": ["A", "C", "D"]},
    ]
    predictions = [
        {"id": "two", "pred": {"tags": ["A", "C", "UNKNOWN"]}, "latency_s": 0.3},
        {"id": "one", "pred": {"tags": ["A", "B", "C"]}, "latency_s": 0.2},
    ]

    report = diagnose(gold, predictions, ["A", "B", "C", "D"])

    assert report["n"] == 2
    assert report["invalid_rate"] == 0.5
    assert report["per_label"]["A"] == {"support": 2, "tp": 1, "fp": 0, "fn": 1, "recall": 0.5}
    assert report["per_label"]["D"]["fn"] == 1
    assert [row["id"] for row in report["per_example"]] == ["one", "two"]
    assert report["per_example"][1]["valid"] is False


@pytest.mark.parametrize(
    "predictions",
    [
        [{"id": "one", "pred": {"tags": ["A", "B", "C"]}}, {"id": "one", "pred": {"tags": ["A", "B", "C"]}}],
        [{"id": "other", "pred": {"tags": ["A", "B", "C"]}}],
    ],
)
def test_diagnostics_reject_duplicate_or_misaligned_ids(predictions) -> None:
    with pytest.raises(ValueError):
        diagnose([{"id": "one", "tags": ["A", "B", "C"]}], predictions, ["A", "B", "C"])
