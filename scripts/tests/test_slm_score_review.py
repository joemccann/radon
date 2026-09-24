from __future__ import annotations

import pytest

from newsfeed.slm.build_review_packet import build_packet
from newsfeed.slm.score_review import score_review

TAXONOMY = ["RARE", "B", "C"]
ALIASES = ("Candidate 1", "Candidate 2", "Candidate 3")


def _holdout(count: int = 200) -> tuple[list[dict], dict[str, list[dict]], list[dict]]:
    gold = []
    metadata = []
    predictions = {arm: [] for arm in ("A", "B", "C")}
    for index in range(count):
        post_id = str(index)
        month = "2026-08" if index < count // 2 else "2026-09"
        gold.append({
            "id": post_id,
            "timestamp": f"{month}-10T00:00:00+00:00",
            "tags": ["RARE", "B", "C"],
            "messages": [{"role": "user", "content": f"Title: title {index}\nBody: body {index}"}],
        })
        metadata.append({"id": post_id, "images": ["https://images.example.invalid/post.png"] if index < 50 else []})
        for arm in predictions:
            predictions[arm].append({"id": post_id, "pred": {"tags": ["RARE", "B", "C"]}})
    return gold, predictions, metadata


def _decisions(packet: dict) -> dict:
    return {
        "schema": "radon.slm-review-decisions.v1",
        "runId": packet["runId"],
        "decisions": [
            {
                "id": item["id"],
                "humanTags": ["RARE", "B", "C"],
                "acceptance": {alias: True for alias in ALIASES},
            }
            for item in packet["items"]
        ],
    }


def _built():
    gold, predictions, metadata = _holdout()
    packet, key = build_packet(
        run_id="synthetic-run",
        gold_rows=gold,
        pred_rows=predictions,
        metadata_rows=metadata,
        taxonomy=TAXONOMY,
        rare=["RARE"],
    )
    return gold, predictions, packet, key


def test_builder_packet_scores_against_blinded_review_ids() -> None:
    gold, predictions, packet, key = _built()
    assert packet["items"][0]["id"] not in {row["id"] for row in gold}
    report = score_review(
        gold_test=gold,
        predictions=predictions,
        packet=packet,
        decisions=_decisions(packet),
        key=key,
        taxonomy=TAXONOMY,
        rare=["RARE"],
        g0=None,
        b0_micro_f1=None,
    )
    assert report["schema"] == "radon.slm-evaluation-gates.v1"
    assert report["human_review_n"] == 200
    assert report["runId"] == "synthetic-run"


def test_score_review_rejects_duplicate_and_foreign_mapped_ids() -> None:
    gold, predictions, packet, key = _built()
    decisions = _decisions(packet)
    duplicated = dict(key)
    review_ids = list(duplicated["reviewIdToPostId"])
    duplicated["reviewIdToPostId"] = dict(duplicated["reviewIdToPostId"])
    duplicated["reviewIdToPostId"][review_ids[1]] = duplicated["reviewIdToPostId"][review_ids[0]]
    with pytest.raises(ValueError, match="duplicated or outside the pinned holdout"):
        score_review(
            gold_test=gold, predictions=predictions, packet=packet, decisions=decisions,
            key=duplicated, taxonomy=TAXONOMY, rare=["RARE"], g0=None, b0_micro_f1=None,
        )

    foreign = dict(key)
    foreign["reviewIdToPostId"] = dict(foreign["reviewIdToPostId"])
    foreign["reviewIdToPostId"][review_ids[0]] = "not-in-holdout"
    with pytest.raises(ValueError, match="duplicated or outside the pinned holdout"):
        score_review(
            gold_test=gold, predictions=predictions, packet=packet, decisions=decisions,
            key=foreign, taxonomy=TAXONOMY, rare=["RARE"], g0=None, b0_micro_f1=None,
        )


def test_score_review_rejects_malformed_decisions() -> None:
    gold, predictions, packet, key = _built()
    decisions = _decisions(packet)
    decisions["decisions"][0]["humanTags"] = ["RARE"]
    with pytest.raises(ValueError, match="Missing or invalid human labels"):
        score_review(
            gold_test=gold, predictions=predictions, packet=packet, decisions=decisions,
            key=key, taxonomy=TAXONOMY, rare=["RARE"], g0=None, b0_micro_f1=None,
        )
